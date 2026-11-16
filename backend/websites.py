from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.auth import get_current_user
from backend.checks import run_check
from backend.database import get_db
from backend.models import DEGRADED, OPERATIONAL, MonitoringCheck, User, Website
from backend.monitor import UnsafeURLError, coerce_scheme, validate_public_url
from backend.schemas import CheckOut, WebsiteIn, WebsiteOut, WebsiteUpdate

router = APIRouter(prefix="/websites", tags=["websites"])

MAX_WEBSITES_PER_USER = 25
STATUS_FILTERS = {"operational", "degraded", "down", "unknown", "issues"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _display(site: Website) -> str:
    return site.display_name or (urlparse(site.url).hostname or site.url)


def _owned(db: Session, user: User, website_id: int) -> Website:
    site = db.scalar(select(Website).where(Website.id == website_id, Website.user_id == user.id))
    if site is None:
        raise HTTPException(404, "Website not found")
    return site


def _latest_check(db: Session, website_id: int):
    return db.scalar(
        select(MonitoringCheck)
        .where(MonitoringCheck.website_id == website_id)
        .order_by(MonitoringCheck.checked_at.desc(), MonitoringCheck.id.desc())
        .limit(1)
    )


def _website_out(db: Session, site: Website) -> WebsiteOut:
    out = WebsiteOut.model_validate(site)
    latest = _latest_check(db, site.id)
    out.latest = CheckOut.model_validate(latest) if latest else None
    return out


def _apply_status(query, status: str | None):
    if not status:
        return query
    if status not in STATUS_FILTERS:
        raise HTTPException(400, "Invalid status filter")
    if status == "issues":
        return query.where(MonitoringCheck.health_status != OPERATIONAL)
    return query.where(MonitoringCheck.health_status == status)


def _bucket_seconds(hours: int) -> int:
    if hours <= 24:
        return 1800
    if hours <= 168:
        return 10800
    return 43200


def _site_analytics(db: Session, site: Website, hours: int) -> dict:
    since = _now() - timedelta(hours=hours)
    rows = db.execute(
        select(
            MonitoringCheck.checked_at,
            MonitoringCheck.response_time_ms,
            MonitoringCheck.health_status,
            MonitoringCheck.is_reachable,
        )
        .where(MonitoringCheck.website_id == site.id, MonitoringCheck.checked_at >= since)
        .order_by(MonitoringCheck.checked_at)
    ).all()

    bucket = _bucket_seconds(hours)
    total = healthy = degraded = down = reachable = 0
    ms_values: list[float] = []
    buckets: dict[int, dict] = {}
    for ts, ms, st, reach in rows:
        total += 1
        if st == OPERATIONAL:
            healthy += 1
        elif st == DEGRADED:
            degraded += 1
        else:
            down += 1
        if reach:
            reachable += 1
        if ms is not None:
            ms_values.append(ms)
        key = int(ts.timestamp() // bucket * bucket)
        b = buckets.setdefault(key, {"sum": 0.0, "n": 0, "checks": 0, "healthy": 0})
        b["checks"] += 1
        if st == OPERATIONAL:
            b["healthy"] += 1
        if ms is not None:
            b["sum"] += ms
            b["n"] += 1

    series = [
        {
            "t": datetime.fromtimestamp(k, tz=timezone.utc).isoformat(),
            "avg_ms": round(v["sum"] / v["n"], 1) if v["n"] else None,
            "checks": v["checks"],
            "healthy": v["healthy"],
            "issues": v["checks"] - v["healthy"],
        }
        for k, v in sorted(buckets.items())
    ]
    return {
        "website_id": site.id,
        "hours": hours,
        "total": total,
        "healthy": healthy,
        "degraded": degraded,
        "down": down,
        "reachable": reachable,
        # Observed check availability (share of operational checks) -- NOT an SLA figure.
        "availability_pct": round(healthy / total * 100, 2) if total else None,
        "avg_ms": round(sum(ms_values) / len(ms_values), 1) if ms_values else None,
        "min_ms": round(min(ms_values), 1) if ms_values else None,
        "max_ms": round(max(ms_values), 1) if ms_values else None,
        "series": series,
    }


# ---- collection routes (declared before /{website_id}) ---------------------

@router.post("", response_model=WebsiteOut, status_code=201)
def add_website(data: WebsiteIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    count = db.scalar(select(func.count()).select_from(Website).where(Website.user_id == user.id))
    if count >= MAX_WEBSITES_PER_USER:
        raise HTTPException(400, f"You can monitor up to {MAX_WEBSITES_PER_USER} websites")
    try:
        url = validate_public_url(coerce_scheme(data.url))
    except UnsafeURLError as e:
        raise HTTPException(400, str(e))
    name = (data.display_name or "").strip() or None
    site = Website(user_id=user.id, url=url, display_name=name)
    db.add(site)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "You are already monitoring this website")
    db.refresh(site)
    return _website_out(db, site)


@router.get("", response_model=list[WebsiteOut])
def list_websites(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    sites = db.scalars(select(Website).where(Website.user_id == user.id).order_by(Website.created_at)).all()
    return [_website_out(db, s) for s in sites]


@router.get("/overview")
def overview(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    sites = db.scalars(select(Website).where(Website.user_id == user.id)).all()
    counts = {"operational": 0, "degraded": 0, "down": 0, "unknown": 0}
    last = None
    for s in sites:
        c = _latest_check(db, s.id)
        key = c.health_status if c and c.health_status in counts else "unknown"
        counts[key] += 1
        if c and (last is None or c.checked_at > last):
            last = c.checked_at

    avg_ms = availability = None
    checks_24h = 0
    ids = [s.id for s in sites]
    if ids:
        row = db.execute(
            select(
                func.count(MonitoringCheck.id),
                func.avg(MonitoringCheck.response_time_ms),
                func.sum(case((MonitoringCheck.health_status == OPERATIONAL, 1), else_=0)),
            ).where(MonitoringCheck.website_id.in_(ids), MonitoringCheck.checked_at >= _now() - timedelta(hours=24))
        ).one()
        checks_24h = row[0] or 0
        avg_ms = round(row[1], 1) if row[1] is not None else None
        healthy = row[2] or 0
        availability = round(healthy / checks_24h * 100, 2) if checks_24h else None

    return {
        "total": len(sites),
        **counts,
        "last_checked_at": last.isoformat() if last else None,
        "avg_response_ms": avg_ms,
        "availability_pct": availability,
        "checks_24h": checks_24h,
    }


@router.get("/activity")
def activity(
    website_id: int | None = None,
    status: str | None = None,
    hours: int | None = Query(None, ge=1, le=720),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = (
        select(MonitoringCheck, Website)
        .join(Website, Website.id == MonitoringCheck.website_id)
        .where(Website.user_id == user.id)
    )
    if website_id:
        q = q.where(MonitoringCheck.website_id == website_id)
    q = _apply_status(q, status)
    if hours:
        q = q.where(MonitoringCheck.checked_at >= _now() - timedelta(hours=hours))
    q = q.order_by(MonitoringCheck.checked_at.desc(), MonitoringCheck.id.desc()).limit(limit).offset(offset)
    return [
        {
            **CheckOut.model_validate(c).model_dump(mode="json"),
            "website_name": _display(w),
            "website_url": w.url,
        }
        for c, w in db.execute(q).all()
    ]


@router.get("/analytics")
def analytics(
    hours: int = Query(24, ge=1, le=720),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sites = db.scalars(select(Website).where(Website.user_id == user.id).order_by(Website.created_at)).all()
    return {
        "hours": hours,
        "bucket_seconds": _bucket_seconds(hours),
        "websites": [
            {"id": s.id, "name": _display(s), "url": s.url, **_site_analytics(db, s, hours)} for s in sites
        ],
    }


# ---- single website routes -------------------------------------------------

@router.get("/{website_id}", response_model=WebsiteOut)
def get_website(website_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _website_out(db, _owned(db, user, website_id))


@router.put("/{website_id}", response_model=WebsiteOut)
def update_website(
    website_id: int,
    data: WebsiteUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    site = _owned(db, user, website_id)
    if "display_name" in data.model_fields_set:
        site.display_name = (data.display_name or "").strip() or None
    if data.monitoring_enabled is not None:
        site.monitoring_enabled = data.monitoring_enabled
    db.commit()
    db.refresh(site)
    return _website_out(db, site)


@router.delete("/{website_id}", status_code=204)
def delete_website(website_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    site = _owned(db, user, website_id)
    db.delete(site)
    db.commit()
    return Response(status_code=204)


@router.post("/{website_id}/check", response_model=CheckOut)
def check_now(website_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    site = _owned(db, user, website_id)
    check = run_check(db, site)
    if check is None:
        raise HTTPException(409, "A check for this website is already in progress")
    return check


@router.get("/{website_id}/history", response_model=list[CheckOut])
def website_history(
    website_id: int,
    status: str | None = None,
    hours: int | None = Query(None, ge=1, le=720),
    limit: int = Query(50, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _owned(db, user, website_id)
    q = select(MonitoringCheck).where(MonitoringCheck.website_id == website_id)
    q = _apply_status(q, status)
    if hours:
        q = q.where(MonitoringCheck.checked_at >= _now() - timedelta(hours=hours))
    q = q.order_by(MonitoringCheck.checked_at.desc(), MonitoringCheck.id.desc()).limit(limit)
    return db.scalars(q).all()


@router.get("/{website_id}/stats")
def website_stats(
    website_id: int,
    hours: int = Query(24, ge=1, le=720),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _site_analytics(db, _owned(db, user, website_id), hours)