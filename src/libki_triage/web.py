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
                SUM(CASE WHEN i.embedding IS NOT NULL THEN 1 ELSE 0 END) AS embedded,
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
        context={
            "repos": [dict(row) for row in rows],
            "has_anthropic_key": bool(settings.anthropic_api_key),
        },
    )


@app.get("/search", response_class=HTMLResponse)
def search_page(request: Request, q: str = "", k: int = 5) -> HTMLResponse:
    """Semantic search + optional Claude verdicts.

    If an Anthropic API key is configured, every search is also classified.
    Otherwise the page renders similarity-only results.
    """
    q = (q or "").strip()
    if not q:
        return templates.TemplateResponse(
            request=request,
            name="search.html",
            context={"query": "", "has_anthropic_key": bool(settings.anthropic_api_key)},
        )

    k = max(1, min(k, 20))

    # Lazy imports keep cold-start of /healthz and / fast.
    from .search import NoEmbeddingsError, search as semantic_search

    error: str | None = None
    results: list = []
    verdicts: list = []
    classified = False

    try:
        if settings.anthropic_api_key:
            from .classify import classify as run_classify

            results, verdicts = run_classify(
                settings.db_path,
                q,
                settings.embedding_model,
                settings.anthropic_api_key,
                settings.classification_model,
                top_k=k,
            )
            classified = True
        else:
            results = semantic_search(
                settings.db_path, q, settings.embedding_model, top_k=k
            )
    except NoEmbeddingsError as e:
        error = str(e)
    except Exception as e:  # noqa: BLE001 — render any other failure, don't crash the page
        error = f"{type(e).__name__}: {e}"

    verdicts_by_idx = {i: v for i, v in enumerate(verdicts) if i < len(results)}
    rows = []
    for i, r in enumerate(results):
        v = verdicts_by_idx.get(i)
        rows.append(
            {
                "repo_owner": r["repo_owner"],
                "repo_name": r["repo_name"],
                "number": r["number"],
                "title": r["title"],
                "url": r["url"],
                "state": r["state"],
                "is_pull_request": r["is_pull_request"],
                "score": r["score"],
                "body_snippet": r["body_snippet"],
                "verdict": v.verdict if v else None,
                "rationale": v.rationale if v else None,
                "suggested_action": v.suggested_action if v else None,
            }
        )

    return templates.TemplateResponse(
        request=request,
        name="search.html",
        context={
            "query": q,
            "k": k,
            "rows": rows,
            "error": error,
            "classified": classified,
            "has_anthropic_key": bool(settings.anthropic_api_key),
            "model": settings.classification_model,
        },
    )
