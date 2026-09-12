import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from './api';
import { migrateLegacyLocalData, resolveAuthSession } from './auth-session';

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('session recovery', () => {
  it('treats only 401 as a logged-out session', async () => {
    const fetchMock = vi.fn(async () => new Response('{"detail":"expired"}', { status: 401 }));
    vi.stubGlobal('fetch', fetchMock);
    expect(await resolveAuthSession()).toBeNull();
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it('recovers one transient network failure', async () => {
    const user = { id: 'user-1' };
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError('network error'))
      .mockResolvedValueOnce(new Response(JSON.stringify({ user })));
    vi.stubGlobal('fetch', fetchMock);
    expect(await resolveAuthSession()).toEqual(user);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('keeps outage distinct from an expired login', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"unavailable"}', { status: 502 })));
    await expect(resolveAuthSession()).rejects.toMatchObject({ status: 502 });
  });

  it('preserves HTTP status on API errors', () => {
    expect(new ApiError('unavailable', 503)).toMatchObject({ message: 'unavailable', status: 503 });
  });

  it('does not let blocked browser storage break login', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.stubGlobal('window', { get localStorage() { throw new Error('storage disabled'); } });
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    await expect(migrateLegacyLocalData()).resolves.toBeUndefined();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('preserves local marks when migration fails', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    const removeItem = vi.fn();
    vi.stubGlobal('window', { localStorage: {
      getItem: (key: string) => key === 'paper_user_id' ? 'anonymous-1' : '{"p1":{"liked":true}}',
      removeItem,
    } });
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 500 })));
    await migrateLegacyLocalData();
    expect(removeItem).not.toHaveBeenCalled();
  });
});
