import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

EXPECTED_TABLES = {
    "alembic_version",
    "projects",
    "analyses",
    "proposal_drafts",
    "submissions",
    "reply_events",
    "audit_events",
    "source_cursors",
    "outbox_events",
}


def test_initial_migration_creates_complete_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "migration-test.sqlite3"
    config = Config("alembic.ini")
    config.set_main_option(
        "sqlalchemy.url", f"sqlite+aiosqlite:///{database_path.as_posix()}"
    )

    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert tables >= EXPECTED_TABLES
