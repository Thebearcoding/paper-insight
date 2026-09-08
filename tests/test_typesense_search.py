import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import database
import typesense_search
import pytest


class FakeResponse:
    def __init__(self, payload, text=""):
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


@pytest.mark.parametrize(
    'failure',
    ['short_import', 'count_mismatch', 'empty_source', 'switch_timeout', 'prune_error'],
)
def test_rebuild_preserves_serving_index_on_failure(monkeypatch, failure):
    monkeypatch.setattr(typesense_search, 'settings', _settings())
    state = {'alias': 'old', 'new': None, 'deleted': []}

    def request(method, path, **kwargs):
        if method == 'GET' and path == '/aliases/papers':
            return FakeResponse({'collection_name': state['alias']})
        if method == 'POST' and path == '/collections':
            state['new'] = kwargs['payload']['name']
            return FakeResponse({})
        if method == 'POST' and path.endswith('/documents/import'):
            return FakeResponse({}, '' if failure == 'short_import' else '{"success":true}')
        if method == 'GET' and path.startswith('/collections/'):
            if path == '/collections/papers':
                return FakeResponse({'num_documents': 1})
            return FakeResponse(
                {'num_documents': 0 if failure in {'count_mismatch', 'empty_source'} else 1}
            )
        if method == 'PUT':
            state['alias'] = kwargs['payload']['collection_name']
            if failure == 'switch_timeout':
                raise typesense_search.TypesenseSearchError('response lost after committed write')
            return FakeResponse({})
        if method == 'DELETE':
            name = path.split('/')[-1]
            if failure == 'prune_error' and name == 'old':
                raise typesense_search.TypesenseSearchError('cleanup failed')
            state['deleted'].append(name)
            return FakeResponse({})
        raise AssertionError((method, path))

    monkeypatch.setattr(typesense_search, '_request', request)
    documents = [] if failure == 'empty_source' else [[{'id': 'p1'}]]
    monkeypatch.setattr(typesense_search, 'iter_postgres_documents', lambda **kwargs: iter(documents))
    if failure in {'short_import', 'count_mismatch', 'empty_source'}:
        with pytest.raises(typesense_search.TypesenseSearchError):
            typesense_search.rebuild_index()
        assert state['alias'] == 'old'
    else:
        assert typesense_search.rebuild_index() == 1
        assert state['alias'] == state['new']
    assert state['alias'] not in state['deleted']


def _settings(**overrides):
    values = {
        "enabled": True,
        "protocol": "http",
        "host": "typesense",
        "port": 8108,
        "api_key": "test-key",
        "collection_alias": "papers",
        "embedding_model": "ts/multilingual-e5-small",
        "semantic_search_enabled": True,
        "vector_alpha": 0.4,
        "vector_k": 500,
        "vector_distance_threshold": 0.45,
        "timeout_seconds": 5,
    }
    values.update(overrides)
    return SimpleNamespace(
        typesense=SimpleNamespace(**values),
        database=SimpleNamespace(url="postgresql://test/paper_online"),
    )


def test_paper_to_document_builds_search_and_sort_fields():
    document = typesense_search.paper_to_document(
        {
            "id": "paper-1",
            "title": "A Defect Detection Paper",
            "abstract": "Industrial inspection",
            "keywords": ["defect detection"],
            "authors": ["Alice"],
            "venue": "CVPR 2026 Oral",
            "primary_area": "Vision",
            "sort_order": 7,
            "created_at": datetime(2026, 8, 20, tzinfo=UTC),
            "code_status": "open_source",
        }
    )

    assert document["venue_base"] == "CVPR 2026"
    assert document["paper_type_priority"] == 1
    assert document["sort_order"] == 7
    assert document["keywords"] == ["defect detection"]
    assert document["created_at"] == 1787184000


