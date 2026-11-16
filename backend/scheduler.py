import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import delete, select

from backend import config
from backend.checks import run_check
from backend.database import SessionLocal
from backend.models import MonitoringCheck, Website

log = logging.getLogger("whm.scheduler")
scheduler = BackgroundScheduler(timezone="UTC")

MAX_WORKERS = 5
RETENTION_DAYS = 90


def _check_one(website_id: int) -> None:
    try:
        with SessionLocal() as db:
            site = db.get(Website, website_id)
            if site is not None and site.monitoring_enabled:
                run_check(db, site)
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


def prune_old_checks() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    with SessionLocal() as db:
        db.execute(delete(MonitoringCheck).where(MonitoringCheck.checked_at < cutoff))
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
        replace_existing=True,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=15),
    )
    scheduler.add_job(prune_old_checks, "interval", hours=24, id="prune", max_instances=1, replace_existing=True)
    scheduler.start()
    log.info("Scheduler started: every %d minute(s)", config.CHECK_INTERVAL_MINUTES)


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)