import { Component, type ErrorInfo, type ReactNode } from "react";

interface State {
  error: Error | null;
}

/** 全局错误边界：渲染异常时给出可读的摘要与重试/回首页入口。 */
export class AppErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`[AppErrorBoundary]`, error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    const digest = `${error.name}: ${error.message}`.slice(0, 300);
    return (
      <div className="flex min-h-screen items-center justify-center bg-zinc-50 p-6 dark:bg-zinc-950">
        <div className="w-full max-w-md rounded-2xl border border-zinc-200 bg-white p-8 text-center shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-amber-100 text-2xl dark:bg-amber-950">
            ⚠️
          </div>
          <h1 className="text-lg font-semibold text-zinc-900 dark:text-zinc-100">页面出了点问题</h1>
          <p className="mt-2 text-sm text-zinc-500 dark:text-zinc-400">
            渲染时遇到异常。你的数据已保存在服务端，不会丢失。
          </p>
          <pre
            data-testid="error-boundary-digest"
            className="mt-4 max-h-32 overflow-auto rounded-lg bg-zinc-100 p-3 text-left text-xs text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300"
          >
            {digest}
          </pre>
          <div className="mt-6 flex justify-center gap-3">
            <button
              onClick={() => this.setState({ error: null })}
              className="rounded-lg bg-amber-500 px-4 py-2 text-sm font-medium text-zinc-950 hover:bg-amber-400 cursor-pointer"
            >
              重试
            </button>
            <a
              href="/"
              className="rounded-lg border border-zinc-200 px-4 py-2 text-sm text-zinc-600 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
            >
              返回首页
            </a>
          </div>
        </div>
      </div>
    );
  }
}
