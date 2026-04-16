# libki-triage

Semantic triage tool for the [Libki](https://github.com/Libki) ecosystem's GitHub issues.

Harvests issues, PRs, and comments across the Libki org and (eventually) lets you ask natural-language questions: has this problem been reported before? Is there an open PR against it? Is it novel?

## Status

**v0 — CLI, read-only.** Harvesting works. Semantic search, Claude-powered verdicts, web UI, and omnibus-issue consolidation come in later phases.

## Roadmap

| Phase | Shape | Writes to GitHub? |
|---|---|---|
| v0 | CLI, harvest + status | No |
| v0.5 | CLI semantic search + Claude verdicts | No |
| v1 | Web UI (chat + cluster browser) | No |
| v2 | Omnibus consolidation via suggested-comment on sibling issues | Yes, with preview + explicit confirm |

## Quick start (Docker)

```bash
git clone https://github.com/brendan1226/libki-triage.git
cd libki-triage
cp .env.example .env     # optionally add a GitHub token for higher rate limits
docker compose build
docker compose run --rm triage harvest
docker compose run --rm triage status
```

## Local development

```bash
# Requires uv (https://docs.astral.sh/uv/)
uv sync
uv run libki-triage --help
uv run libki-triage harvest
uv run libki-triage status
uv run pytest
```

## Configuration

Environment variables (loaded from `.env` if present):

| Var | Default | Purpose |
|---|---|---|
| `LIBKI_TRIAGE_GITHUB_TOKEN` | unset | GitHub PAT for higher rate limits (5000/hr vs 60/hr). Only needs `public_repo` scope. |
| `LIBKI_TRIAGE_DB_PATH` | `./data/libki-triage.db` | SQLite file location. |

## Architecture

- Python 3.12, `typer` for CLI, `httpx` for GitHub REST.
- SQLite for the issue store. Schema lives in [`src/libki_triage/db.py`](src/libki_triage/db.py).
- Pagination via the `Link: rel="next"` header; idempotent upserts keyed on `(repo, number)` for issues and `github_id` for comments.

## License

TBD.
