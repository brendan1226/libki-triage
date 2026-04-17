import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from .db import connect, init_db

GUIDELINES_DIR = Path(__file__).parent / "guidelines"

REPO_GUIDELINE_MAP = {
    "libki-server": "libki-server.md",
    "libki-client": "libki-client.md",
    "libki-print-station": "libki-print-station.md",
    "libki-print-manager": "libki-print-manager.md",
}


class Recommendation(BaseModel):
    summary: str = Field(..., description="1-2 sentence summary of the problem.")
    affected_repos: list[str] = Field(
        ..., description="Repo names likely affected (e.g. libki-server, libki-print-manager)."
    )
    likely_files: list[str] = Field(
        ...,
        description="File paths that likely need changes (e.g. lib/Libki/Utils/Printing.pm).",
    )
    complexity: Literal["easy", "medium", "hard"] = Field(
        ..., description="Estimated complexity of the fix."
    )
    needs_cross_repo: bool = Field(
        ..., description="True if the fix spans multiple repos and needs a CRTID."
    )
    fix_approach: str = Field(
        ...,
        description="A paragraph explaining what to change, why, and the key constraints to honor.",
    )
    key_guidelines: list[str] = Field(
        ...,
        description="Relevant coding-guideline rules that apply to this fix (short phrases).",
    )
    test_plan: str = Field(
        ..., description="How to verify the fix — which scenarios to test."
    )
    suggested_branch_name: str = Field(
        ..., description="Branch name following the issue-N-description convention."
    )


def _load_guidelines(repo_name: str) -> str:
    """Load the ecosystem guidelines + repo-specific AGENTS.md."""
    parts = []
    ecosystem_path = GUIDELINES_DIR / "ecosystem.md"
    if ecosystem_path.exists():
        parts.append(f"# Ecosystem-wide coding guidelines\n\n{ecosystem_path.read_text()}")

    repo_file = REPO_GUIDELINE_MAP.get(repo_name)
    if repo_file:
        repo_path = GUIDELINES_DIR / repo_file
        if repo_path.exists():
            parts.append(f"# {repo_name} AGENTS.md\n\n{repo_path.read_text()}")

    return "\n\n---\n\n".join(parts) if parts else "(no guidelines available)"


SYSTEM_PROMPT = """You are a senior developer familiar with the Libki library kiosk management ecosystem.

You will be given:
1. The project's coding guidelines (ecosystem-wide rules + repo-specific rules)
2. A GitHub issue or PR with its title, body, and comments

Your job: analyze the issue and produce a structured fix recommendation that a developer (or AI coding agent) can act on. Be specific about file paths, function names, and the approach. Reference the coding guidelines when they constrain the fix (e.g., "must scope by instance", "use JSON.parse not eval", "don't extend v1 API").

Be pragmatic — recommend the simplest fix that solves the problem while honoring the guidelines. If the issue is already resolved (closed with a merged PR), say so and recommend verification instead of a new fix."""


def _build_issue_context(issue: dict, comments: list[dict]) -> str:
    lines = [
        f"## Issue: {issue['repo_owner']}/{issue['repo_name']}#{issue['number']}",
        f"**Title:** {issue['title']}",
        f"**State:** {issue['state']} ({'PR' if issue['is_pull_request'] else 'issue'})",
        f"**Author:** {issue.get('author') or 'unknown'}",
        f"**Created:** {issue['created_at']}",
        "",
        "**Body:**",
        issue.get("body") or "(empty)",
    ]

    if comments:
        lines.append("")
        lines.append(f"**Comments ({len(comments)}):**")
        for c in comments[:10]:  # cap at 10 to stay within context budget
            lines.append(f"\n--- {c.get('author', 'unknown')} ({c['created_at'][:10]}):")
            lines.append(c.get("body") or "(empty)")

    return "\n".join(lines)


def generate_recommendation(
    db_path: Path,
    issue_id: int,
    api_key: str,
    model: str = "claude-opus-4-6",
) -> Recommendation:
    """Generate and store a fix recommendation for an issue."""
    init_db(db_path)

    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT i.*, r.owner AS repo_owner, r.name AS repo_name
            FROM issues i JOIN repos r ON i.repo_id = r.id
            WHERE i.id = ?
            """,
            (issue_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Issue {issue_id} not found")

        comments = conn.execute(
            "SELECT * FROM comments WHERE issue_id = ? ORDER BY created_at LIMIT 10",
            (issue_id,),
        ).fetchall()

    issue = dict(row)
    repo_name = issue["repo_name"]
    guidelines = _load_guidelines(repo_name)
    issue_context = _build_issue_context(issue, [dict(c) for c in comments])

    client = anthropic.Anthropic(api_key=api_key)

    response = client.messages.parse(
        model=model,
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    f"# CODING GUIDELINES\n\n{guidelines}\n\n"
                    f"---\n\n# ISSUE TO ANALYZE\n\n{issue_context}\n\n"
                    "Produce a structured fix recommendation."
                ),
            }
        ],
        output_format=Recommendation,
    )

    rec = response.parsed_output
    if rec is None:
        raise RuntimeError("Claude did not return a valid recommendation")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO recommendations (issue_id, model, recommendation, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(issue_id) DO UPDATE SET
                model = excluded.model,
                recommendation = excluded.recommendation,
                created_at = excluded.created_at
            """,
            (issue_id, model, rec.model_dump_json(), now),
        )

    return rec


def get_stored_recommendation(db_path: Path, issue_id: int) -> tuple[Recommendation, str, str] | None:
    """Retrieve a previously generated recommendation.

    Returns (Recommendation, model, created_at) or None.
    """
    init_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT model, recommendation, created_at FROM recommendations WHERE issue_id = ?",
            (issue_id,),
        ).fetchone()
    if row is None:
        return None
    rec = Recommendation.model_validate_json(row["recommendation"])
    return rec, row["model"], row["created_at"]
