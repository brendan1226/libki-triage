import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import settings
from .db import connect, init_db

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="libki-triage", version="0.5.0")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    init_db(settings.db_path)
    with connect(settings.db_path) as conn:
        repos = conn.execute(
            """
            SELECT
                r.owner, r.name, r.default_branch, r.last_harvested_at,
                SUM(CASE WHEN i.is_pull_request = 0 THEN 1 ELSE 0 END) AS issues,
                SUM(CASE WHEN i.is_pull_request = 1 THEN 1 ELSE 0 END) AS prs,
                SUM(CASE WHEN i.embedding IS NOT NULL THEN 1 ELSE 0 END) AS embedded,
                (SELECT COUNT(*) FROM comments c JOIN issues i2 ON c.issue_id = i2.id WHERE i2.repo_id = r.id) AS comments
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
            "repos": [dict(r) for r in repos],
            "has_anthropic_key": bool(settings.anthropic_api_key),
        },
    )


# ---------------------------------------------------------------------------
# Semantic search
# ---------------------------------------------------------------------------

@app.get("/search", response_class=HTMLResponse)
def search_page(request: Request, q: str = "", k: int = 5) -> HTMLResponse:
    q = (q or "").strip()
    if not q:
        return templates.TemplateResponse(
            request=request,
            name="search.html",
            context={"query": "", "has_anthropic_key": bool(settings.anthropic_api_key)},
        )

    k = max(1, min(k, 20))
    from .search import NoEmbeddingsError, search as semantic_search

    error: str | None = None
    results: list = []
    verdicts: list = []
    classified = False

    try:
        if settings.anthropic_api_key:
            from .classify import classify as run_classify
            results, verdicts = run_classify(
                settings.db_path, q, settings.embedding_model,
                settings.anthropic_api_key, settings.classification_model, top_k=k,
            )
            classified = True
        else:
            results = semantic_search(settings.db_path, q, settings.embedding_model, top_k=k)
    except NoEmbeddingsError as e:
        error = str(e)
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    verdicts_by_idx = {i: v for i, v in enumerate(verdicts) if i < len(results)}
    rows = []
    for i, r in enumerate(results):
        v = verdicts_by_idx.get(i)
        rows.append({
            **r,
            "verdict": v.verdict if v else None,
            "rationale": v.rationale if v else None,
            "suggested_action": v.suggested_action if v else None,
        })

    return templates.TemplateResponse(
        request=request, name="search.html",
        context={
            "query": q, "k": k, "rows": rows, "error": error,
            "classified": classified, "has_anthropic_key": bool(settings.anthropic_api_key),
            "model": settings.classification_model,
        },
    )


# ---------------------------------------------------------------------------
# Issue browser
# ---------------------------------------------------------------------------

@app.get("/issues", response_class=HTMLResponse)
def issues_list(
    request: Request,
    repo: str = "",
    state: str = "open",
    kind: str = "all",
    q: str = "",
    page: int = 1,
) -> HTMLResponse:
    init_db(settings.db_path)
    per_page = 50
    offset = (max(1, page) - 1) * per_page

    filters = []
    params: list = []
    if repo:
        filters.append("(r.owner || '/' || r.name) = ?")
        params.append(repo)
    if state and state != "all":
        filters.append("i.state = ?")
        params.append(state)
    if kind == "issues":
        filters.append("i.is_pull_request = 0")
    elif kind == "prs":
        filters.append("i.is_pull_request = 1")
    if q:
        filters.append("i.title LIKE ?")
        params.append(f"%{q}%")

    where = "WHERE " + " AND ".join(filters) if filters else ""

    with connect(settings.db_path) as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM issues i JOIN repos r ON i.repo_id = r.id {where}",
            params,
        ).fetchone()[0]

        rows = conn.execute(
            f"""
            SELECT
                i.id, i.number, i.title, i.state, i.is_pull_request, i.author,
                i.created_at, i.updated_at, i.url, i.labels,
                r.owner AS repo_owner, r.name AS repo_name
            FROM issues i
            JOIN repos r ON i.repo_id = r.id
            {where}
            ORDER BY i.updated_at DESC
            LIMIT ? OFFSET ?
            """,
            [*params, per_page, offset],
        ).fetchall()

        repo_options = conn.execute(
            "SELECT DISTINCT r.owner || '/' || r.name AS full_name FROM repos r ORDER BY full_name"
        ).fetchall()

        groups = conn.execute(
            "SELECT id, name FROM groups ORDER BY name"
        ).fetchall()

    total_pages = max(1, (total + per_page - 1) // per_page)

    return templates.TemplateResponse(
        request=request, name="issues.html",
        context={
            "issues": [dict(r) for r in rows],
            "repo_options": [r["full_name"] for r in repo_options],
            "groups": [dict(g) for g in groups],
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "per_page": per_page,
            "filter_repo": repo,
            "filter_state": state,
            "filter_kind": kind,
            "filter_q": q,
        },
    )


@app.get("/issues/{issue_id}", response_class=HTMLResponse)
def issue_detail(request: Request, issue_id: int) -> HTMLResponse:
    init_db(settings.db_path)
    with connect(settings.db_path) as conn:
        row = conn.execute(
            """
            SELECT
                i.*, r.owner AS repo_owner, r.name AS repo_name
            FROM issues i
            JOIN repos r ON i.repo_id = r.id
            WHERE i.id = ?
            """,
            (issue_id,),
        ).fetchone()
        if row is None:
            return HTMLResponse("Issue not found", status_code=404)

        issue_comments = conn.execute(
            "SELECT * FROM comments WHERE issue_id = ? ORDER BY created_at",
            (issue_id,),
        ).fetchall()

        memberships = conn.execute(
            """
            SELECT g.id, g.name FROM groups g
            JOIN group_members gm ON gm.group_id = g.id
            WHERE gm.issue_id = ?
            """,
            (issue_id,),
        ).fetchall()

        all_groups = conn.execute("SELECT id, name FROM groups ORDER BY name").fetchall()

    labels = json.loads(row["labels"]) if row["labels"] else []

    return templates.TemplateResponse(
        request=request, name="issue_detail.html",
        context={
            "issue": dict(row),
            "labels": labels,
            "comments": [dict(c) for c in issue_comments],
            "memberships": [dict(m) for m in memberships],
            "all_groups": [dict(g) for g in all_groups],
        },
    )


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------

@app.get("/groups", response_class=HTMLResponse)
def groups_list(request: Request) -> HTMLResponse:
    init_db(settings.db_path)
    with connect(settings.db_path) as conn:
        rows = conn.execute(
            """
            SELECT g.*, COUNT(gm.id) AS member_count
            FROM groups g
            LEFT JOIN group_members gm ON gm.group_id = g.id
            GROUP BY g.id
            ORDER BY g.updated_at DESC
            """
        ).fetchall()

    return templates.TemplateResponse(
        request=request, name="groups.html",
        context={"groups": [dict(r) for r in rows]},
    )


@app.post("/groups")
def create_group(name: str = Form(...), description: str = Form("")) -> RedirectResponse:
    init_db(settings.db_path)
    now = _utc_now_iso()
    with connect(settings.db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO groups (name, description, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (name.strip(), description.strip(), now, now),
        )
        group_id = cursor.lastrowid
    return RedirectResponse(url=f"/groups/{group_id}", status_code=303)


@app.get("/groups/{group_id}", response_class=HTMLResponse)
def group_detail(request: Request, group_id: int) -> HTMLResponse:
    init_db(settings.db_path)
    with connect(settings.db_path) as conn:
        group = conn.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
        if group is None:
            return HTMLResponse("Group not found", status_code=404)

        members = conn.execute(
            """
            SELECT i.id, i.number, i.title, i.state, i.is_pull_request, i.url,
                   r.owner AS repo_owner, r.name AS repo_name, gm.added_at
            FROM group_members gm
            JOIN issues i ON gm.issue_id = i.id
            JOIN repos r ON i.repo_id = r.id
            WHERE gm.group_id = ?
            ORDER BY gm.added_at DESC
            """,
            (group_id,),
        ).fetchall()

    return templates.TemplateResponse(
        request=request, name="group_detail.html",
        context={"group": dict(group), "members": [dict(m) for m in members]},
    )


@app.post("/groups/{group_id}/members")
def add_group_member(group_id: int, issue_id: int = Form(...)) -> RedirectResponse:
    init_db(settings.db_path)
    now = _utc_now_iso()
    with connect(settings.db_path) as conn:
        try:
            conn.execute(
                "INSERT INTO group_members (group_id, issue_id, added_at) VALUES (?, ?, ?)",
                (group_id, issue_id, now),
            )
            conn.execute(
                "UPDATE groups SET updated_at = ? WHERE id = ?", (now, group_id)
            )
        except Exception:
            pass  # duplicate or invalid — silently ignore
    return RedirectResponse(url=f"/groups/{group_id}", status_code=303)


@app.post("/groups/{group_id}/members/{issue_id}/remove")
def remove_group_member(group_id: int, issue_id: int) -> RedirectResponse:
    init_db(settings.db_path)
    now = _utc_now_iso()
    with connect(settings.db_path) as conn:
        conn.execute(
            "DELETE FROM group_members WHERE group_id = ? AND issue_id = ?",
            (group_id, issue_id),
        )
        conn.execute("UPDATE groups SET updated_at = ? WHERE id = ?", (now, group_id))
    return RedirectResponse(url=f"/groups/{group_id}", status_code=303)


@app.post("/issues/{issue_id}/add-to-group")
def add_issue_to_group(issue_id: int, group_id: int = Form(...)) -> RedirectResponse:
    init_db(settings.db_path)
    now = _utc_now_iso()
    with connect(settings.db_path) as conn:
        try:
            conn.execute(
                "INSERT INTO group_members (group_id, issue_id, added_at) VALUES (?, ?, ?)",
                (group_id, issue_id, now),
            )
            conn.execute("UPDATE groups SET updated_at = ? WHERE id = ?", (now, group_id))
        except Exception:
            pass
    return RedirectResponse(url=f"/issues/{issue_id}", status_code=303)
