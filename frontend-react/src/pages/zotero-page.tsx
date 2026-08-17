import { useCallback, useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import {
  BookOpen,
  CheckCircle2,
  ExternalLink,
  KeyRound,
  Library,
  Loader2,
  RefreshCw,
  Search,
  Trash2,
} from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import {
  deleteZoteroConnection,
  fetchZoteroCollections,
  fetchZoteroConnection,
  fetchZoteroItems,
  saveZoteroConnection,
  startZoteroSync,
} from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { navigate } from '@/lib/router';
import type { ZoteroCollection, ZoteroConnection, ZoteroItem } from '@/types';


function creatorNames(item: ZoteroItem): string {
  const names = (item.creators ?? [])
    .map((creator) => creator.name || [creator.firstName, creator.lastName].filter(Boolean).join(' '))
    .filter(Boolean);
  return names.slice(0, 4).join('、') || '作者未知';
}

export function ZoteroPage() {
  const { user, isLoading: isAuthLoading } = useAuth();
  const [connection, setConnection] = useState<ZoteroConnection | null>(null);
  const [collections, setCollections] = useState<ZoteroCollection[]>([]);
  const [items, setItems] = useState<ZoteroItem[]>([]);
  const [apiKey, setApiKey] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [collectionKey, setCollectionKey] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadLibrary = useCallback(async () => {
    const [nextCollections, nextItems] = await Promise.all([
      fetchZoteroCollections(),
      fetchZoteroItems(page, search, collectionKey),
    ]);
    setCollections(nextCollections);
    setItems(nextItems.items);
    setPages(nextItems.pages);
    setTotal(nextItems.total);
  }, [collectionKey, page, search]);

  const refreshConnection = useCallback(async () => {
    const next = await fetchZoteroConnection();
    setConnection(next);
    setSyncing(next.sync_status === 'running');
    return next;
  }, []);

  useEffect(() => {
    if (isAuthLoading) {
      return;
    }
    if (!user) {
      setLoading(false);
      return;
    }
    let active = true;
    setLoading(true);
    void refreshConnection()
      .then(async (next) => {
        if (active && next.configured) {
          await loadLibrary();
        }
      })
      .catch((nextError) => {
        if (active) {
          setError(nextError instanceof Error ? nextError.message : 'Zotero 加载失败');
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [isAuthLoading, loadLibrary, refreshConnection, user]);

  useEffect(() => {
    if (!user || connection?.sync_status !== 'running') {
      return;
    }
    let active = true;
    const timer = window.setInterval(() => {
      void refreshConnection()
        .then(async (next) => {
          if (active && next.sync_status !== 'running') {
            window.clearInterval(timer);
            if (next.sync_status === 'idle') {
              await loadLibrary();
            }
          }
        })
        .catch(() => undefined);
    }, 2000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [connection?.sync_status, loadLibrary, refreshConnection, user]);

  const connect = async (event: FormEvent) => {
    event.preventDefault();
    if (!apiKey.trim() || saving) {
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const next = await saveZoteroConnection(apiKey.trim());
      setConnection(next);
      setApiKey('');
      await startZoteroSync();
      setConnection({ ...next, sync_status: 'running' });
      setSyncing(true);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : 'Zotero 连接失败');
    } finally {
      setSaving(false);
    }
  };

  const sync = async () => {
    setError(null);
    setSyncing(true);
    try {
      await startZoteroSync();
      setConnection((current) => current ? { ...current, sync_status: 'running' } : current);
    } catch (nextError) {
      setSyncing(false);
      setError(nextError instanceof Error ? nextError.message : '同步启动失败');
    }
  };

  const disconnect = async () => {
    if (!window.confirm('断开后会删除服务器上的 Zotero 元数据、分析记录、对话和正文缓存。继续吗？')) {
      return;
    }
    setError(null);
    try {
      await deleteZoteroConnection();
      setConnection({ configured: false, credential_encryption_configured: true, sync_status: 'idle' });
      setCollections([]);
      setItems([]);
      setTotal(0);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : '断开 Zotero 失败');
    }
  };

  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    setPage(1);
    setSearch(searchInput.trim());
  };

  if (!isAuthLoading && !user) {
    return (
      <Card className="mx-auto max-w-2xl border-white/80 bg-white/90 shadow-xl">
        <CardHeader>
          <CardTitle>登录后连接 Zotero</CardTitle>
          <CardDescription>你的文库、笔记、批注和阅读对话只会绑定到当前账号。</CardDescription>
        </CardHeader>
        <CardContent>
          <Button onClick={() => navigate('/login')}>前往登录</Button>
        </CardContent>
      </Card>
    );
  }

  if (loading || !connection) {
    return <div className="flex min-h-[40vh] items-center justify-center text-slate-500"><Loader2 className="mr-2 h-5 w-5 animate-spin" />正在加载 Zotero 文库...</div>;
  }

  if (!connection.configured) {
    return (
      <div className="mx-auto grid max-w-5xl gap-6 lg:grid-cols-[1.1fr_0.9fr]">
        <Card className="border-white/80 bg-white/92 shadow-[0_24px_80px_rgba(15,23,42,0.1)]">
          <CardHeader>
            <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-red-50 text-red-600"><KeyRound /></div>
            <CardTitle className="text-2xl">连接你的 Zotero 文库</CardTitle>
            <CardDescription>使用只读 API Key 同步条目元数据，打开论文时再按需读取全文。</CardDescription>
          </CardHeader>
          <CardContent>
            <form className="space-y-4" onSubmit={connect}>
              <Input
                type="password"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder="粘贴 Zotero API Key"
                autoComplete="off"
              />
              {error ? <p className="text-sm text-red-600">{error}</p> : null}
              {!connection.credential_encryption_configured ? (
                <p className="rounded-xl bg-amber-50 p-3 text-sm text-amber-800">服务器管理员需要先配置凭据加密密钥。</p>
              ) : null}
              <Button className="w-full" disabled={saving || !connection.credential_encryption_configured}>
                {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <KeyRound className="mr-2 h-4 w-4" />}
                验证并连接
              </Button>
            </form>
          </CardContent>
        </Card>
        <Card className="border-white/80 bg-white/75">
          <CardHeader><CardTitle>如何创建安全的 Key</CardTitle></CardHeader>
          <CardContent className="space-y-4 text-sm leading-6 text-slate-600">
            <p>在 Zotero 的 Keys 页面创建新 Key，仅勾选“允许读取文库”。Paper Insight 不需要写权限。</p>
            <a className="inline-flex items-center font-medium text-blue-600 hover:underline" href="https://www.zotero.org/settings/keys/new" target="_blank" rel="noreferrer">
              打开 Zotero Keys 页面 <ExternalLink className="ml-1 h-4 w-4" />
            </a>
            <p>Key 会使用服务器配置的独立密钥加密后存入 PostgreSQL，接口不会把 Key 返回到浏览器。</p>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <section className="rounded-[2rem] border border-white/80 bg-white/88 p-6 shadow-[0_24px_80px_rgba(15,23,42,0.09)] backdrop-blur-xl sm:p-8">
        <div className="flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-red-600"><Library className="h-4 w-4" />私人文库</div>
            <h1 className="text-3xl font-bold tracking-tight text-slate-900">{connection.display_name || connection.username || 'Zotero Library'}</h1>
            <p className="mt-2 text-sm text-slate-500">{total} 个论文条目 · 文库版本 {connection.library_version ?? 0}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => void sync()} disabled={syncing}>
              {syncing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
              {syncing ? '同步中' : '增量同步'}
            </Button>
            <Button variant="outline" className="text-red-600" onClick={() => void disconnect()}><Trash2 className="mr-2 h-4 w-4" />断开</Button>
          </div>
        </div>
        {connection.last_sync_at ? <p className="mt-4 text-xs text-slate-500">上次同步：{new Date(connection.last_sync_at).toLocaleString()}</p> : null}
        {connection.last_sync_error ? <p className="mt-3 rounded-xl bg-red-50 p-3 text-sm text-red-700">{connection.last_sync_error}</p> : null}
        {error ? <p className="mt-3 rounded-xl bg-red-50 p-3 text-sm text-red-700">{error}</p> : null}
      </section>

      <div className="grid gap-6 lg:grid-cols-[260px_minmax(0,1fr)]">
        <Card className="h-fit border-white/80 bg-white/82">
          <CardHeader><CardTitle className="text-base">分类</CardTitle></CardHeader>
          <CardContent className="space-y-1">
            <Button variant={collectionKey === null ? 'secondary' : 'ghost'} className="w-full justify-start" onClick={() => { setCollectionKey(null); setPage(1); }}>全部条目</Button>
            {collections.map((collection) => (
              <Button key={collection.collection_key} variant={collectionKey === collection.collection_key ? 'secondary' : 'ghost'} className="w-full justify-start truncate" onClick={() => { setCollectionKey(collection.collection_key); setPage(1); }}>
                {collection.name}
              </Button>
            ))}
          </CardContent>
        </Card>

        <div className="space-y-4">
          <form className="flex gap-2" onSubmit={submitSearch}>
            <Input value={searchInput} onChange={(event) => setSearchInput(event.target.value)} placeholder="搜索标题、摘要、DOI 或期刊/会议" className="bg-white/90" />
            <Button type="submit"><Search className="mr-2 h-4 w-4" />搜索</Button>
          </form>
          {items.length ? items.map((item) => (
            <button key={item.item_key} type="button" onClick={() => navigate(`/zotero/items/${encodeURIComponent(item.item_key)}`)} className="block w-full rounded-2xl border border-white/80 bg-white/90 p-5 text-left shadow-sm transition hover:-translate-y-0.5 hover:shadow-lg">
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <h2 className="text-lg font-semibold text-slate-900">{item.title || '未命名条目'}</h2>
                  <p className="mt-1 text-sm text-slate-500">{creatorNames(item)}</p>
                </div>
                {item.analyzed ? <Badge className="shrink-0 bg-emerald-50 text-emerald-700"><CheckCircle2 className="mr-1 h-3 w-3" />已深读</Badge> : null}
              </div>
              <p className="mt-3 line-clamp-2 text-sm leading-6 text-slate-600">{item.abstract_note || '暂无摘要'}</p>
              <div className="mt-4 flex flex-wrap gap-2 text-xs text-slate-500">
                {item.publication_title ? <Badge variant="outline">{item.publication_title}</Badge> : null}
                {item.item_date ? <span>{item.item_date}</span> : null}
                {item.tags?.slice(0, 4).map((tag) => <Badge key={tag} variant="secondary">{tag}</Badge>)}
              </div>
            </button>
          )) : (
            <Card className="border-dashed bg-white/70"><CardContent className="flex min-h-52 flex-col items-center justify-center text-slate-500"><BookOpen className="mb-3 h-9 w-9" /><p>{syncing ? '正在同步 Zotero 文库...' : '没有找到符合条件的条目'}</p></CardContent></Card>
          )}
          {pages > 1 ? (
            <div className="flex items-center justify-center gap-3 pt-2">
              <Button variant="outline" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>上一页</Button>
              <span className="text-sm text-slate-500">{page} / {pages}</span>
              <Button variant="outline" disabled={page >= pages} onClick={() => setPage((value) => Math.min(pages, value + 1))}>下一页</Button>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
