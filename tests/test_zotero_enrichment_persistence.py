import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import database


@pytest.mark.parametrize("report_matches", [False, True])
def test_enrichment_save_uses_atomic_report_compare_and_swap(monkeypatch, report_matches):
    calls = []
    committed = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, query, params):
            calls.append((" ".join(query.split()), params))

        def fetchone(self):
            return {"item_key": "P1"} if report_matches else None

    class Connection:
        def cursor(self):
            return Cursor()

        def commit(self):
            committed.append(True)

    @contextmanager
    def get_connection():
        yield Connection()

    monkeypatch.setattr(database, "DATABASE_URL", "test-only")
    monkeypatch.setattr(database, "_get_connection", get_connection)
    note = {"note_markdown": "笔记。"}
    raw_report = "  ## 1. 论文解决的任务\n\n"
    assert database.update_zotero_analysis_enrichment(
        "u1", "P1", note, expected_report=raw_report,
    ) is report_matches
    sql, params = calls[0]
    assert "WHERE user_id = %s AND item_key = %s AND llm_response = %s" in sql
    assert "RETURNING item_key" in sql
    assert params[0].obj == note
    assert params[1:] == ("u1", "P1", raw_report)
    assert committed == [True]
