import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import delete, select

from backend import config
from backend.alerts import dispatch_pending
from backend.checks import run_check
from backend.database import SessionLocal
from backend.models import MonitoringCheck, Notification, Website

log = logging.getLogger("whm.scheduler")
scheduler = BackgroundScheduler(timezone="UTC")

# Hide the "Running job ... executed successfully" spam (warnings and errors still show)
logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)

MAX_WORKERS = 5
CHECK_RETENTION_DAYS = 90
NOTIFICATION_RETENTION_DAYS = 30
MISFIRE_GRACE_SECONDS = 20  # a job up to 20 s late still runs instead of being skipped


def _check_one(website_id: int) -> None:
    try:
        with SessionLocal() as db:
            site = db.get(Website, website_id)
            if site is not None and site.monitoring_enabled:
                check = run_check(db, site)
                if check is not None:
                    ms = f"{check.response_time_ms:.0f} ms" if check.response_time_ms is not None else "n/a"
                    log.info(
                        "Checked %s -> %s (HTTP %s, %s)%s",
                        site.url,
                        check.health_status,
                        check.status_code,
                        ms,
                        f" error: {check.error_message}" if check.error_message else "",
                    )
    except Exception:
        log.exception("Scheduled check failed for website %s", website_id)


def run_all_checks() -> None:
    with SessionLocal() as db:
        ids = list(db.scalars(select(Website.id).where(Website.monitoring_enabled.is_(True))))
    if not ids:
        return
    log.info("Scheduled run: checking %d website(s)", len(ids))
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        list(pool.map(_check_one, ids))
    dispatch_pending()  # send any alert emails queued by this run right away


def prune_old_data() -> None:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.execute(delete(MonitoringCheck).where(MonitoringCheck.checked_at < now - timedelta(days=CHECK_RETENTION_DAYS)))
        db.execute(delete(Notification).where(Notification.created_at < now - timedelta(days=NOTIFICATION_RETENTION_DAYS)))
        db.commit()


def start_scheduler() -> None:
    if not config.SCHEDULER_ENABLED:
        log.info("Scheduler disabled (SCHEDULER_ENABLED=false)")
        return
    if scheduler.running:
        return
    scheduler.add_job(
        run_all_checks,
        "interval",
        minutes=config.CHECK_INTERVAL_MINUTES,
        id="check_all",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=MISFIRE_GRACE_SECONDS,
        replace_existing=True,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=15),
    )
    # Safety net: sends emails queued outside a scheduled run and retries failed ones.
    scheduler.add_job(
        dispatch_pending, "interval", seconds=30, id="dispatch_notifications",
        max_instances=1, coalesce=True, misfire_grace_time=MISFIRE_GRACE_SECONDS,
        replace_existing=True,
    )
    scheduler.add_job(
        prune_old_data, "interval", hours=24, id="prune",
        max_instances=1, misfire_grace_time=3600, replace_existing=True,
    )
    scheduler.add_job(keep_render_awake, "interval", minutes=10, id="keep_render_awake", replace_existing=True)
    scheduler.start()
    log.info("Scheduler started: checks every %d minute(s)", config.CHECK_INTERVAL_MINUTES)


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


def keep_render_awake():
    """Ping /health every 10 minutes on Render so free-tier instance never sleeps."""
    import os
    import urllib.request
    base = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    if not base:
        return
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=10) as resp:
            resp.read()
    except Exception:
        pass
