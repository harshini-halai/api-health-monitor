import logging
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend import security
from backend.alert_routes import router as alerts_router
from backend.auth import router as auth_router
from backend.database import init_db
from backend.monitor import UnsafeURLError, check_website, coerce_scheme, validate_public_url
from backend.scheduler import start_scheduler, stop_scheduler
from backend.schemas import DemoIn
from backend.websites import router as websites_router

logging.basicConfig(level=logging.INFO)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    security._secret()  # fail fast with a clear message if JWT_SECRET is missing
    init_db()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(
    title="Website Health Monitor",
    description="Monitor website availability, response time and history.",
    version="2.1.0",
    lifespan=lifespan,
)



app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^chrome-extension://.*$",
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response


# ---- tiny in-memory rate limit for the public demo endpoint ---------------
_demo_hits: dict[str, deque] = defaultdict(deque)
_demo_lock = threading.Lock()
DEMO_LIMIT, DEMO_WINDOW = 8, 60


def _demo_allowed(ip: str) -> bool:
    now = time.monotonic()
    with _demo_lock:
        q = _demo_hits[ip]
        while q and now - q[0] > DEMO_WINDOW:
            q.popleft()
        if len(q) >= DEMO_LIMIT:
            return False
        q.append(now)
        return True


app.include_router(auth_router)
app.include_router(websites_router)
app.include_router(alerts_router)


@app.get("/health", tags=["system"])
def health_check():
    return {"status": "healthy"}


@app.post("/demo/check", tags=["demo"])
def demo_check(data: DemoIn, request: Request):
    """Public live check for the landing page. Nothing is saved."""
    ip = request.client.host if request.client else "unknown"
    if not _demo_allowed(ip):
        raise HTTPException(429, "Too many demo checks. Please wait a minute and try again.")
    try:
        url = validate_public_url(coerce_scheme(data.url))
    except UnsafeURLError as e:
        raise HTTPException(400, str(e))
    result = check_website(url)
    result["demo"] = True
    return result


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/dashboard", include_in_schema=False)
def dashboard():
    return FileResponse(FRONTEND_DIR / "dashboard.html")


@app.get("/dashboard/alerts", include_in_schema=False)
def dashboard_alerts():
    return FileResponse(FRONTEND_DIR / "alerts.html")