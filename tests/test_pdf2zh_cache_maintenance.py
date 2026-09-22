"""Exercise cleanup against real SQLite connections, including live WAL readers."""

import importlib.util
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "docker/pdf2zh/cache_maintenance.py"
spec = importlib.util.spec_from_file_location("pdf2zh_cache_maintenance", SCRIPT)
maintenance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(maintenance)


@pytest.fixture
def cache(tmp_path):
    path = tmp_path / "cache.v1.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA journal_mode=wal")
        connection.execute("CREATE TABLE _translationcache (id INTEGER PRIMARY KEY, translation TEXT)")
        connection.execute("CREATE TABLE unrelated (value TEXT)")
        connection.execute("INSERT INTO unrelated VALUES ('keep')")
        connection.execute("INSERT INTO _translationcache VALUES (1, 'old translation')")
        connection.commit()
        yield path, connection


def test_cleanup_preserves_schema_file_and_live_connection(cache):
    path, live = cache
    inode = path.stat().st_ino
    for _ in range(3):
        assert maintenance.clear_translation_memory(path)
        assert path.stat().st_ino == inode
        assert live.execute("SELECT * FROM _translationcache").fetchall() == []
        assert live.execute("SELECT * FROM unrelated").fetchall() == [("keep",)]
        live.execute("INSERT INTO _translationcache (translation) VALUES ('new translation')")
        live.commit()
        with closing(sqlite3.connect(path)) as fresh:
            assert fresh.execute("SELECT translation FROM _translationcache").fetchall() == [("new translation",)]


def test_cleanup_preserves_active_reader_snapshot(cache):
    path, live = cache
    live.execute("BEGIN")
    assert live.execute("SELECT COUNT(*) FROM _translationcache").fetchone() == (1,)
    assert maintenance.clear_translation_memory(path)
    assert live.execute("SELECT COUNT(*) FROM _translationcache").fetchone() == (1,)
    live.rollback()
    assert live.execute("SELECT COUNT(*) FROM _translationcache").fetchone() == (0,)


def test_busy_writer_is_skipped_then_next_cleanup_succeeds(cache):
    path, live = cache
    live.execute("BEGIN IMMEDIATE")
    assert not maintenance.clear_translation_memory(path, timeout=0)
    assert live.execute("SELECT COUNT(*) FROM _translationcache").fetchone() == (1,)
    live.rollback()
    assert maintenance.clear_translation_memory(path)


def test_missing_database_is_not_created(tmp_path):
    path = tmp_path / "cache.v1.db"
    assert not maintenance.clear_translation_memory(path)
    assert not path.exists()


def test_uninitialized_database_is_not_given_a_handwritten_schema(tmp_path):
    path = tmp_path / "cache.v1.db"
    with closing(sqlite3.connect(path)) as connection:
        assert not maintenance.clear_translation_memory(path)
        assert connection.execute("SELECT name FROM sqlite_master").fetchall() == []


def test_main_cleans_both_upstream_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(maintenance.Path, "home", lambda: tmp_path)
    for package in ("pdf2zh", "babeldoc"):
        path = tmp_path / ".cache" / package / "cache.v1.db"
        path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("CREATE TABLE _translationcache (translation TEXT)")
            connection.execute("INSERT INTO _translationcache VALUES ('old')")
            connection.commit()
    maintenance.main()
    for path in tmp_path.glob(".cache/*/cache.v1.db"):
        with closing(sqlite3.connect(path)) as connection:
            assert connection.execute("SELECT * FROM _translationcache").fetchall() == []
