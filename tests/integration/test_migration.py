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
    "provider_circuits",
}


def test_initial_migration_creates_complete_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "migration-test.sqlite3"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path.as_posix()}")

    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

    assert tables >= EXPECTED_TABLES


def test_existing_database_upgrade_adds_submission_conversation_url(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "migration-upgrade.sqlite3"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path.as_posix()}")

    command.upgrade(config, "0002_assisted_approval")
    with sqlite3.connect(database_path) as connection:
        before = {row[1] for row in connection.execute("PRAGMA table_info(submissions)")}
    assert "conversation_url" not in before

    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        after = {row[1] for row in connection.execute("PRAGMA table_info(submissions)")}
    assert "conversation_url" in after


def test_delivery_upgrade_preserves_pending_outbox(tmp_path):
    database_path = tmp_path / "delivery-upgrade.sqlite3"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    command.upgrade(config, "0004_provider_circuit")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO outbox_events "
            "(id,event_type,deduplication_key,payload,delivered,created_at) "
            "VALUES ('123','system_alert','pending','{}',0,CURRENT_TIMESTAMP)"
        )
    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT delivered,delivery_attempts,next_attempt_at,"
            "telegram_message_id FROM outbox_events "
            "WHERE deduplication_key='pending'"
        ).fetchone()
    assert row == (0, 0, None, None)