def test_venue_base_groups_conference_tracks_under_one_collection():
    assert typesense_search._venue_base("ACL 2025 Long") == "ACL 2025"
    assert typesense_search._venue_base("ACL 2025 Short") == "ACL 2025"
    assert typesense_search._venue_base("NeurIPS 2025 Datasets and Benchmarks") == "NeurIPS 2025"
    assert typesense_search._venue_base("ICLR 2025 Oral") == "ICLR 2025"


def test_collection_schema_keeps_full_text_search_but_limits_embedding_cost(monkeypatch):
    monkeypatch.setattr(typesense_search, "settings", _settings())

    schema = typesense_search._collection_schema("papers_test")
    embedding = next(field for field in schema["fields"] if field["name"] == "embedding")

    assert embedding["embed"]["from"] == ["title", "keywords"]
    assert {field["name"] for field in schema["fields"]}.issuperset(
        {"title", "abstract", "keywords"}
    )


def test_collection_status_rejects_stale_schema_without_embedding(monkeypatch):
    monkeypatch.setattr(typesense_search, "settings", _settings())
    monkeypatch.setattr(
        typesense_search,
        "_request",
        lambda *args, **kwargs: FakeResponse({"num_documents": 42, "fields": []}),
    )

    assert typesense_search.collection_status() == (42, False)


def test_collection_status_accepts_configured_embedding_model(monkeypatch):
    monkeypatch.setattr(typesense_search, "settings", _settings())
    monkeypatch.setattr(
        typesense_search,
        "_request",
        lambda *args, **kwargs: FakeResponse(
            {
                "num_documents": 42,
                "fields": [
                    {
                        "name": "embedding",
                        "type": "float[]",
                        "embed": {
                            "model_config": {"model_name": "ts/multilingual-e5-small"}
                        },
                    }
                ],
            }
        ),
    )

    assert typesense_search.collection_status() == (42, True)


def test_search_paper_ids_builds_multilingual_hybrid_query(monkeypatch):
    monkeypatch.setattr(typesense_search, "settings", _settings())
    captured = {}

    def fake_request(method, path, **kwargs):
        captured.update({"method": method, "path": path, **kwargs})
        return FakeResponse(
            {
                "found": 2,
                "hits": [
                    {"document": {"id": "paper-2"}},
                    {"document": {"id": "paper-1"}},
                ],
            }
        )

    monkeypatch.setattr(typesense_search, "_request", fake_request)

    paper_ids, total = typesense_search.search_paper_ids(
        "缺陷检测",
        "CVPR 2026",
        page=1,
        per_page=8,
        search_title=True,
        search_abstract=True,
        search_keywords=True,
        code_filter="open_source",
    )

    assert paper_ids == ["paper-2", "paper-1"]
    assert total == 2
    assert captured["path"] == "/collections/papers/documents/search"
    assert captured["params"]["q"] == "defect detection"
    assert captured["params"]["query_by"] == "title,keywords,abstract,embedding"
    assert captured["params"]["rerank_hybrid_matches"] == "false"
    assert "alpha:0.4" in captured["params"]["vector_query"]
    assert "distance_threshold:0.45" in captured["params"]["vector_query"]
    assert captured["params"]["filter_by"] == (
        "venue_base:=`CVPR 2026` && code_status:=open_source"
    )


def test_search_paper_ids_respects_field_filters(monkeypatch):
    monkeypatch.setattr(typesense_search, "settings", _settings())
    captured = {}

    def fake_request(method, path, **kwargs):
        captured.update(kwargs)
        return FakeResponse({"found": 0, "hits": []})

    monkeypatch.setattr(typesense_search, "_request", fake_request)

    typesense_search.search_paper_ids(
        "transformer",
        None,
        page=1,
        per_page=8,
        search_title=True,
        search_abstract=False,
        search_keywords=False,
    )

    assert captured["params"]["query_by"] == "title"
    assert "vector_query" not in captured["params"]


def test_multilingual_query_expansion_prefers_specific_domain_terms():
    assert typesense_search._expand_multilingual_query("工业图像缺陷检测") == (
        "industrial image defect detection"
    )
    assert typesense_search._expand_multilingual_query("工业异常检测 Transformer") == (
        "industrial anomaly detection Transformer"
    )


