import sqlite3
from typing import Iterator

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.config import DATABASE_URL, DB_PATH

_is_sqlite = DATABASE_URL.startswith("sqlite")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record):
    """Enforce foreign keys and allow the scheduler and API to share the DB."""
    if _is_sqlite:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from backend import models  # noqa: F401  (registers tables on Base)

    Base.metadata.create_all(bind=engine)


# ---------------------------------------------------------------------------
# LEGACY: original sqlite3 helpers used by the current main.py.
# They stay until Phase 3 replaces main.py, then they get removed.
# ---------------------------------------------------------------------------


def get_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def create_table():
    connection = get_connection()
    connection.execute("""
        CREATE TABLE IF NOT EXISTS monitoring_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            status_code INTEGER,
            response_time_ms REAL,
            is_available BOOLEAN,
            is_healthy BOOLEAN,
            checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    connection.commit()
    connection.close()


def save_check(result):
    connection = get_connection()
    connection.execute("""
        INSERT INTO monitoring_history
        (url, status_code, response_time_ms, is_available, is_healthy)
        VALUES (?, ?, ?, ?, ?)
    """, (
        result.get("url"),
        result.get("status_code"),
        result.get("response_time_ms"),
        result.get("is_available"),
        result.get("is_healthy"),
    ))
    connection.commit()
    connection.close()


def get_history(url):
    connection = get_connection()
    cursor = connection.execute("""
        SELECT * FROM monitoring_history
        WHERE url = ?
        ORDER BY checked_at DESC
        LIMIT 20
    """, (url,))
    history = [dict(row) for row in cursor.fetchall()]
    connection.close()
    return history


def get_stats(url):
    connection = get_connection()
    cursor = connection.execute("""
        SELECT
            COUNT(*) AS total_checks,
            SUM(CASE WHEN is_available = 1 THEN 1 ELSE 0 END) AS available_checks,
            SUM(CASE WHEN is_healthy = 1 THEN 1 ELSE 0 END) AS healthy_checks,
            AVG(response_time_ms) AS average_response_time,
            MIN(response_time_ms) AS fastest_response,
            MAX(response_time_ms) AS slowest_response
        FROM monitoring_history
        WHERE url = ?
    """, (url,))
    stats = dict(cursor.fetchone())
    connection.close()

    total = stats["total_checks"] or 0
    available = stats["available_checks"] or 0
    healthy = stats["healthy_checks"] or 0

    stats["uptime_percentage"] = round((available / total) * 100, 2) if total else 0
    stats["health_percentage"] = round((healthy / total) * 100, 2) if total else 0
    return stats


if __name__ == "__main__":
    init_db()
    print("Tables:", sorted(inspect(engine).get_table_names()))