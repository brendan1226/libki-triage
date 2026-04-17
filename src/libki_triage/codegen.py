"""Generate code fixes via Claude and create PRs via GitHub API."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import anthropic
from pydantic import BaseModel, Field

from .db import connect, init_db
from .github_ops import (
    PRResult,
    commit_file,
    create_branch,
    create_pull_request,
    ensure_fork,
    fetch_file,
    get_default_branch_sha,
    sync_fork,
)
from .recommend import Recommendation, get_stored_recommendation


class FileFix(BaseModel):
    file_path: str = Field(..., description="Path relative to repo root.")
    explanation: str = Field(..., description="What changed and why, 2-3 sentences.")
    content: str = Field(..., description="The complete modified file content.")


class CodeFixResponse(BaseModel):
    fixes: list[FileFix]
    commit_message: str = Field(..., description="A concise commit message for all changes.")


SYSTEM_PROMPT = """You are implementing a code fix for the Libki library kiosk management system.

You will receive:
1. An issue description
2. A fix recommendation (approach, affected files, guidelines)
3. The current content of the file(s) to modify

Your job: produce the COMPLETE modified file content for each file that needs changes. Do not produce diffs — return the entire file with your changes applied. Be surgical: change only what's needed to fix the issue. Follow the coding guidelines referenced in the recommendation.

If a file path from the recommendation doesn't match what was fetched, adapt to the actual file structure you see."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def generate_code_fix(
    db_path: Path,
    issue_id: int,
    api_key: str,
    github_token: str,
    model: str = "claude-opus-4-6",
    max_files: int = 3,
) -> CodeFixResponse:
    """Generate code fix for an issue based on its stored recommendation.

    Fetches the likely_files from GitHub, sends them + the recommendation
    to Claude, stores the result, and returns it.
    """
    init_db(db_path)
    stored = get_stored_recommendation(db_path, issue_id)
    if stored is None:
        raise ValueError("No recommendation exists. Generate one first.")

    rec, _model, _created = stored

    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT i.*, r.owner AS repo_owner, r.name AS repo_name, r.default_branch
            FROM issues i JOIN repos r ON i.repo_id = r.id
            WHERE i.id = ?
            """,
            (issue_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Issue {issue_id} not found")

    issue = dict(row)
    owner = issue["repo_owner"]
    repo = issue["repo_name"]
    default_branch = issue["default_branch"] or "master"

    file_contents: list[dict] = []
    for path in rec.likely_files[:max_files]:
        try:
            content, sha = fetch_file(owner, repo, path, ref=default_branch, token=github_token)
            file_contents.append({"path": path, "content": content, "sha": sha})
        except Exception as e:
            file_contents.append({"path": path, "content": None, "error": str(e)})

    files_context = []
    for fc in file_contents:
        if fc.get("content"):
            files_context.append(f"### {fc['path']}\n```\n{fc['content']}\n```")
        else:
            files_context.append(f"### {fc['path']}\n(Could not fetch: {fc.get('error', 'unknown')})")

    client = anthropic.Anthropic(api_key=api_key)

    response = client.messages.parse(
        model=model,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    f"## Issue: {owner}/{repo}#{issue['number']}\n"
                    f"**Title:** {issue['title']}\n"
                    f"**State:** {issue['state']}\n\n"
                    f"**Body:**\n{issue.get('body') or '(empty)'}\n\n"
                    f"---\n\n## Recommendation\n\n"
                    f"**Fix approach:** {rec.fix_approach}\n\n"
                    f"**Key guidelines:** {', '.join(rec.key_guidelines)}\n\n"
                    f"**Test plan:** {rec.test_plan}\n\n"
                    f"---\n\n## Current file contents\n\n"
                    + "\n\n".join(files_context)
                    + "\n\n---\n\nProduce the complete modified file content for each file that needs changes."
                ),
            }
        ],
        output_format=CodeFixResponse,
    )

    fix = response.parsed_output
    if fix is None:
        raise RuntimeError("Claude did not return a valid code fix")

    now = _utc_now_iso()
    with connect(db_path) as conn:
        conn.execute("DELETE FROM code_fixes WHERE issue_id = ?", (issue_id,))
        for f in fix.fixes:
            conn.execute(
                """
                INSERT INTO code_fixes (issue_id, file_path, original_content, fixed_content, explanation, model, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    issue_id,
                    f.file_path,
                    next((fc["content"] for fc in file_contents if fc["path"] == f.file_path), None),
                    f.content,
                    f.explanation,
                    model,
                    now,
                ),
            )
        conn.execute(
            "INSERT OR REPLACE INTO code_fix_meta (issue_id, commit_message, model, created_at) VALUES (?, ?, ?, ?)",
            (issue_id, fix.commit_message, model, now),
        )

    return fix


