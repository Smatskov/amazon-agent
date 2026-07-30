"""Shared test safety net.

`AGENTS.md` requires SQLite tests to use temporary databases. A test that calls
`agent.agent_brain()` without explicit paths previously wrote into the real
`data/` directory. Redirecting the module-level defaults makes that structurally
impossible instead of relying on every test remembering to pass a path.
"""

import pytest

import memory
import workflow_store


@pytest.fixture(autouse=True)
def isolated_default_databases(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DEFAULT_DATABASE_PATH", tmp_path / "default-memory.db")
    monkeypatch.setattr(
        workflow_store,
        "DEFAULT_WORKFLOW_DATABASE_PATH",
        tmp_path / "default-workflows.db",
    )
