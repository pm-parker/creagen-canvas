import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Camera, Loader2 } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { useCanvasStore } from "@/stores/canvas";
import { useModelChoice, useDomainChannels } from "@/components/canvas/ModelSelect";
import { buildGenerationParams, type GenerationBody } from "@/hooks/useGenerate";

/** 角度模板（中文指令后缀，与 Kolors 等中文理解模型配合良好）。 */
export const ANGLE_PRESETS = [
  { key: "side", label: "侧面", directive: "侧面视角" },
  { key: "back", label: "背面", directive: "背面视角" },
  { key: "top", label: "俯视", directive: "俯视角度，从上往下看" },
  { key: "bottom", label: "仰视", directive: "仰视角度，从下往上看" },
  { key: "closeup", label: "特写", directive: "特写镜头，主体占满画面" },
  { key: "wide", label: "远景", directive: "远景构图，主体缩小并呈现环境" },
  { key: "low", label: "低机位", directive: "低机位仰拍，透视夸张" },
] as const;

interface AngleRun {
  key: string;
  label: string;
  taskId: number | null;
  status: "queued" | "generating" | "completed" | "failed";
  error?: string;
}

/** 多角度生成：基于图片节点 prompt 派生 N 个角度变体并行生成，产物作为新节点排布在源节点下方。 */
export function MultiAngleDialog({
  nodeId,
  nodeType,
  prompt,
  onClose,
}: {
  nodeId: string;
  nodeType: "image";
  prompt: string;
  onClose: () => void;
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set(["side", "top", "closeup"]));
  // P1-2 自定义角度：追加指令行，与预设同权提交
  const [customs, setCustoms] = useState<Array<{ key: string; label: string; directive: string }>>([]);
  const [customDraft, setCustomDraft] = useState("");
  const [runs, setRuns] = useState<AngleRun[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // P1-2 修复：unmount 清理轮询（修复前 interval 泄漏且闭包写已卸载组件）
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);
  const { channels } = useDomainChannels(nodeType);
  const choice = useModelChoice((s) => s.choice)[nodeType];
  const channel = channels.find((c) => c.id === choice?.channelId) ?? channels[0];
  const model = choice?.model || channel?.models.find((m) => m.enabled)?.model_id || "Kwai-Kolors/Kolors";

  function toggle(key: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function buildBody(angleDirective: string): GenerationBody {
    const node = {
      id: nodeId,
      data: { nodeType, prompt: `${prompt.trim()}，${angleDirective}` },
    };
    const body = buildGenerationParams(node as never, model, []);
    if (channel) (body.params as Record<string, unknown>).channel_id = channel.id;
    return body;
  }

  function addCustom() {
    const v = customDraft.trim();
    if (!v || busy) return;
    const key = `c-${Date.now()}`;
    setCustoms((list) => [...list, { key, label: v.length > 8 ? v.slice(0, 8) + "…" : v, directive: v }]);
    setSelected((prev) => new Set([...prev, key]));
    setCustomDraft("");
  }

  async function submit() {
    if (busy || selected.size === 0) return;
    setBusy(true);
    setError("");
    try {
      const presets = [...ANGLE_PRESETS, ...customs].filter((a) => selected.has(a.key));
      const init: AngleRun[] = [];
      for (const a of presets) {
        const resp = await apiFetch<{ task: { id: number } }>("/api/tasks", {
          method: "POST",
          json: buildBody(a.directive),
        });
        init.push({ key: a.key, label: a.label, taskId: resp.task.id, status: "queued" });
      }
      setRuns(init);
      pollRef.current = setInterval(() => void poll(init.map((r) => r.taskId!).filter(Boolean)), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "提交失败");
      setBusy(false);
    }
  }

  async function poll(taskIds: number[]) {
    let allDone = true;
    for (const tid of taskIds) {
      try {
        const detail = await apiFetch<{ task: { id: number; status: string; output_url?: string; error_msg?: string } }>(
          `/api/tasks/${tid}`,
        );
        const t = detail.task;
        setRuns((list) => list.map((r) => (r.taskId === tid ? { ...r, status: t.status as AngleRun["status"], error: t.error_msg } : r)));
        if (t.status === "completed") {
          addResultNode(tid, t.output_url ?? "");
        } else if (t.status !== "completed" && t.status !== "failed") {
          allDone = false;
        }
      } catch {
        allDone = false;
      }
    }
    if (allDone && pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
      setBusy(false);
    }
  }

  function addResultNode(taskId: number, outputUrl: string) {
    const store = useCanvasStore.getState();
    // 幂等：同一任务只加一次
    if (store.nodes.some((n) => n.data.title?.includes(`#${taskId}`))) return;
    const run = runsRef.current.find((r) => r.taskId === taskId);
    const source = store.nodes.find((n) => n.id === nodeId);
    const parentPos = source?.position ?? { x: 0, y: 0 };
    const index = store.nodes.filter((n) => n.data.title?.includes("#")).length;
    const id = store.addNode("image", { x: parentPos.x + index * 320 - 160, y: parentPos.y + 300 });
    store.updateNodeData(id, {
      title: `${run?.label ?? "角度"} #${taskId}`,
      outputUrl,
      prompt,
    });
    store.onConnect({ source: nodeId, target: id, sourceHandle: null, targetHandle: null });
  }

  // runs 的镜像 ref（poll 闭包读取最新值）
  const runsRef = useRef(runs);
  runsRef.current = runs;

  return createPortal(
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="max-h-[92vh] w-full max-w-md space-y-3 overflow-y-auto rounded-2xl bg-white p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-semibold">多角度生成</h3>
          <span className="text-xs text-zinc-400">{model}</span>
        </div>
        <p className="text-xs text-zinc-500">
          基于当前节点的提示词派生角度变体并行生成，每张结果作为新节点连回本节点。
        </p>
        <div className="rounded-lg bg-zinc-50 p-2 text-xs text-zinc-500">
          <span className="line-clamp-2">{prompt}</span>
        </div>

        <div className="flex flex-wrap gap-2">
          {[...ANGLE_PRESETS, ...customs].map((a) => (
            <span key={a.key} className="relative">
              <button
                onClick={() => toggle(a.key)}
                disabled={busy}
                className={`h-8 cursor-pointer rounded-lg px-3 text-xs ${
                  selected.has(a.key)
                    ? "bg-zinc-900 text-white"
                    : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200"
                } disabled:opacity-50`}
              >
                {a.label}
              </button>
              {a.key.startsWith("c-") && (
                <button
                  aria-label={`移除 ${a.label}`}
                  onClick={() => {
                    setCustoms((list) => list.filter((x) => x.key !== a.key));
                    setSelected((prev) => {
                      const n = new Set(prev);
                      n.delete(a.key);
                      return n;
                    });
                  }}
                  className="absolute -right-1.5 -top-1.5 flex h-4 w-4 items-center justify-center rounded-full bg-zinc-300 text-[9px] text-zinc-700 hover:bg-red-400 cursor-pointer"
                >
                  ×
                </button>
              )}
            </span>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <input
            value={customDraft}
            onChange={(e) => setCustomDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addCustom();
              }
            }}
            placeholder="自定义角度/指令，如：俯视45度侧面"
            aria-label="自定义角度"
            className="h-8 flex-1 rounded-lg border border-zinc-200 px-2 text-xs outline-none focus:border-zinc-400"
          />
          <button
            onClick={addCustom}
            disabled={busy || !customDraft.trim()}
            className="h-8 rounded-lg bg-zinc-100 px-3 text-xs text-zinc-600 hover:bg-zinc-200 disabled:opacity-40 cursor-pointer"
          >
            添加
          </button>
        </div>

        {runs.length > 0 && (
          <div className="space-y-1 rounded-lg border border-zinc-100 p-2">
            {runs.map((r) => (
              <div key={r.key} className="flex items-center justify-between text-xs">
                <span className="text-zinc-700">{r.label}</span>
                <span
                  className={
                    r.status === "completed"
                      ? "text-emerald-600"
                      : r.status === "failed"
                        ? "text-red-600"
                        : "text-amber-600"
                  }
                >
                  {r.status === "completed"
                    ? "✓ 完成"
                    : r.status === "failed"
                      ? `✗ ${r.error ?? "失败"}`
                      : r.status === "generating"
                        ? "生成中…"
                        : "排队中"}
                </span>
              </div>
            ))}
          </div>
        )}

        {error && <p className="text-xs text-red-600">{error}</p>}

        <div className="flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={onClose}>
            {runs.length > 0 ? "后台继续，关闭" : "取消"}
          </Button>
          {runs.length === 0 && (
            <Button size="sm" onClick={submit} disabled={busy || selected.size === 0}>
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Camera className="h-4 w-4" />}
              生成 {selected.size} 个角度
            </Button>
          )}
        </div>
      </div>
    </div>
  , document.body);
}
