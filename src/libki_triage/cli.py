import typer
from rich.console import Console
from rich.table import Table

from .config import REPOS, settings
from .db import connect, init_db
from .harvest import harvest_repo

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000

app = typer.Typer(
    help="Semantic triage tool for the Libki ecosystem GitHub issues.",
    no_args_is_help=True,
)
console = Console()


@app.command()
def export(
    output: str = typer.Option("libki-triage-export.json", "--output", "-o", help="Output JSON file."),
    include_embeddings: bool = typer.Option(False, "--include-embeddings", help="Include raw embedding vectors."),
) -> None:
    """Export all issues, comments, and AI-generated content to a single JSON file."""
    import json
    from datetime import datetime, timezone

    init_db(settings.db_path)

    with connect(settings.db_path) as conn:
        repos = [dict(r) for r in conn.execute("SELECT * FROM repos").fetchall()]
        issues = [dict(r) for r in conn.execute("SELECT * FROM issues ORDER BY repo_id, number").fetchall()]
        comments = [dict(r) for r in conn.execute("SELECT * FROM comments ORDER BY issue_id, created_at").fetchall()]
        recs_rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='recommendations'").fetchone()
        recs = [dict(r) for r in conn.execute("SELECT * FROM recommendations").fetchall()] if recs_rows else []
        fixes_rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='code_fixes'").fetchone()
        code_fixes = [dict(r) for r in conn.execute("SELECT * FROM code_fixes").fetchall()] if fixes_rows else []
        meta_rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='code_fix_meta'").fetchone()
        fix_meta = [dict(r) for r in conn.execute("SELECT * FROM code_fix_meta").fetchall()] if meta_rows else []
        groups = [dict(r) for r in conn.execute("SELECT * FROM groups").fetchall()]
        group_members = [dict(r) for r in conn.execute("SELECT * FROM group_members").fetchall()]

    repo_by_id = {r["id"]: r for r in repos}

    comments_by_issue: dict[int, list[dict]] = {}
    for c in comments:
        comments_by_issue.setdefault(c["issue_id"], []).append(c)

    recs_by_issue = {r["issue_id"]: r for r in recs}
    fixes_by_issue: dict[int, list[dict]] = {}
    for f in code_fixes:
        fixes_by_issue.setdefault(f["issue_id"], []).append(f)
    fix_meta_by_issue = {f["issue_id"]: f for f in fix_meta}

    enriched = []
    for i in issues:
        internal_id = i["id"]
        if not include_embeddings:
            i.pop("embedding", None)
        repo = repo_by_id.get(i["repo_id"], {})
        i["repo_owner"] = repo.get("owner")
        i["repo_name"] = repo.get("name")
        i["comments"] = comments_by_issue.get(internal_id, [])
        rec = recs_by_issue.get(internal_id)
        if rec:
            try:
                rec["recommendation"] = json.loads(rec["recommendation"])
            except Exception:
                pass
        i["ai_recommendation"] = rec
        i["ai_code_fixes"] = fixes_by_issue.get(internal_id, [])
        i["ai_fix_meta"] = fix_meta_by_issue.get(internal_id)
        enriched.append(i)

    export_data = {
        "source": "libki-triage",
        "github_org": "Libki",
        "repos": repos,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_issues": len(enriched),
        "total_comments": len(comments),
        "issues": enriched,
        "groups": [
            {**g, "member_issue_ids": [m["issue_id"] for m in group_members if m["group_id"] == g["id"]]}
            for g in groups
        ],
    }

    with open(output, "w") as f:
        json.dump(export_data, f, indent=2, default=str)

    size_mb = len(json.dumps(export_data, default=str)) / 1024 / 1024
    console.print(f"[green]Exported {len(enriched)} issues, {len(comments)} comments to {output} ({size_mb:.1f} MB)[/green]")


@app.command()
def harvest(
    repo: str = typer.Option(
        None,
        "--repo",
        help="Harvest a single repo in the form owner/name. Omit to harvest all Libki repos.",
    ),
) -> None:
    """Fetch issues, PRs, and comments from GitHub into the local SQLite database."""
    if repo:
        owner, name = repo.split("/", 1)
        targets = [(owner, name)]
    else:
        targets = REPOS

    init_db(settings.db_path)

    for owner, name in targets:
        console.print(f"[cyan]Harvesting {owner}/{name}...[/cyan]")
        counts = harvest_repo(settings.db_path, owner, name, settings.github_token)
        console.print(
            f"  {counts['issues']} issues, {counts['prs']} PRs, {counts['comments']} comments"
        )
        if counts.get("skipped_comments"):
            console.print(
                f"  [yellow]({counts['skipped_comments']} comments skipped — "
                f"issue not yet in DB; next harvest will pick them up)[/yellow]"
            )

    console.print("[green]Done.[/green]")


