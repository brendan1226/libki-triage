import difflib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from authlib.integrations.starlette_client import OAuth
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .config import settings
from .db import connect, init_db

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="libki-triage", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)

# Google OAuth setup (optional — if not configured, auth is disabled)
oauth = OAuth()
if settings.google_client_id:
    oauth.register(
        name="google",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _compute_diff(original: str, modified: str, file_path: str) -> list[dict]:
    orig_lines = original.splitlines(keepends=True)
    mod_lines = modified.splitlines(keepends=True)
    diff = difflib.unified_diff(orig_lines, mod_lines, fromfile=f"a/{file_path}", tofile=f"b/{file_path}")
    lines: list[dict] = []
    for raw in diff:
        text = raw.rstrip("\n")
        if text.startswith("+++") or text.startswith("---"):
            lines.append({"type": "header", "text": text})
        elif text.startswith("@@"):
            lines.append({"type": "hunk", "text": text})
        elif text.startswith("+"):
            lines.append({"type": "add", "text": text})
        elif text.startswith("-"):
            lines.append({"type": "del", "text": text})
        else:
            lines.append({"type": "ctx", "text": text})
    return lines


# ---------------------------------------------------------------------------
# Auth middleware — runs AFTER SessionMiddleware so request.session is available
# ---------------------------------------------------------------------------

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    request.state.user = None

    if not settings.google_client_id:
        # Auth disabled — fake local user
        request.state.user = {"id": 0, "email": "local", "name": "Local Dev", "picture_url": ""}
        return await call_next(request)

    public_prefixes = ("/login", "/auth/", "/healthz", "/static")
    if any(request.url.path.startswith(p) for p in public_prefixes):
        return await call_next(request)

    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login")

    init_db(settings.db_path)
    with connect(settings.db_path) as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        request.session.clear()
        return RedirectResponse("/login")

    request.state.user = dict(row)
    return await call_next(request)


def _get_user_github_config(request: Request) -> tuple[str, str]:
    """Get (github_token, fork_owner) for the current user.

    Falls back to system-level config if no per-user settings.
    """
    user = request.state.user
    if user and user.get("id"):
        with connect(settings.db_path) as conn:
            row = conn.execute(
                "SELECT github_token, github_fork_owner FROM user_settings WHERE user_id = ?",
                (user["id"],),
            ).fetchone()
        if row and row["github_token"]:
            return row["github_token"], row["github_fork_owner"] or settings.github_fork_owner

    if settings.github_token:
        return settings.github_token, settings.github_fork_owner
    raise ValueError("No GitHub token configured. Go to Settings and add your GitHub PAT.")


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    if not settings.google_client_id:
        return RedirectResponse("/")
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": error, "allowed_domains": settings.allowed_domains},
    )


@app.get("/auth/start")
async def auth_start(request: Request):
    if not settings.google_client_id:
        return RedirectResponse("/")
    redirect_uri = str(request.url_for("auth_callback"))
    return await oauth.google.authorize_redirect(request, redirect_uri)


@app.get("/auth/callback")
async def auth_callback(request: Request):
    if not settings.google_client_id:
        return RedirectResponse("/")

    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as e:
        return RedirectResponse(f"/login?error={quote(str(e))}")

    user_info = token.get("userinfo")
    if not user_info:
        return RedirectResponse("/login?error=No+user+info+returned")

    email = user_info.get("email", "")
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    allowed = [d.strip() for d in settings.allowed_domains.split(",")]
    if domain not in allowed:
        return RedirectResponse(f"/login?error=Domain+{quote(domain)}+not+allowed")

    now = _utc_now_iso()
    init_db(settings.db_path)
    with connect(settings.db_path) as conn:
        conn.execute(
            """
            INSERT INTO users (email, name, picture_url, created_at, last_login_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                name = excluded.name,
                picture_url = excluded.picture_url,
                last_login_at = excluded.last_login_at
            """,
            (email, user_info.get("name", ""), user_info.get("picture", ""), now, now),
        )
        row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()

    request.session["user_id"] = row["id"]
    return RedirectResponse("/")


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    if settings.google_client_id:
        return RedirectResponse("/login")
    return RedirectResponse("/")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: bool = False):
    user = request.state.user
    current = {}
    token_display = None

    if user and user.get("id"):
        with connect(settings.db_path) as conn:
            row = conn.execute(
                "SELECT github_token, github_fork_owner FROM user_settings WHERE user_id = ?",
                (user["id"],),
            ).fetchone()
        if row:
            current = dict(row)
            t = current.get("github_token") or ""
            token_display = f"...{t[-4:]}" if len(t) > 4 else ("set" if t else None)
            current["github_token"] = ""  # don't send full token to template

    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "user": user,
            "current_settings": current,
            "token_display": token_display,
            "saved": saved,
        },
    )


