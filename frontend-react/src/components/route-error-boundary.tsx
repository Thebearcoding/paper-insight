import { Component } from 'react';
import type { ReactNode } from 'react';

/** A failed lazy chunk (offline or an old deployment) must not blank the app. */
export class RouteErrorBoundary extends Component<{ routeKey: string; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidUpdate(previous: Readonly<{ routeKey: string; children: ReactNode }>) {
    if (previous.routeKey !== this.props.routeKey && this.state.failed) {
      this.setState({ failed: false });
    }
  }

  render() {
    if (this.state.failed) {
      return (
        <div role="alert" className="rounded-2xl bg-white p-6 text-slate-700 shadow-sm">
          <p>页面未能加载，可能是网络中断或网站刚刚更新。已保存的数据不会因此丢失。</p>
          <button className="mt-3 underline" type="button" onClick={() => window.location.reload()}>
            重新加载当前页面
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
