# AGENTS.md — libki-triage

Instructions for AI agents and human contributors.

## Stack snapshot

- Python 3.12 (inside Docker; 3.11+ for local dev).
- `typer` CLI, `httpx` for GitHub REST, `rich` for terminal output, `pydantic-settings` for config.
- SQLite single-file DB. Schema in [src/libki_triage/db.py](src/libki_triage/db.py).
- `uv` for dependency management.

---

## Contribution workflow

Fork → issue → branch → PR. Open issues and PRs against `brendan1226/libki-triage`; push commits to your fork, never to the upstream repo. Default branch: `main`.

---

## Non-negotiables

### Fix discipline

Every bug-fix PR must include: (1) reproducing scenario, (2) root-cause statement, (3) regression test — or an explicit reason none is feasible.

### Schema evolution

All schema changes go through `SCHEMA` in [src/libki_triage/db.py](src/libki_triage/db.py). Use `CREATE TABLE IF NOT EXISTS` and additive `ALTER TABLE`; keep startup idempotent. When that's not enough, add a migrations module — do not ship ad-hoc SQL scripts.

### GitHub API etiquette

- Paginate every listing via the `Link` header. Never assume a single page.
- Respect `X-RateLimit-Remaining` and `X-RateLimit-Reset`. A 403 at high volume is rate limit, not auth — inspect response headers before reporting.
- Cache aggressively; the upstream repos change slowly.

### Secrets

- Never commit `.env`. Document new env vars in `.env.example`.
- Never log token values, even at DEBUG level.

### No `eval()` on network responses

Use `json.loads()` / `pydantic` models. (Yes, the same rule as libki-print-station.)

### Write-back scope

Phases v0–v1 are **read-only**. Nothing in this codebase should create, edit, or close GitHub issues until v2, and v2 requires a preview + explicit-confirm step on every write.

---

## Testing

```bash
uv run pytest
```

Tests use `tmp_path` fixtures and do not touch real GitHub or a persistent DB. If you need fixtures for GitHub responses, use `pytest-httpx` with sample JSON payloads committed under `tests/fixtures/`.
