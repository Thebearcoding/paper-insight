import sys
from pathlib import Path

import pytest
from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import app as app_module


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint', ['search', 'conference'])
@pytest.mark.parametrize('page,limit,expected_limit', [(0, 0, 1), (-1, -8, 1), (1, 10000, 100)])
async def test_search_pagination_rejects_invalid_offsets_and_zero_division(monkeypatch, endpoint, page, limit, expected_limit):
    monkeypatch.setattr(app_module, 'get_current_user_optional', lambda _: None)
    calls = []

    def search(*args, **kwargs):
        calls.append(args)
        return [], 10

    monkeypatch.setattr(app_module, 'search_all_papers', search)
    monkeypatch.setattr(app_module, 'get_conference_papers', search)
    request = Request({'type': 'http', 'headers': []})
    if endpoint == 'search':
        result = await app_module.search_all_papers_endpoint(request, page=page, limit=limit)
        offset, actual_limit = calls[0][:2]
    else:
        venue = next(iter(app_module.CONFERENCE_VENUE_MAP))
        result = await app_module.get_conference_papers_endpoint(venue, request, page=page, limit=limit)
        offset, actual_limit = calls[0][1:3]
    assert offset == 0
    assert actual_limit == expected_limit
    assert result['page'] == 1
    assert result['pages'] >= 1
