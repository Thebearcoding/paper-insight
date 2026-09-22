import asyncio
import sys
import threading
from pathlib import Path

import pytest
from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import app as app_module


ENDPOINTS = ['search', 'conference', 'hf_daily', 'arxiv']
LIST_FUNCTIONS = {
    'search': 'search_all_papers',
    'conference': 'get_conference_papers',
    'hf_daily': 'get_hf_daily_papers',
    'arxiv': 'get_arxiv_papers',
}
COUNT_FUNCTIONS = {
    'search': 'count_search_paper_read_states',
    'conference': 'count_search_paper_read_states',
    'hf_daily': 'count_hf_daily_paper_read_states',
    'arxiv': 'count_arxiv_paper_read_states',
}


async def call_papers_endpoint(endpoint, request, **kwargs):
    function = getattr(app_module, f'{LIST_FUNCTIONS[endpoint]}_endpoint')
    if endpoint == 'conference':
        venue = next(iter(app_module.CONFERENCE_VENUE_MAP))
        return await function(venue, request, **kwargs)
    return await function(request, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint', ENDPOINTS)
@pytest.mark.parametrize('page,limit,expected_limit', [(0, 0, 1), (-1, -8, 1), (1, 10000, 100)])
async def test_search_pagination_rejects_invalid_offsets_and_zero_division(monkeypatch, endpoint, page, limit, expected_limit):
    monkeypatch.setattr(app_module, 'get_current_user_optional', lambda _: None)
    calls = []

    def search(*args, **kwargs):
        calls.append(args)
        return [], 10

    monkeypatch.setattr(app_module, LIST_FUNCTIONS[endpoint], search)
    request = Request({'type': 'http', 'headers': []})
    result = await call_papers_endpoint(endpoint, request, page=page, limit=limit)
    offset, actual_limit = calls[0][1:3] if endpoint == 'conference' else calls[0][:2]
    assert offset == 0
    assert actual_limit == min(expected_limit, 24 if endpoint == 'arxiv' else 100)
    assert result['page'] == 1
    assert result['pages'] >= 1


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint', ENDPOINTS)
@pytest.mark.parametrize('logged_in', [False, True], ids=['anonymous', 'authenticated'])
@pytest.mark.parametrize('use_typesense', [False, True], ids=['database', 'typesense'])
async def test_paper_lists_offload_auth_list_and_count_sequentially(monkeypatch, endpoint, logged_in, use_typesense):
    loop_thread = threading.get_ident()
    real_to_thread = asyncio.to_thread
    in_flight = False
    calls = []
    request = Request({'type': 'http', 'headers': []})
    user_id = 'test-user' if logged_in else None
    papers = [{'id': 'paper-1', 'title': 'Test paper'}]
    read_counts = {'all': 9, 'read': 2, 'unread': 7}

    async def sequential_to_thread(function, /, *args, **kwargs):
        nonlocal in_flight
        assert not in_flight, 'Blocking calls must be awaited sequentially'
        in_flight = True
        try:
            # Yield before execution so concurrent submissions fail deterministically.
            await asyncio.sleep(0)
            return await real_to_thread(function, *args, **kwargs)
        finally:
            in_flight = False

    def record_call(stage, args, kwargs):
        assert threading.get_ident() != loop_thread, f'{stage} blocked the event loop thread'
        calls.append((stage, args, kwargs))

    def authenticate(actual_request):
        record_call('auth', (actual_request,), {})
        assert actual_request is request
        return {'id': user_id} if logged_in else None

    def list_papers(*args, **kwargs):
        record_call('list', args, kwargs)
        return papers, 9

    def count_read_states(*args, **kwargs):
        record_call('count', args, kwargs)
        return read_counts

    monkeypatch.setattr(app_module.asyncio, 'to_thread', sequential_to_thread)
    monkeypatch.setattr(app_module, 'get_current_user_optional', authenticate)
    monkeypatch.setattr(app_module, LIST_FUNCTIONS[endpoint], list_papers)
    monkeypatch.setattr(app_module, COUNT_FUNCTIONS[endpoint], count_read_states)
    monkeypatch.setattr(app_module.typesense_search, 'should_use_search', lambda *args: use_typesense)

    result = await call_papers_endpoint(
        endpoint, request, page=2, limit=4, search='attention',
        search_title=False, search_abstract=True, search_keywords=False,
        code_status='open_source',
    )

    should_count = logged_in and (endpoint in {'hf_daily', 'arxiv'} or not use_typesense)
    assert [stage for stage, _, _ in calls] == ['auth', 'list'] + (['count'] if should_count else [])
    assert result == {
        'papers': papers, 'total': 9, 'read_counts': read_counts if should_count else None,
        'page': 2, 'pages': 3,
    }

    list_args = (4, 4, 'attention', False, True, False)
    list_kwargs = {'user_id': user_id, 'read_status': 'all', 'code_filter': 'open_source'}
    venue_name = next(iter(app_module.CONFERENCE_VENUE_MAP.values()))
    if endpoint == 'conference':
        list_args = (venue_name, *list_args)
    elif endpoint == 'arxiv':
        list_args = (4, 4)
        list_kwargs.update(
            analyzed_only=True, search='attention', search_title=False,
            search_abstract=True, search_keywords=False,
        )
    assert calls[1][1:] == (list_args, list_kwargs)
    if should_count:
        count_args = ('attention', False, True, False, user_id)
        if endpoint in {'search', 'conference'}:
            count_args = (venue_name if endpoint == 'conference' else None, *count_args)
        elif endpoint == 'arxiv':
            count_args = (True, *count_args)
        assert calls[2][1:] == (count_args, {'code_filter': 'open_source'})


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint', ENDPOINTS)
@pytest.mark.parametrize('read_status', ['read', 'unread'])
async def test_anonymous_read_filter_still_requires_login(monkeypatch, endpoint, read_status):
    loop_thread = threading.get_ident()
    calls = []

    def authenticate(request):
        assert threading.get_ident() != loop_thread
        calls.append('auth')
        return None

    def unexpected_query(*args, **kwargs):
        pytest.fail('Anonymous read filters must be rejected before querying papers')

    monkeypatch.setattr(app_module, 'get_current_user_optional', authenticate)
    monkeypatch.setattr(app_module, LIST_FUNCTIONS[endpoint], unexpected_query)
    monkeypatch.setattr(app_module, COUNT_FUNCTIONS[endpoint], unexpected_query)
    request = Request({'type': 'http', 'headers': []})
    with pytest.raises(app_module.HTTPException) as exc:
        await call_papers_endpoint(endpoint, request, read_status=read_status)
    assert exc.value.status_code == 401
    assert calls == ['auth']


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint', ENDPOINTS)
@pytest.mark.parametrize('failure_stage', ['list', 'count'])
async def test_offloaded_database_errors_still_return_502(monkeypatch, endpoint, failure_stage):
    loop_thread = threading.get_ident()
    calls = []

    def authenticate(request):
        assert threading.get_ident() != loop_thread
        calls.append('auth')
        return {'id': 'test-user'}

    def query(stage, result):
        assert threading.get_ident() != loop_thread
        calls.append(stage)
        if stage == failure_stage:
            raise app_module.DatabaseError('database unavailable')
        return result

    monkeypatch.setattr(app_module, 'get_current_user_optional', authenticate)
    monkeypatch.setattr(app_module, LIST_FUNCTIONS[endpoint], lambda *args, **kwargs: query('list', ([], 0)))
    monkeypatch.setattr(app_module, COUNT_FUNCTIONS[endpoint], lambda *args, **kwargs: query('count', {}))
    monkeypatch.setattr(app_module.typesense_search, 'should_use_search', lambda *args: False)
    request = Request({'type': 'http', 'headers': []})
    with pytest.raises(app_module.HTTPException) as exc:
        await call_papers_endpoint(endpoint, request)
    assert exc.value.status_code == 502
    assert exc.value.detail == 'Database temporarily unavailable'
    assert isinstance(exc.value.__cause__, app_module.DatabaseError)
    assert calls == ['auth', 'list'] + (['count'] if failure_stage == 'count' else [])
