import html
import logging
import math
import secrets
import threading
from datetime import timedelta
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend import config, security
from backend.database import SessionLocal
from backend.models import (
    DOWN,
    AlertSettings,
    Incident,
    MonitoringCheck,
    Notification,
    User,
    Website,
    utcnow,
)
from backend.notifications import EmailError, send_email

log = logging.getLogger("whm.alerts")

DEFAULT_THRESHOLD = 2
MAX_ATTEMPTS = 5
VERIFY_TTL_HOURS = 24
VERIFY_COOLDOWN_SECONDS = 60

_dispatch_lock = threading.Lock()


# ---- small helpers -----------------------------------------------------------

def _clean(value) -> str:
    """Collapse whitespace/newlines (prevents header injection via website names)."""
    return " ".join(str(value).split())


def site_label(website: Website) -> str:
    return _clean(website.display_name or urlparse(website.url).hostname or website.url)


def _fmt_utc(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} seconds"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} h {minutes} min"
    days, hours = divmod(hours, 24)
    return f"{days} d {hours} h"


def _render(*, heading, color, intro, rows, link_label=None, link_url=None, footer=""):
    """Return (plain_text, html). Every dynamic value is HTML-escaped."""
    lines = [intro, ""] + [f"{k}: {v}" for k, v in rows]
    if link_url:
        lines += ["", f"{link_label}: {link_url}"]
    if footer:
        lines += ["", footer]
    text_body = "\n".join(lines)

    e = html.escape
    rows_html = "".join(
        f'<tr><td style="padding:6px 14px 6px 0;color:#667085;white-space:nowrap">{e(k)}</td>'
        f'<td style="padding:6px 0;font-weight:600">{e(str(v))}</td></tr>'
        for k, v in rows
    )
    button = (
        f'<p style="margin:22px 0"><a href="{e(link_url)}" style="background:#5457ee;color:#ffffff;'
        f'padding:10px 18px;border-radius:8px;text-decoration:none;font-weight:600">{e(link_label)}</a></p>'
        if link_url
        else ""
    )
    html_body = (
        '<div style="font-family:Arial,sans-serif;max-width:540px;margin:auto;padding:24px;color:#0e1530">'
        f'<div style="border-left:5px solid {color};padding-left:14px">'
        f'<h2 style="margin:0 0 6px;color:{color}">{e(heading)}</h2><p style="margin:0">{e(intro)}</p></div>'
        f'<table style="margin-top:18px;font-size:14px">{rows_html}</table>{button}'
        f'<p style="color:#667085;font-size:12px">{e(footer)}</p></div>'
    )
    return text_body, html_body


def _enqueue(db: Session, *, user_id, kind, to_email, subject, text_body, html_body,
             website_id=None, incident_id=None) -> None:
    db.add(
        Notification(
            user_id=user_id, website_id=website_id, incident_id=incident_id, kind=kind,
            to_email=to_email, subject=_clean(subject)[:300], body_text=text_body, body_html=html_body,
        )
    )
    db.commit()


def _deliverable(settings: AlertSettings | None) -> bool:
    return bool(settings and settings.enabled and settings.verified_at and config.email_configured())


# ---- settings + verification ---------------------------------------------------

def get_or_create_settings(db: Session, user: User) -> AlertSettings:
    settings = db.scalar(select(AlertSettings).where(AlertSettings.user_id == user.id))
    if settings is None:
        settings = AlertSettings(user_id=user.id, email=user.email)
        db.add(settings)
        try:
            db.commit()
        except IntegrityError:  # created concurrently by another request
            db.rollback()
            settings = db.scalar(select(AlertSettings).where(AlertSettings.user_id == user.id))
        else:
            db.refresh(settings)
    return settings


def verification_cooldown_left(settings: AlertSettings) -> int:
    if not settings.verification_sent_at:
        return 0
    left = VERIFY_COOLDOWN_SECONDS - (utcnow() - settings.verification_sent_at).total_seconds()
    return max(0, math.ceil(left))


def start_verification(db: Session, settings: AlertSettings) -> None:
    token = secrets.token_urlsafe(32)
    now = utcnow()
    settings.verify_token_hash = security.hash_secret(token)  # only the hash is stored
    settings.verify_expires_at = now + timedelta(hours=VERIFY_TTL_HOURS)
    settings.verification_sent_at = now
    db.commit()

    link = f"{config.APP_BASE_URL}/alerts/verify?token={token}"
    text_body, html_body = _render(
        heading="Confirm your alert email",
        color="#5457ee",
        intro="Confirm that you want to receive website alerts at this address.",
        rows=[],
        link_label="Confirm email address",
        link_url=link,
        footer=f"This link expires in {VERIFY_TTL_HOURS} hours. If you did not request it, ignore this email.",
    )
    _enqueue(
        db, user_id=settings.user_id, kind="verify", to_email=settings.email,
        subject="Confirm your email for Website Health Monitor alerts",
        text_body=text_body, html_body=html_body,
    )


def confirm_verification(db: Session, token: str) -> bool:
    if not token or len(token) > 200:
        return False
    settings = db.scalar(
        select(AlertSettings).where(AlertSettings.verify_token_hash == security.hash_secret(token))
    )
    if settings is None or settings.verify_expires_at is None or settings.verify_expires_at < utcnow():
        return False
    settings.verified_at = utcnow()
    settings.verify_token_hash = None  # single use
    settings.verify_expires_at = None
    db.commit()
    return True


# ---- incident state machine ----------------------------------------------------

