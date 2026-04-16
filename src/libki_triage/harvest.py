import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

import httpx

from .db import connect, init_db

GITHUB_API = "https://api.github.com"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _build_client(token: str | None) -> httpx.Client:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "libki-triage/0.0.1",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(base_url=GITHUB_API, headers=headers, timeout=30.0)


def _paginate(
    client: httpx.Client,
    url: str,
    params: dict,
    on_page: Optional[Callable[[int, int], None]] = None,
) -> Iterable[dict]:
    current_params = params
    page = 0
    while url:
        response = client.get(url, params=current_params)
        response.raise_for_status()
        page += 1
        items = response.json()
        if on_page is not None:
            on_page(page, len(items))
        for item in items:
            yield item
        next_link = response.links.get("next", {}).get("url")
        url = next_link or ""
        current_params = {}  # the `next` URL already carries pagination params


def _page_logger(label: str) -> Callable[[int, int], None]:
    def log(page: int, count: int) -> None:
        print(f"    {label} page {page}: {count} records", flush=True)

    return log


def _issue_number_from_url(issue_url: str) -> int:
    """Extract the issue number from a GitHub API issue URL.

    Example: https://api.github.com/repos/Libki/libki-server/issues/123 -> 123
    """
    return int(issue_url.rsplit("/", 1)[-1])


def upsert_repo(conn, owner: str, name: str, default_branch: str | None) -> int:
    conn.execute(
        """
        INSERT INTO repos (owner, name, default_branch, last_harvested_at)
        VALUES (?, ?, ?, NULL)
        ON CONFLICT(owner, name) DO UPDATE SET default_branch = excluded.default_branch
        """,
        (owner, name, default_branch),
    )
    row = conn.execute(
        "SELECT id FROM repos WHERE owner = ? AND name = ?", (owner, name)
    ).fetchone()
    return row["id"]


def _get_last_harvested_at(conn, repo_id: int) -> str | None:
    row = conn.execute(
        "SELECT last_harvested_at FROM repos WHERE id = ?", (repo_id,)
    ).fetchone()
    return row["last_harvested_at"] if row else None


def upsert_issue(conn, repo_id: int, issue: dict, harvested_at: str) -> None:
    is_pr = 1 if "pull_request" in issue else 0
    labels = json.dumps([label["name"] for label in issue.get("labels", [])])
    conn.execute(
        """
        INSERT INTO issues (
            repo_id, number, title, body, state, is_pull_request, author,
            created_at, updated_at, closed_at, url, labels, harvested_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(repo_id, number) DO UPDATE SET
            title = excluded.title,
            body = excluded.body,
            state = excluded.state,
            author = excluded.author,
            updated_at = excluded.updated_at,
            closed_at = excluded.closed_at,
            labels = excluded.labels,
            harvested_at = excluded.harvested_at
        """,
        (
            repo_id,
            issue["number"],
            issue["title"],
            issue.get("body") or "",
            issue["state"],
            is_pr,
            (issue.get("user") or {}).get("login"),
            issue["created_at"],
            issue["updated_at"],
            issue.get("closed_at"),
            issue["html_url"],
            labels,
            harvested_at,
        ),
    )


def _find_issue_id(conn, repo_id: int, number: int) -> int | None:
    row = conn.execute(
        "SELECT id FROM issues WHERE repo_id = ? AND number = ?",
        (repo_id, number),
    ).fetchone()
    return row["id"] if row else None


def upsert_comment(conn, issue_id: int, comment: dict) -> None:
    conn.execute(
        """
        INSERT INTO comments (issue_id, github_id, author, body, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(github_id) DO UPDATE SET
            body = excluded.body,
            updated_at = excluded.updated_at
        """,
        (
            issue_id,
            comment["id"],
            (comment.get("user") or {}).get("login"),
            comment.get("body") or "",
            comment["created_at"],
            comment["updated_at"],
        ),
    )


def harvest_repo(db_path: Path, owner: str, name: str, token: str | None) -> dict:
    """Harvest issues, PRs, and comments for a single repo into the local DB.

    Uses `since=<last_harvested_at>` for incremental runs: first run pulls
    everything; subsequent runs only pull records updated since the previous
    run. Comments are fetched via the repo-wide `/issues/comments` endpoint
    in a single paginated call, not per-issue.
    """
    init_db(db_path)
    new_harvested_at = _utc_now_iso()
    counts = {"issues": 0, "prs": 0, "comments": 0, "skipped_comments": 0}

    with _build_client(token) as client:
        repo_info = client.get(f"/repos/{owner}/{name}")
        repo_info.raise_for_status()
        default_branch = repo_info.json().get("default_branch")

        with connect(db_path) as conn:
            repo_id = upsert_repo(conn, owner, name, default_branch)
            since = _get_last_harvested_at(conn, repo_id)

            issue_params: dict = {"state": "all", "per_page": 100}
            if since:
                issue_params["since"] = since

            for issue in _paginate(
                client,
                f"/repos/{owner}/{name}/issues",
                issue_params,
                on_page=_page_logger("issues"),
            ):
                upsert_issue(conn, repo_id, issue, new_harvested_at)
                if "pull_request" in issue:
                    counts["prs"] += 1
                else:
                    counts["issues"] += 1

            comment_params: dict = {"per_page": 100}
            if since:
                comment_params["since"] = since

            for comment in _paginate(
                client,
                f"/repos/{owner}/{name}/issues/comments",
                comment_params,
                on_page=_page_logger("comments"),
            ):
                issue_number = _issue_number_from_url(comment["issue_url"])
                issue_id = _find_issue_id(conn, repo_id, issue_number)
                if issue_id is None:
                    # Comment for an issue not yet in the DB — can happen only
                    # if the issue was created and commented on between our
                    # issues-page fetch and our comments-page fetch. Next
                    # harvest picks it up because its updated_at > since.
                    counts["skipped_comments"] += 1
                    continue
                upsert_comment(conn, issue_id, comment)
                counts["comments"] += 1

            conn.execute(
                "UPDATE repos SET last_harvested_at = ? WHERE id = ?",
                (new_harvested_at, repo_id),
            )

    return counts
