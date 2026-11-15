import { Link } from "react-router";

export function NotFoundPage() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-zinc-50">
      <p className="text-5xl font-semibold text-zinc-300">404</p>
      <p className="text-sm text-zinc-500">页面不存在或已被移除</p>
      <Link
        to="/"
        className="rounded-lg bg-zinc-900 px-4 py-2 text-sm text-white hover:bg-zinc-700"
      >
        回到工作台
      </Link>
    </div>
  );
}
