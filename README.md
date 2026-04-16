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
docker compose run --rm triage harvest     # populate the database
docker compose up -d                        # start the status dashboard
open http://localhost:8000                  # view it
```

The dashboard listens on `127.0.0.1:8000` by default (loopback only) so it's safe to run behind a reverse proxy like Caddy on a server.

### One-shot CLI commands

```bash
docker compose run --rm triage harvest
docker compose run --rm triage harvest --repo Libki/libki-server
docker compose run --rm triage status
```

## Local development

```bash
# Requires uv (https://docs.astral.sh/uv/)
uv sync
uv run libki-triage --help
uv run libki-triage harvest
uv run libki-triage status
uv run libki-triage serve --reload        # web UI at http://localhost:8000
uv run pytest
```

## Configuration

Environment variables (loaded from `.env` if present):

| Var | Default | Purpose |
|---|---|---|
| `LIBKI_TRIAGE_GITHUB_TOKEN` | unset | GitHub PAT for higher rate limits (5000/hr vs 60/hr). Only needs `public_repo` scope. |
| `LIBKI_TRIAGE_DB_PATH` | `./data/libki-triage.db` | SQLite file location. |

## Architecture

- Python 3.12, `typer` for CLI, `httpx` for GitHub REST, `FastAPI` + `uvicorn` for the web dashboard.
- SQLite for the issue store. Schema lives in [`src/libki_triage/db.py`](src/libki_triage/db.py).
- Pagination via the `Link: rel="next"` header; idempotent upserts keyed on `(repo, number)` for issues and `github_id` for comments.
- Jinja2 templates in [`src/libki_triage/templates/`](src/libki_triage/templates/), static assets in [`src/libki_triage/static/`](src/libki_triage/static/).

## Deployment notes (DigitalOcean Droplet)

Hosted at `https://libki-triage.gallagher-family-hub.com` (planned). Deployment pattern:

1. Droplet with Docker + Docker Compose installed.
2. Caddy as the public-facing reverse proxy, handling TLS via Let's Encrypt automatically.
3. `libki-triage` container binds to `127.0.0.1:8000` (loopback only); Caddy proxies to it.
4. Cron on the host runs `docker compose -f /opt/libki-triage/docker-compose.yml run --rm triage harvest` every few hours.

Minimal `Caddyfile`:

```
libki-triage.gallagher-family-hub.com {
    reverse_proxy localhost:8000
}
```

## License

TBD.
