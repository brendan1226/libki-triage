from pathlib import Path
from typing import TypedDict

import numpy as np
from fastembed import TextEmbedding

from .db import connect, init_db
from .embed import _normalize, deserialize_embedding


class SearchResult(TypedDict):
    repo_owner: str
    repo_name: str
    number: int
    title: str
    url: str
    state: str
    is_pull_request: bool
    score: float
    body_snippet: str
    body: str


SNIPPET_CHARS = 300


class NoEmbeddingsError(RuntimeError):
    """Raised when the DB has no embedded rows to search against."""


def _embed_query(model_name: str, query: str) -> np.ndarray:
    model = TextEmbedding(model_name=model_name)
    vec = next(model.embed([query]))
    return _normalize(np.array(vec, dtype=np.float32))


def search(
    db_path: Path,
    query: str,
    model_name: str,
    top_k: int = 5,
    exclude_prs: bool = False,
) -> list[SearchResult]:
    """Rank embedded issues by cosine similarity to the query.

    Raises NoEmbeddingsError if no rows have been embedded yet.
    """
    init_db(db_path)
    query_vec = _embed_query(model_name, query)

    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                i.id, i.title, i.body, i.number, i.url, i.state,
                i.is_pull_request, i.embedding,
                r.owner AS repo_owner, r.name AS repo_name
            FROM issues i
            JOIN repos r ON i.repo_id = r.id
            WHERE i.embedding IS NOT NULL
            """
        ).fetchall()

    if exclude_prs:
        rows = [r for r in rows if not r["is_pull_request"]]

    if not rows:
        raise NoEmbeddingsError(
            "No embedded issues. Run `libki-triage embed` first to index issues."
        )

    matrix = np.vstack([deserialize_embedding(r["embedding"]) for r in rows])
    scores = matrix @ query_vec
    top_indices = np.argsort(-scores)[:top_k]

    results: list[SearchResult] = []
    for idx in top_indices:
        row = rows[int(idx)]
        body = row["body"] or ""
        snippet = body.strip().replace("\r\n", "\n")
        if len(snippet) > SNIPPET_CHARS:
            snippet = snippet[:SNIPPET_CHARS].rstrip() + "..."
        results.append(
            SearchResult(
                repo_owner=row["repo_owner"],
                repo_name=row["repo_name"],
                number=row["number"],
                title=row["title"],
                url=row["url"],
                state=row["state"],
                is_pull_request=bool(row["is_pull_request"]),
                score=float(scores[int(idx)]),
                body_snippet=snippet,
                body=body,
            )
        )
    return results
