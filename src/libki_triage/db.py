import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 6

SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL, name TEXT NOT NULL,
    default_branch TEXT, last_harvested_at TEXT,
    UNIQUE(owner, name)
);
CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL REFERENCES repos(id),
    number INTEGER NOT NULL, title TEXT NOT NULL, body TEXT,
    state TEXT NOT NULL, is_pull_request INTEGER NOT NULL DEFAULT 0,
    author TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    closed_at TEXT, url TEXT NOT NULL, labels TEXT, harvested_at TEXT NOT NULL,
    embedding BLOB, embedded_at TEXT, embed_text_hash TEXT,
    UNIQUE(repo_id, number)
);
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL REFERENCES issues(id),
    github_id INTEGER NOT NULL UNIQUE, author TEXT, body TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL, description TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS group_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    issue_id INTEGER NOT NULL REFERENCES issues(id),
    added_at TEXT NOT NULL, UNIQUE(group_id, issue_id)
);
CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL REFERENCES issues(id),
    model TEXT NOT NULL, recommendation TEXT NOT NULL,
    created_at TEXT NOT NULL, UNIQUE(issue_id)
);
CREATE TABLE IF NOT EXISTS code_fixes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL REFERENCES issues(id),
    file_path TEXT NOT NULL, original_content TEXT,
    fixed_content TEXT NOT NULL, explanation TEXT,
    model TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS code_fix_meta (
    issue_id INTEGER PRIMARY KEY REFERENCES issues(id),
    commit_message TEXT NOT NULL, model TEXT NOT NULL,
    created_at TEXT NOT NULL, pr_url TEXT, pr_number INTEGER
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    name TEXT, picture_url TEXT,
    created_at TEXT NOT NULL, last_login_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER PRIMARY KEY REFERENCES users(id),
    github_token TEXT, github_fork_owner TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_issues_repo_state ON issues(repo_id, state);
CREATE INDEX IF NOT EXISTS idx_issues_is_pr ON issues(is_pull_request);
CREATE INDEX IF NOT EXISTS idx_comments_issue ON comments(issue_id);
CREATE INDEX IF NOT EXISTS idx_group_members_group ON group_members(group_id);
CREATE INDEX IF NOT EXISTS idx_group_members_issue ON group_members(issue_id);
CREATE INDEX IF NOT EXISTS idx_recommendations_issue ON recommendations(issue_id);
CREATE INDEX IF NOT EXISTS idx_code_fixes_issue ON code_fixes(issue_id);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current < 2:
        for col, typ in [("embedding", "BLOB"), ("embedded_at", "TEXT"), ("embed_text_hash", "TEXT")]:
            try:
                conn.execute(f"ALTER TABLE issues ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass
        conn.execute("CREATE INDEX IF NOT EXISTS idx_issues_embed_hash ON issues(embed_text_hash)")
    if current < 3:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS groups (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, description TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS group_members (id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER NOT NULL, issue_id INTEGER NOT NULL, added_at TEXT NOT NULL, UNIQUE(group_id, issue_id));
            CREATE INDEX IF NOT EXISTS idx_group_members_group ON group_members(group_id);
            CREATE INDEX IF NOT EXISTS idx_group_members_issue ON group_members(issue_id);
        """)
    if current < 4:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS recommendations (id INTEGER PRIMARY KEY AUTOINCREMENT, issue_id INTEGER NOT NULL, model TEXT NOT NULL, recommendation TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(issue_id));
        """)
    if current < 5:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS code_fixes (id INTEGER PRIMARY KEY AUTOINCREMENT, issue_id INTEGER NOT NULL, file_path TEXT NOT NULL, original_content TEXT, fixed_content TEXT NOT NULL, explanation TEXT, model TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS code_fix_meta (issue_id INTEGER PRIMARY KEY, commit_message TEXT NOT NULL, model TEXT NOT NULL, created_at TEXT NOT NULL, pr_url TEXT, pr_number INTEGER);
            CREATE INDEX IF NOT EXISTS idx_code_fixes_issue ON code_fixes(issue_id);
        """)
    if current < 6:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL UNIQUE, name TEXT, picture_url TEXT, created_at TEXT NOT NULL, last_login_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS user_settings (user_id INTEGER PRIMARY KEY, github_token TEXT, github_fork_owner TEXT, updated_at TEXT NOT NULL);
        """)
    if current < SCHEMA_VERSION:
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
