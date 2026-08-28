import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft, Cpu, ExternalLink, FileText, Images, Loader2, RefreshCw, Sparkles, Tags, UploadCloud } from 'lucide-react';

import { ChatPanel } from '@/components/chat-panel';
import { ReasoningStreamPanel } from '@/components/reasoning-stream-panel';
import { RichContent } from '@/components/rich-content';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  fetchSelectableLlmModels,
  fetchZoteroConnection,
  fetchZoteroItem,
  generateZoteroEnrichment,
  streamSse,
  writebackZoteroEnrichment,
  zoteroItemApiPath,
} from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { navigate } from '@/lib/router';
import type {
  SelectableLlmCatalog,
  SelectableLlmProvider,
  ZoteroAnalysisEnrichment,
  ZoteroAnalysisFigure,
  ZoteroConnection,
  ZoteroItem,
} from '@/types';


interface ZoteroItemPageProps {
  itemKey: string;
}

function creators(item: ZoteroItem): string {
  return (item.creators ?? [])
    .map((creator) => creator.name || [creator.firstName, creator.lastName].filter(Boolean).join(' '))
    .filter(Boolean)
    .join('、') || '作者未知';
}

const MODEL_SELECTION_SEPARATOR = '::';

function modelSelectionValue(providerId: string, modelName: string): string {
  return `${providerId}${MODEL_SELECTION_SEPARATOR}${encodeURIComponent(modelName)}`;
}

function parseModelSelection(value: string): { provider_id: string; model_name: string } | null {
  const separatorIndex = value.indexOf(MODEL_SELECTION_SEPARATOR);
  if (separatorIndex <= 0) {
    return null;
  }
  const providerId = value.slice(0, separatorIndex);
  const encodedModelName = value.slice(separatorIndex + MODEL_SELECTION_SEPARATOR.length);
  try {
    const modelName = decodeURIComponent(encodedModelName);
    return providerId && modelName ? { provider_id: providerId, model_name: modelName } : null;
  } catch {
    return null;
  }
}

function catalogHasSelection(catalog: SelectableLlmCatalog, value: string): boolean {
  const selection = parseModelSelection(value);
  return Boolean(selection && catalog.providers.some(
    (provider) => provider.id === selection.provider_id
      && provider.models.some((model) => model.model_name === selection.model_name),
  ));
}

function sourceLabel(source?: string | null): string {
  if (!source) {
    return '旧报告未记录材料来源';
  }
  if (source === 'metadata') {
    return '仅条目元数据与摘要';
  }
  if (source === 'zotero-fulltext') {
    return 'Zotero 已索引全文';
  }
  if (source === 'attachment-pdf') {
    return 'Zotero 云端 PDF 全文';
  }
  if (source === 'cache') {
    return '已缓存的论文全文';
  }
  if (source.startsWith('public-document:')) {
    return `公开全文 · ${source.slice('public-document:'.length)}`;
  }
  return source;
}

function providerModelLabel(provider: SelectableLlmProvider, modelName: string): string {
  const model = provider.models.find((entry) => entry.model_name === modelName);
  return model?.display_name || modelName;
}

