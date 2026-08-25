from __future__ import annotations

import html
import json
import logging
import re
import shutil
import secrets
import time
from pathlib import Path
from typing import Any

import requests

from config import REPO_ROOT, settings
from paper_resources import (
    build_repository_context,
    discover_code_repositories,
    extract_pdf_text_bounded,
    resolve_public_document,
)
from utils import ReaderError, truncate_content_for_llm


logger = logging.getLogger(__name__)
ZOTERO_API_VERSION = "3"
PAGE_SIZE = 100
MAX_RETRIES = 4
DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "zotero_cache"
ZOTERO_FULLTEXT_TOKEN_LIMIT = 160_000


class ZoteroError(Exception):
    """Base exception for Zotero API and content errors."""


class ZoteroAuthError(ZoteroError):
    """The Zotero key is invalid or lacks private-library access."""


class ZoteroNotFoundError(ZoteroError):
    """The requested Zotero resource does not exist."""


class ZoteroContentError(ZoteroError):
    """No readable full text could be obtained for an item."""


class ZoteroWriteConflictError(ZoteroError):
    """The Zotero object changed after it was read."""


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _plain_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def normalize_collection(record: dict[str, Any]) -> dict[str, Any]:
    data = record.get("data") if isinstance(record.get("data"), dict) else {}
    collection_key = str(record.get("key") or data.get("key") or "").strip()
    if not collection_key:
        raise ZoteroError("Zotero collection is missing its key")
    return {
        "collection_key": collection_key,
        "collection_version": _as_int(record.get("version") or data.get("version")),
        "name": str(data.get("name") or "Untitled collection"),
        "parent_collection": data.get("parentCollection") or None,
        "raw": record,
    }


def normalize_item(record: dict[str, Any]) -> dict[str, Any]:
    data = record.get("data") if isinstance(record.get("data"), dict) else {}
    item_key = str(record.get("key") or data.get("key") or "").strip()
    if not item_key:
        raise ZoteroError("Zotero item is missing its key")

    creators = data.get("creators") if isinstance(data.get("creators"), list) else []
    tags = data.get("tags") if isinstance(data.get("tags"), list) else []
    tag_names = [str(tag.get("tag")) for tag in tags if isinstance(tag, dict) and tag.get("tag")]
    collections = data.get("collections") if isinstance(data.get("collections"), list) else []

    return {
        "item_key": item_key,
        "item_version": _as_int(record.get("version") or data.get("version")),
        "item_type": str(data.get("itemType") or "unknown"),
        "parent_item_key": data.get("parentItem") or None,
        "title": data.get("title") or data.get("name") or None,
        "abstract_note": _plain_text(data.get("abstractNote")) or None,
        "publication_title": data.get("publicationTitle") or data.get("proceedingsTitle") or None,
        "item_date": data.get("date") or None,
        "doi": data.get("DOI") or None,
        "url": data.get("url") or None,
        "creators": creators,
        "tags": tag_names,
        "collections": [str(value) for value in collections if value],
        "content_type": data.get("contentType") or None,
        "link_mode": data.get("linkMode") or None,
        "filename": data.get("filename") or None,
        "note": _plain_text(data.get("note")) or None,
        "annotation_text": _plain_text(data.get("annotationText")) or None,
        "annotation_comment": _plain_text(data.get("annotationComment")) or None,
        "raw": record,
    }


def public_key_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    access = payload.get("access") if isinstance(payload.get("access"), dict) else {}
    user_access = access.get("user") if isinstance(access.get("user"), dict) else {}
    can_read = bool(user_access.get("library"))
    can_write = bool(user_access.get("write"))
    zotero_user_id = _as_int(payload.get("userID"))
    if not zotero_user_id or not can_read:
        raise ZoteroAuthError("该 Zotero API Key 没有个人文库读取权限")
    return {
        "zotero_user_id": zotero_user_id,
        "username": payload.get("username") or None,
        "display_name": payload.get("displayName") or payload.get("username") or None,
        "can_read": can_read,
        "can_write": can_write,
    }


