import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    default_branch TEXT,
    last_harvested_at TEXT,
    UNIQUE(owner, name)
);

CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL REFERENCES repos(id),
    number INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT,
    state TEXT NOT NULL,
    is_pull_request INTEGER NOT NULL DEFAULT 0,
    author TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    closed_at TEXT,
    url TEXT NOT NULL,
    labels TEXT,
    harvested_at TEXT NOT NULL,
    embedding BLOB,
    embedded_at TEXT,
    embed_text_hash TEXT,
    UNIQUE(repo_id, number)
);

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL REFERENCES issues(id),
    github_id INTEGER NOT NULL UNIQUE,
    author TEXT,
    body TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_issues_repo_state ON issues(repo_id, state);
CREATE INDEX IF NOT EXISTS idx_issues_is_pr ON issues(is_pull_request);
CREATE INDEX IF NOT EXISTS idx_comments_issue ON comments(issue_id);
"""

# Indexes that depend on columns added in migrations live in _migrate(),
# because `CREATE INDEX` on a not-yet-migrated DB errors before the ALTER
# TABLE has a chance to run.


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply additive column migrations to already-existing DBs.

    The SCHEMA above is authoritative for fresh DBs; this function only runs
    ALTER TABLE for older DBs whose `user_version` predates the current schema.
    """
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current < 2:
        # v1 -> v2: add embedding columns. Idempotent under duplicate-column errors.
        for column, coltype in [
            ("embedding", "BLOB"),
            ("embedded_at", "TEXT"),
            ("embed_text_hash", "TEXT"),
        ]:
            try:
                conn.execute(f"ALTER TABLE issues ADD COLUMN {column} {coltype}")
            except sqlite3.OperationalError:
                pass  # column already exists
        conn.execute("CREATE INDEX IF NOT EXISTS idx_issues_embed_hash ON issues(embed_text_hash)")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


@contextmanager
def connect(db_path: Path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
