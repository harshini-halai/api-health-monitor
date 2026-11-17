import os
import secrets
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

def _ensure_jwt_secret() -> str:
    val = os.getenv("JWT_SECRET", "").strip()
    if val:
        return val
    secret_file = Path(__file__).resolve().parent.parent / ".jwt_secret"
    if secret_file.exists():
        val = secret_file.read_text(encoding="utf-8").strip()
        if val:
            os.environ["JWT_SECRET"] = val
            return val
    val = secrets.token_urlsafe(48)
    try:
        secret_file.write_text(val, encoding="utf-8")
    except Exception:
        pass
    os.environ["JWT_SECRET"] = val
    return val

_ensure_jwt_secret()

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DB_PATH = BASE_DIR / "monitor.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH.as_posix()}")

JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_MINUTES = int(os.getenv("ACCESS_TOKEN_MINUTES", "60"))
EXTENSION_TOKEN_DAYS = int(os.getenv("EXTENSION_TOKEN_DAYS", "30"))
PAIRING_CODE_MINUTES = int(os.getenv("PAIRING_CODE_MINUTES", "10"))

CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "5"))
REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "10"))
SCHEDULER_ENABLED = os.getenv("SCHEDULER_ENABLED", "true").lower() in ("1", "true", "yes")

# Used to build links inside emails (verification, "open dashboard")
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

# Outgoing email (SMTP)
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "false").lower() in ("1", "true", "yes")  # true for port 465
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "")


def email_configured() -> bool:
    return bool(SMTP_HOST and EMAIL_FROM)