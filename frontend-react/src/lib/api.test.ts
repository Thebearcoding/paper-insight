import { afterEach, describe, expect, it, vi } from 'vitest';

import { fetchOnlineSearchPapers, streamSse } from './api';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('streamSse', () => {
  it('parses CRLF boundaries even when each byte arrives separately', async () => {
    const bytes = new TextEncoder().encode('data: 中文\r\n\r\nevent: done\r\ndata: \r\n\r\n');
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
        controller.close();
      },
    });
    vi.stubGlobal('fetch', vi.fn(async () => new Response(body)));
    const chunks: string[] = [];
    const events: string[] = [];
    await streamSse('/test-stream', {}, {
      onChunk: (chunk) => chunks.push(chunk),
      onEvent: (event) => events.push(event),
    });
    expect(chunks).toEqual(['中文']);
    expect(events).toContain('done');
  });

  it('rejects a stream that closes without confirming completion', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('data: half a report\n\n')));
    await expect(streamSse('/test-stream', {}, {})).rejects.toThrow('完成');
  });

  it('stops and releases the connection on done without waiting for EOF', async () => {
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('event: done\ndata: \n\n'));
      },
      cancel,
    });
    vi.stubGlobal('fetch', vi.fn(async () => new Response(body)));
    await streamSse('/test-stream', {}, {});
    expect(cancel).toHaveBeenCalledOnce();
  });

  it('preserves a server error instead of replacing it with an EOF error', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('event: error\ndata: 模型不可用\n\n')));
    await expect(streamSse('/test-stream', {}, {})).rejects.toThrow('模型不可用');
  });

  it('shows the API explanation for an unsuccessful request', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: '请重新登录' }), { status: 401 })));
    await expect(streamSse('/test-stream', {}, {})).rejects.toThrow('请重新登录');
  });

  it('removes only the protocol space and preserves content indentation', async () => {
    const chunks: string[] = [];
    const body = 'data: top\ndata:   nested\n\ndata:  \n\nevent: done\ndata: \n\n';
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
