#!/usr/bin/env python3
"""Reanalyze every paper in a Zotero collection through the deployed API."""

from __future__ import annotations

import argparse
import atexit
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote

import requests


def _credential_values(path: Path, *labels: str) -> dict[str, str]:
    """Read credentials once so non-seekable sources such as /dev/stdin work."""
    lines = path.read_text(encoding="utf-8").splitlines()
    values: dict[str, str] = {}
    for label in labels:
        prefix = f"{label}: "
        value = next(
            (line.removeprefix(prefix).strip() for line in lines if line.startswith(prefix)),
            "",
        )
        if not value:
            raise RuntimeError(f"credentials file is missing {label!r}")
        values[label] = value
    return values


def _json(response: requests.Response) -> dict[str, Any]:
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected JSON object from {response.url}")
    return payload


def _create_trusted_session(session: requests.Session, user_id: str) -> None:
    """Create a short-lived server-side session without changing user credentials."""
    backend_dir = Path(__file__).resolve().parents[1] / "backend"
    backend_path = str(backend_dir)
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)

    from auth import generate_session_token, hash_session_token
    from config import settings
    from database import create_user_session, get_user_by_id, revoke_session

    user = get_user_by_id(user_id)
    if not user or not user.get("is_active"):
        raise RuntimeError(f"active Paper Insight user {user_id!r} was not found")

    token = generate_session_token()
    token_hash = hash_session_token(token)
    create_user_session(
        user_id,
        token_hash,
        datetime.now(timezone.utc) + timedelta(hours=1),
        "paper-insight-batch-reanalysis/1.0",
        "127.0.0.1",
    )
    session.cookies.set(settings.auth.session_cookie_name, token)
    atexit.register(revoke_session, token_hash)


def _sse_events(response: requests.Response) -> Iterator[tuple[str, str]]:
    event_name = "message"
    data_lines: list[str] = []
    for raw_line in response.iter_lines(decode_unicode=True):
        line = raw_line or ""
        if not line:
            if data_lines:
                yield event_name, "\n".join(data_lines)
            event_name = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_name = value
        elif field == "data":
            data_lines.append(value)
    if data_lines:
        yield event_name, "\n".join(data_lines)


def _resolve_provider(catalog: dict[str, Any], provider_key: str, model_name: str) -> str:
    for provider in catalog.get("providers") or []:
        if str(provider.get("provider_key") or "") != provider_key:
            continue
        models = {str(model.get("model_name") or "") for model in provider.get("models") or []}
        if model_name not in models:
            raise RuntimeError(f"model {model_name!r} is not enabled for provider {provider_key!r}")
        return str(provider["id"])
    raise RuntimeError(f"provider {provider_key!r} is not selectable")


