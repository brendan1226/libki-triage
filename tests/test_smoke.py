from pathlib import Path

from libki_triage.db import connect, init_db


def test_init_db_creates_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    with connect(db_path) as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert {"repos", "issues", "comments"}.issubset(tables)


def test_init_db_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    init_db(db_path)  # second call must not raise
    with connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM repos").fetchone()[0]
    assert count == 0
