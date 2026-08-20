"""Typesense search index and query helpers.

PostgreSQL remains the source of truth. Typesense stores only searchable and
filterable paper metadata; search hits are hydrated from PostgreSQL by the
database module before they are returned to the frontend.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import psycopg
import requests
from config import settings
from psycopg.rows import dict_row


logger = logging.getLogger(__name__)


class TypesenseSearchError(RuntimeError):
    """Raised when Typesense cannot serve or update the paper index."""


def is_enabled() -> bool:
    config = settings.typesense
    return bool(config.enabled and config.api_key and config.host)


def should_use_search(
    search: str | None,
    search_title: bool,
    search_abstract: bool,
    search_keywords: bool,
    read_status: str = "all",
) -> bool:
    return bool(
        is_enabled()
        and search
        and str(search).strip()
        and (search_title or search_abstract or search_keywords)
        and read_status == "all"
    )


def _base_url() -> str:
    config = settings.typesense
    return f"{config.protocol}://{config.host}:{config.port}"


def _request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    data: str | None = None,
    content_type: str = "application/json",
    allow_not_found: bool = False,
) -> requests.Response | None:
    if not is_enabled():
        raise TypesenseSearchError("Typesense is not enabled or api_key is missing")

    headers = {
        "X-TYPESENSE-API-KEY": settings.typesense.api_key or "",
        "Content-Type": content_type,
    }
    try:
        response = requests.request(
            method,
            f"{_base_url()}{path}",
            params=params,
            json=payload,
            data=data,
            headers=headers,
            timeout=settings.typesense.timeout_seconds,
        )
    except requests.RequestException as exc:
        raise TypesenseSearchError(f"Typesense request failed: {exc}") from exc

    if allow_not_found and response.status_code == 404:
        return None
    if not response.ok:
        detail = response.text.strip()[:500]
        raise TypesenseSearchError(
            f"Typesense {method} {path} returned {response.status_code}: {detail}"
        )
    return response


def _collection_schema(collection_name: str) -> dict[str, Any]:
    fields: list[dict[str, Any]] = [
        {"name": "id", "type": "string"},
        {"name": "title", "type": "string"},
        {"name": "abstract", "type": "string"},
        {"name": "keywords", "type": "string[]", "optional": True},
        {"name": "authors", "type": "string[]", "optional": True},
        {"name": "venue", "type": "string", "optional": True},
        {"name": "venue_base", "type": "string", "facet": True, "optional": True},
        {"name": "primary_area", "type": "string", "facet": True, "optional": True},
        {"name": "code_status", "type": "string", "facet": True},
        {"name": "paper_type_priority", "type": "int32", "sort": True},
        {"name": "sort_order", "type": "int32", "sort": True},
        {"name": "created_at", "type": "int64", "sort": True, "optional": True},
    ]
    if settings.typesense.semantic_search_enabled:
        fields.append(
            {
                "name": "embedding",
                "type": "float[]",
                "embed": {
                    "from": ["title", "abstract", "keywords"],
                    "model_config": {
                        "model_name": settings.typesense.embedding_model,
                        "indexing_prefix": "passage: ",
                        "query_prefix": "query: ",
                    },
                },
            }
        )

    return {
        "name": collection_name,
        "fields": fields,
        "default_sorting_field": "sort_order",
    }


def _venue_base(venue: object) -> str:
    value = str(venue or "").strip()
    lowered = value.casefold()
    for suffix in (" oral", " spotlight", " poster"):
        if lowered.endswith(suffix):
            return value[: -len(suffix)].rstrip()
    return value


def _paper_type_priority(venue: object) -> int:
    value = str(venue or "").casefold()
    if "oral" in value:
        return 1
    if "spotlight" in value:
        return 2
    if "poster" in value:
        return 3
    return 4


def _timestamp(value: object) -> int | None:
    if isinstance(value, datetime):
        return int(value.timestamp())
    return None


def paper_to_document(paper: dict[str, Any]) -> dict[str, Any]:
    sort_order = paper.get("sort_order")
    try:
        normalized_sort_order = int(sort_order) if sort_order is not None else 2_147_483_647
    except (TypeError, ValueError):
        normalized_sort_order = 2_147_483_647

    document: dict[str, Any] = {
        "id": str(paper["id"]),
        "title": str(paper.get("title") or ""),
        "abstract": str(paper.get("abstract") or ""),
        "keywords": [
            str(value) for value in (paper.get("keywords") or []) if str(value).strip()
        ],
        "authors": [
            str(value) for value in (paper.get("authors") or []) if str(value).strip()
        ],
        "venue": str(paper.get("venue") or ""),
        "venue_base": _venue_base(paper.get("venue")),
        "primary_area": str(paper.get("primary_area") or ""),
        "code_status": str(paper.get("code_status") or "unknown"),
        "paper_type_priority": _paper_type_priority(paper.get("venue")),
        "sort_order": normalized_sort_order,
    }
    created_at = _timestamp(paper.get("created_at"))
    if created_at is not None:
        document["created_at"] = created_at
    return document


_INDEX_SELECT = """
    SELECT
        p.id,
        p.title,
        p.abstract,
        p.venue,
        p.primary_area,
        p.sort_order,
        p.created_at,
        p.code_status,
        COALESCE(
            (SELECT array_agg(k.keyword ORDER BY k.id) FROM keywords k WHERE k.paper_id = p.id),
            ARRAY[]::TEXT[]
        ) AS keywords,
        COALESCE(
            (
                SELECT array_agg(a.author_name ORDER BY a.author_order)
                FROM authors a
                WHERE a.paper_id = p.id
            ),
            ARRAY[]::TEXT[]
        ) AS authors
    FROM papers p
