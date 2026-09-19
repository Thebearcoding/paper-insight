import { useCallback, useEffect, useRef, useState } from 'react';
import { Bookmark, ChevronDown, ChevronLeft, ExternalLink, Eye, FileText, Heart, Images, Languages, Loader2, Sparkles, Table2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ActiveModelBadge } from '@/components/active-model-badge';
import { ChatPanel } from '@/components/chat-panel';
import { CodeAvailabilityBadge } from '@/components/code-availability-badge';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { ReasoningStreamPanel } from '@/components/reasoning-stream-panel';
import { RichContent } from '@/components/rich-content';
import { fetchOpenInAiPrompt, fetchPaperInfo, fetchPaperMarks, fetchPaperTranslationStatus, apiUrl, paperApiPath, startPaperTranslation, streamSse, updatePaperMark, type PaperTranslationStatus } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { buildPaperKeywordSearchPath } from '@/lib/constants';
import { getVenueParts, normalizeKeywords } from '@/lib/content';
import { splitAnalysisAtMethodSection } from '@/lib/analysis-layout';
import { useZoteroPaperMetadata } from '@/hooks/use-zotero-paper-metadata';
import { navigate } from '@/lib/router';
import type { Paper, ZoteroAnalysisFigure } from '@/types';

interface PaperPageProps {
  paperId: string;
}

const BACK_BUTTON_FADE_DISTANCE = 72;
const BACK_BUTTON_MAX_TRANSLATE_Y = 8;
const AUTO_VIEWED_DELAY_MS = 10_000;
const EMPTY_MARKS = { viewed: false, liked: false, favorited: false };

function buildChatGptUrl(prompt: string) {
  const params = new URLSearchParams({
    hints: 'search',
    q: prompt,
  });
  return `https://chatgpt.com/?${params.toString()}`;
}

