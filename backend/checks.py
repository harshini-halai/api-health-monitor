import threading

from sqlalchemy.orm import Session

from backend.alerts import process_check
from backend.models import MonitoringCheck, Website
from backend.monitor import check_website

_lock = threading.Lock()
_running: set[int] = set()


def run_check(db: Session, website: Website) -> MonitoringCheck | None:
    """Run and store one check. Returns None if a check for this website is already running
    (prevents the scheduler and a manual click from overlapping)."""
    with _lock:
        if website.id in _running:
            return None
        _running.add(website.id)
    try:
        result = check_website(website.url)
        check = MonitoringCheck(website_id=website.id, **result)
        db.add(check)
        db.commit()
        db.refresh(check)
        process_check(db, website, check)  # never raises
        return check
    finally:
        with _lock:
            _running.discard(website.id)