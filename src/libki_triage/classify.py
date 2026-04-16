from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from .search import SearchResult, search as semantic_search


VERDICT_DESCRIPTIONS = """
- `has_open_pr`: A candidate describes essentially the same problem AND has an open PR proposing a fix. The asker should review/test that PR rather than file new work.
- `closed_with_fix`: A candidate was closed with a merged fix. The asker should update to the latest release.
- `reported_only`: A candidate describes the same or very similar problem but has no fix in progress. The asker should comment / +1 / add details rather than file new.
- `likely_duplicate`: A candidate is essentially the same problem. The asker should comment there, not file new.
- `tangentially_related`: Same general area but different root cause. Worth mentioning as context when filing new.
- `unrelated`: Semantic-search false positive; no meaningful overlap with the asker's problem.
""".strip()


SYSTEM_PROMPT = (
    "You are a triage assistant for the Libki GitHub issue backlog.\n\n"
    "Given a user's problem description and a list of candidate related issues/PRs that were "
    "surfaced by semantic search, classify each candidate's relevance to the user's problem.\n\n"
    "Verdict vocabulary (use these exact values):\n"
    + VERDICT_DESCRIPTIONS
    + "\n\n"
    "For each candidate, produce:\n"
    "  - match_id: the 1-indexed position of the candidate in the input list\n"
    "  - verdict: one of the values above\n"
    "  - rationale: ONE sentence explaining the classification\n"
    "  - suggested_action: ONE sentence telling the user what to do about this candidate "
    "(e.g., 'Comment on #459 with your reproduction rather than filing a new issue')\n\n"
    "Be conservative: `likely_duplicate` and `has_open_pr` are strong claims — use them only when "
    "the core problem clearly matches. When in doubt, use `tangentially_related` or `unrelated`.\n\n"
    "Return a JSON object with an ordered `verdicts` array, one entry per candidate, in the same "
    "order as the input."
)


class Verdict(BaseModel):
    match_id: int = Field(
        ...,
        description="The 1-indexed position of this candidate in the input list.",
    )
    verdict: Literal[
        "has_open_pr",
        "closed_with_fix",
        "reported_only",
        "likely_duplicate",
        "tangentially_related",
        "unrelated",
    ] = Field(..., description="The relevance classification from the vocabulary.")
    rationale: str = Field(
        ...,
        description="One sentence explaining why this verdict was chosen.",
    )
    suggested_action: str = Field(
        ...,
        description="One sentence action the user should take about this candidate.",
    )


class ClassifyResponse(BaseModel):
    verdicts: list[Verdict]


def _build_candidate_text(results: list[SearchResult]) -> str:
    lines = []
    for i, r in enumerate(results, start=1):
        kind = "PR" if r["is_pull_request"] else "issue"
        body = r["body_snippet"] if r["body_snippet"] else "(no body)"
        lines.append(
            f"{i}. [{r['repo_owner']}/{r['repo_name']}#{r['number']}] "
            f"(state={r['state']}, type={kind}) \"{r['title']}\"\n"
            f"   Body: {body}"
        )
    return "\n\n".join(lines)


def classify(
    db_path: Path,
    query: str,
    embedding_model: str,
    api_key: str,
    classification_model: str = "claude-opus-4-6",
    top_k: int = 5,
    exclude_prs: bool = False,
) -> tuple[list[SearchResult], list[Verdict]]:
    """Run semantic search, then ask Claude to classify each candidate.

    Returns (results, verdicts) aligned by index. Verdicts may be shorter
    than results if Claude failed to return a verdict for every match
    (that should not happen under `output_format`, but we guard anyway).
    """
    results = semantic_search(
        db_path, query, embedding_model, top_k=top_k, exclude_prs=exclude_prs
    )
    if not results:
        return [], []

    client = anthropic.Anthropic(api_key=api_key)
    candidate_text = _build_candidate_text(results)

    response = client.messages.parse(
        model=classification_model,
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    f"QUERY: {query}\n\n"
                    f"CANDIDATES:\n\n{candidate_text}\n\n"
                    "Classify each candidate and return the `verdicts` array in the same order."
                ),
            }
        ],
        output_format=ClassifyResponse,
    )

    parsed = response.parsed_output
    if parsed is None:
        return results, []

    # Align verdicts to results by match_id (1-indexed). If Claude returns them
    # out of order or drops one, this still yields the correct mapping; any
    # missing match_id surfaces as None and is filtered out.
    verdicts_by_idx = {v.match_id: v for v in parsed.verdicts}
    aligned = [verdicts_by_idx.get(i + 1) for i in range(len(results))]
    return results, [v for v in aligned if v is not None]
