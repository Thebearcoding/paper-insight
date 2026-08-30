#!/usr/bin/env python3
"""Backfill Zotero framework figures and SOTA result tables without calling an LLM."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from database import (  # noqa: E402
    get_zotero_connection,
    get_zotero_item,
    list_zotero_collections,
    list_zotero_items,
    update_zotero_analysis_figures,
)
from paper_figures import (  # noqa: E402
    RESULTS_TABLE_KIND,
    extract_and_save_zotero_analysis_assets,
)
from zotero import ZoteroClient, get_item_reading_context  # noqa: E402


def _asset_counts(assets: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "framework": sum(asset.get("kind") == "framework" for asset in assets),
        "results_table": sum(asset.get("kind") == RESULTS_TABLE_KIND for asset in assets),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--collection", default="ZSAD")
    parser.add_argument("--item-key", action="append", default=[])
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("/tmp/zotero-analysis-assets-report.json"),
    )
    args = parser.parse_args()

    connection = get_zotero_connection(args.user_id, include_api_key=True)
    if not connection:
        raise RuntimeError(f"Zotero connection for user {args.user_id!r} was not found")
    collection = next(
        (
            entry
            for entry in list_zotero_collections(args.user_id)
            if entry.get("name") == args.collection
        ),
        None,
    )
    if not collection:
        raise RuntimeError(f"Zotero collection {args.collection!r} was not found")

    items, _ = list_zotero_items(
        args.user_id,
        limit=500,
        collection_key=str(collection["collection_key"]),
    )
    requested_keys = set(args.item_key)
    if requested_keys:
        items = [item for item in items if str(item.get("item_key") or "") in requested_keys]
        found_keys = {str(item.get("item_key") or "") for item in items}
        missing_keys = sorted(requested_keys - found_keys)
        if missing_keys:
            raise RuntimeError(f"Zotero item keys were not found: {missing_keys}")

    client = ZoteroClient(str(connection["api_key"]))
    results: list[dict[str, Any]] = []
    for index, summary in enumerate(items, start=1):
        item_key = str(summary["item_key"])
        title = str(summary.get("title") or "(untitled)")
        print(f"[{index}/{len(items)}] {title}", flush=True)
        try:
            item = get_zotero_item(args.user_id, item_key)
            if not item:
                raise RuntimeError("Zotero item disappeared during backfill")
            context = ""
            source = str(item.get("analysis_source") or "direct-pdf")
            warning = item.get("analysis_warning")
            assets = extract_and_save_zotero_analysis_assets(
                user_id=args.user_id,
                zotero_user_id=int(connection["zotero_user_id"]),
                item=item,
                children=item.get("children") or [],
                client=client,
                reading_context=context,
                force_refresh=args.force_refresh,
            )
            if not any(asset.get("kind") == RESULTS_TABLE_KIND for asset in assets):
                context, source, warning = get_item_reading_context(
                    user_id=args.user_id,
                    zotero_user_id=int(connection["zotero_user_id"]),
                    item=item,
                    children=item.get("children") or [],
                    client=client,
                )
                assets = extract_and_save_zotero_analysis_assets(
                    user_id=args.user_id,
                    zotero_user_id=int(connection["zotero_user_id"]),
                    item={**item, "analysis_figures": assets},
                    children=item.get("children") or [],
                    client=client,
                    reading_context=context,
                    force_refresh=False,
                )
            if assets != list(item.get("analysis_figures") or []):
                update_zotero_analysis_figures(args.user_id, item_key, assets)
            counts = _asset_counts(assets)
            result = {
                "item_key": item_key,
                "title": title,
                "ok": True,
                "source": source,
                "warning": warning,
                **counts,
            }
            print(
                f"    framework={counts['framework']} results_table={counts['results_table']} source={source}",
                flush=True,
            )
        except Exception as exc:
            result = {
                "item_key": item_key,
                "title": title,
                "ok": False,
                "error": str(exc),
                "framework": 0,
                "results_table": 0,
            }
            print(f"    FAILED: {exc}", flush=True)
        results.append(result)

    report = {
        "collection": args.collection,
        "total": len(results),
        "succeeded": sum(bool(result["ok"]) for result in results),
        "with_framework": sum(bool(result["framework"]) for result in results),
        "with_results_table": sum(bool(result["results_table"]) for result in results),
        "results": results,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Finished: {report['succeeded']}/{report['total']} succeeded, "
        f"{report['with_results_table']} with SOTA result tables. Report: {args.report}",
        flush=True,
    )
    return 0 if report["succeeded"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
