"""Local SQLite storage for simple persistent agent memory."""

import sqlite3
from pathlib import Path


DEFAULT_DATABASE_PATH = Path(__file__).resolve().parent.parent / "data" / "memory.db"

# TODO: Record purchase history only after a verified successful checkout event.
# TODO: Add preference inference only after an explicit, reviewable policy is adopted.


def _connect(database_path: str | Path) -> sqlite3.Connection:
    """Open the memory database and ensure its key/value table exists."""
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS memory (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    return connection


def remember(
    key: str, value: str, database_path: str | Path = DEFAULT_DATABASE_PATH
) -> None:
    """Store a string value, replacing the value for an existing key."""
    with _connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO memory (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


def recall(key: str, database_path: str | Path = DEFAULT_DATABASE_PATH) -> str | None:
    """Return a stored value, or None when the key is not present."""
    with _connect(database_path) as connection:
        row = connection.execute(
            "SELECT value FROM memory WHERE key = ?", (key,)
        ).fetchone()
    return row[0] if row else None


def forget(key: str, database_path: str | Path = DEFAULT_DATABASE_PATH) -> None:
    """Remove a stored value; missing keys are intentionally ignored."""
    with _connect(database_path) as connection:
        connection.execute("DELETE FROM memory WHERE key = ?", (key,))