def get_stored_fixes(db_path: Path, issue_id: int) -> tuple[list[dict], dict | None]:
    """Get stored code fixes and metadata for an issue.

    Returns (fixes_list, meta_dict_or_none).
    """
    init_db(db_path)
    with connect(db_path) as conn:
        fixes = conn.execute(
            "SELECT * FROM code_fixes WHERE issue_id = ? ORDER BY id",
            (issue_id,),
        ).fetchall()
        meta = conn.execute(
            "SELECT * FROM code_fix_meta WHERE issue_id = ?",
            (issue_id,),
        ).fetchone()
    return [dict(f) for f in fixes], dict(meta) if meta else None


def create_pr_from_fixes(
    db_path: Path,
    issue_id: int,
    github_token: str,
    fork_owner: str,
) -> PRResult:
    """Create a GitHub PR from stored code fixes."""
    init_db(db_path)
    fixes, meta = get_stored_fixes(db_path, issue_id)
    if not fixes:
        raise ValueError("No code fixes stored. Generate them first.")

    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT i.number, i.title, r.owner AS repo_owner, r.name AS repo_name, r.default_branch
            FROM issues i JOIN repos r ON i.repo_id = r.id
            WHERE i.id = ?
            """,
            (issue_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Issue {issue_id} not found")

    issue = dict(row)
    upstream_owner = issue["repo_owner"]
    repo = issue["repo_name"]
    default_branch = issue["default_branch"] or "master"
    issue_number = issue["number"]

    rec_stored = get_stored_recommendation(db_path, issue_id)
    branch_name = f"fix-{issue_number}"
    if rec_stored:
        rec, _, _ = rec_stored
        branch_name = rec.suggested_branch_name or branch_name

    ensure_fork(upstream_owner, repo, fork_owner, github_token)
    sync_fork(fork_owner, repo, default_branch, github_token)
    base_sha = get_default_branch_sha(fork_owner, repo, default_branch, github_token)
    create_branch(fork_owner, repo, branch_name, base_sha, github_token)

    commit_msg = (meta or {}).get("commit_message", f"Fix #{issue_number}: {issue['title']}")
    for fix in fixes:
        commit_file(
            fork_owner, repo, branch_name,
            fix["file_path"], fix["fixed_content"],
            commit_msg, github_token,
        )

    pr_title = f"{commit_msg} (Closes #{issue_number})"
    pr_body = (
        f"Closes {upstream_owner}/{repo}#{issue_number}\n\n"
        f"## Changes\n\n"
    )
    for fix in fixes:
        pr_body += f"- `{fix['file_path']}`: {fix['explanation']}\n"
    pr_body += (
        f"\n## Generated by\n\n"
        f"[libki-triage](https://github.com/brendan1226/libki-triage) v0.5 — "
        f"AI-recommended fix based on issue analysis + coding guidelines."
    )

    result = create_pull_request(
        upstream_owner, repo, pr_title, pr_body,
        head=f"{fork_owner}:{branch_name}",
        base=default_branch,
        token=github_token,
        draft=True,
    )

    now = _utc_now_iso()
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE code_fix_meta SET pr_url = ?, pr_number = ? WHERE issue_id = ?",
            (result.html_url, result.number, issue_id),
        )

    return result