def process_check(db: Session, website: Website, check: MonitoringCheck) -> None:
    """Called after every stored check. Opens/resolves incidents and queues emails.
    Never raises: an alerting bug must not break monitoring."""
    try:
        _process(db, website, check)
    except Exception:
        db.rollback()
        log.exception("Alert processing failed for website %s", website.id)


def _process(db: Session, website: Website, check: MonitoringCheck) -> None:
    failing = check.health_status == DOWN
    settings = db.scalar(select(AlertSettings).where(AlertSettings.user_id == website.user_id))
    threshold = settings.failure_threshold if settings else DEFAULT_THRESHOLD

    open_incident = db.scalar(
        select(Incident).where(Incident.website_id == website.id, Incident.resolved_at.is_(None))
    )

    if open_incident is not None:
        if not failing:
            open_incident.resolved_at = check.checked_at
            db.commit()
            _queue_recovered(db, website, open_incident, settings)
        return

    if not failing:
        return

    recent = db.scalars(
        select(MonitoringCheck)
        .where(MonitoringCheck.website_id == website.id)
        .order_by(MonitoringCheck.checked_at.desc(), MonitoringCheck.id.desc())
        .limit(threshold)
    ).all()
    if len(recent) < threshold or any(c.health_status != DOWN for c in recent):
        return

    incident = Incident(
        website_id=website.id,
        started_at=recent[-1].checked_at,  # first check of the failing streak
        first_error=check.error_message,
        last_status_code=check.status_code,
    )
    db.add(incident)
    try:
        db.commit()
    except IntegrityError:  # another open incident already exists
        db.rollback()
        return
    db.refresh(incident)
    _queue_down(db, website, incident, check, settings)


def _queue_down(db, website, incident, check, settings) -> None:
    if not _deliverable(settings):
        return
    name = site_label(website)
    text_body, html_body = _render(
        heading="Website is DOWN",
        color="#d4303d",
        intro=f"{name} is not responding as expected.",
        rows=[
            ("Website", name),
            ("URL", website.url),
            ("Down since", _fmt_utc(incident.started_at)),
            ("Reason", check.error_message or "No details available"),
            ("HTTP status", check.status_code or "No response"),
            ("Confirmed by", f"{settings.failure_threshold} consecutive failed check(s)"),
        ],
        link_label="Open in dashboard",
        link_url=f"{config.APP_BASE_URL}/dashboard#site-{website.id}",
        footer="You will get another email when the website recovers.",
    )
    _enqueue(
        db, user_id=website.user_id, kind="down", to_email=settings.email,
        subject=f"[DOWN] {name} is not responding", text_body=text_body, html_body=html_body,
        website_id=website.id, incident_id=incident.id,
    )


def _queue_recovered(db, website, incident, settings) -> None:
    if not _deliverable(settings):
        return
    # Only send "recovered" if we actually told the user it was down.
    told_down = db.scalar(
        select(Notification.id)
        .where(Notification.incident_id == incident.id, Notification.kind == "down")
        .limit(1)
    )
    if told_down is None:
        return
    name = site_label(website)
    duration = (incident.resolved_at - incident.started_at).total_seconds()
    text_body, html_body = _render(
        heading="Website has recovered",
        color="#0f9d58",
        intro=f"{name} is responding normally again.",
        rows=[
            ("Website", name),
            ("URL", website.url),
            ("Down since", _fmt_utc(incident.started_at)),
            ("Recovered at", _fmt_utc(incident.resolved_at)),
            ("Total downtime", _fmt_duration(duration)),
        ],
        link_label="Open in dashboard",
        link_url=f"{config.APP_BASE_URL}/dashboard#site-{website.id}",
    )
    _enqueue(
        db, user_id=website.user_id, kind="recovered", to_email=settings.email,
        subject=f"[RECOVERED] {name} is back up", text_body=text_body, html_body=html_body,
        website_id=website.id, incident_id=incident.id,
    )


# ---- outbox dispatcher -----------------------------------------------------------

def _record_failure(n: Notification, error: str) -> None:
    n.last_error = error[:500]
    if n.attempts >= MAX_ATTEMPTS:
        n.status = "failed"
    else:
        n.next_attempt_at = utcnow() + timedelta(minutes=2 ** n.attempts)  # 2, 4, 8, 16 min
    log.warning("Notification %s failed (attempt %s): %s", n.id, n.attempts, error)


def dispatch_pending(limit: int = 20) -> int:
    """Send due notifications. Returns how many were sent. Never raises.
    The lock makes this safe within ONE process (matches the single-instance design)."""
    if not _dispatch_lock.acquire(blocking=False):
        return 0
    sent = 0
    try:
        with SessionLocal() as db:
            due = db.scalars(
                select(Notification)
                .where(Notification.status == "pending", Notification.next_attempt_at <= utcnow())
                .order_by(Notification.created_at)
                .limit(limit)
            ).all()
            for n in due:
                n.attempts += 1
                try:
                    send_email(n.to_email, n.subject, n.body_text, n.body_html)
                except EmailError as e:
                    _record_failure(n, str(e))
                except Exception:
                    log.exception("Unexpected error sending notification %s", n.id)
                    _record_failure(n, "Unexpected error while sending")
                else:
                    n.status = "sent"
                    n.sent_at = utcnow()
                    n.last_error = None
                    sent += 1
                db.commit()
    except Exception:
        log.exception("Notification dispatch failed")
    finally:
        _dispatch_lock.release()
    return sent