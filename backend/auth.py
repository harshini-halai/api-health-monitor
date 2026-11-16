import io
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend import config, security
from backend.database import get_db
from backend.models import ExtensionPairing, ExtensionToken, User
from backend.schemas import (
    ExtensionStatusOut,
    ExtensionTokenOut,
    LoginIn,
    PairingExchangeIn,
    PairingStartOut,
    RegisterIn,
    TokenOut,
    UserOut,
)

router = APIRouter(tags=["auth"])
bearer = HTTPBearer(auto_error=False)

_DUMMY_HASH = security.hash_password("dummy-password-for-timing")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None:
        raise HTTPException(401, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})

    raw_token = creds.credentials.strip()

    if raw_token.startswith("whm_"):
        token_hash = security.hash_secret(raw_token)
        ext_token = db.scalar(
            select(ExtensionToken).where(ExtensionToken.token_hash == token_hash)
        )
        now = _utcnow()
        if (
            ext_token is None
            or ext_token.revoked_at is not None
            or ext_token.expires_at <= now
        ):
            raise HTTPException(
                401,
                "Extension token invalid, expired, or revoked",
                headers={"WWW-Authenticate": "Bearer"},
            )
        user = db.get(User, ext_token.user_id)
        if user is None:
            raise HTTPException(401, "User not found", headers={"WWW-Authenticate": "Bearer"})
        ext_token.last_used_at = now
        db.commit()
        return user

    user_id = security.decode_access_token(raw_token)
    user = db.get(User, user_id) if user_id else None
    if user is None:
        raise HTTPException(401, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
    return user


def _token_response(user: User) -> TokenOut:
    return TokenOut(
        access_token=security.create_access_token(user.id),
        user=UserOut.model_validate(user),
    )


@router.post("/auth/register", response_model=TokenOut, status_code=201)
def register(data: RegisterIn, db: Session = Depends(get_db)):
    email = data.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "An account with this email already exists")
    user = User(name=data.name, email=email, password_hash=security.hash_password(data.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "An account with this email already exists")
    db.refresh(user)
    return _token_response(user)


@router.post("/auth/login", response_model=TokenOut)
def login(data: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == data.email.lower()))
    ok = security.verify_password(data.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not ok:
        raise HTTPException(401, "Invalid email or password")
    return _token_response(user)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.post("/extension/pair/start", response_model=PairingStartOut, status_code=201)
def start_extension_pairing(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    now = _utcnow()
    db.execute(
        update(ExtensionPairing)
        .where(
            ExtensionPairing.user_id == user.id,
            ExtensionPairing.used_at.is_(None),
        )
        .values(used_at=now)
    )
    code = security.generate_pairing_code()
    normalized = security.normalize_pairing_code(code)
    ttl_minutes = getattr(config, "PAIRING_CODE_MINUTES", 10)
    expires_at = now + timedelta(minutes=ttl_minutes)

    db.add(
        ExtensionPairing(
            user_id=user.id,
            code_hash=security.hash_secret(normalized),
            expires_at=expires_at,
        )
    )
    db.commit()
    return PairingStartOut(
        code=code,
        expires_at=expires_at,
        expires_in_seconds=ttl_minutes * 60,
    )


@router.post("/extension/pair/exchange", response_model=ExtensionTokenOut)
def exchange_extension_pairing(
    data: PairingExchangeIn,
    db: Session = Depends(get_db),
):
    normalized = security.normalize_pairing_code(data.code)
    if len(normalized) != 8:
        raise HTTPException(400, "Invalid pairing code format")

    code_hash = security.hash_secret(normalized)
    pairing = db.scalar(
        select(ExtensionPairing).where(ExtensionPairing.code_hash == code_hash)
    )
    now = _utcnow()

    if pairing is None:
        raise HTTPException(401, "Invalid pairing code")
    if pairing.used_at is not None:
        raise HTTPException(401, "This pairing code has already been used")
    if pairing.expires_at <= now:
        raise HTTPException(401, "This pairing code has expired. Generate a new one from your dashboard.")

    user = db.get(User, pairing.user_id)
    if user is None:
        raise HTTPException(401, "Account not found")

    pairing.used_at = now
    raw_ext_token = security.generate_extension_token()
    ttl_days = getattr(config, "EXTENSION_TOKEN_DAYS", 90)
    expires_at = now + timedelta(days=ttl_days)

    db.add(
        ExtensionToken(
            user_id=user.id,
            token_hash=security.hash_secret(raw_ext_token),
            expires_at=expires_at,
            last_used_at=now,
        )
    )
    db.commit()
    return ExtensionTokenOut(
        access_token=raw_ext_token,
        expires_at=expires_at,
        user=UserOut.model_validate(user),
    )


@router.get("/extension/status", response_model=ExtensionStatusOut)
def extension_status(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    now = _utcnow()
    tokens = db.scalars(
        select(ExtensionToken).where(
            ExtensionToken.user_id == user.id,
            ExtensionToken.revoked_at.is_(None),
            ExtensionToken.expires_at > now,
        )
    ).all()
    last_used = max((t.last_used_at for t in tokens if t.last_used_at), default=None)
    return ExtensionStatusOut(active_tokens=len(tokens), last_used_at=last_used)


@router.post("/extension/disconnect")
def disconnect_extension(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    now = _utcnow()
    raw_token = creds.credentials.strip() if creds else ""
    if raw_token.startswith("whm_"):
        token_hash = security.hash_secret(raw_token)
        db.execute(
            update(ExtensionToken)
            .where(
                ExtensionToken.user_id == user.id,
                ExtensionToken.token_hash == token_hash,
                ExtensionToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
    else:
        db.execute(
            update(ExtensionToken)
            .where(
                ExtensionToken.user_id == user.id,
                ExtensionToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
    db.commit()
    return {"disconnected": True}


@router.get("/extension/download")
def download_extension_bundle():
    ext_dir = Path(__file__).resolve().parent.parent / "extension"
    if not ext_dir.exists():
        raise HTTPException(404, "Extension package not found")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in ext_dir.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, arcname=f"website-health-monitor-extension/{file_path.relative_to(ext_dir)}")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=website-health-monitor-extension.zip"},
    )
