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
    uvicorn.run("libki_triage.web:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
