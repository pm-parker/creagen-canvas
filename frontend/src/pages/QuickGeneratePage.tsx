import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import { Download, Settings2, Sparkles } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { GenerationPulseCard } from "@/components/GenerationPulse";
import { ModelSelect } from "@/components/canvas/ModelSelect";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface RunItem {
  taskId: number;
  prompt: string;
  status: "queued" | "generating" | "completed" | "failed";
  outputUrl?: string;
  error?: string;
  startedAt: number;
}

/** 独立快捷生成页（image / video 共用）。提交后本地轮询结果。 */
export function QuickGeneratePage({ domain }: { domain: "image" | "video" }) {
  const [searchParams] = useSearchParams();
  const [prompt, setPrompt] = useState(searchParams.get("prompt") ?? "");
  const [count, setCount] = useState(domain === "video" ? 6 : 1);
  const [size, setSize] = useState("1024x1024");
  const [negative, setNegative] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [runs, setRuns] = useState<RunItem[]>([]);

  useEffect(() => {
    const fromQuery = searchParams.get("prompt");
    if (fromQuery) setPrompt(fromQuery);
  }, [searchParams]);

  const { channels } = useDomainChannelsPublic(domain);

  const [submitting, setSubmitting] = useState(false);
  const prices = useQuery({
    queryKey: ["costs", "prices"],
    queryFn: () =>
      apiFetch<{
        image: Record<string, number>;
        video: Record<string, number>;
        fallback: { image: number; video: number };
      }>("/api/costs/prices"),
    staleTime: 300_000,
  });
  const [submitError, setSubmitError] = useState("");

  /** 预估成本（CNY）：图按张、视频按秒；价格表未覆盖时用兜底价并标"约"。 */
  function estimate(): { text: string; approx: boolean } | null {
    const p = prices.data;
    if (!p) return null;
    const model = chosenModel(domain, channels);
    if (domain === "image") {
      const known = p.image[model];
      const price = known ?? p.fallback.image;
      const total = price * count;
      return { text: `¥${total.toFixed(2)}`, approx: known === undefined };
    }
    const known = p.video[model];
    const price = known ?? p.fallback.video;
    const total = price * count;
    return { text: `¥${total.toFixed(2)}`, approx: known === undefined };
  }

  async function submit() {
    if (!prompt.trim() || submitting) return;
    setSubmitting(true);
    setSubmitError("");
    try {
      const n = domain === "image" ? count : 1;
      const batch: RunItem[] = [];
      for (let i = 0; i < n; i++) {
        const resp = await apiFetch<{ task: { id: number; status: string } }>("/api/tasks", {
          method: "POST",
          json: {
            type: domain,
            prompt: prompt.trim(),
            model: chosenModel(domain, channels),
            params: domain === "image"
              ? { image_count: count }
              : { duration: count, resolution: size },
          },
        });
        batch.push({ taskId: resp.task.id, prompt: prompt.trim(), status: "queued", startedAt: Date.now() });
      }
      setRuns((r) => [...batch, ...r]);
      pollBatch(batch);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : "提交失败，请稍后重试");
    } finally {
      setSubmitting(false);
    }
  }

  function pollBatch(batch: RunItem[]) {
    const timer = setInterval(async () => {
      let allDone = true;
      for (const item of batch) {
        try {
          const detail = await apiFetch<{ task: { id: number; status: string; output_url?: string; error_msg?: string } }>(
            `/api/tasks/${item.taskId}`,
          );
          const t = detail.task;
          if (t.status === "completed" || t.status === "failed") {
            setRuns((rs) =>
              rs.map((r) =>
                r.taskId === t.id
                  ? { ...r, status: t.status as RunItem["status"], outputUrl: t.output_url, error: t.error_msg }
                  : r,
              ),
            );
          } else {
            allDone = false;
          }
        } catch {
          allDone = false;
        }
      }
      if (allDone) clearInterval(timer);
    }, 2500);
  }

  const placeholder = domain === "image" ? "描述你想要的画面，如：赛博朋克风格的夜景街道" : "描述视频内容，如：海浪拍打礁石的慢镜头";

  return (
    <div className="min-h-screen bg-zinc-50">
      <header className="border-b border-zinc-200 bg-white px-8 py-6">
        <h1 className="text-xl font-semibold">{domain === "image" ? "AI 生图" : "AI 生视频"}</h1>
        <p className="mt-1 text-sm text-zinc-500">选模型、写提示词、一键生成</p>
      </header>
      <main className="mx-auto max-w-4xl space-y-5 p-8">
        <div className="space-y-2 rounded-2xl border border-zinc-200 bg-white p-5">
          <div className="flex items-center gap-3">
            <Label className="shrink-0">模型</Label>
            <ModelSelect domain={domain} />
            {channels.length === 0 && (
              <span className="text-xs text-zinc-400">（未配置渠道时将使用内置演示通道）</span>
            )}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="quick-prompt">提示词</Label>
            <textarea
              id="quick-prompt"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder={placeholder}
              rows={4}
              maxLength={4000}
              className="w-full rounded-xl border border-zinc-200 p-3 text-sm outline-none focus:border-zinc-400"
            />
          </div>
          {domain === "video" && (
            <div className="flex items-center gap-3">
              <Label className="shrink-0">时长</Label>
              <div className="flex gap-1">
                {[6, 10].map((n) => (
                  <button
                    key={n}
                    onClick={() => setCount(n)}
                    className={"h-8 w-12 rounded-lg text-sm cursor-pointer " + (count === n ? "bg-zinc-900 text-white" : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200")}
                  >
                    {n}s
                  </button>
                ))}
              </div>
              <Label className="ml-4 shrink-0">分辨率</Label>
              <select
                value={size}
                onChange={(e) => setSize(e.target.value)}
                className="h-8 rounded-lg border border-zinc-200 bg-white px-2 text-sm"
              >
                <option value="768p">768P</option>
                <option value="1080p">1080P</option>
              </select>
            </div>
          )}
          {submitError && <p className="text-xs text-red-600">{submitError}</p>}
          {domain === "image" && (
            <div className="flex items-center gap-3">
              <Label className="shrink-0">张数</Label>
              <div className="flex gap-1">
                {[1, 2, 4].map((n) => (
                  <button
                    key={n}
                    onClick={() => setCount(n)}
                    className={`h-8 w-10 rounded-lg text-sm cursor-pointer ${
                      count === n ? "bg-zinc-900 text-white" : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200"
                    }`}
                  >
                    {n}
                  </button>
                ))}
              </div>
              <Label className="ml-4 shrink-0">尺寸</Label>
              <select
                value={size}
                onChange={(e) => setSize(e.target.value)}
                className="h-8 rounded-lg border border-zinc-200 bg-white px-2 text-sm"
              >
                <option value="1024x1024">方图 1:1</option>
                <option value="1024x1792">竖图 9:16</option>
                <option value="1792x1024">横图 16:9</option>
              </select>
            </div>
          )}
          <div>
            <button
              onClick={() => setShowAdvanced((v) => !v)}
              className="flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-800 cursor-pointer"
            >
              <Settings2 className={`h-3.5 w-3.5 transition-transform ${showAdvanced ? "rotate-90" : ""}`} />
              高级选项
            </button>
            {showAdvanced && domain === "image" && (
              <div className="mt-2 space-y-1.5">
                <Label htmlFor="quick-negative">负面提示词（不想出现的元素）</Label>
                <Input
                  id="quick-negative"
                  placeholder="如：模糊、多余手指、低质量"
                  value={negative}
                  onChange={(e) => setNegative(e.target.value)}
                />
              </div>
            )}
          </div>
          <Button onClick={submit} disabled={!prompt.trim() || submitting}>
            {submitting ? "提交中…" : domain === "image" ? `生成 ${count} 张` : "生成视频"}
          </Button>
          {(() => {
            const est = estimate();
            if (!est || submitting) return null;
            return (
              <span className="text-xs text-zinc-400" title="按当前模型单价估算，实际以渠道计费为准">
                预计 {est.approx ? "约 " : ""}
                {est.text}
              </span>
            );
          })()}
        </div>

        {runs.length === 0 && (
          <section className="flex flex-col items-center justify-center gap-2 rounded-2xl border border-dashed border-zinc-200 bg-white/60 py-12 text-center">
            <Sparkles className="h-5 w-5 text-zinc-300" />
            <p className="text-sm text-zinc-400">填写提示词点击生成，结果会出现在这里</p>
          </section>
        )}
        {runs.length > 0 && (
          <section className="space-y-3">
            <h2 className="text-sm font-medium text-zinc-500">生成结果</h2>
            <div className="columns-2 gap-4 lg:columns-3 [&>*]:mb-4">
              {runs.map((r) => (
                <div
                  key={r.taskId}
                  data-testid={`gen-result-${r.taskId}`}
                  className="break-inside-avoid overflow-hidden rounded-xl border border-zinc-200 bg-white shadow-xs"
                >
                  {r.status === "completed" && r.outputUrl ? (
                    domain === "image" ? (
                      <img src={r.outputUrl} alt={r.prompt} className="w-full" />
                    ) : (
                      <div className="flex aspect-video items-center justify-center bg-zinc-100 text-sm text-zinc-500">
                        视频已生成
                      </div>
                    )
                  ) : r.status === "failed" ? (
                    <div className="flex aspect-video items-center justify-center bg-red-50 p-3 text-xs text-red-600">{r.error}</div>
                  ) : (
                    <div className="flex aspect-square flex-col items-center justify-center gap-3 p-4">
                      <GenerationPulseCard startedAt={r.startedAt} />
                    </div>
                  )}
                  <div className="flex items-center justify-between gap-2 px-3 py-2">
                    <span className="truncate text-xs text-zinc-500">{r.prompt}</span>
                    {r.outputUrl && (
                      <a href={r.outputUrl} download target="_blank" rel="noreferrer" aria-label="下载">
                        <Download className="h-3.5 w-3.5 shrink-0 text-zinc-400 hover:text-zinc-700" />
                      </a>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}
      </main>
    </div>
  );
}

// ---- 模型选择：localStorage 记忆 → 渠道目录首个启用模型 → 域默认 ----
function chosenModel(
  domain: string,
  channels: Array<{ models: Array<{ model_id: string; enabled: boolean }> }>,
): string {
  try {
    const raw = localStorage.getItem("cc-model-choice");
    if (raw) {
      const parsed = JSON.parse(raw) as { state?: { choice?: Record<string, { model: string }> } };
      const m = parsed.state?.choice?.[domain]?.model;
      if (m) return m;
    }
  } catch {
    /* ignore */
  }
  const first = channels[0]?.models.find((m) => m.enabled);
  if (first) return first.model_id;
  return domain === "image" ? "Kwai-Kolors/Kolors" : "MiniMax-Hailuo-2.3-Fast";
}

function useDomainChannelsPublic(domain: string) {
  const query = useQuery({
    queryKey: ["providers", "channels"],
    queryFn: () => apiFetch<{ channels: Array<{ id: number; domain: string; name: string; enabled: boolean; models: Array<{ model_id: string; enabled: boolean }> }> }>("/api/providers/channels"),
  });
  const channels = (query.data?.channels ?? []).filter((c) => c.domain === domain && c.enabled);
  return { channels };
}
