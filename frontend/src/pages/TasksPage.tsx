import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import { copyText } from "@/lib/clipboard";
import { Button } from "@/components/ui/button";

interface TaskMeta {
  id: number;
  type: string;
  prompt: string;
  model: string;
  status: string;
  error_msg?: string | null;
  created_at?: string | null;
  output_url?: string | null;
  can_retry?: boolean;
}

const STATUS_TABS = [
  { key: "", label: "全部" },
  { key: "queued", label: "排队" },
  { key: "generating", label: "生成中" },
  { key: "completed", label: "已完成" },
  { key: "failed", label: "失败" },
];

const STATUS_TONE: Record<string, string> = {
  completed: "bg-emerald-50 text-emerald-700",
  failed: "bg-red-50 text-red-700",
  generating: "bg-amber-50 text-amber-700",
  queued: "bg-zinc-100 text-zinc-600",
};

function StatusIcon({ status }: { status: string }) {
  const cls = "h-4 w-4 shrink-0";
  if (status === "completed")
    return (
      <svg viewBox="0 0 16 16" className={`${cls} text-emerald-600`} aria-hidden="true">
        <circle cx="8" cy="8" r="6" fill="currentColor" opacity="0.16" />
        <path d="M5.2 8.2 7.1 10.1 10.9 6.1" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  if (status === "failed")
    return (
      <svg viewBox="0 0 16 16" className={`${cls} text-red-600`} aria-hidden="true">
        <circle cx="8" cy="8" r="6" fill="currentColor" opacity="0.14" />
        <path d="M5.6 5.6 10.4 10.4 M10.4 5.6 5.6 10.4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
    );
  if (status === "generating")
    return (
      <svg viewBox="0 0 16 16" className={`${cls} animate-spin text-amber-600`} aria-hidden="true">
        <circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" strokeWidth="1.6" opacity="0.25" />
        <path d="M8 2.5a5.5 5.5 0 0 1 5.5 5.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
    );
  return (
    <svg viewBox="0 0 16 16" className={`${cls} text-zinc-400`} aria-hidden="true">
      <circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" strokeWidth="1.4" strokeDasharray="2.2 2.4" />
    </svg>
  );
}

const STATUS_LABEL: Record<string, string> = {
  queued: "排队中",
  generating: "生成中",
  completed: "已完成",
  failed: "失败",
};

/** 失败原因人话化：技术错误 → 可读 + 可行动的建议。 */
function humanizeError(msg: string): string {
  if (/渠道返回 400/.test(msg))
    return "渠道拒绝了本次请求：当前模型可能不支持该参数或模型名，可尝试更换模型或渠道后重试。";
  if (/渠道返回 40[13]/.test(msg)) return "渠道鉴权失败：API Key 无效或已过期，请到「模型渠道」检查密钥。";
  if (/渠道返回 429/.test(msg)) return "触发渠道限流：请求过于频繁，稍等片刻再重试。";
  if (/超时|timeout/i.test(msg)) return "生成超时：模型响应时间过长，可重试或换更快的模型。";
  return msg;
}

function timeAgo(iso: string | null): string {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "刚刚";
  if (m < 60) return `${m} 分钟前`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} 小时前`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d} 天前`;
  return new Date(iso).toLocaleDateString("zh-CN");
}