@pytest.mark.parametrize('mode', ['fallback', 'unread', 'read_counts'])
def test_chinese_query_is_preserved_across_search_backends(monkeypatch, mode):
    monkeypatch.setattr(database, 'DATABASE_URL', 'postgresql://test/paper_online')
    database._conference_cache.clear()
    database._cache_timestamp.clear()
    monkeypatch.setattr(typesense_search, 'should_use_search', lambda *a, **kw: False)
    queries = []

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql, params): queries.append(params[0])
        def fetchall(self): return []
        def fetchone(self): return {'total': 0, 'read_total': 0}

    @contextmanager
    def connection():
        yield SimpleNamespace(cursor=lambda: Cursor())

    monkeypatch.setattr(database, '_get_connection', connection)
    monkeypatch.setattr(database, '_load_keywords_for_papers', lambda rows: (rows, {}))
    if mode == 'fallback':
        database._search_papers(None, 0, 8, '缺陷检测', True, True, True)
    elif mode == 'unread':
        database._search_papers_with_read_filter(None, 0, 8, '缺陷检测', True, True, True, 'user', 'unread')
    else:
        database.count_search_paper_read_states(None, '缺陷检测', True, True, True, 'user')
    assert queries
    assert all(query == 'defect detection' for query in queries)


def test_database_search_prefers_typesense_and_preserves_order(monkeypatch):
    monkeypatch.setattr(database, "DATABASE_URL", "postgresql://test/paper_online")
    monkeypatch.setattr(database.typesense_search, "should_use_search", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        database.typesense_search,
        "search_paper_ids",
        lambda *args, **kwargs: (["paper-2", "paper-1"], 2),
    )
    monkeypatch.setattr(
        database,
        "_load_papers_by_ids",
        lambda paper_ids: [{"id": paper_id} for paper_id in paper_ids],
    )

    papers, total = database._search_papers(
        None,
        0,
        8,
        "缺陷检测",
        True,
        True,
        True,
    )

    assert [paper["id"] for paper in papers] == ["paper-2", "paper-1"]
    assert total == 2


def test_database_search_falls_back_when_typesense_fails(monkeypatch):
    database._conference_cache.clear()
    database._cache_timestamp.clear()
    monkeypatch.setattr(database, "DATABASE_URL", "postgresql://test/paper_online")
    monkeypatch.setattr(database.typesense_search, "should_use_search", lambda *args, **kwargs: True)

    def fail_search(*args, **kwargs):
        raise typesense_search.TypesenseSearchError("offline")

    monkeypatch.setattr(database.typesense_search, "search_paper_ids", fail_search)
    monkeypatch.setattr(
        database,
        "_search_papers_via_rpc",
        lambda *args, **kwargs: ([{"id": "postgres-paper"}], 1),
    )

    papers, total = database._search_papers(
        None,
        0,
        8,
        "transformer",
        True,
        True,
        True,
    )

    assert papers == [{"id": "postgres-paper"}]
    assert total == 1


def test_api_search_uses_the_same_typesense_ranking(monkeypatch):
    monkeypatch.setattr(database, "DATABASE_URL", "postgresql://test/paper_online")
    monkeypatch.setattr(database.typesense_search, "should_use_search", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        database.typesense_search,
        "search_paper_ids",
        lambda *args, **kwargs: (["paper-2", "paper-1"], 2),
    )
    monkeypatch.setattr(
        database,
        "_load_api_papers_by_ids",
        lambda paper_ids: [
            {
                "id": paper_id,
                "title": paper_id,
                "abstract": "abstract",
                "venue": "CVPR 2026",
                "code_status": "unknown",
                "authors": ["Alice"],
                "keywords": ["vision"],
            }
            for paper_id in paper_ids
        ],
    )

    papers, total = database.api_search_papers("缺陷检测", None, "all", 10, 0)

    assert [paper["id"] for paper in papers] == ["paper-2", "paper-1"]
    assert total == 2