@app.command()
def status() -> None:
    """Show current harvest state per repo."""
    init_db(settings.db_path)
    with connect(settings.db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                r.owner, r.name, r.default_branch, r.last_harvested_at,
                SUM(CASE WHEN i.is_pull_request = 0 THEN 1 ELSE 0 END) AS issues,
                SUM(CASE WHEN i.is_pull_request = 1 THEN 1 ELSE 0 END) AS prs
            FROM repos r
            LEFT JOIN issues i ON i.repo_id = r.id
            GROUP BY r.id
            ORDER BY r.owner, r.name
            """
        ).fetchall()

    table = Table(title="libki-triage status")
    table.add_column("Repo")
    table.add_column("Default branch")
    table.add_column("Issues", justify="right")
    table.add_column("PRs", justify="right")
    table.add_column("Last harvested (UTC)")
    for row in rows:
        table.add_row(
            f"{row['owner']}/{row['name']}",
            row["default_branch"] or "?",
            str(row["issues"] or 0),
            str(row["prs"] or 0),
            row["last_harvested_at"] or "never",
        )
    console.print(table)


@app.command()
def serve(
    host: str = typer.Option(DEFAULT_HOST, "--host", help="Interface to bind to."),
    port: int = typer.Option(DEFAULT_PORT, "--port", help="Port to listen on."),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload for development."),
) -> None:
    """Run the web status dashboard."""
    import uvicorn

    init_db(settings.db_path)
    console.print(f"[cyan]libki-triage serving on http://{host}:{port}[/cyan]")
    uvicorn.run(
        "libki_triage.web:app",
        host=host,
        port=port,
        reload=reload,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


@app.command()
def embed(
    batch_size: int = typer.Option(32, "--batch-size", help="Texts per embedding batch."),
) -> None:
    """Compute embeddings for issues whose title/body changed since the last run."""
    from .embed import embed_pending

    def on_progress(stage: str, payload) -> None:
        if stage == "loading_model":
            console.print(f"[cyan]Loading embedding model {payload}...[/cyan]")
        elif stage == "embedding":
            console.print(f"[cyan]Embedding {payload} issues...[/cyan]")

    counts = embed_pending(
        settings.db_path, settings.embedding_model, batch_size, on_progress=on_progress
    )
    console.print(
        f"[green]Embedded {counts['embedded']} / {counts['total']}  "
        f"(skipped {counts['skipped']} unchanged)[/green]"
    )


@app.command()
def search(
    query: str = typer.Argument(..., help="Problem description to search for."),
    top_k: int = typer.Option(5, "--top-k", "-k", help="Number of matches to return."),
    exclude_prs: bool = typer.Option(
        False, "--exclude-prs", help="Return issues only, skip PRs."
    ),
) -> None:
    """Rank harvested issues by semantic similarity to the query."""
    from .search import NoEmbeddingsError, search as semantic_search

    try:
        results = semantic_search(
            settings.db_path,
            query,
            settings.embedding_model,
            top_k=top_k,
            exclude_prs=exclude_prs,
        )
    except NoEmbeddingsError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    table = Table(title=f"Top {len(results)} matches for: {query!r}")
    table.add_column("Score", justify="right")
    table.add_column("Repo")
    table.add_column("#", justify="right")
    table.add_column("State")
    table.add_column("Title")

    for r in results:
        kind = " PR" if r["is_pull_request"] else ""
        table.add_row(
            f"{r['score']:.3f}",
            f"{r['repo_owner']}/{r['repo_name']}",
            str(r["number"]),
            f"{r['state']}{kind}",
            r["title"],
        )
    console.print(table)


@app.command()
def classify(
    query: str = typer.Argument(..., help="Problem description to classify against."),
    top_k: int = typer.Option(5, "--top-k", "-k", help="Number of matches to classify."),
    exclude_prs: bool = typer.Option(
        False, "--exclude-prs", help="Return issues only, skip PRs."
    ),
) -> None:
    """Semantic search plus a Claude-generated verdict per match."""
    from .classify import classify as run_classify
    from .search import NoEmbeddingsError

    if not settings.anthropic_api_key:
        console.print(
            "[red]LIBKI_TRIAGE_ANTHROPIC_API_KEY is not set.[/red] "
            "Add it to .env or export it in your shell."
        )
        raise typer.Exit(code=1)

    console.print(f"[cyan]Classifying top {top_k} matches with {settings.classification_model}...[/cyan]")
    try:
        results, verdicts = run_classify(
            settings.db_path,
            query,
            settings.embedding_model,
            settings.anthropic_api_key,
            settings.classification_model,
            top_k=top_k,
            exclude_prs=exclude_prs,
        )
    except NoEmbeddingsError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    if not results:
        console.print("[yellow]No matches found.[/yellow]")
        return

    verdicts_by_idx = {i: v for i, v in enumerate(verdicts) if i < len(results)}
    for i, r in enumerate(results):
        console.print()
        kind = "PR" if r["is_pull_request"] else "issue"
        console.print(
            f"[bold cyan]{r['repo_owner']}/{r['repo_name']}#{r['number']}[/bold cyan] "
            f"[dim]({r['state']} {kind}, score {r['score']:.3f})[/dim]"
        )
        console.print(f"  [bold]{r['title']}[/bold]")
        v = verdicts_by_idx.get(i)
        if v is not None:
            console.print(f"  Verdict:   [yellow]{v.verdict}[/yellow]")
            console.print(f"  Why:       {v.rationale}")
            console.print(f"  Suggested: {v.suggested_action}")
        else:
            console.print("  [dim](no verdict returned)[/dim]")
        console.print(f"  {r['url']}")


if __name__ == "__main__":
    app()
