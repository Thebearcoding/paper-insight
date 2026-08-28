import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { OnlinePaperCard } from './online-paper-card';
import type { Paper } from '@/types';

const ONLINE_PAPER: Paper = {
  id: 'openalex:W123',
  title: 'Industrial Defect Detection with Vision Transformers',
  abstract: 'A practical method for detecting surface defects.',
  authors: ['Ada Lovelace', 'Alan Turing'],
  keywords: ['Defect detection', 'Computer vision'],
  venue: 'Example Conference',
  online: {
    provider: 'OpenAlex',
    work_id: 'W123',
    url: 'https://doi.org/10.1234/example',
    openalex_url: 'https://openalex.org/W123',
    pdf_url: 'https://arxiv.org/pdf/2501.01234',
    publication_year: 2025,
    publication_date: '2025-06-01',
    cited_by_count: 42,
    is_oa: true,
  },
};

describe('OnlinePaperCard', () => {
  it('renders provider metadata and external actions without local-library actions', () => {
    const html = renderToStaticMarkup(
      <OnlinePaperCard paper={ONLINE_PAPER} index={0} searchQuery="defect detection" />,
    );

    expect(html).toContain('OpenAlex 在线');
    expect(html).toContain('Example Conference');
    expect(html).toContain('被引 42');
    expect(html).toContain('开放获取');
    expect(html).toContain('PDF');
    expect(html).toContain('论文页面');
    expect(html).not.toContain('收藏');
    expect(html).not.toContain('已读');
  });

  it('shows the canonical top-conference badge for top-venue results', () => {
    const html = renderToStaticMarkup(
      <OnlinePaperCard
        paper={{
          ...ONLINE_PAPER,
          id: 's2:abc123',
          online: {
            ...ONLINE_PAPER.online!,
            provider: 'DBLP + OpenAlex',
            work_id: 'abc123',
            provider_url: 'https://www.semanticscholar.org/paper/abc123',
            openalex_url: null,
            top_venue: 'CVPR',
          },
        }}
        index={0}
      />,
    );

    expect(html).toContain('CVPR · 顶会');
    expect(html).not.toContain('OpenAlex 在线');
  });
});
