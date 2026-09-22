"""Resource bounds and query shape regressions for small-host search."""
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import database


@pytest.fixture(autouse=True)
def clear_cache():
    database._clear_search_cache()
    yield
    database._clear_search_cache()


def test_cache_evicts_least_recently_used_entries(monkeypatch):
    monkeypatch.setattr(database, "_CACHE_MAX_ENTRIES", 2)
    for key in ("a", "b"):
        database._set_cached_result(key, [{"id": key}], 1)
    assert database._get_cached_result("a") == ([{"id": "a"}], 1)
    database._set_cached_result("c", [], 0)
    assert database._get_cached_result("b") is None
    assert list(database._conference_cache) == ["a", "c"]


def test_cache_prunes_unrequested_expired_entries(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(database.time, "monotonic", lambda: now[0])
    database._set_cached_result("old", [{"id": "old"}], 1)
    now[0] += database._CACHE_TTL_SECONDS
    assert database._get_cached_result("unrelated") is None
    assert not database._conference_cache
    assert database._cache_bytes == 0


def test_cache_enforces_byte_budget_and_skips_oversized_reports(monkeypatch):
    papers = [{"id": "p", "llm_response": "x" * 2000}]
    size = database._cache_value_size(("a", papers, 1))
    monkeypatch.setattr(database, "_CACHE_MAX_BYTES", size + 1)
    database._set_cached_result("a", papers, 1)
    database._set_cached_result("b", papers, 1)
    assert database._get_cached_result("a") is None
    assert database._get_cached_result("b") == (papers, 1)
    assert database._cache_bytes <= size + 1
    # Replacing an existing key also removes its stale result if too large.
    database._set_cached_result("b", [{"llm_response": "x" * (size * 2)}], 1)
    assert database._get_cached_result("b") is None
    assert database._cache_bytes == 0


def test_cache_skips_entry_limit_even_with_room_in_total_budget(monkeypatch):
    monkeypatch.setattr(database, "_CACHE_MAX_ENTRY_BYTES", 1024)
    database._set_cached_result("large", [{"llm_response": "x" * 2048}], 1)
    assert database._get_cached_result("large") is None
    assert database._cache_bytes == 0


@pytest.mark.parametrize("operation", ["save_paper", "update_llm_response", "update_paper_analysis"])
@pytest.mark.parametrize("succeeds", [True, False])
def test_paper_changes_invalidate_cache_only_after_success(monkeypatch, operation, succeeds):
    monkeypatch.setattr(database, "DATABASE_URL", "postgresql://test/test")
    monkeypatch.setattr(database, "_sync_typesense_papers", lambda ids: None)

    def run_write(*args):
        if not succeeds:
            raise database.DatabaseError("write failed")

    monkeypatch.setattr(database, "_run_with_retry", run_write)
    database._set_cached_result("key", [{"id": "p"}], 1)
    args = ({"id": "p"}, "report") if operation == "save_paper" else ("p", "report")
    if succeeds:
        getattr(database, operation)(*args)
        assert database._get_cached_result("key") is None
    else:
        with pytest.raises(database.DatabaseError):
            getattr(database, operation)(*args)
        assert database._get_cached_result("key") == ([{"id": "p"}], 1)


def test_cache_defensively_copies_nested_rows():
    papers = [{"id": "p", "keywords": ["original"]}]
    database._set_cached_result("key", papers, 1)
    papers[0]["keywords"].append("writer mutation")
    cached, total = database._get_cached_result("key")
    cached[0]["keywords"].append("reader mutation")
    assert database._get_cached_result("key") == ([{"id": "p", "keywords": ["original"]}], total)


def test_cache_is_safe_for_concurrent_clear_read_and_write(monkeypatch):
    monkeypatch.setattr(database, "_CACHE_MAX_ENTRIES", 8)

    def exercise(index):
        key = str(index % 12)
        database._set_cached_result(key, [{"id": key}], index)
        database._get_cached_result(key)
        if index % 7 == 0:
            database._clear_search_cache()

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(exercise, range(300)))
    assert len(database._conference_cache) <= 8
    assert database._cache_bytes == sum(entry[1] for entry in database._conference_cache.values())


@pytest.mark.parametrize("flags,expected", [
    ((True, True, True), ["p.title", "p.abstract", "k.keyword"]),
    ((True, False, False), ["p.title"]),
    ((False, False, True), ["k.keyword"]),
    ((False, False, False), []),
])
def test_read_count_scope_uses_only_enabled_fts_id_branches(flags, expected):
    sql, params = database._search_paper_ids_sql("CVPR 2026", " attention ", *flags, "open_source")
    assert "llm_response" not in sql
    assert "ORDER BY" not in sql
    assert "LIMIT" not in sql
    assert "search_papers_optimized" not in sql
    assert "UNION ALL" not in sql  # A paper matching multiple fields counts once.
    assert sql.count("to_tsvector") == len(expected)
    for field in expected:
        assert f"COALESCE({field}, '')" in sql
    assert params == ["CVPR 2026%", "attention"] * len(expected)
    if "k.keyword" in expected:
        assert "SELECT DISTINCT p.id FROM keywords k" in sql
    if expected:
        assert sql.count("p.code_status = 'open_source'") == len(expected)


@pytest.mark.parametrize("search", [None, "", "  "])
def test_browse_count_scope_has_no_sort_or_search_function(search):
    sql, params = database._search_paper_ids_sql(None, search, True, True, True, "all")
    assert sql == "SELECT p.id FROM papers p WHERE TRUE"
    assert params == []


@pytest.mark.parametrize("status", ["read", "unread"])
@pytest.mark.parametrize("search", [None, "  "])
def test_browse_read_filter_is_applied_before_pagination(monkeypatch, status, search):
    queries = []

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql, params): queries.append((sql, params))
        def fetchall(self): return [{"id": "p"}]
        def fetchone(self): return {"total": 25}

    @contextmanager
    def connection():
        yield SimpleNamespace(cursor=lambda: Cursor())

    monkeypatch.setattr(database, "_get_connection", connection)
    monkeypatch.setattr(database, "_fetch_keywords_for_papers", lambda conn, ids: {"p": ["vision"]})
    papers, total = database._search_papers_with_read_filter(
        "CVPR 2026", 16, 8, search, True, True, True, "user", status, "open_source"
    )
    assert papers == [{"id": "p", "keywords": ["vision"]}]
    assert total == 25
    page_sql, page_params = queries[0]
    count_sql, count_params = queries[1]
    assert "search_papers_optimized" not in page_sql + count_sql
    assert "paper_marks" in page_sql
    assert ("NOT EXISTS" in page_sql) == (status == "unread")
    assert "COALESCE(p.sort_order, 2147483647)" in page_sql
    assert "LIMIT %s OFFSET %s" in page_sql
    assert page_params == ["CVPR 2026%", "user", 8, 16]
    assert count_params == ["CVPR 2026%", "user"]
    assert "ORDER BY" not in count_sql
    assert "llm_response" not in count_sql
