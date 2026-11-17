import threading
import time
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import alerts, config
from backend.auth import get_current_user
from backend.database import get_db
from backend.models import Incident, User, Website, utcnow
from backend.notifications import EmailError, send_email

router = APIRouter(prefix="/alerts", tags=["alerts"])

TEST_COOLDOWN_SECONDS = 30
_last_test: dict[int, float] = {}
_test_lock = threading.Lock()


class AlertSettingsIn(BaseModel):
    email: EmailStr | None = None
    enabled: bool | None = None
    failure_threshold: int | None = Field(default=None, ge=1, le=5)


class AlertSettingsOut(BaseModel):
    email: str
    enabled: bool
    failure_threshold: int
    verified: bool
    verification_sent_at: datetime | None
    email_configured: bool


def _out(s) -> AlertSettingsOut:
    return AlertSettingsOut(
        email=s.email,
        enabled=s.enabled,
        failure_threshold=s.failure_threshold,
        verified=s.verified_at is not None,
        verification_sent_at=s.verification_sent_at,
        email_configured=config.email_configured(),
    )


@router.get("/settings", response_model=AlertSettingsOut)
def get_settings(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _out(alerts.get_or_create_settings(db, user))


@router.put("/settings", response_model=AlertSettingsOut)
def update_settings(data: AlertSettingsIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = alerts.get_or_create_settings(db, user)
    if data.email is not None:
        new_email = data.email.lower()
        if new_email != s.email.lower():
            s.email = new_email
            # A new address must be verified again before any alert is sent to it.
            s.verified_at = None
            s.verify_token_hash = None
            s.verify_expires_at = None
    if data.enabled is not None:
        s.enabled = data.enabled
    if data.failure_threshold is not None:
        s.failure_threshold = data.failure_threshold
    db.commit()
    db.refresh(s)
    return _out(s)


@router.post("/verification", status_code=202)
def send_verification(
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not config.email_configured():
        raise HTTPException(503, "Email sending is not configured on this server")
    s = alerts.get_or_create_settings(db, user)
    if s.verified_at:
        raise HTTPException(409, "This email address is already verified")
    wait = alerts.verification_cooldown_left(s)
    if wait:
        raise HTTPException(429, f"Please wait {wait} seconds before requesting another email")
    alerts.start_verification(db, s)
    background.add_task(alerts.dispatch_pending)
    return {"detail": f"Verification email queued for {s.email}. Check your inbox (and spam folder)."}


@router.get("/verify", include_in_schema=False)
def verify(token: str = "", db: Session = Depends(get_db)):
    ok = alerts.confirm_verification(db, token)
    return RedirectResponse(f"/dashboard/alerts?verified={'1' if ok else '0'}", status_code=303)


@router.post("/test")
def send_test(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not config.email_configured():
        raise HTTPException(503, "Email sending is not configured on this server")
    s = alerts.get_or_create_settings(db, user)
    if not s.verified_at:
        raise HTTPException(400, "Verify your email address first")
    now = time.monotonic()
    with _test_lock:
        last = _last_test.get(user.id)
        if last is not None and now - last < TEST_COOLDOWN_SECONDS:
            raise HTTPException(429, "Please wait a few seconds before sending another test email")
        _last_test[user.id] = now
    text_body, html_body = alerts._render(
        heading="Test email",
        color="#5457ee",
        intro="Email alerts are working. You will get a message like this when a website goes down or recovers.",
        rows=[],
    )
    try:
        send_email(s.email, "Test email from Website Health Monitor", text_body, html_body)
    except EmailError as e:
        raise HTTPException(502, str(e))
    return {"detail": f"Test email sent to {s.email}"}


@router.get("/incidents")
def list_incidents(
    status: str | None = Query(None, pattern="^(open|resolved)$"),
    website_id: int | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = (
        select(Incident, Website)
        .join(Website, Website.id == Incident.website_id)
        .where(Website.user_id == user.id)
    )
    if status == "open":
        q = q.where(Incident.resolved_at.is_(None))
    elif status == "resolved":
        q = q.where(Incident.resolved_at.is_not(None))
    if website_id:
        q = q.where(Incident.website_id == website_id)
    q = q.order_by(Incident.started_at.desc(), Incident.id.desc()).limit(limit).offset(offset)

    now = utcnow()
    return [
        {
            "id": i.id,
            "website_id": w.id,
            "website_name": alerts.site_label(w),
            "website_url": w.url,
            "started_at": i.started_at.isoformat(),
            "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
            "status": "open" if i.resolved_at is None else "resolved",
            "duration_seconds": ((i.resolved_at or now) - i.started_at).total_seconds(),
            "first_error": i.first_error,
            "last_status_code": i.last_status_code,
        }
        for i, w in db.execute(q).all()
    ]