"""


def iter_postgres_documents(batch_size: int = 100) -> Iterator[list[dict[str, Any]]]:
    if not settings.database.url:
        raise TypesenseSearchError("database.url is required to build the Typesense index")

    with psycopg.connect(settings.database.url, row_factory=dict_row) as conn:
        with conn.cursor(name="typesense_paper_reindex") as cur:
            cur.execute(f"{_INDEX_SELECT} ORDER BY p.id")
            while rows := cur.fetchmany(batch_size):
                yield [paper_to_document(row) for row in rows]


def _paper_document_from_postgres(paper_id: str) -> dict[str, Any] | None:
    if not settings.database.url:
        return None
    with psycopg.connect(settings.database.url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(f"{_INDEX_SELECT} WHERE p.id = %s", (paper_id,))
            row = cur.fetchone()
    return paper_to_document(row) if row else None


def _paper_documents_from_postgres(paper_ids: list[str]) -> list[dict[str, Any]]:
    if not settings.database.url or not paper_ids:
        return []
    with psycopg.connect(settings.database.url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"{_INDEX_SELECT} WHERE p.id = ANY(%s) ORDER BY p.id",
                (paper_ids,),
            )
            rows = cur.fetchall()
    return [paper_to_document(row) for row in rows]


def _import_documents(collection_name: str, documents: list[dict[str, Any]]) -> int:
    if not documents:
        return 0
    body = "\n".join(json.dumps(document, ensure_ascii=False) for document in documents)
    response = _request(
        "POST",
        f"/collections/{collection_name}/documents/import",
        params={"action": "upsert"},
        data=body,
        content_type="text/plain",
    )
    assert response is not None

    failures: list[str] = []
    imported = 0
    for line in response.text.splitlines():
        if not line.strip():
            continue
        result = json.loads(line)
        if result.get("success"):
            imported += 1
        else:
            failures.append(str(result.get("error") or result))
    if failures:
        raise TypesenseSearchError(
            f"Typesense import failed for {len(failures)} documents: {failures[0][:300]}"
        )
    return imported


def collection_document_count() -> int | None:
    response = _request(
        "GET",
        f"/collections/{settings.typesense.collection_alias}",
        allow_not_found=True,
    )
    if response is None:
        return None
    return int(response.json().get("num_documents") or 0)


def rebuild_index(batch_size: int = 100, prune_old: bool = True) -> int:
    """Build a fresh physical collection and atomically switch the alias."""

    alias = settings.typesense.collection_alias
    physical_name = f"{alias}_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    previous_alias = _request("GET", f"/aliases/{alias}", allow_not_found=True)
    previous_collection = (
        str(previous_alias.json().get("collection_name")) if previous_alias is not None else None
    )

    _request("POST", "/collections", payload=_collection_schema(physical_name))
    total = 0
    try:
        for documents in iter_postgres_documents(batch_size=batch_size):
            total += _import_documents(physical_name, documents)
            logger.info("Typesense indexed %s papers", total)
        _request(
            "PUT",
            f"/aliases/{alias}",
            payload={"collection_name": physical_name},
        )
    except Exception:
        _request("DELETE", f"/collections/{physical_name}", allow_not_found=True)
        raise

    if prune_old and previous_collection and previous_collection != physical_name:
        _request("DELETE", f"/collections/{previous_collection}", allow_not_found=True)
    return total


def upsert_paper(paper_id: str) -> bool:
    if not is_enabled():
        return False
    document = _paper_document_from_postgres(paper_id)
    if document is None:
        return False
    _import_documents(settings.typesense.collection_alias, [document])
    return True


def upsert_papers(paper_ids: list[str]) -> int:
    if not is_enabled() or not paper_ids:
        return 0
    documents = _paper_documents_from_postgres(paper_ids)
    return _import_documents(settings.typesense.collection_alias, documents)


def _filter_value(value: str) -> str:
    escaped = value.replace("`", "\\`")
    return f"`{escaped}`"


def _build_filter(venue_prefix: str | None, code_filter: str) -> str | None:
    filters: list[str] = []
    if venue_prefix:
        filters.append(f"venue_base:={_filter_value(venue_prefix)}")
    if code_filter == "open_source":
        filters.append("code_status:=open_source")
    elif code_filter == "not_open_source":
        filters.append("code_status:!=open_source")
    elif code_filter != "all":
        filters.append(f"code_status:={_filter_value(code_filter)}")
    return " && ".join(filters) or None


def search_paper_ids(
    search: str,
    venue_prefix: str | None,
    page: int,
    per_page: int,
    search_title: bool,
    search_abstract: bool,
    search_keywords: bool,
    code_filter: str = "all",
) -> tuple[list[str], int]:
    if not should_use_search(
        search,
        search_title,
        search_abstract,
        search_keywords,
    ):
        raise TypesenseSearchError("Typesense search is not available for this query")

    query_fields: list[str] = []
    if search_title:
        query_fields.append("title")
    if search_keywords:
        query_fields.append("keywords")
    if search_abstract:
        query_fields.append("abstract")

    semantic_search = bool(
        settings.typesense.semantic_search_enabled
        and search_title
        and search_abstract
        and search_keywords
    )
    if semantic_search:
        query_fields.append("embedding")

    params: dict[str, Any] = {
        "q": search.strip(),
        "query_by": ",".join(query_fields),
        "page": max(page, 1),
        "per_page": max(per_page, 1),
        "sort_by": "_text_match:desc,paper_type_priority:asc,sort_order:asc",
        "prioritize_exact_match": "true",
    }
    if settings.typesense.semantic_search_enabled:
        params["exclude_fields"] = "embedding"
    filter_by = _build_filter(venue_prefix, code_filter)
    if filter_by:
        params["filter_by"] = filter_by
    if semantic_search:
        config = settings.typesense
        params["rerank_hybrid_matches"] = "true"
        params["vector_query"] = (
            "embedding:([], "
            f"alpha:{config.vector_alpha}, "
            f"k:{max(config.vector_k, page * per_page)}, "
            f"distance_threshold:{config.vector_distance_threshold})"
        )

    response = _request(
        "GET",
        f"/collections/{settings.typesense.collection_alias}/documents/search",
        params=params,
    )
    assert response is not None
    payload = response.json()
    ids = [
        str(hit.get("document", {}).get("id"))
        for hit in payload.get("hits", [])
        if hit.get("document", {}).get("id")
    ]
    return ids, int(payload.get("found") or 0)
