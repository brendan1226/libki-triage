import hashlib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from fastembed import TextEmbedding

from .db import connect, init_db


def _embedding_text(title: str, body: str | None) -> str:
    """The canonical text to feed into the embedding model for an issue."""
    parts = [title]
    if body:
        parts.append(body.strip())
    return "\n\n".join(parts)


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize(vec: np.ndarray) -> np.ndarray:
    """L2-normalize so dot product equals cosine similarity."""
    norm = np.linalg.norm(vec, axis=-1, keepdims=True)
    norm = np.where(norm == 0, 1.0, norm)
    return vec / norm


def _serialize_embedding(vec: np.ndarray) -> bytes:
    """Store as float32 bytes (384 dims -> 1.5 KB)."""
    return vec.astype(np.float32).tobytes()


def deserialize_embedding(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype=np.float32)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def embed_pending(
    db_path: Path,
    model_name: str,
    batch_size: int = 32,
    on_progress=None,
) -> dict:
    """Compute embeddings for issues that don't have them yet, or whose
    title/body changed since the last embedding.

    Idempotent: re-running with no new data is near-instant (hash compare only).
    """
    init_db(db_path)
    embedded_at = _utc_now_iso()
    counts = {"embedded": 0, "skipped": 0, "total": 0}

    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, title, body, embed_text_hash FROM issues"
        ).fetchall()
        counts["total"] = len(rows)

        pending_ids: list[int] = []
        pending_texts: list[str] = []
        pending_hashes: list[str] = []
        for row in rows:
            text = _embedding_text(row["title"], row["body"])
            h = _text_hash(text)
            if row["embed_text_hash"] == h and row["embed_text_hash"] is not None:
                counts["skipped"] += 1
                continue
            pending_ids.append(row["id"])
            pending_texts.append(text)
            pending_hashes.append(h)

        if not pending_ids:
            return counts

        if on_progress is not None:
            on_progress("loading_model", model_name)
        model = TextEmbedding(model_name=model_name)

        if on_progress is not None:
            on_progress("embedding", len(pending_ids))

        # fastembed yields one vector per text; embed() is a generator.
        vectors = list(model.embed(pending_texts, batch_size=batch_size))
        matrix = _normalize(np.array(vectors, dtype=np.float32))

        for i, issue_id in enumerate(pending_ids):
            conn.execute(
                """
                UPDATE issues
                SET embedding = ?,
                    embedded_at = ?,
                    embed_text_hash = ?
                WHERE id = ?
                """,
                (
                    _serialize_embedding(matrix[i]),
                    embedded_at,
                    pending_hashes[i],
                    issue_id,
                ),
            )
            counts["embedded"] += 1

    return counts