function AnalysisAsset({ figure, paperTitle }: { figure: ZoteroAnalysisFigure; paperTitle: string }) {
  const isResultsTable = figure.kind === 'results_table';
  const rows = figure.table_data?.rows ?? [];
  const Icon = isResultsTable ? Table2 : Images;
  const title = isResultsTable ? 'SOTA 对比表' : '论文架构图';
  return (
    <figure className="overflow-hidden rounded-lg border border-[#e8edf4] bg-[#f8fafc]">
      <div className="flex items-center gap-2 border-b border-[#e8edf4] bg-white px-4 py-3 text-sm font-medium text-[#334155]">
        <Icon className="h-4 w-4 text-[#ff9900]" />
        {title} · {figure.label}
      </div>
      {isResultsTable && rows.length ? (
        <div className="max-h-[48rem] overflow-auto bg-white p-3 sm:p-5">
          <table className="min-w-full border-collapse text-[11px] leading-5 text-slate-700 sm:text-xs" aria-label={figure.caption || title}>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={rowIndex} className="border-b border-slate-200 last:border-b-0">
                  {row.map((cell, cellIndex) => {
                    const CellTag = cell.header || rowIndex === 0 ? 'th' : 'td';
                    return <CellTag key={cellIndex} colSpan={cell.col_span || 1} rowSpan={cell.row_span || 1} className={`min-w-20 whitespace-nowrap border-r border-slate-100 px-2.5 py-2 text-center align-middle last:border-r-0 ${CellTag === 'th' ? 'bg-slate-50 font-semibold text-slate-900' : ''} ${cell.emphasis === 'best' ? 'font-semibold text-[#c2410c]' : ''}`}>
                      {cell.text || ' '}
                    </CellTag>;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : figure.url ? (
        <a href={figure.url} target="_blank" rel="noreferrer" className="block bg-white p-3 sm:p-5">
          <img src={figure.url} alt={figure.caption || `${paperTitle}${title}`} className="mx-auto max-h-[48rem] w-auto max-w-full rounded-md object-contain" loading="lazy" />
        </a>
      ) : null}
      <figcaption className="space-y-1 px-4 py-3 text-sm leading-6 text-[#64748b]">
        <p>{figure.caption}</p>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-[#94a3b8]">
          <span>来源：{figure.source}</span>
          {figure.page_number ? <span>PDF 第 {figure.page_number} 页</span> : null}
          {figure.source_url ? <a href={figure.source_url} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline">原始材料</a> : null}
        </div>
      </figcaption>
    </figure>
  );
}

export function PaperPage({ paperId }: PaperPageProps) {
  const { user, isLoading: isAuthLoading } = useAuth();
  const [paper, setPaper] = useState<Paper | null>(null);
  const [paperError, setPaperError] = useState<string | null>(null);
  const [paperLoading, setPaperLoading] = useState(true);
  const [analysisText, setAnalysisText] = useState('');
  const [analysisReasoning, setAnalysisReasoning] = useState('');
  const [analysisStatus, setAnalysisStatus] = useState('正在获取论文信息...');
  const [analysisLoading, setAnalysisLoading] = useState(true);
  const [analysisStreaming, setAnalysisStreaming] = useState(false);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [analysisWarning, setAnalysisWarning] = useState<string | null>(null);
  const [analysisFigures, setAnalysisFigures] = useState<ZoteroAnalysisFigure[]>([]);
  const [analysisSource, setAnalysisSource] = useState<string | null>(null);
  const [analysisModel, setAnalysisModel] = useState<string | null>(null);
  const [openInAiPrompt, setOpenInAiPrompt] = useState('');
  const [openInAiPromptError, setOpenInAiPromptError] = useState<string | null>(null);
  const [marks, setMarks] = useState(EMPTY_MARKS);
  const [isLikeAnimating, setIsLikeAnimating] = useState(false);
  const [backButtonProgress, setBackButtonProgress] = useState(0);
  const [translation, setTranslation] = useState<PaperTranslationStatus | null>(null);
  const [translationBusy, setTranslationBusy] = useState(false);
  const translationPollRef = useRef<number | null>(null);
  const analysisRequestIdRef = useRef(0);
  const analysisAbortRef = useRef<AbortController | null>(null);
  const zoteroMetadataPaper = paper?.id === paperId ? paper : null;

  useZoteroPaperMetadata(zoteroMetadataPaper);

  // ---- PDF 翻译（pdf2zh）----
  const stopTranslationPolling = useCallback(() => {
    if (translationPollRef.current !== null) {
      window.clearInterval(translationPollRef.current);
      translationPollRef.current = null;
    }
  }, []);

  useEffect(() => {
    let active = true;
    setTranslation(null);
    setTranslationBusy(false);
    stopTranslationPolling();
    void fetchPaperTranslationStatus(paperId)
      .then((status) => {
        if (active) {
          setTranslation(status);
        }
      })
      .catch(() => {
        // 翻译功能不可用时保持 idle，不打扰页面
      });
    return () => {
      active = false;
      stopTranslationPolling();
    };
  }, [paperId, stopTranslationPolling]);

  // 进行中则自动轮询
  useEffect(() => {
    if (!translation || (translation.status !== 'pending' && translation.status !== 'progress')) {
      stopTranslationPolling();
      return;
    }
    if (translationPollRef.current !== null) {
      return;
    }
    translationPollRef.current = window.setInterval(() => {
      void fetchPaperTranslationStatus(paperId)
        .then((status) => setTranslation(status))
        .catch(() => {
          // 网络抖动时继续轮询，等下一个周期
        });
    }, 3000);
    return stopTranslationPolling;
  }, [translation, paperId, stopTranslationPolling]);

  const handleStartTranslation = useCallback(() => {
    if (translationBusy) {
      return;
    }
    setTranslationBusy(true);
    void startPaperTranslation(paperId)
      .then((status) => setTranslation(status))
      .catch((error: unknown) => {
        setTranslation({
          paper_id: paperId,
          status: 'error',
          progress: 0,
          mono_url: null,
          dual_url: null,
          error: error instanceof Error ? error.message : '翻译任务提交失败',
        });
      })
      .finally(() => setTranslationBusy(false));
  }, [paperId, translationBusy]);

  useEffect(() => {
    let active = true;
    setMarks(EMPTY_MARKS);
    if (isAuthLoading || !user) {
      return () => {
        active = false;
      };
    }
    void fetchPaperMarks([paperId])
      .then((nextMarks) => {
        if (active) {
          setMarks(nextMarks[paperId] ?? EMPTY_MARKS);
        }
      })
      .catch(() => {
        if (active) {
          setMarks(EMPTY_MARKS);
        }
      });
    return () => {
      active = false;
    };
  }, [isAuthLoading, paperId, user]);

  useEffect(() => {
    if (isAuthLoading || !user || !paper || paperError || marks.viewed) {
      return;
    }

    let active = true;
    const timerId = window.setTimeout(() => {
      void updatePaperMark(paperId, { viewed: true })
        .then((nextMarks) => {
          if (active) {
            setMarks(nextMarks);
          }
        })
        .catch(() => {
          // Keep the page quiet; manual marking still remains available.
        });
    }, AUTO_VIEWED_DELAY_MS);

    return () => {
      active = false;
      window.clearTimeout(timerId);
    };
  }, [isAuthLoading, marks.viewed, paper, paperError, paperId, user]);

  useEffect(() => {
    const handleScroll = () => {
      const nextProgress = Math.min(Math.max(window.scrollY / BACK_BUTTON_FADE_DISTANCE, 0), 1);
      setBackButtonProgress(nextProgress);
    };

    handleScroll();
    window.addEventListener('scroll', handleScroll, { passive: true });
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  useEffect(() => {
    let active = true;
    setPaperLoading(true);
    setPaperError(null);

    void fetchPaperInfo(paperId)
      .then((payload) => {
        if (active) {
          setPaper(payload);
          setAnalysisFigures(payload.analysis_figures ?? []);
          setAnalysisSource(payload.analysis_source ?? null);
          setAnalysisModel(payload.analysis_model_name ?? null);
        }
      })
      .catch((error) => {
        if (active) {
          setPaperError(error instanceof Error ? error.message : '加载失败');
          setPaper(null);
        }
      })
      .finally(() => {
        if (active) {
          setPaperLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, [paperId]);

  const loadAnalysis = useCallback(async (reanalyze = false) => {
    if (reanalyze && analysisAbortRef.current) return;
    analysisAbortRef.current?.abort();
    const controller = new AbortController();
    analysisAbortRef.current = controller;
    const requestId = analysisRequestIdRef.current + 1;
    analysisRequestIdRef.current = requestId;

    setAnalysisText('');
    setAnalysisReasoning('');
    setAnalysisError(null);
    setAnalysisWarning(null);
    setAnalysisSource(null);
    setAnalysisModel(null);
    if (reanalyze) setAnalysisFigures([]);
    setAnalysisLoading(true);
    setAnalysisStreaming(true);
    setAnalysisStatus(reanalyze ? '正在重新分析论文...' : '正在获取论文信息...');

    try {
      await streamSse(
        paperApiPath(paperId, reanalyze ? '?reanalyze=true' : ''),
        { method: 'GET', signal: controller.signal },
        {
          onChunk: (chunk) => {
            if (analysisRequestIdRef.current !== requestId) {
              return;
            }
            setAnalysisLoading(false);
            setAnalysisText((current) => current + chunk);
          },
          onEvent: (event, data) => {
            if (analysisRequestIdRef.current !== requestId) {
              return;
            }
            if (event === 'status') {
              setAnalysisStatus(data);
            }
            if (event === 'warning') {
              setAnalysisWarning((current) => [current, data].filter(Boolean).join('；'));
            }
            if (event === 'figures') {
              try {
                const figures = JSON.parse(data) as ZoteroAnalysisFigure[];
                setAnalysisFigures(Array.isArray(figures) ? figures : []);
              } catch {
                setAnalysisWarning((current) => [current, '论文图表数据格式无效，已忽略'].filter(Boolean).join('；'));
              }
            }
            if (event === 'analysis-meta') {
              try {
                const metadata = JSON.parse(data) as { source?: string; warning?: string | null; model_name?: string | null };
                setAnalysisSource(metadata.source ?? null);
                setAnalysisModel(metadata.model_name ?? null);
                if (metadata.warning) setAnalysisWarning(metadata.warning);
              } catch {
                // Provenance is supplementary; the report stream remains usable.
              }
            }
            if (event === 'reasoning') {
              setAnalysisLoading(false);
              setAnalysisReasoning((current) => current + data);
            }
            if (event === 'final') {
              setAnalysisText(data);
            }
            if (event === 'error') {
              setAnalysisLoading(false);
              setAnalysisStreaming(false);
              setAnalysisReasoning('');
              setAnalysisError(data || '分析失败');
              setAnalysisStatus('');
            }
            if (event === 'done') {
              setAnalysisLoading(false);
              setAnalysisStreaming(false);
              setAnalysisReasoning('');
              setAnalysisStatus('');
              void fetchPaperInfo(paperId).then((nextPaper) => {
                if (!controller.signal.aborted && analysisRequestIdRef.current === requestId) setPaper(nextPaper);
              }).catch(() => {
                // The analysis result is already available; keep the existing metadata if refresh fails.
              });
            }
          },
        },
      );
    } catch (error) {
      if (controller.signal.aborted) {
        return;
      }
      setAnalysisLoading(false);
      setAnalysisStreaming(false);
      setAnalysisReasoning('');
      setAnalysisError(error instanceof Error ? error.message : '分析失败');
      setAnalysisStatus('');
    } finally {
      if (analysisAbortRef.current === controller) {
        analysisAbortRef.current = null;
      }
    }
  }, [paperId]);

  useEffect(() => {
    void loadAnalysis(false);
    return () => {
      analysisAbortRef.current?.abort();
      analysisAbortRef.current = null;
    };
  }, [loadAnalysis]);

  useEffect(() => {
    let active = true;
    setOpenInAiPrompt('');
    setOpenInAiPromptError(null);

    void fetchOpenInAiPrompt(paperId)
      .then((prompt) => {
        if (active) {
          setOpenInAiPrompt(prompt);
        }
      })
      .catch((error) => {
        if (active) {
          setOpenInAiPromptError(error instanceof Error ? error.message : '提示词加载失败');
        }
      });

    return () => {
      active = false;
    };
  }, [paperId]);

  const venue = getVenueParts(paper?.venue);
  const keywords = normalizeKeywords(paper?.keywords);
  const analysisSplit = splitAnalysisAtMethodSection(analysisText);
  const pdfUrl = paper?.pdf || `https://openreview.net/pdf?id=${paperId}`;
  const aiTutorTargets = openInAiPrompt ? [
    {
      id: 'kimi',
      label: 'Kimi',
      description: '使用相同提示词并自动发送',
      url: `https://www.kimi.com/?prefill_prompt=${encodeURIComponent(openInAiPrompt)}&send_immediately=true`,
    },
    {
      id: 'openai',
      label: 'OpenAI ChatGPT',
      description: '使用 ChatGPT Search 深链',
      url: buildChatGptUrl(openInAiPrompt),
    },
  ] : [];
  const isBackButtonHidden = backButtonProgress >= 1;
  const backButtonOpacity = 1 - backButtonProgress;
  const requireLogin = () => {
    if (isAuthLoading) {
      return false;
    }
    if (!user) {
      navigate('/login');
      return false;
    }
    return true;
  };
  const openKeywordSearch = (keyword: string) => {
    const keywordSearchPath = buildPaperKeywordSearchPath(paper?.venue, keyword);
    if (keywordSearchPath) {
      navigate(keywordSearchPath);
    }
  };

  return (
    <div className="mx-auto max-w-5xl animate-fade-in">
      <div
        className="mb-4 origin-left transition-[opacity,transform] duration-150"
        style={{
          opacity: backButtonOpacity,
          transform: `translateY(${-BACK_BUTTON_MAX_TRANSLATE_Y * backButtonProgress}px)`,
          pointerEvents: isBackButtonHidden ? 'none' : 'auto',
        }}
      >
        <Button
          variant="ghost"
          className="rounded-full px-0 text-[#728095]"
          tabIndex={isBackButtonHidden ? -1 : 0}
          onClick={() => {
            if (window.history.length > 1) {
              window.history.back();
              return;
            }
            navigate('/');
          }}
        >
          <ChevronLeft className="mr-1 h-4 w-4" />
          返回
        </Button>
      </div>

      <div className="space-y-6">
        <section className="rounded-[32px] bg-white p-6 shadow-sm ring-1 ring-black/5">
            {paperLoading ? (
              <div className="flex items-center gap-2 text-[#728095]">
                <Loader2 className="h-5 w-5 animate-spin" />
                加载论文信息...
              </div>
            ) : paperError ? (
              <div className="text-[#b91c1c]">{paperError}</div>
            ) : paper ? (
              <div className="space-y-6">
                <div>
                  <h1 className="text-3xl font-semibold leading-tight text-[#172033]">
                    <RichContent content={paper.title} inline className="paper-title-math" />
                  </h1>
                  <div className="mt-4 flex flex-wrap gap-2.5">
                    <Badge variant="outline" className="border-blue-200 bg-blue-50 px-3 py-1 text-sm text-blue-700">
                      {venue.label}
                    </Badge>
                    {paper.primary_area ? (
                      <Badge
                        variant="outline"
                        className="border-[#e6ebf2] bg-[#f8fafc] px-3 py-1 text-sm text-[#516072]"
                      >
                        {paper.primary_area}
                      </Badge>
                    ) : null}
                    <CodeAvailabilityBadge
                      status={paper.code_status}
                      codeUrl={paper.code_url}
                      className="px-3 py-1 text-sm"
                    />
                    {keywords.slice(0, 6).map((keyword, index) => {
                      const className =
                        index % 2 === 0
                          ? 'border-orange-100 bg-orange-50 px-3 py-1 text-sm text-orange-700'
                          : 'border-violet-100 bg-violet-50 px-3 py-1 text-sm text-violet-700';
                      const keywordSearchPath = buildPaperKeywordSearchPath(paper.venue, keyword);

                      if (!keywordSearchPath) {
                        return (
                          <Badge key={`${paper.id}-${keyword}`} variant="outline" className={className}>
                            {keyword}
                          </Badge>
                        );
                      }

                      return (
                        <Badge
                          key={`${paper.id}-${keyword}`}
                          asChild
                          variant="outline"
                          className={`${className} cursor-pointer transition hover:-translate-y-0.5 hover:shadow-sm`}
                        >
                          <button
                            type="button"
                            aria-label={`搜索关键词 ${keyword}`}
                            title={`搜索关键词：${keyword}`}
                            onClick={() => openKeywordSearch(keyword)}
                          >
                            {keyword}
                          </button>
                        </Badge>
                      );
                    })}
                  </div>
                </div>

                <div>
                  <h2 className="mb-3 text-sm font-semibold uppercase tracking-[0.2em] text-[#8a98ac]">Abstract</h2>
                  <RichContent content={paper.abstract || '暂无摘要'} className="markdown-body text-base leading-7 text-[#475569]" />
                </div>

                <div className="flex flex-wrap gap-2">
                  <a href={pdfUrl} target="_blank" rel="noreferrer">
                    <Button variant="outline" className="rounded-full border-[#bfdbfe] bg-[#eff6ff] text-[#2563eb]">
                      <FileText className="mr-1.5 h-4 w-4" />
                      PDF
                    </Button>
                  </a>
                  {translation?.status === 'success' ? (
                    <>
                      <a
                        href={apiUrl(translation.dual_url ?? '#')}
                        target="_blank"
                        rel="noreferrer"
                        title="下载中英双语 PDF 到本机（服务端只做临时缓存，服务重启后需重新翻译）"
                      >
                        <Button variant="outline" className="rounded-full border-[#bbf7d0] bg-[#f0fdf4] text-[#16a34a]">
                          <Languages className="mr-1.5 h-4 w-4" />
                          双语 PDF
                        </Button>
                      </a>
                      <a
                        href={apiUrl(translation.mono_url ?? '#')}
                        target="_blank"
                        rel="noreferrer"
                        title="下载纯中文 PDF 到本机（服务端只做临时缓存，服务重启后需重新翻译）"
                      >
                        <Button variant="outline" className="rounded-full border-[#bbf7d0] bg-[#f0fdf4] text-[#16a34a]">
                          <Languages className="mr-1.5 h-4 w-4" />
                          中文 PDF
                        </Button>
                      </a>
                    </>
                  ) : translation?.status === 'expired' ? (
                    <Button
                      variant="outline"
                      className="rounded-full border-[#fde68a] bg-[#fffbeb] text-[#d97706]"
                      disabled={translationBusy}
                      title={translation.error ?? '翻译结果已过期，请重新翻译'}
                      onClick={handleStartTranslation}
                    >
                      <Languages className="mr-1.5 h-4 w-4" />
                      翻译结果已失效，点击重新翻译
                    </Button>
                  ) : translation?.status === 'error' ? (
                    <Button
                      variant="outline"
                      className="rounded-full border-[#fecaca] bg-[#fff1f2] text-[#e11d48]"
                      disabled={translationBusy}
                      title={translation.error ?? '翻译失败'}
                      onClick={handleStartTranslation}
                    >
                      <Languages className="mr-1.5 h-4 w-4" />
                      翻译失败，点击重试
                    </Button>
                  ) : translation && (translation.status === 'pending' || translation.status === 'progress') ? (
                    <Button
                      variant="outline"
                      className="rounded-full border-[#fde68a] bg-[#fffbeb] text-[#d97706]"
                      disabled
                      title="正在翻译，通常需要 1~5 分钟"
                    >
                      <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                      {translation.status === 'progress' && translation.progress > 0
                        ? `翻译中 ${translation.progress}%`
                        : '排队翻译中…'}
                    </Button>
                  ) : (
                    <Button
                      variant="outline"
                      className="rounded-full border-[#d8b4fe] bg-[#faf5ff] text-[#9333ea]"
                      disabled={translationBusy}
                      title="使用 pdf2zh 翻译为中文（保留原始排版）"
                      onClick={handleStartTranslation}
                    >
                      {translationBusy ? (
                        <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                      ) : (
                        <Languages className="mr-1.5 h-4 w-4" />
                      )}
                      翻译 PDF
                    </Button>
                  )}
	                <DropdownMenu>
	                  <DropdownMenuTrigger asChild>
	                      <Button
	                        variant="outline"
	                        className="rounded-full border-[#d8b4fe] bg-[#faf5ff] text-[#9333ea]"
	                        disabled={!openInAiPrompt}
	                        title={openInAiPromptError ?? (openInAiPrompt ? 'Open in AI' : '正在加载 AI 提示词')}
	                      >
	                        <Sparkles className="mr-1.5 h-4 w-4" />
	                        {openInAiPrompt ? 'Open in AI' : '加载 AI 提示词'}
	                        <ChevronDown className="ml-0.5 h-4 w-4" />
	                      </Button>
	                    </DropdownMenuTrigger>
                    <DropdownMenuContent
                      align="start"
                      className="w-56 overflow-hidden rounded-2xl border border-white/55 bg-[linear-gradient(135deg,rgba(255,255,255,0.42),rgba(248,245,255,0.30)_46%,rgba(219,234,254,0.24))] p-1.5 shadow-[0_18px_50px_rgba(37,99,235,0.16),inset_0_1px_0_rgba(255,255,255,0.72)] backdrop-blur-3xl backdrop-saturate-150"
                    >
                      {aiTutorTargets.map((target) => (
                        <DropdownMenuItem
                          key={target.id}
                          asChild
                          className="rounded-xl border border-transparent px-3 py-2.5 transition-[background-color,border-color,box-shadow,transform] duration-150 hover:-translate-y-0.5 hover:border-white/55 hover:bg-white/32 hover:shadow-[0_10px_26px_rgba(37,99,235,0.14),inset_0_1px_0_rgba(255,255,255,0.70)] focus:-translate-y-0.5 focus:border-white/60 focus:bg-white/38 focus:text-[#172033] focus:shadow-[0_10px_26px_rgba(37,99,235,0.16),inset_0_1px_0_rgba(255,255,255,0.74)] focus:outline-none focus-visible:outline-none data-[highlighted]:-translate-y-0.5 data-[highlighted]:border-white/60 data-[highlighted]:bg-white/38 data-[highlighted]:text-[#172033] data-[highlighted]:shadow-[0_10px_26px_rgba(37,99,235,0.16),inset_0_1px_0_rgba(255,255,255,0.74)] data-[highlighted]:outline-none"
                        >
                          <a href={target.url} target="_blank" rel="noreferrer" className="cursor-pointer">
                            <div className="flex min-w-0 flex-1 flex-col">
                              <span className="font-medium text-[#172033]">{target.label}</span>
                              <span className="truncate text-xs text-[#728095]">{target.description}</span>
                            </div>
                            <ExternalLink className="h-3.5 w-3.5 text-[#8a98ac]" />
                          </a>
                        </DropdownMenuItem>
                      ))}
                    </DropdownMenuContent>
                  </DropdownMenu>
                  <Button
                    variant="outline"
                    className={`rounded-full ${
                      marks.viewed
                        ? 'border-[#bfdbfe] bg-[#eff6ff] text-[#2563eb]'
                        : 'border-[#dbe2ea] text-[#66768b]'
                    }`}
                    onClick={() => {
                      if (!requireLogin()) {
                        return;
                      }
                      void updatePaperMark(paperId, { viewed: !marks.viewed }).then(setMarks);
                    }}
                  >
                    <Eye className={`mr-1.5 h-4 w-4 ${marks.viewed ? 'fill-current' : ''}`} />
                    {marks.viewed ? '已看过' : '看过'}
                  </Button>
                  <Button
                    variant="outline"
                    className={`rounded-full ${
                      marks.liked
                        ? 'border-[#fecaca] bg-[#fff1f2] text-[#e11d48]'
                        : 'border-[#dbe2ea] text-[#66768b]'
                    }`}
                    onClick={() => {
                      if (!requireLogin()) {
                        return;
                      }
                      setIsLikeAnimating(true);
                      void updatePaperMark(paperId, { liked: !marks.liked }).then(setMarks);
                      window.setTimeout(() => setIsLikeAnimating(false), 400);
                    }}
                  >
                    <Heart className={`mr-1.5 h-4 w-4 ${isLikeAnimating ? 'animate-heart-beat' : ''} ${marks.liked ? 'fill-current' : ''}`} />
                    {marks.liked ? '已点赞' : '点赞'}
                  </Button>
                  <Button
                    variant="outline"
                    className={`rounded-full ${
                      marks.favorited
                        ? 'border-[#fed7aa] bg-[#fff7ed] text-[#ea580c]'
                        : 'border-[#dbe2ea] text-[#66768b]'
                    }`}
                    onClick={() => {
                      if (!requireLogin()) {
                        return;
                      }
                      void updatePaperMark(paperId, { favorited: !marks.favorited }).then(setMarks);
                    }}
                  >
                    <Bookmark className={`mr-1.5 h-4 w-4 ${marks.favorited ? 'fill-current' : ''}`} />
                    {marks.favorited ? '已收藏' : '收藏'}
                  </Button>
                </div>
              </div>
            ) : null}
        </section>

        <section className="rounded-[32px] bg-white p-6 shadow-sm ring-1 ring-black/5">
            <div className="flex flex-col gap-3 border-b border-[#eef2f7] pb-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex shrink-0 items-center gap-2">
                <Sparkles className="h-5 w-5 text-[#ff9900]" />
                <div>
                  <h2 className="whitespace-nowrap text-xl font-semibold text-[#172033]">AI 分析</h2>
                  {analysisStatus ? <p className="text-sm text-[#728095]">{analysisStatus}</p> : null}
                </div>
              </div>
              <div className="flex max-w-full flex-wrap items-center gap-2">
                <ActiveModelBadge className="max-w-[18rem]" />
                <Button
                  variant="outline"
                  className="rounded-full"
                  disabled={analysisStreaming}
                  onClick={() => void loadAnalysis(true)}
                >
                  {analysisStreaming ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                  {analysisStreaming ? '分析中…' : '重新分析'}
                </Button>
              </div>
            </div>

            {analysisWarning ? <p role="status" className="mt-4 rounded-xl bg-amber-50 p-3 text-sm text-amber-800">{analysisWarning}</p> : null}
            {(analysisSource || analysisModel) && (analysisText || analysisStreaming) ? (
              <div className="mt-4 flex flex-wrap gap-x-5 gap-y-1 rounded-xl border border-emerald-100 bg-emerald-50/70 px-4 py-3 text-sm text-emerald-900">
                {analysisSource ? <span><strong>分析材料：</strong>{analysisSource === 'fulltext' ? '已提取论文全文与图表证据' : '仅论文元数据与摘要'}</span> : null}
                {analysisModel ? <span><strong>报告模型：</strong>{analysisModel}</span> : null}
              </div>
            ) : null}
            {analysisLoading ? (
              <div className="mt-6 flex items-center gap-2 text-[#728095]">
                <Loader2 className="h-5 w-5 animate-spin" />
                {analysisStatus || '正在分析论文...'}
              </div>
            ) : analysisError ? (
              <div role="alert" className="mt-6 rounded-2xl bg-[#fff1f2] p-4 text-[#b91c1c]">{analysisError}</div>
            ) : (
              <div className="mt-6 space-y-4">
                <ReasoningStreamPanel reasoning={analysisStreaming ? analysisReasoning : ''} />
                {analysisText ? (
                  <>
                    <RichContent
                      content={analysisSplit.beforeAssets}
                      analysisMode
                      isStreaming={analysisStreaming}
                      className="markdown-body analysis-markdown text-base leading-7 text-[#334155]"
                    />
                    {analysisFigures.length ? (
                      <div className="space-y-4 border-l-2 border-[#fed7aa] pl-3 sm:pl-5">
                        {analysisFigures.map((figure) => <AnalysisAsset key={`${figure.kind}-${figure.id}`} figure={figure} paperTitle={paper?.title || '论文'} />)}
                      </div>
                    ) : null}
                    {analysisSplit.afterAssets ? (
                      <RichContent
                        content={analysisSplit.afterAssets}
                        analysisMode
                        isStreaming={analysisStreaming}
                        className="markdown-body analysis-markdown text-base leading-7 text-[#334155]"
                      />
                    ) : null}
                  </>
                ) : null}
              </div>
            )}
        </section>
      </div>

      <ChatPanel key={paperId} paperId={paperId} />
    </div>
  );
}
