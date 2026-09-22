"""Clear translation-memory rows without replacing a live SQLite database.

Run with python -E: maintenance needs only the standard library, not the
memory-heavy sitecustomize imports. Schema creation belongs to upstream.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from pathlib import Path

logger = logging.getLogger(__name__)


def clear_translation_memory(path: Path, *, timeout: float = 1.0) -> bool:
    """Best-effort cleanup; preserve schema, open connections and WAL files.

    Missing/uninitialized or busy databases are skipped until the next cycle.
    DELETE lets SQLite reuse freed pages; VACUUM or unlinking files while the
    worker is running would introduce locks or break its inherited schema.
    """
    try:
        # mode=rw never creates an empty database if upstream has not started.
        with closing(sqlite3.connect(
            path.resolve().as_uri() + "?mode=rw", uri=True, timeout=timeout
        )) as connection:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                ("_translationcache",),
            ).fetchone()
            if not exists:
                return False
            with connection:
                connection.execute('DELETE FROM "_translationcache"')
            # PASSIVE never waits for active readers; SQLite retains any WAL
            # pages they need. Do not truncate/remove WAL or SHM files manually.
            connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
        return True
    except sqlite3.Error as exc:
        logger.warning("Translation cache cleanup skipped for %s: %s", path, exc)
        return False


def main() -> None:
    for package in ("pdf2zh", "babeldoc"):
        path = Path.home() / ".cache" / package / "cache.v1.db"
        if path.is_file():
            clear_translation_memory(path)


if __name__ == "__main__":
    main()
