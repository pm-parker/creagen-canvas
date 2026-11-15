import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { Bell } from "lucide-react";
import { useAuthStore } from "@/stores/auth";
import { subscribeRunNotices, type RunNotice } from "@/lib/runNotices";

/** 通知停留 8s；同屏最多 4 条（超出移除最旧的）。 */
const NOTICE_TTL_MS = 8000;
const MAX_VISIBLE = 4;

/** 用户级 agent 运行通知 toast（右上角，点击跳任务中心）。 */
export function RunNoticeToasts() {
  const navigate = useNavigate();
  const token = useAuthStore((s) => s.token);
  const [notices, setNotices] = useState<RunNotice[]>([]);

  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    let hideTimer: ReturnType<typeof setTimeout> | null = null;

    const push = (notice: RunNotice) => {
      setNotices((prev) => [...prev.slice(-(MAX_VISIBLE - 1)), notice]);
      if (hideTimer) clearTimeout(hideTimer);
      hideTimer = setTimeout(() => setNotices([]), NOTICE_TTL_MS);
    };
    const loop = () => {
      subscribeRunNotices(push, { signal: controller.signal }).then(() => {
        if (!controller.signal.aborted) setTimeout(loop, 5000);
      });
    };
    loop();

    return () => {
      controller.abort();
      if (hideTimer) clearTimeout(hideTimer);
    };
  }, [token]);

  if (notices.length === 0) return null;

  return (
    <div className="pointer-events-none fixed right-4 top-4 z-50 flex w-80 flex-col gap-2">
      {notices.map((notice, i) => (
        <div
          key={`${notice.run_id}-${i}`}
          data-testid="run-notice"
          className="pointer-events-auto flex items-start gap-2 rounded-xl border border-zinc-200 bg-white p-3 shadow-lg"
        >
          <Bell className="mt-0.5 h-4 w-4 shrink-0 text-indigo-500" />
          <div className="min-w-0">
            <p className="text-sm font-medium text-zinc-800">{notice.title}</p>
            <p className="mt-0.5 whitespace-pre-line text-xs text-zinc-500">{notice.body}</p>
            <button
              type="button"
              className="mt-1 text-xs text-indigo-600 hover:underline"
              onClick={() => navigate("/tasks")}
            >
              查看任务 →
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
