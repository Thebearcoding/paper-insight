import { CalendarDays, ExternalLink, FileText, Quote } from 'lucide-react';
import { useMemo } from 'react';

import { HighlightedText } from '@/components/search-highlight';
import { RichContent } from '@/components/rich-content';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { buildSearchHighlightTerms } from '@/lib/search-highlight';
import type { Paper } from '@/types';

interface OnlinePaperCardProps {
  paper: Paper;
  index: number;
  searchQuery?: string;
}

function openExternal(url?: string | null) {
  if (!url) {
    return;
  }
  const opened = window.open(url, '_blank', 'noopener,noreferrer');
  if (opened) {
    opened.opener = null;
  }
}

function formatAuthors(authors?: string[]): string {
  if (!authors?.length) {
    return '作者信息暂无';
  }
  const visible = authors.slice(0, 5).join(', ');
  return authors.length > 5 ? `${visible} 等 ${authors.length} 位作者` : visible;
}

export function OnlinePaperCard({ paper, index, searchQuery = '' }: OnlinePaperCardProps) {
  const online = paper.online;
  const highlightTerms = useMemo(() => buildSearchHighlightTerms(searchQuery), [searchQuery]);
  const keywords = (paper.keywords ?? []).slice(0, 6);
  const sourceUrl = online?.url || online?.provider_url || online?.openalex_url;
  const providerLabel = online?.top_venue
    ? `${online.top_venue} · 顶会`
    : `${online?.provider ?? '在线索引'} 在线`;

  return (
    <article
      role="link"
      tabIndex={0}
      onClick={() => openExternal(sourceUrl)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          openExternal(sourceUrl);
        }
      }}
      className="group cursor-pointer rounded-lg bg-white/95 p-5 shadow-sm ring-1 ring-black/5 transition duration-300 hover:-translate-y-0.5 hover:shadow-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#ff9900]"
      style={{ animationDelay: `${index * 0.04}s` }}
    >
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Badge variant="outline" className="border-[#bae6fd] bg-[#ecfeff] text-[#0e7490]">
          {providerLabel}
        </Badge>
        {online?.publication_year ? (
          <Badge variant="outline" className="border-[#e6ebf2] bg-[#f8fafc] text-[#516072]">
            <CalendarDays className="mr-1 h-3 w-3" />
            {online.publication_year}
          </Badge>
        ) : null}
        {paper.venue ? (
          <Badge
            variant="outline"
            className="max-w-full min-w-0 truncate border-[#e6ebf2] bg-white text-[#516072]"
            title={paper.venue}
          >
            {paper.venue}
          </Badge>
        ) : null}
        {online?.is_oa ? (
          <Badge variant="outline" className="border-[#bbf7d0] bg-[#f0fdf4] text-[#15803d]">
            开放获取
          </Badge>
        ) : null}
      </div>

      <h3 className="mb-2 text-xl font-semibold leading-snug text-[#1f2937] transition-colors group-hover:text-[#ff7a00]">
        <RichContent content={paper.title} inline className="paper-title-math" highlightTerms={highlightTerms} />
      </h3>
      <p className="mb-4 truncate text-sm text-[#64748b]" title={formatAuthors(paper.authors)}>
        {formatAuthors(paper.authors)}
      </p>

      {keywords.length ? (
        <div className="mb-4 flex flex-wrap gap-2">
          {keywords.map((keyword) => (
            <span key={`${paper.id}-${keyword}`} className="rounded-md border border-[#e0e7ff] bg-[#eef2ff] px-2.5 py-1 text-xs text-[#4338ca]">
              <HighlightedText text={keyword} terms={highlightTerms} />
            </span>
          ))}
        </div>
      ) : null}

      <p className="mb-5 line-clamp-3 text-sm leading-6 text-[#67758a]">
        <HighlightedText text={paper.abstract || '暂无摘要'} terms={highlightTerms} />
      </p>

      <div className="flex flex-col gap-3 border-t border-[#e8edf3] pt-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap items-center gap-4 text-xs text-[#64748b]">
          <span className="inline-flex items-center gap-1.5">
            <Quote className="h-3.5 w-3.5 text-[#7c3aed]" />
            被引 {online?.cited_by_count ?? 0}
          </span>
          {online?.publication_date ? <span>{online.publication_date}</span> : null}
        </div>
        <div className="flex flex-wrap gap-2">
          {online?.pdf_url ? (
            <Button
              variant="outline"
              size="sm"
              className="rounded-md border-[#bae6fd] text-[#0e7490]"
              onClick={(event) => {
                event.stopPropagation();
                openExternal(online.pdf_url);
              }}
            >
              <FileText className="mr-1.5 h-3.5 w-3.5" />
              PDF
            </Button>
          ) : null}
          <Button
            variant="outline"
            size="sm"
            className="rounded-md border-[#fed7aa] text-[#c2410c]"
            onClick={(event) => {
              event.stopPropagation();
              openExternal(sourceUrl);
            }}
          >
            <ExternalLink className="mr-1.5 h-3.5 w-3.5" />
            论文页面
          </Button>
        </div>
      </div>
    </article>
  );
}
