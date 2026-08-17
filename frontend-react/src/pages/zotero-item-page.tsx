import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft, ExternalLink, FileText, Loader2, RefreshCw, Sparkles } from 'lucide-react';

import { ChatPanel } from '@/components/chat-panel';
import { ReasoningStreamPanel } from '@/components/reasoning-stream-panel';
import { RichContent } from '@/components/rich-content';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { fetchZoteroItem, streamSse, zoteroItemApiPath } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { navigate } from '@/lib/router';
import type { ZoteroItem } from '@/types';


interface ZoteroItemPageProps {
  itemKey: string;
}

function creators(item: ZoteroItem): string {
  return (item.creators ?? [])
    .map((creator) => creator.name || [creator.firstName, creator.lastName].filter(Boolean).join(' '))
    .filter(Boolean)
    .join('、') || '作者未知';
}

export function ZoteroItemPage({ itemKey }: ZoteroItemPageProps) {
  const { user, isLoading: isAuthLoading } = useAuth();
  const [item, setItem] = useState<ZoteroItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState('');
  const [reasoning, setReasoning] = useState('');
  const [analysisStatus, setAnalysisStatus] = useState('准备读取 Zotero 条目...');
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (isAuthLoading) {
      return;
    }
    if (!user) {
      navigate('/login');
      return;
    }
    let active = true;
    setLoading(true);
    void fetchZoteroItem(itemKey)
      .then((payload) => {
        if (active) {
          setItem(payload);
        }
      })
      .catch((nextError) => {
        if (active) {
          setError(nextError instanceof Error ? nextError.message : '条目加载失败');
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
  }, [isAuthLoading, itemKey, user]);

  const loadAnalysis = useCallback(async (reanalyze = false) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setAnalysis('');
    setReasoning('');
    setAnalysisError(null);
    setAnalyzing(true);
    setAnalysisStatus(reanalyze ? '正在重新生成深度阅读报告...' : '正在读取 Zotero 全文、笔记和批注...');
    try {
      await streamSse(
        zoteroItemApiPath(itemKey, `/analysis${reanalyze ? '?reanalyze=true' : ''}`),
        { method: 'GET', signal: controller.signal },
        {
          onChunk: (chunk) => setAnalysis((current) => current + chunk),
          onEvent: (event, data) => {
            if (event === 'status') {
              setAnalysisStatus(data);
            } else if (event === 'reasoning') {
              setReasoning((current) => current + data);
            } else if (event === 'final') {
              setAnalysis(data);
            } else if (event === 'error') {
              throw new Error(data || '深度阅读失败');
            } else if (event === 'done') {
              setAnalyzing(false);
              setReasoning('');
            }
          },
        },
      );
    } catch (nextError) {
      if (controller.signal.aborted) {
        return;
      }
      setAnalysisError(nextError instanceof Error ? nextError.message : '深度阅读失败');
      setAnalyzing(false);
    }
  }, [itemKey]);

  useEffect(() => {
    if (item) {
      void loadAnalysis(false);
    }
    return () => abortRef.current?.abort();
  }, [item, loadAnalysis]);

  if (loading || isAuthLoading) {
    return <div className="flex min-h-[40vh] items-center justify-center text-slate-500"><Loader2 className="mr-2 h-5 w-5 animate-spin" />正在加载条目...</div>;
  }
  if (error || !item) {
    return <Card className="mx-auto max-w-2xl"><CardContent className="p-8 text-center text-red-600">{error || '条目不存在'}</CardContent></Card>;
  }

  const notes = (item.children ?? []).filter((child) => child.note);
  const annotations = (item.children ?? []).filter((child) => child.annotation_text || child.annotation_comment);
  const attachments = (item.children ?? []).filter((child) => child.item_type === 'attachment');

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <Button variant="ghost" onClick={() => navigate('/zotero')}><ArrowLeft className="mr-2 h-4 w-4" />返回 Zotero 文库</Button>
      <section className="rounded-[2rem] border border-white/80 bg-white/90 p-6 shadow-[0_24px_80px_rgba(15,23,42,0.09)] sm:p-8">
        <div className="mb-4 flex flex-wrap gap-2">
          <Badge variant="secondary">{item.item_type}</Badge>
          {item.publication_title ? <Badge variant="outline">{item.publication_title}</Badge> : null}
          {item.item_date ? <Badge variant="outline">{item.item_date}</Badge> : null}
        </div>
        <h1 className="text-3xl font-bold leading-tight tracking-tight text-slate-950 sm:text-4xl">{item.title || '未命名条目'}</h1>
        <p className="mt-4 text-slate-600">{creators(item)}</p>
        {item.abstract_note ? <p className="mt-5 whitespace-pre-wrap text-sm leading-7 text-slate-600">{item.abstract_note}</p> : null}
        <div className="mt-5 flex flex-wrap gap-2">
          {item.tags?.map((tag) => <Badge key={tag} variant="secondary">{tag}</Badge>)}
        </div>
        <div className="mt-6 flex flex-wrap gap-3 text-sm">
          {item.url ? <a href={item.url} target="_blank" rel="noreferrer" className="inline-flex items-center font-medium text-blue-600 hover:underline">原始链接 <ExternalLink className="ml-1 h-4 w-4" /></a> : null}
          {item.doi ? <span className="text-slate-500">DOI: {item.doi}</span> : null}
          <span className="text-slate-500">PDF 附件 {attachments.length} 个</span>
        </div>
      </section>

      <Card className="border-white/80 bg-white/92 shadow-lg">
        <CardHeader className="flex-row items-center justify-between gap-4">
          <div>
            <CardTitle className="flex items-center"><Sparkles className="mr-2 h-5 w-5 text-amber-500" />深度阅读报告</CardTitle>
            {analyzing ? <p className="mt-2 text-sm text-slate-500">{analysisStatus}</p> : null}
          </div>
          <Button variant="outline" disabled={analyzing} onClick={() => void loadAnalysis(true)}><RefreshCw className={`mr-2 h-4 w-4 ${analyzing ? 'animate-spin' : ''}`} />重新分析</Button>
        </CardHeader>
        <CardContent>
          {reasoning ? <ReasoningStreamPanel reasoning={reasoning} /> : null}
          {analysis ? <RichContent content={analysis} /> : null}
          {analyzing && !analysis ? <div className="flex min-h-40 items-center justify-center text-slate-500"><Loader2 className="mr-2 h-5 w-5 animate-spin" />{analysisStatus}</div> : null}
          {analysisError ? <p className="rounded-xl bg-red-50 p-4 text-sm text-red-700">{analysisError}</p> : null}
        </CardContent>
      </Card>

      {(notes.length || annotations.length) ? (
        <div className="grid gap-6 lg:grid-cols-2">
          <Card className="border-white/80 bg-white/85">
            <CardHeader><CardTitle className="flex items-center text-lg"><FileText className="mr-2 h-5 w-5" />Zotero 笔记</CardTitle></CardHeader>
            <CardContent className="space-y-4">{notes.length ? notes.map((note) => <div key={note.item_key} className="whitespace-pre-wrap rounded-xl bg-slate-50 p-4 text-sm leading-6 text-slate-700">{note.note}</div>) : <p className="text-sm text-slate-500">暂无笔记</p>}</CardContent>
          </Card>
          <Card className="border-white/80 bg-white/85">
            <CardHeader><CardTitle className="text-lg">PDF 批注</CardTitle></CardHeader>
            <CardContent className="space-y-4">{annotations.length ? annotations.map((annotation) => <div key={annotation.item_key} className="rounded-xl border border-amber-100 bg-amber-50/60 p-4 text-sm leading-6 text-slate-700"><p className="whitespace-pre-wrap">{annotation.annotation_text}</p>{annotation.annotation_comment ? <p className="mt-2 whitespace-pre-wrap text-slate-500">{annotation.annotation_comment}</p> : null}</div>) : <p className="text-sm text-slate-500">暂无批注</p>}</CardContent>
          </Card>
        </div>
      ) : null}

      <ChatPanel key={itemKey} zoteroItemKey={itemKey} />
    </div>
  );
}
