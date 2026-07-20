import { Component, type ErrorInfo, type ReactNode } from "react";

interface State {
  error: Error | null;
  info: ErrorInfo | null;
}

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null, info: null };

  static getDerivedStateFromError(error: Error): State {
    return { error, info: null };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // eslint-disable-next-line no-console
    console.error("ErrorBoundary caught:", error, info);
    this.setState({ error, info });
  }

  reset = () => this.setState({ error: null, info: null });

  render() {
    if (this.state.error) {
      return (
        <div className="min-h-screen bg-[#eef2f6] p-6">
          <div className="mx-auto max-w-3xl rounded border border-red-300 bg-red-50 p-6 text-sm">
            <h2 className="mb-2 text-lg font-semibold text-red-800">页面渲染出错</h2>
            <p className="mb-3 text-red-700">React 组件抛出未捕获错误。后端服务仍在运行，可重置 UI 或刷新页面。</p>
            <pre className="max-h-64 overflow-auto rounded bg-white p-3 font-mono text-xs text-red-900">{String(this.state.error?.stack || this.state.error)}</pre>
            {this.state.info?.componentStack && (
              <details className="mt-2">
                <summary className="cursor-pointer text-xs text-red-700">组件栈</summary>
                <pre className="mt-1 max-h-64 overflow-auto rounded bg-white p-3 font-mono text-xs text-red-900">{this.state.info.componentStack}</pre>
              </details>
            )}
            <div className="mt-4 flex gap-2">
              <button className="rounded border border-red-300 bg-white px-3 py-1.5 text-sm hover:bg-red-100" onClick={this.reset}>重置 UI</button>
              <button className="rounded border border-red-300 bg-white px-3 py-1.5 text-sm hover:bg-red-100" onClick={() => location.reload()}>刷新页面</button>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
