import { lazy, Suspense } from 'react';
import { SiteNavbar } from '@/components/site-navbar';
import { RouteErrorBoundary } from '@/components/route-error-boundary';
import { HomePage } from '@/pages/home-page';
import { decodeRouteSegment, useAppLocation } from '@/lib/router';

const ConferencePage = lazy(() => import('@/pages/conference-page').then((module) => ({ default: module.ConferencePage })));
const HfDailyPage = lazy(() => import('@/pages/hf-daily-page').then((module) => ({ default: module.HfDailyPage })));
const ArxivPage = lazy(() => import('@/pages/arxiv-page').then((module) => ({ default: module.ArxivPage })));
const ChangelogPage = lazy(() => import('@/pages/changelog-page').then((module) => ({ default: module.ChangelogPage })));
const PaperPage = lazy(() => import('@/pages/paper-page').then((module) => ({ default: module.PaperPage })));
const SearchPage = lazy(() => import('@/pages/search-page').then((module) => ({ default: module.SearchPage })));
const AdminPage = lazy(() => import('@/pages/admin-page').then((module) => ({ default: module.AdminPage })));
const AuthPage = lazy(() => import('@/pages/auth-page').then((module) => ({ default: module.AuthPage })));
const ProfilePage = lazy(() => import('@/pages/profile-page').then((module) => ({ default: module.ProfilePage })));
const ZoteroItemPage = lazy(() => import('@/pages/zotero-item-page').then((module) => ({ default: module.ZoteroItemPage })));
const ZoteroPage = lazy(() => import('@/pages/zotero-page').then((module) => ({ default: module.ZoteroPage })));

function App() {
  const location = useAppLocation();

  const pathname = location.pathname;

  let content = <HomePage />;
  if (pathname === '/search') {
    content = <SearchPage />;
  } else if (pathname === '/hf-daily') {
    content = <HfDailyPage />;
  } else if (pathname === '/arxiv') {
    content = <ArxivPage />;
  } else if (pathname === '/changelog') {
    content = <ChangelogPage />;
  } else if (pathname === '/login') {
    content = <AuthPage mode="login" />;
  } else if (pathname === '/register') {
    content = <AuthPage mode="register" />;
  } else if (pathname === '/admin') {
    content = <AdminPage />;
  } else if (pathname === '/me') {
    content = <ProfilePage />;
  } else if (pathname === '/zotero') {
    content = <ZoteroPage />;
  } else if (pathname.startsWith('/zotero/items/')) {
    const itemKey = decodeRouteSegment(pathname.replace('/zotero/items/', '').split('/')[0]);
    content = <ZoteroItemPage itemKey={itemKey} />;
  } else if (pathname.startsWith('/conference/')) {
    const venue = pathname.replace('/conference/', '').split('/')[0];
    content = <ConferencePage venue={venue} />;
  } else if (pathname.startsWith('/papers/')) {
    const paperId = decodeRouteSegment(pathname.replace('/papers/', '').split('/')[0]);
    content = <PaperPage paperId={paperId} />;
  }

  return (
    <div className="min-h-screen bg-[#f3f4f6] text-[#172033]">
      <div className="fixed inset-0 -z-10 bg-[radial-gradient(circle_at_top,_rgba(255,214,107,0.35),_transparent_30%),radial-gradient(circle_at_bottom_right,_rgba(125,211,252,0.22),_transparent_28%),linear-gradient(180deg,_#f7f9fc_0%,_#eef2f8_100%)]" />
      <SiteNavbar />
      <main className="px-4 pb-16 pt-28 sm:px-6 lg:px-8">
        <RouteErrorBoundary routeKey={pathname}>
          <Suspense key={pathname} fallback={<p role="status" className="p-6 text-slate-500">正在加载页面…</p>}>
            {content}
          </Suspense>
        </RouteErrorBoundary>
      </main>
    </div>
  );
}

export default App;
