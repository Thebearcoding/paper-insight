import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { SearchControls } from './search-controls';

describe('SearchControls', () => {
  it('names the search input and exposes the mobile search action', () => {
    const html = renderToStaticMarkup(
      <SearchControls
        query="缺陷检测"
        filters={{ title: true, abstract: true, keywords: true }}
        onQueryChange={() => undefined}
        onFiltersChange={() => undefined}
        onSubmit={() => undefined}
        placeholder="输入标题或关键词"
      />,
    );

    expect(html).toContain('type="search"');
    expect(html).toContain('aria-label="论文搜索关键词"');
    expect(html).toContain('enterKeyHint="search"');
    expect(html).toContain('flex flex-col items-stretch gap-3 sm:flex-row');
    expect(html).toContain('relative min-w-0 flex-1');
    expect(html).toContain('p-4 sm:p-8');
  });
});
