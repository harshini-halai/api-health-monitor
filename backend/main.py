
from pathlib import Path
from unittest import result
from backend.database import create_table, save_check, get_history
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl
from backend.database import get_stats
from backend.monitor import check_website


app = FastAPI(
    title="API Health Monitor",
    description="A simple website availability monitoring tool",
    version="1.0.0"
)

@app.on_event("startup")
def startup():
    create_table()

# Locate the frontend folder
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


# Serve CSS and JavaScript files
app.mount(
    "/static",
    StaticFiles(directory=FRONTEND_DIR),
    name="static"
)


class MonitorRequest(BaseModel):
    url: HttpUrl


@app.get("/")
def home():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/health")
def health_check():
    return {
        "status": "healthy"
    }


@app.post("/monitor")
def monitor_website(data: MonitorRequest):

    try:
        result = check_website(str(data.url))
        save_check(result)
        return result

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error)
        )
@app.get("/history")
def monitoring_history(url: HttpUrl):

    return get_history(str(url))

@app.get("/stats")
def website_stats(url: HttpUrl):

    return get_stats(str(url))