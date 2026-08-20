#!/usr/bin/env python3
"""Build or refresh the Typesense paper search index."""

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from config import settings
from typesense_search import (
    TypesenseSearchError,
    collection_document_count,
    is_enabled,
    rebuild_index,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild the Typesense paper index")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument(
        "--if-empty",
        action="store_true",
        help="Skip rebuilding when the configured collection alias already contains documents.",
    )
    parser.add_argument(
        "--keep-old",
        action="store_true",
        help="Keep the previously aliased physical collection after the alias switch.",
    )
    args = parser.parse_args()

    if not is_enabled():
        print("Typesense is disabled or TYPESENSE_API_KEY is missing.")
        return 1
    if not settings.database.url:
        print("database.url is required.")
        return 1

    try:
        if args.if_empty:
            current_count = collection_document_count()
            if current_count:
                print(f"Typesense index already contains {current_count} papers; skipping.")
                return 0

        total = rebuild_index(
            batch_size=max(args.batch_size, 1),
            prune_old=not args.keep_old,
        )
    except TypesenseSearchError as exc:
        print(f"Typesense reindex failed: {exc}")
        return 1

    print(
        f"Typesense alias '{settings.typesense.collection_alias}' now indexes {total} papers."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
