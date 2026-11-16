from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from backend.database import Base

# Health status values stored in monitoring_checks.health_status
OPERATIONAL = "operational"
DEGRADED = "degraded"
DOWN = "down"
UNKNOWN = "unknown"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Store UTC, always return timezone-aware UTC (SQLite drops tzinfo)."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    websites: Mapped[list["Website"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


class Website(Base):
    __tablename__ = "websites"
    __table_args__ = (UniqueConstraint("user_id", "url", name="uq_website_user_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(String(2048))
    display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    monitoring_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    user: Mapped[User] = relationship(back_populates="websites")
    checks: Mapped[list["MonitoringCheck"]] = relationship(
        back_populates="website", cascade="all, delete-orphan", passive_deletes=True
    )


class MonitoringCheck(Base):
    __tablename__ = "monitoring_checks"
    __table_args__ = (Index("ix_checks_website_time", "website_id", "checked_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    website_id: Mapped[int] = mapped_column(
        ForeignKey("websites.id", ondelete="CASCADE")
    )
    requested_url: Mapped[str] = mapped_column(String(2048))
    final_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_time_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_reachable: Mapped[bool] = mapped_column(Boolean, default=False)
    health_status: Mapped[str] = mapped_column(String(16), default=UNKNOWN)
    page_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    server: Mapped[str | None] = mapped_column(String(200), nullable=True)
    redirect_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)

    website: Mapped[Website] = relationship(back_populates="checks")


class ExtensionPairing(Base):
    """Short-lived, single-use code shown in the dashboard. Only a hash is stored."""

    __tablename__ = "extension_pairings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class ExtensionToken(Base):
    """Long-lived extension credential. Only a hash is stored; revoke by timestamp."""

    __tablename__ = "extension_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)