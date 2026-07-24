from pathlib import Path

import memory


def test_remember_and_recall(tmp_path: Path):
    database_path = tmp_path / "memory.db"

    memory.remember("favorite drink", "tea", database_path)

    assert memory.recall("favorite drink", database_path) == "tea"


def test_remember_updates_an_existing_key(tmp_path: Path):
    database_path = tmp_path / "memory.db"
    memory.remember("favorite drink", "tea", database_path)

    memory.remember("favorite drink", "coffee", database_path)

    assert memory.recall("favorite drink", database_path) == "coffee"


def test_recall_returns_none_for_a_missing_key(tmp_path: Path):
    assert memory.recall("missing", tmp_path / "memory.db") is None


def test_forget_removes_an_existing_key(tmp_path: Path):
    database_path = tmp_path / "memory.db"
    memory.remember("favorite drink", "tea", database_path)

    memory.forget("favorite drink", database_path)

    assert memory.recall("favorite drink", database_path) is None


def test_forget_ignores_a_missing_key(tmp_path: Path):
    database_path = tmp_path / "memory.db"

    memory.forget("missing", database_path)

    assert memory.recall("missing", database_path) is None


def test_memory_persists_across_separate_connections(tmp_path: Path):
    database_path = tmp_path / "memory.db"
    memory.remember("shipping preference", "standard", database_path)

    assert memory.recall("shipping preference", database_path) == "standard"
