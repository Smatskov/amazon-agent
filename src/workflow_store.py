"""SQLite persistence for one active purchasing workflow per Telegram user."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from workflow_models import PurchaseWorkflow, TERMINAL_STATES


DEFAULT_WORKFLOW_DATABASE_PATH = Path(__file__).resolve().parent.parent / "data" / "workflows.db"
# An abandoned search must not block every later request. After this long an
# untouched workflow stops counting as active; the stored row is kept for inspection.
MAX_ACTIVE_WORKFLOW_AGE = timedelta(hours=24)


def _connect(database_path: str | Path) -> sqlite3.Connection:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS purchase_workflows (
            telegram_user_id INTEGER PRIMARY KEY,
            workflow_id TEXT NOT NULL,
            state TEXT NOT NULL,
            state_version INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    return connection


def get_workflow(
    telegram_user_id: int, database_path: str | Path = DEFAULT_WORKFLOW_DATABASE_PATH
) -> PurchaseWorkflow | None:
    with _connect(database_path) as connection:
        row = connection.execute(
            "SELECT payload_json FROM purchase_workflows WHERE telegram_user_id = ?",
            (telegram_user_id,),
        ).fetchone()
    return PurchaseWorkflow.from_record(json.loads(row[0])) if row else None


def get_active_workflow(
    telegram_user_id: int, database_path: str | Path = DEFAULT_WORKFLOW_DATABASE_PATH
) -> PurchaseWorkflow | None:
    workflow = get_workflow(telegram_user_id, database_path)
    if not workflow or not workflow.is_active or _is_stale(workflow):
        return None
    return workflow


def _is_stale(workflow: PurchaseWorkflow) -> bool:
    """An unparseable or missing timestamp is treated as fresh, never as expired."""
    try:
        updated_at = datetime.fromisoformat(workflow.updated_at)
    except (TypeError, ValueError):
        return False
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return datetime.now(UTC) - updated_at > MAX_ACTIVE_WORKFLOW_AGE


def save_workflow(
    workflow: PurchaseWorkflow,
    database_path: str | Path = DEFAULT_WORKFLOW_DATABASE_PATH,
) -> PurchaseWorkflow:
    workflow.updated_at = datetime.now(UTC).isoformat()
    payload = json.dumps(workflow.to_record())
    with _connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO purchase_workflows
                (telegram_user_id, workflow_id, state, state_version, payload_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_user_id) DO UPDATE SET
                workflow_id = excluded.workflow_id,
                state = excluded.state,
                state_version = excluded.state_version,
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            (
                workflow.telegram_user_id,
                workflow.workflow_id,
                workflow.state.value,
                workflow.state_version,
                payload,
                workflow.updated_at,
            ),
        )
    return workflow


def transition(
    workflow: PurchaseWorkflow, state, *, pending_question: str | None = None
) -> PurchaseWorkflow:
    """Apply a state change in orchestration code; no purchase states are entered in M1."""
    workflow.state = state
    workflow.state_version += 1
    workflow.pending_question = pending_question
    if state in TERMINAL_STATES:
        workflow.completion_status = state.value
    return workflow
