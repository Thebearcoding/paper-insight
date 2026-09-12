import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { PaginationBar } from './pagination-bar';

describe('PaginationBar', () => {
  it('uses a named form so Enter submits the jump field', () => {
    const html = renderToStaticMarkup(<PaginationBar page={2} pages={12} onPageChange={() => undefined} />);

    expect(html).toContain('<nav aria-label="论文结果分页"');
    expect(html).toContain('<form aria-label="跳转到指定页"');
    expect(html).toContain('aria-label="页码，共 12 页"');
    expect(html).toContain('aria-live="polite"');
    expect(html).toContain('type="submit"');
    expect(html).toContain('step="1"');
    expect(html).not.toContain('disabled=""');
  });

  it.each([0, 1.5, 13])('disables jumping to invalid page %s instead of truncating it', (page) => {
    const html = renderToStaticMarkup(<PaginationBar page={page} pages={12} onPageChange={() => undefined} />);
    const form = html.match(/<form\b[\s\S]*?<\/form>/)?.[0] ?? '';

    expect(form).toContain('type="submit"');
    expect(form).toMatch(/<button\b[^>]*disabled=""/);
  });

  it('omits pagination for a single page', () => {
    expect(renderToStaticMarkup(<PaginationBar page={1} pages={1} onPageChange={() => undefined} />)).toBe('');
  });
});
