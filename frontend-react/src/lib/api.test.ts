import { afterEach, describe, expect, it, vi } from 'vitest';

import { fetchOnlineSearchPapers, streamSse } from './api';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('streamSse', () => {
  it('removes only the protocol space and preserves content indentation', async () => {
    const chunks: string[] = [];
    const body = 'data: top\ndata:   nested\n\ndata:  \n\n';
    vi.stubGlobal('fetch', vi.fn(async () => new Response(body, { status: 200 })));

    await streamSse('/test-stream', { method: 'GET' }, {
      onChunk: (chunk) => chunks.push(chunk),
    });

    expect(chunks).toEqual(['top\n  nested', ' ']);
  });

  it('delivers the canonical final event without appending it as another chunk', async () => {
    const chunks: string[] = [];
    const events: Array<[string, string]> = [];
    const body = 'data: partial\n\nevent: final\ndata: # Heading\ndata:   indented\n\nevent: done\ndata: \n\n';
    vi.stubGlobal('fetch', vi.fn(async () => new Response(body, { status: 200 })));

    await streamSse('/test-stream', { method: 'GET' }, {
      onChunk: (chunk) => chunks.push(chunk),
      onEvent: (event, data) => events.push([event, data]),
    });

    expect(chunks).toEqual(['partial']);
    expect(events).toContainEqual(['final', '# Heading\n  indented']);
    expect(events).toContainEqual(['done', '']);
  });
});

describe('fetchOnlineSearchPapers', () => {
  it('sends the query, year range, and sort without a persistence request', async () => {
    const fetchMock = vi.fn(async (...args: Parameters<typeof fetch>) => {
      void args;
      return new Response(JSON.stringify({
        papers: [],
        total: 0,
        page: 2,
        pages: 1,
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', fetchMock);

    await fetchOnlineSearchPapers(2, ' defect detection ', 2022, 2026, 'cited');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [request, init] = fetchMock.mock.calls[0];
    const url = new URL(String(request));
    expect(url.pathname).toBe('/online-search/papers');
    expect(url.searchParams.get('page')).toBe('2');
    expect(url.searchParams.get('limit')).toBe('8');
    expect(url.searchParams.get('search')).toBe('defect detection');
    expect(url.searchParams.get('from_year')).toBe('2022');
    expect(url.searchParams.get('to_year')).toBe('2026');
    expect(url.searchParams.get('sort')).toBe('cited');
    expect(url.searchParams.get('venue_scope')).toBe('top');
    expect(init).toMatchObject({ credentials: 'include' });
    expect(init?.method).toBeUndefined();
    expect(init?.body).toBeUndefined();
  });

  it('allows searching all online sources explicitly', async () => {
    const fetchMock = vi.fn(async (...args: Parameters<typeof fetch>) => {
      void args;
      return new Response(JSON.stringify({
        papers: [],
        total: 0,
        page: 1,
        pages: 1,
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', fetchMock);

    await fetchOnlineSearchPapers(1, 'retrieval', 2024, 2026, 'relevance', 'all');

    const [request] = fetchMock.mock.calls[0];
    const url = new URL(String(request));
    expect(url.searchParams.get('venue_scope')).toBe('all');
  });
});
