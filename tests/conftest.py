"""Shared test safety net.

`AGENTS.md` requires SQLite tests to use temporary databases. A test that calls
`agent.agent_brain()` without explicit paths previously wrote into the real
`data/` directory. Redirecting the module-level defaults makes that structurally
impossible instead of relying on every test remembering to pass a path.
"""

import pytest

import amazon
import intent_classifier
import llm_client
import memory
import workflow_store


@pytest.fixture(autouse=True)
def no_real_amazon(monkeypatch):
    """No test may open a browser or touch the real Amazon account.

    A test that confirmed an order once launched Chromium and hit Amazon for real,
    which hung the suite. Blocking the browser entrypoint makes that impossible rather
    than relying on every test remembering to mock it.
    """

    def refuse(*args, **kwargs):
        raise AssertionError(
            "A test tried to launch a real browser. Mock the amazon.* function it needs."
        )

    # Every real browser use funnels through this one call, so blocking it here catches
    # any path without blocking the guard checks that raise before reaching it. A test
    # that legitimately drives a fake Playwright simply patches it again.
    monkeypatch.setattr(amazon, "async_playwright", refuse)


@pytest.fixture(autouse=True)
def no_real_model(monkeypatch):
    """No test may reach LM Studio.

    An unmocked call waits on a 125-second HTTP timeout, which made the suite crawl. A
    test that needs the model patches `intent_classifier.generate_response` itself.
    """

    async def refuse(*args, **kwargs):
        raise AssertionError(
            "A test tried to reach LM Studio. Mock intent_classifier.generate_response."
        )

    monkeypatch.setattr(llm_client, "generate_response", refuse)
    monkeypatch.setattr(intent_classifier, "generate_response", refuse)


@pytest.fixture(autouse=True)
def isolated_default_databases(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DEFAULT_DATABASE_PATH", tmp_path / "default-memory.db")
    monkeypatch.setattr(
        workflow_store,
        "DEFAULT_WORKFLOW_DATABASE_PATH",
        tmp_path / "default-workflows.db",
    )
