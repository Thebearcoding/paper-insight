"""Upstream page counters must not advertise an unfinished PDF as complete."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from pdf_translation import _map_remote_task_state


@pytest.mark.parametrize(
    ("current", "total", "expected"),
    [(1, 1, 99), (12, 10, 99), (-1, 10, 0), (0, 0, 0), (3, 10, 30)],
)
def test_in_progress_page_count_is_bounded_below_completion(current, total, expected):
    assert _map_remote_task_state({
        "state": "PROGRESS", "info": {"n": current, "total": total},
    }) == {"state": "progress", "progress": expected}


def test_only_success_reports_complete():
    assert _map_remote_task_state({"state": "SUCCESS"}) == {
        "state": "success", "progress": 100,
    }
