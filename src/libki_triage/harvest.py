import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

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


def _paginate(client: httpx.Client, url: str, params: dict) -> Iterable[dict]:
    current_params = params
    while url:
        response = client.get(url, params=current_params)
        response.raise_for_status()
        for item in response.json():
            yield item
        next_link = response.links.get("next", {}).get("url")
        url = next_link or ""
        current_params = {}  # the `next` URL already carries pagination params


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


def upsert_issue(conn, repo_id: int, issue: dict, harvested_at: str) -> int:
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
    row = conn.execute(
        "SELECT id FROM issues WHERE repo_id = ? AND number = ?",
        (repo_id, issue["number"]),
    ).fetchone()
    return row["id"]


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
    init_db(db_path)
    harvested_at = _utc_now_iso()
    counts = {"issues": 0, "prs": 0, "comments": 0}

    with _build_client(token) as client:
        repo_info = client.get(f"/repos/{owner}/{name}")
        repo_info.raise_for_status()
        default_branch = repo_info.json().get("default_branch")

        with connect(db_path) as conn:
            repo_id = upsert_repo(conn, owner, name, default_branch)

            for issue in _paginate(
                client,
                f"/repos/{owner}/{name}/issues",
                {"state": "all", "per_page": 100},
            ):
                issue_id = upsert_issue(conn, repo_id, issue, harvested_at)
                if "pull_request" in issue:
                    counts["prs"] += 1
                else:
                    counts["issues"] += 1

                if issue.get("comments", 0) > 0:
                    for comment in _paginate(
                        client,
                        f"/repos/{owner}/{name}/issues/{issue['number']}/comments",
                        {"per_page": 100},
                    ):
                        upsert_comment(conn, issue_id, comment)
                        counts["comments"] += 1

            conn.execute(
                "UPDATE repos SET last_harvested_at = ? WHERE id = ?",
                (harvested_at, repo_id),
            )

    return counts