class ZoteroClient:
    def __init__(self, api_key: str):
        self.api_key = api_key.strip()
        if not self.api_key:
            raise ZoteroAuthError("Zotero API Key 不能为空")
        self.base_url = settings.zotero.api_base_url.rstrip("/")
        self.timeout = max(settings.zotero.request_timeout_seconds, 5)
        self.max_attachment_bytes = max(settings.zotero.max_attachment_mb, 1) * 1024 * 1024
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Zotero-API-Key": self.api_key,
                "Zotero-API-Version": ZOTERO_API_VERSION,
                "User-Agent": "Paper-Insight/1.0 Zotero integration",
            }
        )
        self._backoff_until = 0.0

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        stream: bool = False,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            wait_seconds = self._backoff_until - time.monotonic()
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            try:
                response = self.session.request(
                    method,
                    f"{self.base_url}{path}",
                    params=params,
                    json=json_body,
                    headers=headers,
                    timeout=self.timeout,
                    stream=stream,
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt == MAX_RETRIES - 1:
                    break
                time.sleep(min(2**attempt, 8))
                continue

            backoff = _as_int(response.headers.get("Backoff"), 0)
            if backoff > 0:
                self._backoff_until = time.monotonic() + backoff

            if response.status_code in {409, 429, 502, 503, 504}:
                retry_after = max(_as_int(response.headers.get("Retry-After"), 0), backoff, 2**attempt)
                response.close()
                if attempt == MAX_RETRIES - 1:
                    last_error = ZoteroError(f"Zotero API 暂时不可用（HTTP {response.status_code}）")
                    break
                time.sleep(min(retry_after, 30))
                continue
            if response.status_code in {401, 403}:
                response.close()
                raise ZoteroAuthError("Zotero API Key 无效或缺少当前操作所需的文库权限")
            if response.status_code == 412:
                response.close()
                raise ZoteroWriteConflictError("Zotero 条目已在其他位置更新，请刷新后重试")
            if response.status_code == 404:
                response.close()
                raise ZoteroNotFoundError("Zotero 资源不存在")
            try:
                response.raise_for_status()
            except requests.RequestException as exc:
                response.close()
                raise ZoteroError(f"Zotero API 请求失败（HTTP {response.status_code}）") from exc
            return response

        raise ZoteroError("连接 Zotero API 失败，请稍后重试") from last_error

    def verify_key(self) -> dict[str, Any]:
        response = self._request("GET", "/keys/current")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ZoteroError("Zotero 返回了无效的验证结果") from exc
        finally:
            response.close()
        return public_key_metadata(payload)

    def _library_path(self, zotero_user_id: int, suffix: str) -> str:
        return f"/users/{zotero_user_id}{suffix}"

    def _fetch_all(
        self,
        path: str,
        *,
        since: int = 0,
        extra_params: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        start = 0
        records: list[dict[str, Any]] = []
        latest_version = since
        while True:
            params: dict[str, Any] = {"format": "json", "limit": PAGE_SIZE, "start": start}
            if since > 0:
                params["since"] = since
            if extra_params:
                params.update(extra_params)
            response = self._request("GET", path, params=params)
            try:
                page = response.json()
                if not isinstance(page, list):
                    raise ZoteroError("Zotero 返回了无效的列表数据")
                latest_version = max(
                    latest_version,
                    _as_int(response.headers.get("Last-Modified-Version"), latest_version),
                )
                total = _as_int(response.headers.get("Total-Results"), len(records) + len(page))
            finally:
                response.close()
            records.extend(record for record in page if isinstance(record, dict))
            start += len(page)
            if not page or start >= total or len(page) < PAGE_SIZE:
                break
        return records, latest_version

    def fetch_sync_data(self, zotero_user_id: int, since: int = 0) -> dict[str, Any]:
        collections_raw, collection_version = self._fetch_all(
            self._library_path(zotero_user_id, "/collections"),
            since=since,
        )
        items_raw, item_version = self._fetch_all(
            self._library_path(zotero_user_id, "/items"),
            since=since,
            extra_params={"include": "data"},
        )
        deleted_response = self._request(
            "GET",
            self._library_path(zotero_user_id, "/deleted"),
            params={"since": since},
        )
        try:
            deleted = deleted_response.json()
            if not isinstance(deleted, dict):
                deleted = {}
            deleted_version = _as_int(
                deleted_response.headers.get("Last-Modified-Version"),
                since,
            )
        finally:
            deleted_response.close()

        return {
            "zotero_user_id": zotero_user_id,
            "collections": [normalize_collection(record) for record in collections_raw],
            "items": [normalize_item(record) for record in items_raw],
            "deleted_collection_keys": [str(key) for key in deleted.get("collections", [])],
            "deleted_item_keys": [str(key) for key in deleted.get("items", [])],
            "library_version": max(since, collection_version, item_version, deleted_version),
        }

    def fetch_fulltext(self, zotero_user_id: int, attachment_key: str) -> str | None:
        try:
            response = self._request(
                "GET",
                self._library_path(zotero_user_id, f"/items/{attachment_key}/fulltext"),
            )
        except ZoteroNotFoundError:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        finally:
            response.close()
        content = payload.get("content") if isinstance(payload, dict) else None
        return str(content).strip() if content else None

    def download_attachment(self, zotero_user_id: int, attachment_key: str) -> bytes:
        response = self._request(
            "GET",
            self._library_path(zotero_user_id, f"/items/{attachment_key}/file"),
            stream=True,
        )
        try:
            content_length = _as_int(response.headers.get("Content-Length"), 0)
            if content_length > self.max_attachment_bytes:
                raise ZoteroContentError(
                    f"附件超过 {settings.zotero.max_attachment_mb} MB 的读取上限"
                )
            chunks: list[bytes] = []
            received = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                received += len(chunk)
                if received > self.max_attachment_bytes:
                    raise ZoteroContentError(
                        f"附件超过 {settings.zotero.max_attachment_mb} MB 的读取上限"
                    )
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            response.close()

    def fetch_item(self, zotero_user_id: int, item_key: str) -> dict[str, Any]:
        response = self._request(
            "GET",
            self._library_path(zotero_user_id, f"/items/{item_key}"),
            params={"format": "json"},
        )
        try:
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
                raise ZoteroError("Zotero 返回了无效的条目数据")
            return payload
        finally:
            response.close()

    def patch_item(
        self,
        zotero_user_id: int,
        item_key: str,
        version: int,
        changes: dict[str, Any],
    ) -> int:
        response = self._request(
            "PATCH",
            self._library_path(zotero_user_id, f"/items/{item_key}"),
            json_body=changes,
            headers={"If-Unmodified-Since-Version": str(version)},
        )
        try:
            return _as_int(response.headers.get("Last-Modified-Version"), version)
        finally:
            response.close()

    def create_note(
        self,
        zotero_user_id: int,
        parent_item_key: str,
        note_html: str,
    ) -> dict[str, Any]:
        response = self._request(
            "POST",
            self._library_path(zotero_user_id, "/items"),
            json_body=[
                {
                    "itemType": "note",
                    "parentItem": parent_item_key,
                    "note": note_html,
                    "tags": [{"tag": "来源/Paper Insight"}],
                }
            ],
            headers={"Zotero-Write-Token": secrets.token_hex(16)},
        )
        try:
            payload = response.json()
            successful = None
            if isinstance(payload, dict):
                successful = payload.get("successful") or payload.get("success")
            saved = successful.get("0") if isinstance(successful, dict) else None
            if isinstance(saved, str):
                return {"key": saved, "version": _as_int(response.headers.get("Last-Modified-Version"))}
            if isinstance(saved, dict):
                return {
                    "key": str(saved.get("key") or ""),
                    "version": _as_int(saved.get("version") or response.headers.get("Last-Modified-Version")),
                }
            failed = payload.get("failed") if isinstance(payload, dict) else None
            raise ZoteroError(f"Zotero 笔记创建失败：{failed or 'unknown error'}")
        finally:
            response.close()

    def write_analysis_note_and_tags(
        self,
        zotero_user_id: int,
        parent_item_key: str,
        *,
        note_html: str,
        suggested_tags: list[str],
        note_item_key: str | None = None,
    ) -> dict[str, Any]:
        parent = self.fetch_item(zotero_user_id, parent_item_key)
        parent_data = parent["data"]
        existing_tags = parent_data.get("tags") if isinstance(parent_data.get("tags"), list) else []
        merged_tags: list[dict[str, Any]] = []
        seen: set[str] = set()
        for tag in existing_tags:
            if not isinstance(tag, dict) or not tag.get("tag"):
                continue
            name = str(tag["tag"]).strip()
            folded = name.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            merged_tags.append(dict(tag))
        added_tags: list[str] = []
        for value in suggested_tags:
            name = str(value).strip()
            folded = name.casefold()
            if not name or folded in seen:
                continue
            seen.add(folded)
            merged_tags.append({"tag": name})
            added_tags.append(name)

        parent_version = _as_int(parent.get("version") or parent_data.get("version"))
        new_parent_version = parent_version
        if added_tags:
            try:
                new_parent_version = self.patch_item(
                    zotero_user_id,
                    parent_item_key,
                    parent_version,
                    {"tags": merged_tags},
                )
            except ZoteroWriteConflictError:
                refreshed_parent = self.fetch_item(zotero_user_id, parent_item_key)
                refreshed_data = refreshed_parent["data"]
                refreshed_tags = (
                    refreshed_data.get("tags")
                    if isinstance(refreshed_data.get("tags"), list)
                    else []
                )
                refreshed_seen = {
                    str(tag.get("tag") or "").strip().casefold()
                    for tag in refreshed_tags
                    if isinstance(tag, dict) and tag.get("tag")
                }
                merged_tags = [dict(tag) for tag in refreshed_tags if isinstance(tag, dict)]
                added_tags = []
                for value in suggested_tags:
                    name = str(value).strip()
                    folded = name.casefold()
                    if not name or folded in refreshed_seen:
                        continue
                    refreshed_seen.add(folded)
                    merged_tags.append({"tag": name})
                    added_tags.append(name)
                if added_tags:
                    new_parent_version = self.patch_item(
                        zotero_user_id,
                        parent_item_key,
                        _as_int(refreshed_parent.get("version") or refreshed_data.get("version")),
                        {"tags": merged_tags},
                    )

        saved_note_key = ""
        if note_item_key:
            try:
                note = self.fetch_item(zotero_user_id, note_item_key)
                note_data = note["data"]
                if (
                    note_data.get("itemType") == "note"
                    and note_data.get("parentItem") == parent_item_key
                ):
                    note_version = _as_int(note.get("version") or note_data.get("version"))
                    self.patch_item(
                        zotero_user_id,
                        note_item_key,
                        note_version,
                        {"note": note_html, "tags": [{"tag": "来源/Paper Insight"}]},
                    )
                    saved_note_key = note_item_key
            except ZoteroNotFoundError:
                saved_note_key = ""
        if not saved_note_key:
            saved_note = self.create_note(zotero_user_id, parent_item_key, note_html)
            saved_note_key = str(saved_note.get("key") or "")
        if not saved_note_key:
            raise ZoteroError("Zotero 没有返回新建笔记的条目 Key")

        return {
            "note_item_key": saved_note_key,
            "added_tags": added_tags,
            "all_tags": [str(tag.get("tag")) for tag in merged_tags if tag.get("tag")],
            "parent_version": new_parent_version,
        }


def _cache_dir() -> Path:
    configured = settings.paths.zotero_content_cache_dir
    if not configured:
        return DEFAULT_CACHE_DIR
    path = Path(configured)
    return path if path.is_absolute() else REPO_ROOT / path


def _cache_paths(user_id: str, attachment_key: str) -> tuple[Path, Path]:
    safe_user = re.sub(r"[^A-Za-z0-9._-]", "_", str(user_id))
    safe_key = re.sub(r"[^A-Za-z0-9._-]", "_", attachment_key)
    base = _cache_dir() / safe_user
    return base / f"{safe_key}.txt", base / f"{safe_key}.json"


def delete_user_cache(user_id: str) -> None:
    safe_user = re.sub(r"[^A-Za-z0-9._-]", "_", str(user_id))
    root = _cache_dir().resolve()
    target = (root / safe_user).resolve()
    if target.parent != root:
        raise ZoteroContentError("无效的 Zotero 缓存路径")
    if target.exists():
        shutil.rmtree(target)


def _read_cached_content(user_id: str, attachment_key: str, version: int) -> str | None:
    content_path, meta_path = _cache_paths(user_id, attachment_key)
    if not content_path.exists() or not meta_path.exists():
        return None
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        if _as_int(metadata.get("version"), -1) != version:
            return None
        content = content_path.read_text(encoding="utf-8").strip()
        return content or None
    except (OSError, ValueError, TypeError):
        return None


def _write_cached_content(
    user_id: str,
    attachment_key: str,
    version: int,
    source: str,
    content: str,
) -> None:
    content_path, meta_path = _cache_paths(user_id, attachment_key)
    content_path.parent.mkdir(parents=True, exist_ok=True)
    content_path.write_text(content, encoding="utf-8")
    meta_path.write_text(
        json.dumps({"version": version, "source": source}, ensure_ascii=False),
        encoding="utf-8",
    )


def _creator_name(creator: Any) -> str:
    if not isinstance(creator, dict):
        return str(creator)
    if creator.get("name"):
        return str(creator["name"])
    return " ".join(
        value for value in [str(creator.get("firstName") or ""), str(creator.get("lastName") or "")] if value
    ).strip()


def build_metadata_context(item: dict[str, Any], children: list[dict[str, Any]]) -> str:
    creators = "、".join(filter(None, (_creator_name(value) for value in item.get("creators") or []))) or "未知"
    tags = "、".join(str(value) for value in item.get("tags") or []) or "无"
    lines = [
        "Zotero 条目元数据：",
        f"标题：{item.get('title') or '未知'}",
        f"作者：{creators}",
        f"条目类型：{item.get('item_type') or '未知'}",
        f"期刊/会议：{item.get('publication_title') or '未知'}",
        f"日期：{item.get('item_date') or '未知'}",
        f"DOI：{item.get('doi') or '无'}",
        f"URL：{item.get('url') or '无'}",
        f"标签：{tags}",
        f"摘要：{item.get('abstract_note') or '无'}",
    ]
    notes = [child.get("note") for child in children if child.get("note")]
    annotations = []
    for child in children:
        text = child.get("annotation_text")
        comment = child.get("annotation_comment")
        if text or comment:
            annotations.append("\n".join(part for part in [text, comment] if part))
    if notes:
        lines.append("\nZotero 笔记：\n" + "\n\n---\n\n".join(notes))
    if annotations:
        lines.append("\nZotero PDF 批注：\n" + "\n\n---\n\n".join(annotations))
    return "\n".join(lines)


def build_reading_context(
    metadata: str,
    *,
    item: dict[str, Any],
    children: list[dict[str, Any]],
    content: str | None = None,
    content_source: str | None = None,
    content_url: str | None = None,
) -> str:
    parts = [metadata]
    if content_source:
        parts.append(f"论文全文来源：{content_source}")
    if content_url:
        parts.append(f"公开 PDF 地址：{content_url}")
    repositories = discover_code_repositories(item, children, content)
    repository_context = build_repository_context(repositories)
    if repository_context:
        parts.append(repository_context)
    if content:
        parts.append(
            "论文全文：\n"
            + truncate_content_for_llm(content, max_tokens=ZOTERO_FULLTEXT_TOKEN_LIMIT)
        )
    return "\n\n".join(parts)


def get_item_reading_context(
    *,
    user_id: str,
    zotero_user_id: int,
    item: dict[str, Any],
    children: list[dict[str, Any]],
    client: ZoteroClient,
) -> tuple[str, str, str | None]:
    metadata = build_metadata_context(item, children)
    attachments = [
        child
        for child in children
        if child.get("item_type") == "attachment"
        and (
            str(child.get("content_type") or "").lower() == "application/pdf"
            or str(child.get("filename") or "").lower().endswith(".pdf")
        )
    ]
    errors: list[str] = []
    if not attachments:
        errors.append("该条目没有可用的 Zotero PDF 附件")
    for attachment in attachments:
        key = str(attachment["item_key"])
        version = _as_int(attachment.get("item_version"))
        cached = _read_cached_content(user_id, key, version)
        if cached:
            return (
                build_reading_context(
                    metadata,
                    item=item,
                    children=children,
                    content=cached,
                    content_source="Zotero 正文缓存",
                ),
                "cache",
                None,
            )

        try:
            content = client.fetch_fulltext(zotero_user_id, key)
            source = "zotero-fulltext"
            if not content:
                link_mode = str(attachment.get("link_mode") or "").casefold()
                if link_mode == "linked_file":
                    errors.append("Zotero 附件是本地 linked_file，云端不保存该文件")
                    continue
                if link_mode == "linked_url":
                    errors.append("Zotero 附件是 linked_url，将尝试公开地址")
                    continue
                pdf_bytes = client.download_attachment(zotero_user_id, key)
                content = extract_pdf_text_bounded(pdf_bytes, f"zotero:{key}")
                source = "attachment-pdf"
            content = content.strip()
            if content:
                _write_cached_content(user_id, key, version, source, content)
                return (
                    build_reading_context(
                        metadata,
                        item=item,
                        children=children,
                        content=content,
                        content_source=(
                            "Zotero 已索引全文"
                            if source == "zotero-fulltext"
                            else "Zotero 云端 PDF"
                        ),
                    ),
                    source,
                    None,
                )
        except (ZoteroError, ReaderError) as exc:
            errors.append(str(exc))
            logger.info("Unable to read Zotero attachment %s: %s", key, exc)

    public_document, public_errors = resolve_public_document(item, children)
    errors.extend(public_errors)
    if public_document:
        return (
            build_reading_context(
                metadata,
                item=item,
                children=children,
                content=public_document.content,
                content_source=f"公开全文（{public_document.source}）",
                content_url=public_document.url,
            ),
            f"public-document:{public_document.source}",
            None,
        )

    reason = "；".join(dict.fromkeys(errors)) or "未能读取 Zotero PDF 正文"
    return (
        build_reading_context(metadata, item=item, children=children),
        "metadata",
        reason,
    )
