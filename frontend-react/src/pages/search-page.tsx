import { useEffect, useMemo, useState } from 'react';
import { ChevronLeft, Database, Globe2, Loader2 } from 'lucide-react';

import { OnlinePaperCard } from '@/components/online-paper-card';
import { PaginationBar } from '@/components/pagination-bar';
import { PaperCard } from '@/components/paper-card';
import { PaperReadFilterBar } from '@/components/paper-read-filter-bar';
import { SearchControls } from '@/components/search-controls';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { fetchOnlineSearchPapers, fetchSearchPapers } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import {
  applyCodeFilter,
  applyFilters,
  applyReadFilter,
  buildQueryString,
  navigate,
  parseCodeFilter,
  parseFilters,
  parsePage,
  parseReadFilter,
  useAppLocation,
} from '@/lib/router';
import type {
  OnlineSearchSort,
  PaperCodeFilter,
  PaperListResponse,
  PaperReadFilter,
  SearchFilters,
} from '@/types';

type SearchScope = 'local' | 'online';

const CURRENT_YEAR = new Date().getFullYear();
const DEFAULT_FROM_YEAR = CURRENT_YEAR - 4;
const YEAR_OPTIONS = Array.from({ length: 11 }, (_value, index) => CURRENT_YEAR - index);
const EMPTY_RESULTS: PaperListResponse = {
  papers: [],
  total: 0,
  page: 1,
  pages: 1,
};

function parseYear(value: string | null, fallback: number): number {
  if (!value) {
    return fallback;
  }
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed >= CURRENT_YEAR - 10 && parsed <= CURRENT_YEAR ? parsed : fallback;
}

function parseOnlineSort(value: string | null): OnlineSearchSort {
  return value === 'newest' || value === 'cited' ? value : 'relevance';
}

