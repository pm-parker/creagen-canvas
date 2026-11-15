import { useEffect, useState } from "react";
import { Play, Check, X, Loader2 } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { useCanvasStore, type CanvasNode } from "@/stores/canvas";
import { useTaskStore, type TaskRun } from "@/stores/tasks";
import { usePanel } from "@/stores/panel";
import { buildGenerationParams, canSubmit } from "@/hooks/useGenerate";
import { inflowResult } from "@/hooks/useResultInflow";
import { useModelChoice, useDomainChannels } from "@/components/canvas/ModelSelect";

const DEFAULT_MODEL: Record<string, string> = {
  text: "gpt-4o-mini",
  image: "Kwai-Kolors/Kolors",
  video: "MiniMax-Hailuo-2.3-Fast",
  audio: "tts-1",
};

/** 节点生成按钮 + 任务状态徽标（订阅→回流→幂等）。 */
export function GenerateButton({ node }: { node: CanvasNode }) {
  const [taskId, setTaskId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const edges = useCanvasStore((s) => s.edges);
  const nodes = useCanvasStore((s) => s.nodes);
  const { channels: domainChannels } = useDomainChannels(node.data.nodeType);
  const modelChoice = useModelChoice((s) => s.choice)[node.data.nodeType];
  const effectiveModel =
    modelChoice?.model ||
    domainChannels.find((c) => c.models.some((m) => m.model_id === DEFAULT_MODEL[node.data.nodeType]))?.models[0]
      ?.model_id ||
    DEFAULT_MODEL[node.data.nodeType];

  const runs = useTaskStore((s) => s.runs);
  const attach = useTaskStore((s) => s.attach);
  const update = useTaskStore((s) => s.update);
  // 节点的历次 run：优先 pending（提示词面板提交的任务也在此接管，否则卡"生成中"），否则最新一条
  const nodeRuns = Object.values(runs).filter((r) => r.sourceNodeId === node.id);
  const existing =
    nodeRuns.find((r) => r.status === "queued" || r.status === "generating") ??
    nodeRuns[nodeRuns.length - 1] ??
    null;

  // 订阅目标：自己提交的 streamId，或本节点任何 pending 任务（面板代提交的也回流）
  const [streamId, setStreamId] = useState<number | null>(null);
  const pendingTaskId =
    streamId ??
    (existing && (existing.status === "queued" || existing.status === "generating")
      ? existing.taskId
      : null);

  useEffect(() => {
    if (!pendingTaskId) return;
    let cancelled = false;
    import("@/lib/sse")
      .then(({ consumeSseStream }) =>
        consumeSseStream(
        pendingTaskId,
        (type, data) => {
          if (cancelled) return;
          if (type === "completed") {
            const output = String(data.output_url ?? "");
            const isText = !/\.(png|jpe?g|webp|mp4|mp3|wav)(\?|$)/i.test(output);
            update(pendingTaskId, { status: "completed", outputUrl: output });
            useTaskStore
              .getState()
              .recordGeneration(node.id, { taskId: pendingTaskId, outputUrl: output, prompt: node.data.prompt });
            inflowResult(`t-${pendingTaskId}`, node.id, {
              status: "completed",
              output_url: output,
              result_type: isText ? "text" : undefined,
            });
          } else if (type === "failed") {
            update(pendingTaskId, { status: "failed", error: String(data.error ?? "") });
          } else {
            update(pendingTaskId, { status: "generating", phase: String(data.phase ?? "") });
          }
        },
        {
          fallback: async () => {
            const detail = await apiFetch<{ task: { status: string; output_url?: string; error_msg?: string } }>(
              `/api/tasks/${pendingTaskId}`,
            );
            const t = detail.task;
            if (t.status !== "completed" && t.status !== "failed") return undefined;
            return {
              type: t.status,
              data: { output_url: t.output_url ?? "", error: t.error_msg ?? "" },
            };
          },
        },
      ),
    )
    .catch(() => undefined); // 订阅链任何异常不成 unhandled rejection（pageerror）
    return () => {
      cancelled = true;
    };
  }, [pendingTaskId, node.id, update]);

  async function handleGenerate() {
    setError("");
    // 上游参考节点：连入本节点的边的 source
    const upstream = edges
      .filter((e) => e.target === node.id)
      .map((e) => nodes.find((n) => n.id === e.source))
      .filter((n): n is CanvasNode => Boolean(n));
    const excluded = usePanel.getState().excludedRefs[node.id] ?? [];
    const choice = useModelChoice.getState().choice[node.data.nodeType];
    const body = buildGenerationParams(node, effectiveModel || "test-model", upstream, excluded);
    if (choice?.channelId) (body.params as Record<string, unknown>).channel_id = choice.channelId;
    try {
      const resp = await apiFetch<{ task: { id: number } }>("/api/tasks", {
        method: "POST",
        json: body,
      });
      attach(node.id, resp.task.id);
      setTaskId(resp.task.id);
      setStreamId(resp.task.id);
    } catch (err) {
      const e = err as { message?: string; hint?: string };
      setError(`${e.message ?? "提交失败"}${e.hint ? `：${e.hint}` : ""}`);
    }
  }

  const current: TaskRun | null = taskId ? runs[taskId] ?? existing ?? null : existing ?? null;
  const pending = current && (current.status === "queued" || current.status === "generating");

  if (current?.status === "completed") {
    return (
      <span aria-label="生成完成" className="absolute -top-2 -right-2 rounded-full bg-emerald-600 p-1">
        <Check className="h-3 w-3 text-white" />
      </span>
    );
  }
  if (current?.status === "failed") {
    return (
      <span
        aria-label={`生成失败：${current.error ?? ""}`}
        title={current.error}
        className="absolute -top-2 -right-2 rounded-full bg-red-600 p-1"
      >
        <X className="h-3 w-3 text-white" />
      </span>
    );
  }
  if (pending) {
    return (
      <span aria-label="生成中" className="absolute -top-2 -right-2 rounded-full bg-zinc-900 p-1">
        <Loader2 className="h-3 w-3 animate-spin text-white" />
      </span>
    );
  }

  return (
    <div className="absolute -top-3 right-2">
      <button
        aria-label="生成"
        data-testid="generate-button"
        disabled={!canSubmit(node)}
        onClick={handleGenerate}
        className="flex items-center gap-1 rounded-full bg-zinc-900 px-2.5 py-1 text-xs text-white hover:bg-zinc-700 disabled:opacity-40 cursor-pointer"
      >
        <Play className="h-3 w-3" /> 生成
      </button>
      {error && <p className="mt-1 max-w-48 text-right text-xs text-red-600">{error}</p>}
    </div>
  );
}

