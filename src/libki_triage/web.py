from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import settings
from .db import connect, init_db

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="libki-triage", version="0.0.1")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    init_db(settings.db_path)
    with connect(settings.db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                r.owner,
                r.name,
                r.default_branch,
                r.last_harvested_at,
                SUM(CASE WHEN i.is_pull_request = 0 THEN 1 ELSE 0 END) AS issues,
                SUM(CASE WHEN i.is_pull_request = 1 THEN 1 ELSE 0 END) AS prs,
                (
                    SELECT COUNT(*)
                    FROM comments c
                    JOIN issues i2 ON c.issue_id = i2.id
                    WHERE i2.repo_id = r.id
                ) AS comments
            FROM repos r
            LEFT JOIN issues i ON i.repo_id = r.id
            GROUP BY r.id
            ORDER BY r.owner, r.name
            """
        ).fetchall()

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"repos": [dict(row) for row in rows]},
    )