export function ZoteroItemPage({ itemKey }: ZoteroItemPageProps) {
  const { user, isLoading: isAuthLoading } = useAuth();
  const [item, setItem] = useState<ZoteroItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState('');
  const [analysisFigures, setAnalysisFigures] = useState<ZoteroAnalysisFigure[]>([]);
  const [analysisEnrichment, setAnalysisEnrichment] = useState<ZoteroAnalysisEnrichment>({});
  const [connection, setConnection] = useState<ZoteroConnection | null>(null);
  const [enrichmentBusy, setEnrichmentBusy] = useState(false);
  const [enrichmentError, setEnrichmentError] = useState<string | null>(null);
  const [reasoning, setReasoning] = useState('');
  const [analysisStatus, setAnalysisStatus] = useState('');
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [analysisSource, setAnalysisSource] = useState<string | null>(null);
  const [analysisWarning, setAnalysisWarning] = useState<string | null>(null);
  const [analysisProviderName, setAnalysisProviderName] = useState<string | null>(null);
  const [analysisModelName, setAnalysisModelName] = useState<string | null>(null);
  const [modelCatalog, setModelCatalog] = useState<SelectableLlmCatalog | null>(null);
  const [modelCatalogReady, setModelCatalogReady] = useState(false);
  const [modelCatalogError, setModelCatalogError] = useState<string | null>(null);
  const [selectedModel, setSelectedModel] = useState('');
  const abortRef = useRef<AbortController | null>(null);
  const selectedModelRef = useRef('');

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
    void Promise.all([fetchZoteroItem(itemKey), fetchZoteroConnection()])
      .then(([payload, connectionPayload]) => {
        if (active) {
          setItem(payload);
          setAnalysisFigures(payload.analysis_figures ?? []);
          setAnalysisEnrichment(payload.analysis_enrichment ?? {});
          setAnalysisSource(payload.analysis_source ?? null);
          setAnalysisWarning(payload.analysis_warning ?? null);
          setAnalysisProviderName(payload.analysis_provider_name ?? null);
          setAnalysisModelName(payload.analysis_model_name ?? null);
          setConnection(connectionPayload);
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

  useEffect(() => {
    if (isAuthLoading || !user) {
      return;
    }
    let active = true;
    setModelCatalogReady(false);
    setModelCatalogError(null);
    void fetchSelectableLlmModels(true)
      .then((payload) => {
        if (active) {
          setModelCatalog(payload);
        }
      })
      .catch((nextError) => {
        if (active) {
          setModelCatalogError(nextError instanceof Error ? nextError.message : '模型列表加载失败');
        }
      })
      .finally(() => {
        if (active) {
          setModelCatalogReady(true);
        }
      });
    return () => {
      active = false;
    };
  }, [isAuthLoading, user]);

  useEffect(() => {
    if (!item || !modelCatalog) {
      return;
    }
    setSelectedModel((current) => {
      if (current && catalogHasSelection(modelCatalog, current)) {
        return current;
      }
      const reportSelection = item.analysis_provider_id && item.analysis_model_name
        ? modelSelectionValue(item.analysis_provider_id, item.analysis_model_name)
        : '';
      if (reportSelection && catalogHasSelection(modelCatalog, reportSelection)) {
        return reportSelection;
      }
      const activeSelection = modelCatalog.active_provider_id && modelCatalog.active_model_name
        ? modelSelectionValue(modelCatalog.active_provider_id, modelCatalog.active_model_name)
        : '';
      if (activeSelection && catalogHasSelection(modelCatalog, activeSelection)) {
        return activeSelection;
      }
      const firstProvider = modelCatalog.providers.find((provider) => provider.models.length > 0);
      const firstModel = firstProvider?.models[0];
      return firstProvider && firstModel
        ? modelSelectionValue(firstProvider.id, firstModel.model_name)
        : '';
    });
  }, [item, modelCatalog]);

  useEffect(() => {
    selectedModelRef.current = selectedModel;
  }, [selectedModel]);

  const loadAnalysis = useCallback(async (reanalyze = false) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setAnalysis('');
    setReasoning('');
    setAnalysisError(null);
    setAnalyzing(true);
    if (reanalyze) {
      setAnalysisSource(null);
      setAnalysisWarning(null);
      setAnalysisProviderName(null);
      setAnalysisModelName(null);
    }
    setAnalysisStatus(reanalyze ? '正在重新生成深度阅读报告...' : '正在读取 Zotero 全文、笔记和批注...');
    try {
      const params = new URLSearchParams();
      if (reanalyze) {
        params.set('reanalyze', 'true');
      }
      const selection = parseModelSelection(selectedModelRef.current);
      if (selection) {
        params.set('provider_id', selection.provider_id);
        params.set('model_name', selection.model_name);
      }
      const query = params.toString();
      await streamSse(
        zoteroItemApiPath(itemKey, `/analysis${query ? `?${query}` : ''}`),
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
            } else if (event === 'source') {
              setAnalysisSource(data || null);
            } else if (event === 'analysis-meta') {
              try {
                const metadata = JSON.parse(data) as {
                  source?: string | null;
                  warning?: string | null;
                  provider_name?: string | null;
                  model_name?: string | null;
                };
                setAnalysisSource(metadata.source ?? null);
                setAnalysisWarning(metadata.warning ?? null);
                setAnalysisProviderName(metadata.provider_name ?? null);
                setAnalysisModelName(metadata.model_name ?? null);
              } catch {
                // Keep the source event as a best-effort fallback.
              }
            } else if (event === 'figures') {
              try {
                const figures = JSON.parse(data) as ZoteroAnalysisFigure[];
                setAnalysisFigures(Array.isArray(figures) ? figures : []);
              } catch {
                setAnalysisFigures([]);
              }
            } else if (event === 'enrichment') {
              try {
                const enrichment = JSON.parse(data) as ZoteroAnalysisEnrichment;
                setAnalysisEnrichment(enrichment ?? {});
              } catch {
                setAnalysisEnrichment({});
              }
            } else if (event === 'error') {
              throw new Error(data || '深度阅读失败');
            } else if (event === 'done') {
              setAnalyzing(false);
              setReasoning('');
              setAnalysisStatus('');
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
      setAnalysisStatus('');
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
    }
  }, [itemKey]);

  useEffect(() => {
    if (!item) {
      return undefined;
    }

    if (item.llm_response?.trim()) {
      abortRef.current?.abort();
      abortRef.current = null;
      setAnalysis(item.llm_response);
      setReasoning('');
      setAnalysisError(null);
      setAnalyzing(false);
      setAnalysisStatus('');
      return undefined;
    }

    if (!modelCatalogReady) {
      return undefined;
    }
    void loadAnalysis(false);
    return () => abortRef.current?.abort();
  }, [item, loadAnalysis, modelCatalogReady]);

  const generateEnrichment = useCallback(async () => {
    setEnrichmentBusy(true);
    setEnrichmentError(null);
    try {
      setAnalysisEnrichment(await generateZoteroEnrichment(
        itemKey,
        parseModelSelection(selectedModelRef.current) ?? undefined,
      ));
    } catch (nextError) {
      setEnrichmentError(nextError instanceof Error ? nextError.message : '笔记与标签生成失败');
    } finally {
      setEnrichmentBusy(false);
    }
  }, [itemKey]);

  const writebackEnrichment = useCallback(async () => {
    setEnrichmentBusy(true);
    setEnrichmentError(null);
    try {
      const result = await writebackZoteroEnrichment(itemKey);
      setAnalysisEnrichment(result.analysis_enrichment);
      setItem((current) => current ? { ...current, tags: result.tags } : current);
    } catch (nextError) {
      setEnrichmentError(nextError instanceof Error ? nextError.message : '写回 Zotero 失败');
    } finally {
      setEnrichmentBusy(false);
    }
  }, [itemKey]);

  if (loading || isAuthLoading) {
    return <div className="flex min-h-[40vh] items-center justify-center text-slate-500"><Loader2 className="mr-2 h-5 w-5 animate-spin" />正在加载条目...</div>;
  }
  if (error || !item) {
    return <Card className="mx-auto max-w-2xl"><CardContent className="p-8 text-center text-red-600">{error || '条目不存在'}</CardContent></Card>;
  }

  const notes = (item.children ?? []).filter((child) => child.note);
  const annotations = (item.children ?? []).filter((child) => child.annotation_text || child.annotation_comment);
  const attachments = (item.children ?? []).filter((child) => child.item_type === 'attachment');
  const reportSourceLabel = analyzing && !analysisSource
    ? '正在确认全文来源...'
    : sourceLabel(analysisSource);
  const sourceNeedsAttention = analysisSource === 'metadata' || (!analysisSource && Boolean(analysis) && !analyzing);

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

      <section className="rounded-[32px] bg-white p-6 shadow-sm ring-1 ring-black/5 sm:p-8">
        <div className="flex flex-col gap-3 border-b border-[#eef2f7] pb-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex shrink-0 items-center gap-2">
            <Sparkles className="h-5 w-5 text-[#ff9900]" />
            <div>
              <h2 className="whitespace-nowrap text-xl font-semibold text-[#172033]">AI 分析</h2>
              {analysisStatus ? <p className="text-sm text-[#728095]">{analysisStatus}</p> : null}
            </div>
          </div>
          <div className="flex max-w-full flex-wrap items-center gap-2">
            <Select
              value={selectedModel}
              onValueChange={setSelectedModel}
              disabled={analyzing || !modelCatalog?.providers.some((provider) => provider.models.length > 0)}
            >
              <SelectTrigger className="h-10 w-full max-w-[22rem] rounded-full border-[#fed7aa] bg-gradient-to-r from-[#fff7ed] via-white to-[#eef6ff] text-[#243047] sm:w-[22rem]">
                <Cpu className="h-4 w-4 shrink-0 text-[#f08300]" />
                <SelectValue placeholder={modelCatalogReady ? '没有可用模型' : '正在读取模型...'} />
              </SelectTrigger>
              <SelectContent position="popper" align="end" className="max-h-[24rem] min-w-[22rem]">
                {modelCatalog?.providers.map((provider) => (
                  <SelectGroup key={provider.id}>
                    <SelectLabel>
                      {provider.name}{provider.is_active ? ' · 默认供应商' : ''}
                    </SelectLabel>
                    {provider.models.map((model) => (
                      <SelectItem
                        key={model.id}
                        value={modelSelectionValue(provider.id, model.model_name)}
                      >
                        {providerModelLabel(provider, model.model_name)}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                ))}
              </SelectContent>
            </Select>
            <Button
              variant="outline"
              className="rounded-full"
              disabled={analyzing}
              onClick={() => void loadAnalysis(true)}
            >
              <RefreshCw className={`mr-2 h-4 w-4 ${analyzing ? 'animate-spin' : ''}`} />
              重新分析
            </Button>
          </div>
        </div>

        {modelCatalogError ? (
          <p className="mt-4 rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-800">
            模型列表刷新失败：{modelCatalogError}。仍可使用服务器当前默认模型进行分析。
          </p>
        ) : null}

        {(analysis || analyzing) ? (
          <div
            className={`mt-5 rounded-2xl border px-4 py-3 text-sm leading-6 ${
              sourceNeedsAttention
                ? 'border-amber-200 bg-amber-50 text-amber-900'
                : 'border-emerald-100 bg-emerald-50/70 text-emerald-900'
            }`}
          >
            <div className="flex flex-wrap gap-x-5 gap-y-1">
              <span><strong>分析材料：</strong>{reportSourceLabel}</span>
              {analysisModelName ? (
                <span>
                  <strong>报告模型：</strong>
                  {analysisProviderName ? `${analysisProviderName} / ` : ''}{analysisModelName}
                </span>
              ) : analysis && !analyzing ? (
                <span><strong>报告模型：</strong>旧报告未记录</span>
              ) : null}
            </div>
            {analysisWarning ? <p className="mt-1">{analysisWarning}</p> : null}
            {!analysisSource && analysis && !analyzing ? (
              <p className="mt-1">这通常是全文读取与来源记录功能上线前保存的旧结果；请选择模型后点“重新分析”即可升级。</p>
            ) : null}
          </div>
        ) : null}

        {analyzing && !analysis && !reasoning ? (
          <div className="mt-6 flex min-h-40 items-center justify-center gap-2 text-[#728095]">
            <Loader2 className="h-5 w-5 animate-spin" />
            {analysisStatus || '正在分析论文...'}
          </div>
        ) : analysisError ? (
          <div className="mt-6 rounded-2xl bg-[#fff1f2] p-4 text-[#b91c1c]">{analysisError}</div>
        ) : (
          <div className="mt-6 space-y-4">
            <ReasoningStreamPanel reasoning={analyzing ? reasoning : ''} />
            {analysisFigures.map((figure) => (
              <figure
                key={figure.id}
                className="overflow-hidden rounded-2xl border border-[#e8edf4] bg-[#f8fafc]"
              >
                <div className="flex items-center gap-2 border-b border-[#e8edf4] bg-white px-4 py-3 text-sm font-medium text-[#334155]">
                  <Images className="h-4 w-4 text-[#ff9900]" />
                  论文框架图 · {figure.label}
                </div>
                <a href={figure.url} target="_blank" rel="noreferrer" className="block bg-white p-3 sm:p-5">
                  <img
                    src={figure.url}
                    alt={figure.caption || `${item.title || '论文'}框架图`}
                    className="mx-auto max-h-[42rem] w-auto max-w-full rounded-lg object-contain"
                    loading="lazy"
                  />
                </a>
                <figcaption className="space-y-2 px-4 py-3 text-sm leading-6 text-[#64748b]">
                  <p>{figure.caption}</p>
                  <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-[#94a3b8]">
                    <span>来源：{figure.source}</span>
                    {figure.page_number ? <span>PDF 第 {figure.page_number} 页</span> : null}
                    {figure.source_url ? (
                      <a href={figure.source_url} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline">
                        查看原图 <ExternalLink className="ml-1 inline h-3 w-3" />
                      </a>
                    ) : null}
                  </div>
                </figcaption>
              </figure>
            ))}
            {analysis ? (
              <RichContent
                content={analysis}
                analysisMode
                isStreaming={analyzing}
                className="markdown-body analysis-markdown text-base leading-7 text-[#334155]"
              />
            ) : null}
          </div>
        )}
      </section>

      <section className="rounded-[32px] bg-white p-6 shadow-sm ring-1 ring-black/5 sm:p-8">
        <div className="flex flex-col gap-3 border-b border-[#eef2f7] pb-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-2">
            <Tags className="h-5 w-5 text-[#ff9900]" />
            <div>
              <h2 className="text-xl font-semibold text-[#172033]">Zotero 笔记与标签</h2>
              <p className="text-sm text-[#728095]">使用上方所选模型生成建议，写回时保留你的原笔记与原标签</p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" disabled={enrichmentBusy || !analysis} onClick={() => void generateEnrichment()}>
              {enrichmentBusy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Sparkles className="mr-2 h-4 w-4" />}
              {analysisEnrichment.note_markdown ? '重新生成建议' : '生成笔记与标签'}
            </Button>
            <Button
              disabled={enrichmentBusy || !analysisEnrichment.note_markdown || !connection?.can_write}
              onClick={() => void writebackEnrichment()}
            >
              {enrichmentBusy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <UploadCloud className="mr-2 h-4 w-4" />}
              {analysisEnrichment.writeback?.status === 'applied' ? '更新到 Zotero' : '写回 Zotero'}
            </Button>
          </div>
        </div>
        {!connection?.can_write ? (
          <p className="mt-4 rounded-xl bg-amber-50 p-3 text-sm leading-6 text-amber-800">
            当前 Zotero API Key 只有读取权限。建议内容可以正常生成；如需写回，请在 Zotero Keys 页面开启文库写入权限后重新连接。
          </p>
        ) : null}
        {enrichmentError ? <p className="mt-4 rounded-xl bg-red-50 p-3 text-sm text-red-700">{enrichmentError}</p> : null}
        {analysisEnrichment.note_markdown ? (
          <div className="mt-5 grid gap-5 lg:grid-cols-[minmax(0,1fr)_18rem]">
            <div className="rounded-2xl border border-slate-200 bg-slate-50/70 p-5">
              <RichContent
                content={analysisEnrichment.note_markdown}
                className="markdown-body text-sm leading-7 text-slate-700"
              />
            </div>
            <div className="rounded-2xl border border-slate-200 p-5">
              <h3 className="font-semibold text-slate-800">建议新增标签</h3>
              <div className="mt-4 flex flex-wrap gap-2">
                {(analysisEnrichment.tags ?? []).map((tag) => (
                  <Badge key={tag.tag} variant="secondary">{tag.tag}</Badge>
                ))}
              </div>
              {analysisEnrichment.writeback?.status === 'applied' ? (
                <p className="mt-4 text-sm text-emerald-700">已写入 Zotero；后续写回会更新同一份 AI 笔记。</p>
              ) : null}
            </div>
          </div>
        ) : (
          <p className="mt-5 text-sm text-slate-500">完成 AI 分析后，会自动生成一份精读笔记和分层标签建议。</p>
        )}
      </section>

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