def _analyze_item(
    session: requests.Session,
    base_url: str,
    item: dict[str, Any],
    provider_id: str,
    model_name: str,
    timeout_seconds: int,
    context_token_limit: int | None,
) -> dict[str, Any]:
    item_key = str(item["item_key"])
    response = session.get(
        f"{base_url}/me/zotero/items/{quote(item_key, safe='')}/analysis",
        params={
            "reanalyze": "true",
            "provider_id": provider_id,
            "model_name": model_name,
            **(
                {"context_token_limit": context_token_limit}
                if context_token_limit is not None
                else {}
            ),
        },
        stream=True,
        timeout=(15, timeout_seconds),
    )
    response.raise_for_status()
    errors: list[str] = []
    statuses: list[str] = []
    final_seen = False
    figures_seen = 0
    try:
        for event, data in _sse_events(response):
            if event == "status" and data:
                statuses.append(data)
                print(f"    {data}", flush=True)
            elif event == "figures" and data:
                try:
                    figures_seen = len(json.loads(data))
                except (TypeError, ValueError):
                    figures_seen = 0
            elif event == "error":
                errors.append(data or "unknown analysis error")
            elif event == "final":
                final_seen = bool(data.strip())
    finally:
        response.close()

    refreshed = _json(
        session.get(
            f"{base_url}/me/zotero/items/{quote(item_key, safe='')}",
            timeout=30,
        )
    )
    return {
        "item_key": item_key,
        "title": item.get("title") or "(untitled)",
        "ok": not errors and bool(refreshed.get("llm_response")) and final_seen,
        "errors": errors,
        "statuses": statuses,
        "analysis_source": refreshed.get("analysis_source"),
        "analysis_warning": refreshed.get("analysis_warning"),
        "analysis_provider_name": refreshed.get("analysis_provider_name"),
        "analysis_model_name": refreshed.get("analysis_model_name"),
        "figure_count": len(refreshed.get("analysis_figures") or []),
        "figures_seen_during_stream": figures_seen,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://paper.athebear.me")
    auth_group = parser.add_mutually_exclusive_group(required=True)
    auth_group.add_argument("--credentials-file", type=Path)
    auth_group.add_argument(
        "--trusted-user-id",
        help="Create a temporary internal session for this user; intended for the app container only.",
    )
    parser.add_argument("--collection", default="ZSAD")
    parser.add_argument("--provider-key", default="sub2api")
    parser.add_argument("--model", default="claude-opus-5")
    parser.add_argument(
        "--item-key",
        action="append",
        default=[],
        help="Only reanalyze this Zotero item key; repeat to select multiple items.",
    )
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    parser.add_argument(
        "--context-token-limit",
        type=int,
        help="Override the GLM analysis context limit for targeted retries.",
    )
    parser.add_argument("--report", type=Path, default=Path("/tmp/zotero-reanalysis-report.json"))
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    session = requests.Session()
    session.headers.update({"Accept": "application/json", "User-Agent": "paper-insight-batch-reanalysis/1.0"})
    if args.trusted_user_id:
        _create_trusted_session(session, args.trusted_user_id)
    else:
        credentials = _credential_values(args.credentials_file, "Admin email", "Admin password")
        login = session.post(
            f"{base_url}/auth/login",
            json={
                "email": credentials["Admin email"],
                "password": credentials["Admin password"],
            },
            timeout=30,
        )
        login.raise_for_status()

    collections = _json(session.get(f"{base_url}/me/zotero/collections", timeout=30)).get("collections") or []
    collection = next((entry for entry in collections if entry.get("name") == args.collection), None)
    if not collection:
        raise RuntimeError(f"Zotero collection {args.collection!r} was not found")

    items_payload = _json(
        session.get(
            f"{base_url}/me/zotero/items",
            params={"limit": 100, "collection_key": collection["collection_key"]},
            timeout=30,
        )
    )
    items = items_payload.get("items") or []
    if args.item_key:
        requested_keys = set(args.item_key)
        items_by_key = {str(item.get("item_key") or ""): item for item in items}
        missing_keys = sorted(requested_keys - items_by_key.keys())
        if missing_keys:
            raise RuntimeError(f"Zotero item keys were not found in {args.collection!r}: {missing_keys}")
        items = [item for item in items if str(item.get("item_key") or "") in requested_keys]
    catalog = _json(session.get(f"{base_url}/me/llm/models", params={"refresh": "false"}, timeout=30))
    provider_id = _resolve_provider(catalog, args.provider_key, args.model)

    print(
        f"Reanalyzing {len(items)} papers from {args.collection} with {args.provider_key}/{args.model}",
        flush=True,
    )
    results: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        title = str(item.get("title") or "(untitled)")
        print(f"[{index}/{len(items)}] {title}", flush=True)
        try:
            result = _analyze_item(
                session,
                base_url,
                item,
                provider_id,
                args.model,
                max(args.timeout_seconds, 60),
                args.context_token_limit,
            )
        except Exception as exc:
            result = {
                "item_key": item.get("item_key"),
                "title": title,
                "ok": False,
                "errors": [str(exc)],
                "figure_count": 0,
            }
        results.append(result)
        print(
            f"    {'OK' if result['ok'] else 'FAILED'} source={result.get('analysis_source') or '-'} "
            f"figures={result.get('figure_count', 0)}",
            flush=True,
        )
        if index < len(items) and args.delay_seconds > 0:
            time.sleep(args.delay_seconds)

    report = {
        "collection": args.collection,
        "provider_key": args.provider_key,
        "model": args.model,
        "total": len(results),
        "succeeded": sum(bool(result.get("ok")) for result in results),
        "with_framework_figure": sum(int(result.get("figure_count") or 0) > 0 for result in results),
        "results": results,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Finished: {report['succeeded']}/{report['total']} succeeded, "
        f"{report['with_framework_figure']} with framework figures. Report: {args.report}",
        flush=True,
    )
    return 0 if report["succeeded"] == report["total"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        raise SystemExit(130)
