import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from backend import config

MAX_PASSWORD_BYTES = 72  # bcrypt limit
_PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I


def _secret() -> bytes:
    if not config.JWT_SECRET:
        raise RuntimeError(
            "JWT_SECRET is not set. Copy .env.example to .env and set it with:\n"
            '  python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
    return config.JWT_SECRET.encode()


# ---- passwords -------------------------------------------------------------

def hash_password(password: str) -> str:
    raw = password.encode("utf-8")
    if len(raw) > MAX_PASSWORD_BYTES:
        raise ValueError("Password must be at most 72 bytes long")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode())
    except ValueError:
        return False


# ---- website login JWT -----------------------------------------------------

def create_access_token(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=config.ACCESS_TOKEN_MINUTES),
    }
    return jwt.encode(payload, _secret(), algorithm=config.JWT_ALGORITHM)


def decode_access_token(token: str) -> int | None:
    """Return the user id, or None if the token is invalid or expired."""
    try:
        payload = jwt.decode(token, _secret(), algorithms=[config.JWT_ALGORITHM])
        if payload.get("type") != "access":
            return None
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


# ---- extension pairing and tokens -----------------------------------------

def hash_secret(value: str) -> str:
    """Keyed hash for pairing codes and extension tokens (what the DB stores)."""
    return hmac.new(_secret(), value.encode(), hashlib.sha256).hexdigest()


def generate_pairing_code() -> str:
    """8 characters shown as XXXX-XXXX, about 40 bits of randomness."""
    raw = "".join(secrets.choice(_PAIRING_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_pairing_code(code: str) -> str:
    return code.replace("-", "").replace(" ", "").upper()


def generate_extension_token() -> str:
    return "whm_" + secrets.token_urlsafe(32)


if __name__ == "__main__":
    h = hash_password("correct horse battery")
    assert verify_password("correct horse battery", h)
    assert not verify_password("wrong", h)
    assert decode_access_token(create_access_token(7)) == 7
    assert decode_access_token("garbage") is None
    code = generate_pairing_code()
    assert hash_secret(normalize_pairing_code(code)) == hash_secret(normalize_pairing_code(code.lower()))
    print("security self-check passed; sample pairing code:", code)