export function SearchPage() {
  const location = useAppLocation();
  const { user, isLoading: isAuthLoading } = useAuth();
  const { query, page, filters, readFilter, codeFilter, scope, fromYear, toYear, onlineSort } = useMemo(() => {
    const params = new URLSearchParams(location.search);
    const parsedFromYear = parseYear(params.get('from_year'), DEFAULT_FROM_YEAR);
    const parsedToYear = parseYear(params.get('to_year'), CURRENT_YEAR);
    return {
      query: params.get('q') ?? '',
      page: parsePage(params.get('page')),
      filters: parseFilters(params),
      readFilter: parseReadFilter(params.get('read')),
      codeFilter: parseCodeFilter(params.get('code')),
      scope: params.get('scope') === 'online' ? ('online' as const) : ('local' as const),
      fromYear: Math.min(parsedFromYear, parsedToYear),
      toYear: Math.max(parsedFromYear, parsedToYear),
      onlineSort: parseOnlineSort(params.get('sort')),
    };
  }, [location.search]);
  const [draftQuery, setDraftQuery] = useState(query);
  const [draftFilters, setDraftFilters] = useState<SearchFilters>(filters);
  const [results, setResults] = useState<PaperListResponse>(EMPTY_RESULTS);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshVersion, setRefreshVersion] = useState(0);

  useEffect(() => {
    setDraftQuery(query);
    setDraftFilters(filters);
  }, [query, filters]);

  useEffect(() => {
    if (isAuthLoading) {
      return;
    }
    if (!query.trim()) {
      setResults(EMPTY_RESULTS);
      setIsLoading(false);
      setError(null);
      return;
    }

    let active = true;
    setIsLoading(true);
    setError(null);
    const effectiveReadFilter = user ? readFilter : 'all';
    const request = scope === 'online'
      ? fetchOnlineSearchPapers(page, query, fromYear, toYear, onlineSort)
      : fetchSearchPapers(page, query, filters, effectiveReadFilter, codeFilter);

    void request
      .then((payload) => {
        if (active) {
          setResults(payload);
        }
      })
      .catch((requestError) => {
        if (active) {
          setError(requestError instanceof Error ? requestError.message : '加载失败');
          setResults(EMPTY_RESULTS);
        }
      })
      .finally(() => {
        if (active) {
          setIsLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, [codeFilter, filters, fromYear, isAuthLoading, onlineSort, page, query, readFilter, refreshVersion, scope, toYear, user]);

  const submitSearch = () => {
    const next = new URLSearchParams();
    const trimmedQuery = draftQuery.trim();
    if (scope === 'online') {
      next.set('scope', 'online');
      next.set('from_year', String(fromYear));
      next.set('to_year', String(toYear));
      next.set('sort', onlineSort);
    } else {
      applyFilters(next, draftFilters);
      applyReadFilter(next, user ? readFilter : 'all');
      applyCodeFilter(next, codeFilter);
    }
    if (trimmedQuery) {
      next.set('q', trimmedQuery);
    }
    navigate(`/search${buildQueryString(next)}`);
  };

  const onScopeChange = (nextScope: SearchScope) => {
    const next = new URLSearchParams();
    const trimmedQuery = draftQuery.trim();
    if (trimmedQuery) {
      next.set('q', trimmedQuery);
    }
    if (nextScope === 'online') {
      next.set('scope', 'online');
      next.set('from_year', String(fromYear));
      next.set('to_year', String(toYear));
      next.set('sort', onlineSort);
    } else {
      applyFilters(next, draftFilters);
      applyReadFilter(next, user ? readFilter : 'all');
      applyCodeFilter(next, codeFilter);
    }
    navigate(`/search${buildQueryString(next)}`);
  };

  const updateOnlineOptions = (
    nextFromYear: number,
    nextToYear: number,
    nextSort: OnlineSearchSort,
  ) => {
    const next = new URLSearchParams();
    if (query.trim()) {
      next.set('q', query.trim());
    }
    next.set('scope', 'online');
    next.set('from_year', String(Math.min(nextFromYear, nextToYear)));
    next.set('to_year', String(Math.max(nextFromYear, nextToYear)));
    next.set('sort', nextSort);
    navigate(`/search${buildQueryString(next)}`);
  };

  const onPageChange = (nextPage: number) => {
    const next = new URLSearchParams(location.search);
    next.set('page', String(nextPage));
    navigate(`/search${buildQueryString(next)}`);
  };

  const onReadFilterChange = (nextReadFilter: PaperReadFilter) => {
    const next = new URLSearchParams(location.search);
    applyReadFilter(next, nextReadFilter);
    next.delete('page');
    navigate(`/search${buildQueryString(next)}`);
  };

  const onCodeFilterChange = (nextCodeFilter: PaperCodeFilter) => {
    const next = new URLSearchParams(location.search);
    applyCodeFilter(next, nextCodeFilter);
    next.delete('page');
    navigate(`/search${buildQueryString(next)}`);
  };

  const activeReadFilter = user ? readFilter : 'all';
  const resultSummary = scope === 'online'
    ? `${fromYear}–${toYear} · ${results.total.toLocaleString('zh-CN')} 篇`
    : activeReadFilter === 'unread'
      ? `未读 ${results.total} 篇论文`
      : activeReadFilter === 'read'
        ? `已读 ${results.total} 篇论文`
        : `共 ${results.total} 篇论文`;

  return (
    <div className="mx-auto max-w-6xl animate-fade-in">
      <div className="mb-6 space-y-2">
        <Button variant="ghost" className="rounded-md px-0 text-[#728095]" onClick={() => navigate('/')}>
          <ChevronLeft className="mr-1 h-4 w-4" />
          返回首页
        </Button>
        <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h1 className="text-3xl font-semibold text-[#172033]">全局搜索</h1>
            <p className="mt-1 text-sm text-[#728095]">
              {scope === 'online' ? `${fromYear}–${toYear} · OpenAlex` : query ? `关键词: "${query}"` : '本地论文库'}
            </p>
          </div>
          <Tabs value={scope} onValueChange={(value) => onScopeChange(value as SearchScope)}>
            <TabsList className="h-10 bg-white shadow-sm ring-1 ring-black/5">
              <TabsTrigger value="local" className="px-4">
                <Database className="h-4 w-4" />
                本地论文库
              </TabsTrigger>
              <TabsTrigger value="online" className="px-4">
                <Globe2 className="h-4 w-4" />
                在线近年
              </TabsTrigger>
            </TabsList>
          </Tabs>
        </div>
      </div>

      <SearchControls
        query={draftQuery}
        filters={draftFilters}
        onQueryChange={setDraftQuery}
        onFiltersChange={setDraftFilters}
        onSubmit={submitSearch}
        placeholder={scope === 'online' ? '搜索近年论文...' : '搜索关键词... (Shift+Enter 搜索)'}
        compact
        showFilters={scope === 'local'}
      />

      {scope === 'online' ? (
        <div className="mt-4 flex flex-wrap items-end gap-4 border-y border-[#dfe6ee] py-3">
          <label className="space-y-1 text-xs font-medium text-[#64748b]">
            <span className="block">起始年份</span>
            <Select value={String(fromYear)} onValueChange={(value) => updateOnlineOptions(Number(value), toYear, onlineSort)}>
              <SelectTrigger className="w-32 bg-white">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {YEAR_OPTIONS.map((year) => <SelectItem key={year} value={String(year)}>{year}</SelectItem>)}
              </SelectContent>
            </Select>
          </label>
          <label className="space-y-1 text-xs font-medium text-[#64748b]">
            <span className="block">截止年份</span>
            <Select value={String(toYear)} onValueChange={(value) => updateOnlineOptions(fromYear, Number(value), onlineSort)}>
              <SelectTrigger className="w-32 bg-white">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {YEAR_OPTIONS.map((year) => <SelectItem key={year} value={String(year)}>{year}</SelectItem>)}
              </SelectContent>
            </Select>
          </label>
          <label className="space-y-1 text-xs font-medium text-[#64748b]">
            <span className="block">排序</span>
            <Select value={onlineSort} onValueChange={(value) => updateOnlineOptions(fromYear, toYear, value as OnlineSearchSort)}>
              <SelectTrigger className="w-36 bg-white">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="relevance">相关度</SelectItem>
                <SelectItem value="newest">最新发表</SelectItem>
                <SelectItem value="cited">高被引</SelectItem>
              </SelectContent>
            </Select>
          </label>
        </div>
      ) : null}

      {query ? (
        <>
          {scope === 'local' ? (
            <div className="mt-6">
              <PaperReadFilterBar
                value={activeReadFilter}
                counts={results.read_counts}
                codeValue={codeFilter}
                disabled={!user || isAuthLoading}
                onChange={onReadFilterChange}
                onCodeChange={onCodeFilterChange}
              />
            </div>
          ) : null}
          <div className="mt-4 border-y border-[#dfe6ee] py-3 text-sm text-[#596579]">
            {resultSummary}
          </div>
        </>
      ) : null}

      {!query ? (
        <div className="mt-8 rounded-lg bg-white/90 p-8 text-center text-[#728095] shadow-sm ring-1 ring-black/5">
          输入关键词后开始搜索。
        </div>
      ) : isLoading ? (
        <div className="mt-8 flex items-center justify-center gap-2 rounded-lg bg-white/90 p-8 text-[#728095] shadow-sm ring-1 ring-black/5">
          <Loader2 className="h-5 w-5 animate-spin" />
          {scope === 'online' ? '正在检索 OpenAlex...' : '加载论文中...'}
        </div>
      ) : error ? (
        <div className="mt-8 rounded-lg bg-white/90 p-8 text-center text-[#b91c1c] shadow-sm ring-1 ring-black/5">
          {error}
        </div>
      ) : results.papers.length === 0 ? (
        <div className="mt-8 rounded-lg bg-white/90 p-8 text-center text-[#728095] shadow-sm ring-1 ring-black/5">
          没有找到匹配结果
        </div>
      ) : (
        <div className="mt-8 space-y-4">
          {results.papers.map((paper, index) => scope === 'online' ? (
            <OnlinePaperCard key={paper.id} paper={paper} index={index} searchQuery={query} />
          ) : (
            <PaperCard
              key={paper.id}
              paper={paper}
              index={index}
              onOpen={(nextPaper) => navigate(`/papers/${nextPaper.id}`)}
              searchQuery={query}
              searchFilters={filters}
              onMarkChange={() => setRefreshVersion((version) => version + 1)}
            />
          ))}
        </div>
      )}

      <PaginationBar page={results.page} pages={results.pages} onPageChange={onPageChange} />
    </div>
  );
}
