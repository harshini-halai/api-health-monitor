import os
import tempfile
from datetime import timedelta
from pathlib import Path

# Must be set BEFORE importing anything from backend.
os.environ.setdefault("DATABASE_URL", "sqlite:///" + (Path(tempfile.mkdtemp()) / "alerts_test.db").as_posix())
os.environ.setdefault("JWT_SECRET", "test-secret-" + "x" * 40)

import pytest
from sqlalchemy import select

from backend import alerts, config, security
from backend.database import Base, SessionLocal, engine, init_db
from backend.models import (
    DOWN,
    OPERATIONAL,
    AlertSettings,
    Incident,
    MonitoringCheck,
    Notification,
    User,
    Website,
    utcnow,
)
from backend.notifications import EmailError

BASE_TIME = utcnow()
_tick = [0]


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.test.invalid")
    monkeypatch.setattr(config, "EMAIL_FROM", "Monitor <monitor@test.invalid>")
    Base.metadata.drop_all(engine)
    init_db()
    yield


def setup_site(db, verified=True, threshold=2):
    user = User(name="T", email="t@example.com", password_hash=security.hash_password("correct-horse-12"))
    db.add(user)
    db.commit()
    site = Website(user_id=user.id, url="https://example.com")
    db.add(site)
    db.add(
        AlertSettings(
            user_id=user.id, email=user.email, failure_threshold=threshold,
            verified_at=utcnow() if verified else None,
        )
    )
    db.commit()
    return site


def add_check(db, site, health):
    _tick[0] += 1
    check = MonitoringCheck(
        website_id=site.id, requested_url=site.url, health_status=health,
        is_reachable=health != DOWN, checked_at=BASE_TIME + timedelta(seconds=_tick[0]),
        error_message="boom" if health == DOWN else None,
    )
    db.add(check)
    db.commit()
    db.refresh(check)
    alerts.process_check(db, site, check)
    return check


def kinds(db):
    return [n.kind for n in db.scalars(select(Notification).order_by(Notification.id))]


def test_no_incident_before_threshold():
    with SessionLocal() as db:
        site = setup_site(db)
        add_check(db, site, DOWN)
        assert db.scalars(select(Incident)).all() == []
        assert kinds(db) == []


def test_incident_opens_after_threshold_with_one_down_email():
    with SessionLocal() as db:
        site = setup_site(db)
        first = add_check(db, site, DOWN)
        add_check(db, site, DOWN)
        add_check(db, site, DOWN)  # still down: must NOT create another incident or email
        incidents = db.scalars(select(Incident)).all()
        assert len(incidents) == 1
        assert incidents[0].resolved_at is None
        assert incidents[0].started_at == first.checked_at
        assert kinds(db) == ["down"]


def test_recovery_resolves_incident_and_sends_one_recovered_email():
    with SessionLocal() as db:
        site = setup_site(db)
        add_check(db, site, DOWN)
        add_check(db, site, DOWN)
        add_check(db, site, OPERATIONAL)
        add_check(db, site, OPERATIONAL)  # extra healthy check: no extra email
        incident = db.scalars(select(Incident)).one()
        assert incident.resolved_at is not None
        assert kinds(db) == ["down", "recovered"]


def test_unverified_address_gets_no_emails_but_incident_is_still_tracked():
    with SessionLocal() as db:
        site = setup_site(db, verified=False)
        add_check(db, site, DOWN)
        add_check(db, site, DOWN)
        add_check(db, site, OPERATIONAL)
        assert len(db.scalars(select(Incident)).all()) == 1
        assert kinds(db) == []


def test_dispatch_marks_sent(monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "send_email", lambda to, subject, text, html=None: sent.append((to, subject)))
    with SessionLocal() as db:
        site = setup_site(db)
        add_check(db, site, DOWN)
        add_check(db, site, DOWN)
    assert alerts.dispatch_pending() == 1
    assert len(sent) == 1 and sent[0][0] == "t@example.com"
    with SessionLocal() as db:
        n = db.scalars(select(Notification)).one()
        assert n.status == "sent" and n.sent_at is not None


def test_failed_send_is_retried_later_not_immediately(monkeypatch):
    def boom(*args, **kwargs):
        raise EmailError("smtp down")

    monkeypatch.setattr(alerts, "send_email", boom)
    with SessionLocal() as db:
        site = setup_site(db)
        add_check(db, site, DOWN)
        add_check(db, site, DOWN)
    assert alerts.dispatch_pending() == 0
    with SessionLocal() as db:
        n = db.scalars(select(Notification)).one()
        assert n.status == "pending" and n.attempts == 1 and n.next_attempt_at > utcnow()
    assert alerts.dispatch_pending() == 0  # backoff: not due yet
    with SessionLocal() as db:
        assert db.scalars(select(Notification)).one().attempts == 1


def test_verification_token_is_single_use():
    with SessionLocal() as db:
        setup_site(db, verified=False)
        settings = db.scalars(select(AlertSettings)).one()
        alerts.start_verification(db, settings)
        note = db.scalars(select(Notification)).one()
        token = note.body_text.split("token=")[1].split()[0]
        assert alerts.confirm_verification(db, "wrong-token") is False
        assert alerts.confirm_verification(db, token) is True
        assert alerts.confirm_verification(db, token) is False