@app.post("/settings")
def save_settings(
    request: Request,
    github_token: str = Form(""),
    github_fork_owner: str = Form(""),
):
    user = request.state.user
    if not user or not user.get("id"):
        return RedirectResponse("/login")

    now = _utc_now_iso()
    with connect(settings.db_path) as conn:
        # If token is empty, keep the existing one (user didn't re-enter)
        existing = conn.execute(
            "SELECT github_token FROM user_settings WHERE user_id = ?", (user["id"],)
        ).fetchone()
        if not github_token.strip() and existing:
            github_token = existing["github_token"] or ""

        conn.execute(
            """
            INSERT INTO user_settings (user_id, github_token, github_fork_owner, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                github_token = excluded.github_token,
                github_fork_owner = excluded.github_fork_owner,
                updated_at = excluded.updated_at
            """,
            (user["id"], github_token.strip(), github_fork_owner.strip(), now),
        )

    return RedirectResponse("/settings?saved=1", status_code=303)


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
            SELECT r.owner, r.name, r.default_branch, r.last_harvested_at,
                SUM(CASE WHEN i.is_pull_request = 0 THEN 1 ELSE 0 END) AS issues,
                SUM(CASE WHEN i.is_pull_request = 1 THEN 1 ELSE 0 END) AS prs,
                SUM(CASE WHEN i.embedding IS NOT NULL THEN 1 ELSE 0 END) AS embedded,
                (SELECT COUNT(*) FROM comments c JOIN issues i2 ON c.issue_id = i2.id WHERE i2.repo_id = r.id) AS comments
            FROM repos r LEFT JOIN issues i ON i.repo_id = r.id
            GROUP BY r.id ORDER BY r.owner, r.name
            """
        ).fetchall()
    return templates.TemplateResponse(
        request=request, name="index.html",
        context={"repos": [dict(r) for r in repos], "has_anthropic_key": bool(settings.anthropic_api_key)},
    )


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@app.get("/search", response_class=HTMLResponse)
def search_page(request: Request, q: str = "", k: int = 5) -> HTMLResponse:
    q = (q or "").strip()
    if not q:
        return templates.TemplateResponse(
            request=request, name="search.html",
            context={"query": "", "has_anthropic_key": bool(settings.anthropic_api_key)},
        )
    k = max(1, min(k, 20))
    from .search import NoEmbeddingsError, search as semantic_search
    error = None
    results = []
    verdicts = []
    classified = False
    try:
        if settings.anthropic_api_key:
            from .classify import classify as run_classify
            results, verdicts = run_classify(settings.db_path, q, settings.embedding_model, settings.anthropic_api_key, settings.classification_model, top_k=k)
            classified = True
        else:
            results = semantic_search(settings.db_path, q, settings.embedding_model, top_k=k)
    except NoEmbeddingsError as e:
        error = str(e)
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
    verdicts_by_idx = {i: v for i, v in enumerate(verdicts) if i < len(results)}
    rows = [{**r, "verdict": (v := verdicts_by_idx.get(i)) and v.verdict, "rationale": v and v.rationale, "suggested_action": v and v.suggested_action} for i, r in enumerate(results)]
    return templates.TemplateResponse(request=request, name="search.html", context={"query": q, "k": k, "rows": rows, "error": error, "classified": classified, "has_anthropic_key": bool(settings.anthropic_api_key), "model": settings.classification_model})


# ---------------------------------------------------------------------------
# Issue browser
# ---------------------------------------------------------------------------

@app.get("/issues", response_class=HTMLResponse)
def issues_list(request: Request, repo: str = "", state: str = "open", kind: str = "all", q: str = "", page: int = 1) -> HTMLResponse:
    init_db(settings.db_path)
    per_page = 50
    offset = (max(1, page) - 1) * per_page
    filters, params = [], []
    if repo:
        filters.append("(r.owner || '/' || r.name) = ?"); params.append(repo)
    if state and state != "all":
        filters.append("i.state = ?"); params.append(state)
    if kind == "issues":
        filters.append("i.is_pull_request = 0")
    elif kind == "prs":
        filters.append("i.is_pull_request = 1")
    if q:
        filters.append("i.title LIKE ?"); params.append(f"%{q}%")
    where = "WHERE " + " AND ".join(filters) if filters else ""
    with connect(settings.db_path) as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM issues i JOIN repos r ON i.repo_id = r.id {where}", params).fetchone()[0]
        rows = conn.execute(f"SELECT i.id, i.number, i.title, i.state, i.is_pull_request, i.author, i.created_at, i.updated_at, i.url, i.labels, r.owner AS repo_owner, r.name AS repo_name FROM issues i JOIN repos r ON i.repo_id = r.id {where} ORDER BY i.updated_at DESC LIMIT ? OFFSET ?", [*params, per_page, offset]).fetchall()
        repo_options = conn.execute("SELECT DISTINCT r.owner || '/' || r.name AS full_name FROM repos r ORDER BY full_name").fetchall()
        groups = conn.execute("SELECT id, name FROM groups ORDER BY name").fetchall()
    return templates.TemplateResponse(request=request, name="issues.html", context={"issues": [dict(r) for r in rows], "repo_options": [r["full_name"] for r in repo_options], "groups": [dict(g) for g in groups], "total": total, "page": page, "total_pages": max(1, (total + per_page - 1) // per_page), "per_page": per_page, "filter_repo": repo, "filter_state": state, "filter_kind": kind, "filter_q": q})


@app.get("/issues/{issue_id}", response_class=HTMLResponse)
def issue_detail(request: Request, issue_id: int, error: str = "") -> HTMLResponse:
    init_db(settings.db_path)
    with connect(settings.db_path) as conn:
        row = conn.execute("SELECT i.*, r.owner AS repo_owner, r.name AS repo_name FROM issues i JOIN repos r ON i.repo_id = r.id WHERE i.id = ?", (issue_id,)).fetchone()
        if row is None:
            return HTMLResponse("Issue not found", status_code=404)
        issue_comments = conn.execute("SELECT * FROM comments WHERE issue_id = ? ORDER BY created_at", (issue_id,)).fetchall()
        memberships = conn.execute("SELECT g.id, g.name FROM groups g JOIN group_members gm ON gm.group_id = g.id WHERE gm.issue_id = ?", (issue_id,)).fetchall()
        all_groups = conn.execute("SELECT id, name FROM groups ORDER BY name").fetchall()
    labels = json.loads(row["labels"]) if row["labels"] else []

    from .recommend import get_stored_recommendation
    stored = get_stored_recommendation(settings.db_path, issue_id)
    rec, rec_meta = None, None
    if stored:
        rec_obj, rec_model, rec_created = stored
        rec = rec_obj.model_dump()
        rec_meta = {"model": rec_model, "created_at": rec_created}

    from .codegen import get_stored_fixes
    code_fixes, fix_meta = get_stored_fixes(settings.db_path, issue_id)
    for fix in code_fixes:
        fix["diff_lines"] = _compute_diff(fix.get("original_content") or "", fix.get("fixed_content") or "", fix.get("file_path", "unknown"))

    has_github = False
    try:
        _get_user_github_config(request)
        has_github = True
    except ValueError:
        pass

    return templates.TemplateResponse(request=request, name="issue_detail.html", context={
        "issue": dict(row), "labels": labels, "comments": [dict(c) for c in issue_comments],
        "memberships": [dict(m) for m in memberships], "all_groups": [dict(g) for g in all_groups],
        "rec": rec, "rec_meta": rec_meta, "code_fixes": code_fixes, "fix_meta": fix_meta,
        "has_anthropic_key": bool(settings.anthropic_api_key), "has_github_token": has_github,
        "error": error,
    })


# ---------------------------------------------------------------------------
# Issue actions (recommend, generate fix, create PR)
# ---------------------------------------------------------------------------

@app.post("/issues/{issue_id}/recommend")
def generate_issue_recommendation(issue_id: int) -> RedirectResponse:
    if not settings.anthropic_api_key:
        return RedirectResponse(f"/issues/{issue_id}?error=No+Anthropic+API+key", status_code=303)
    try:
        from .recommend import generate_recommendation
        generate_recommendation(settings.db_path, issue_id, settings.anthropic_api_key, settings.classification_model)
    except Exception as e:
        return RedirectResponse(f"/issues/{issue_id}?error={quote(str(e))}", status_code=303)
    return RedirectResponse(f"/issues/{issue_id}", status_code=303)


@app.post("/issues/{issue_id}/generate-fix")
def generate_fix(request: Request, issue_id: int) -> RedirectResponse:
    try:
        github_token, _ = _get_user_github_config(request)
        if not settings.anthropic_api_key:
            raise ValueError("No Anthropic API key configured.")
        from .codegen import generate_code_fix
        generate_code_fix(settings.db_path, issue_id, settings.anthropic_api_key, github_token, settings.classification_model)
    except Exception as e:
        return RedirectResponse(f"/issues/{issue_id}?error={quote(str(e))}", status_code=303)
    return RedirectResponse(f"/issues/{issue_id}", status_code=303)


@app.post("/issues/{issue_id}/create-pr")
def create_pr(request: Request, issue_id: int) -> RedirectResponse:
    try:
        github_token, fork_owner = _get_user_github_config(request)
        from .codegen import create_pr_from_fixes
        result = create_pr_from_fixes(settings.db_path, issue_id, github_token, fork_owner)
    except Exception as e:
        return RedirectResponse(f"/issues/{issue_id}?error={quote(str(e))}", status_code=303)
    return RedirectResponse(f"/issues/{issue_id}", status_code=303)


# ---------------------------------------------------------------------------
# Issue group membership
# ---------------------------------------------------------------------------

@app.post("/issues/{issue_id}/add-to-group")
def add_issue_to_group(issue_id: int, group_id: int = Form(...)) -> RedirectResponse:
    now = _utc_now_iso()
    with connect(settings.db_path) as conn:
        try:
            conn.execute("INSERT INTO group_members (group_id, issue_id, added_at) VALUES (?, ?, ?)", (group_id, issue_id, now))
            conn.execute("UPDATE groups SET updated_at = ? WHERE id = ?", (now, group_id))
        except Exception:
            pass
    return RedirectResponse(f"/issues/{issue_id}", status_code=303)


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------

@app.get("/groups", response_class=HTMLResponse)
def groups_list(request: Request) -> HTMLResponse:
    init_db(settings.db_path)
    with connect(settings.db_path) as conn:
        rows = conn.execute("SELECT g.*, COUNT(gm.id) AS member_count FROM groups g LEFT JOIN group_members gm ON gm.group_id = g.id GROUP BY g.id ORDER BY g.updated_at DESC").fetchall()
    return templates.TemplateResponse(request=request, name="groups.html", context={"groups": [dict(r) for r in rows]})


@app.post("/groups")
def create_group(name: str = Form(...), description: str = Form("")) -> RedirectResponse:
    init_db(settings.db_path)
    now = _utc_now_iso()
    with connect(settings.db_path) as conn:
        cursor = conn.execute("INSERT INTO groups (name, description, created_at, updated_at) VALUES (?, ?, ?, ?)", (name.strip(), description.strip(), now, now))
    return RedirectResponse(f"/groups/{cursor.lastrowid}", status_code=303)


@app.get("/groups/{group_id}", response_class=HTMLResponse)
def group_detail(request: Request, group_id: int) -> HTMLResponse:
    init_db(settings.db_path)
    with connect(settings.db_path) as conn:
        group = conn.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
        if group is None:
            return HTMLResponse("Group not found", status_code=404)
        members = conn.execute("SELECT i.id, i.number, i.title, i.state, i.is_pull_request, i.url, r.owner AS repo_owner, r.name AS repo_name, gm.added_at FROM group_members gm JOIN issues i ON gm.issue_id = i.id JOIN repos r ON i.repo_id = r.id WHERE gm.group_id = ? ORDER BY gm.added_at DESC", (group_id,)).fetchall()
    return templates.TemplateResponse(request=request, name="group_detail.html", context={"group": dict(group), "members": [dict(m) for m in members]})


@app.post("/groups/{group_id}/members")
def add_group_member(group_id: int, issue_id: int = Form(...)) -> RedirectResponse:
    now = _utc_now_iso()
    with connect(settings.db_path) as conn:
        try:
            conn.execute("INSERT INTO group_members (group_id, issue_id, added_at) VALUES (?, ?, ?)", (group_id, issue_id, now))
            conn.execute("UPDATE groups SET updated_at = ? WHERE id = ?", (now, group_id))
        except Exception:
            pass
    return RedirectResponse(f"/groups/{group_id}", status_code=303)


@app.post("/groups/{group_id}/members/{issue_id}/remove")
def remove_group_member(group_id: int, issue_id: int) -> RedirectResponse:
    now = _utc_now_iso()
    with connect(settings.db_path) as conn:
        conn.execute("DELETE FROM group_members WHERE group_id = ? AND issue_id = ?", (group_id, issue_id))
        conn.execute("UPDATE groups SET updated_at = ? WHERE id = ?", (now, group_id))
    return RedirectResponse(f"/groups/{group_id}", status_code=303)
