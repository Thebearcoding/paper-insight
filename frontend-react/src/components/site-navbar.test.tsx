import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';

import { SiteNavbar } from './site-navbar';

vi.mock('@/lib/router', () => ({
  navigate: vi.fn(),
  useAppLocation: () => ({ pathname: '/zotero/items/ABC123', search: '' }),
}));

vi.mock('@/lib/auth', () => ({
  useAuth: () => ({ user: { id: 1, role: 'admin' }, logout: vi.fn() }),
}));

describe('SiteNavbar', () => {
  it('keeps every mobile icon action named and identifies the current section', () => {
    const html = renderToStaticMarkup(<SiteNavbar />);

    for (const name of ['Paper Insight 首页', '我的论文', 'Zotero 私人文库', '后台管理', '更新日志', '退出登录']) {
      expect(html).toContain(`aria-label="${name}"`);
    }
    expect(html).toContain('<nav aria-label="主导航"');
    expect(html).toMatch(/aria-label="Zotero 私人文库"[^>]*aria-current="page"/);
    expect(html.match(/aria-current="page"/g)).toHaveLength(1);
  });

  it('keeps the dock compact through tablet widths and reveals it for keyboard focus', () => {
    const html = renderToStaticMarkup(<SiteNavbar />);

    expect(html).toContain('class="hidden xl:inline">后台管理');
    expect(html).not.toContain('hidden sm:inline');
    expect(html).toContain('focus-within:translate-y-0');
    expect(html).toContain('focus-visible:ring-2');
  });
});