export function TasksPage() {
  const qc = useQueryClient();
  const [status, setStatus] = useState("");
  const tasks = useQuery({
    queryKey: ["tasks", "all", status],
    queryFn: () => apiFetch<{ tasks: TaskMeta[] }>(`/api/tasks?limit=100${status ? `&status=${status}` : ""}`),
    refetchInterval: 5_000,
  });

  // 全量计数（不随筛选变）：完成进度徽章用
  const allTasks = useQuery({
    queryKey: ["tasks", "all", ""],
    queryFn: () => apiFetch<{ tasks: TaskMeta[] }>("/api/tasks?limit=200"),
    refetchInterval: 15_000,
  });
  const total = (allTasks.data?.tasks ?? []).length;
  const doneCount = (allTasks.data?.tasks ?? []).filter((t) => t.status === "completed").length;

  const retry = useMutation({
    mutationFn: (t: TaskMeta) =>
      apiFetch("/api/tasks", {
        method: "POST",
        json: { type: t.type, prompt: t.prompt, model: t.model },
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tasks"] }),
  });

  return (
    <div className="min-h-screen bg-zinc-50">
      <header className="border-b border-zinc-200 bg-white px-8 py-6">
        <h1 className="text-xl font-semibold">任务中心</h1>
        <p className="mt-1 text-sm text-zinc-500">所有生成任务的进度、结果与失败原因</p>
      </header>
      <main className="mx-auto max-w-5xl space-y-4 p-8">
        <div className="flex gap-1">
          {STATUS_TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setStatus(t.key)}
              className={`h-8 rounded-lg px-3 text-sm cursor-pointer ${
                status === t.key ? "bg-zinc-900 text-white" : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200"
              }`}
            >
              {t.label}
              {t.key === "completed" && doneCount > 0 && (
                <span className="ml-1.5 rounded-full bg-zinc-200 px-1.5 py-0.5 text-[10px] text-zinc-600">
                  {doneCount}/{total}
                </span>
              )}
            </button>
          ))}
        </div>

        {tasks.data?.tasks?.length ? (
          <div className="space-y-2">
            {tasks.data.tasks.map((t) => (
              <div key={t.id} className="rounded-xl border border-zinc-200 bg-white p-4">
                <div className="flex items-start justify-between gap-3">
                  {t.output_url && /\.(png|jpe?g|webp)(\?|$)/i.test(t.output_url) && (
                    <img
                      src={t.output_url}
                      alt=""
                      onError={(e) => {
                        (e.target as HTMLImageElement).style.display = "none";
                      }}
                      className="h-16 w-16 shrink-0 rounded-lg object-cover"
                    />
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <StatusIcon status={t.status} />
                      <span className={`rounded px-1.5 py-0.5 text-xs ${STATUS_TONE[t.status] ?? "bg-zinc-100 text-zinc-600"}`}>
                        {STATUS_LABEL[t.status] ?? t.status}
                      </span>
                      <span className="text-xs text-zinc-400">
                        {t.type === "llm" ? "文案" : t.type === "image" ? "图片" : t.type === "video" ? "视频" : t.type} · {t.model}
                      </span>
                      <span className="ml-auto text-xs text-zinc-300">{timeAgo(t.created_at ?? null)}</span>
                    </div>
                    <p className="mt-1.5 truncate text-sm text-zinc-800">{t.prompt}</p>
                    {t.status === "failed" && t.error_msg && (
                      <p className="mt-1 text-xs leading-relaxed text-red-600">失败原因：{humanizeError(t.error_msg)}</p>
                    )}
                    {t.output_url && /\.(png|jpe?g|webp)(\?|$)/i.test(t.output_url) ? (
                      <a href={t.output_url} target="_blank" rel="noreferrer" className="mt-1 inline-block text-xs text-zinc-500 underline">
                        查看原图
                      </a>
                    ) : t.output_url ? (
                      <div className="mt-1.5 flex items-start gap-2">
                        <p className="min-w-0 flex-1 whitespace-pre-wrap break-all text-xs leading-relaxed text-zinc-600 [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:3] overflow-hidden">
                          {t.output_url.replace(/^<think>[\s\S]*?<\/think>/, "").trim()}
                        </p>
                        <button
                          className="shrink-0 text-xs text-zinc-400 underline hover:text-zinc-600"
                          onClick={() => {
                            void copyText(t.output_url ?? "");
                          }}
                        >
                          复制
                        </button>
                      </div>
                    ) : null}
                  </div>
                  {t.status === "failed" && (
                    <Button variant="outline" size="sm" onClick={() => retry.mutate(t)}>
                      重试
                    </Button>
                  )}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-zinc-400">该状态下暂无任务</p>
        )}
      </main>
    </div>
  );
}
