import { useEffect, useRef, useState } from "react";
import { Sparkles, X } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { useCanvasStore } from "@/stores/canvas";
import { usePanel } from "@/stores/panel";
import { ModelSelect, useModelChoice } from "@/components/canvas/ModelSelect";
import { useTaskStore } from "@/stores/tasks";

const DEBOUNCE_MS = 300;
const MIN_HEIGHT = 200;
const MIN_WIDTH = 420;

/** 节点放大提示词面板（对标 BeefTV：右下角对角拖拽、方向键微调、Esc 关闭）。 */
export function PromptPanel({ initial = { width: 800, height: 240 } }: { initial?: { width: number; height: number } }) {
  const activeNodeId = usePanel((s) => s.activeNodeId);
  const close = usePanel((s) => s.close);
  const nodes = useCanvasStore((s) => s.nodes);
  const updateNodeData = useCanvasStore((s) => s.updateNodeData);
  const toggleRef = usePanel((s) => s.toggleRef);

  const node = nodes.find((n) => n.id === activeNodeId);
  const allEdges = useCanvasStore((s) => s.edges);
  const upstreamImages = allEdges
    .filter((e) => e.target === activeNodeId)
    .map((e) => nodes.find((n) => n.id === e.source))
    .filter((n): n is NonNullable<typeof n> => Boolean(n) && n!.data.nodeType === "image")
    .map((n) => ({ id: n!.id, title: n!.data.title, url: n!.data.outputUrl as string | undefined }))
    .filter((u) => Boolean(u.url));
  const [draft, setDraft] = useState("");
  const [size, setSize] = useState(initial);
  const [optimizing, setOptimizing] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [optimizeError, setOptimizeError] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const resizing = useRef<{ startX: number; startY: number; w: number; h: number } | null>(null);

  // 切换节点时重置草稿
  useEffect(() => {
    setDraft(node?.data.prompt ?? "");
  }, [activeNodeId]); // eslint-disable-line react-hooks/exhaustive-deps -- 仅在切换节点时同步

  // 卸载前把未落盘草稿写回
  useEffect(() => {
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  function flush(value: string) {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      if (activeNodeId) updateNodeData(activeNodeId, { prompt: value });
    }, DEBOUNCE_MS);
  }

  function handleResizeStart(e: React.PointerEvent) {
    e.preventDefault();
    resizing.current = { startX: e.clientX, startY: e.clientY, w: size.width, h: size.height };
    const move = (ev: PointerEvent) => {
      if (!resizing.current) return;
      setSize({
        width: Math.max(MIN_WIDTH, resizing.current.w + (ev.clientX - resizing.current.startX)),
        height: Math.max(MIN_HEIGHT, resizing.current.h + (ev.clientY - resizing.current.startY)),
      });
    };
    const up = () => {
      resizing.current = null;
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  if (!activeNodeId || !node) return null;

  return (
    <div
      data-testid="prompt-panel"
      className="fixed bottom-6 left-1/2 z-20 -translate-x-1/2 rounded-2xl bg-white shadow-2xl border border-zinc-200"
      style={{ width: size.width }}
      onKeyDown={(e) => {
        // 阻断画布快捷键（Delete 等）：面板内所有键盘事件不冒泡到画布容器
        e.stopPropagation();
      }}
    >
      <div className="flex items-center justify-between px-4 pt-3">
        <p className="text-xs font-medium text-zinc-500">
          提示词 · {node.data.title || node.data.nodeType}
        </p>
        <button
          aria-label="关闭面板"
          onClick={() => {
            if (timer.current) clearTimeout(timer.current);
            if (activeNodeId) updateNodeData(activeNodeId, { prompt: draft });
            close();
          }}
          className="text-zinc-400 hover:text-zinc-700 cursor-pointer"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      {upstreamImages.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 px-4 pt-2" data-testid="smart-refs">
          <span className="text-xs text-zinc-400">引用：</span>
          {upstreamImages.map((u) => {
            const excluded = (usePanel.getState().excludedRefs[activeNodeId!] ?? []).includes(u.id);
            return (
              <button
                key={u.id}
                onClick={() => activeNodeId && toggleRef(activeNodeId, u.id)}
                className={
                  "rounded-full px-2 py-0.5 text-xs cursor-pointer " +
                  (excluded
                    ? "bg-zinc-100 text-zinc-400 line-through"
                    : "bg-emerald-50 text-emerald-700 border border-emerald-200")
                }
              >
                @{u.title || u.id.slice(0, 6)}
              </button>
            );
          })}
        </div>
      )}
      {optimizeError && <p className="px-4 text-xs text-red-600">{optimizeError}</p>}
      <div className="px-4 pb-3 pt-2">
        <textarea
          autoFocus
          aria-label="提示词编辑"
          data-testid="prompt-textarea"
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value);
            flush(e.target.value);
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              if (timer.current) clearTimeout(timer.current);
              updateNodeData(activeNodeId, { prompt: draft });
              close();
            }
            // 方向键在面板内消费，不触发画布平移/快捷键
            if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(e.key)) {
              e.stopPropagation();
            }
          }}
          className="w-full resize-none rounded-xl border border-zinc-200 p-3 text-sm leading-relaxed outline-none focus:border-zinc-400"
          style={{ height: size.height, minHeight: MIN_HEIGHT }}
          placeholder="描述这一节点的画面、内容或要生成的目标…"
        />
        <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-zinc-100 pt-2">
          {upstreamImages.length > 0 && (
            <div className="flex max-w-[45%] flex-wrap items-center gap-1" data-testid="smart-refs">
              {upstreamImages.slice(0, 3).map((u) => {
                const excluded = (usePanel.getState().excludedRefs[activeNodeId!] ?? []).includes(u.id);
                return (
                  <button
                    key={u.id}
                    onClick={() => activeNodeId && toggleRef(activeNodeId, u.id)}
                    title={excluded ? "点击恢复引用" : "点击排除引用"}
                    className={
                      "rounded-full px-2 py-0.5 text-[11px] cursor-pointer " +
                      (excluded
                        ? "bg-zinc-100 text-zinc-400 line-through"
                        : "bg-emerald-50 text-emerald-700 border border-emerald-200")
                    }
                  >
                    @{u.title || u.id.slice(0, 6)}
                  </button>
                );
              })}
              {upstreamImages.length > 3 && (
                <span className="text-[11px] text-zinc-400">+{upstreamImages.length - 3}</span>
              )}
            </div>
          )}
          <span className="flex-1" />
          <ModelSelect domain={node.data.nodeType === "text" ? "llm" : node.data.nodeType} />
          <button
            aria-label="AI 优化提示词"
            data-testid="optimize-button"
            title="AI 优化"
            disabled={optimizing || draft.trim().length < 4}
            onClick={async () => {
              setOptimizing(true);
              setOptimizeError("");
              try {
                const resp = await apiFetch<{ optimized: string }>("/api/prompts/optimize", {
                  method: "POST",
                  json: { prompt: draft },
                });
                setDraft(resp.optimized);
                if (activeNodeId) updateNodeData(activeNodeId, { prompt: resp.optimized });
              } catch (err) {
                setOptimizeError(err instanceof Error ? err.message : "优化失败");
              } finally {
                setOptimizing(false);
              }
            }}
            className="flex h-8 items-center gap-1 rounded-lg bg-indigo-50 px-2.5 text-xs text-indigo-600 hover:bg-indigo-100 disabled:opacity-40 cursor-pointer"
          >
            <Sparkles className="h-3.5 w-3.5" />
            {optimizing ? "优化中…" : "AI 优化"}
          </button>
          <button
            aria-label="生成"
            data-testid="composer-generate"
            title="保存并生成本节点"
            disabled={generating || draft.trim().length < 1}
            onClick={async () => {
              if (activeNodeId) updateNodeData(activeNodeId, { prompt: draft });
              setGenerating(true);
              try {
                const store = useCanvasStore.getState();
                const node = store.nodes.find((n) => n.id === activeNodeId);
                if (!node) return;
                const edges = store.edges;
                const nodes = store.nodes;
                const excluded = usePanel.getState().excludedRefs[activeNodeId!] ?? [];
                const upstream = edges
                  .filter((e) => e.target === node.id)
                  .map((e) => nodes.find((n) => n.id === e.source))
                  .filter((n): n is NonNullable<typeof n> => Boolean(n));
                const referenceUrls = upstream
                  .filter((n) => !excluded.includes(n.id))
                  .filter((n) => n.data.nodeType === "image" && n.data.outputUrl)
                  .map((n) => n.data.outputUrl as string);
                const domain = node.data.nodeType === "text" ? "llm" : node.data.nodeType;
                const params: Record<string, unknown> = {};
                if (node.data.nodeType === "image") params.image_count = 1;
                if (node.data.nodeType === "video") params.duration = 5;
                if (referenceUrls.length > 0) params.reference_urls = referenceUrls;
                const choice = useModelChoice.getState().choice[node.data.nodeType === "text" ? "llm" : node.data.nodeType];
                if (choice?.channelId) params.channel_id = choice.channelId;
                const DOMAIN_DEFAULT_MODEL: Record<string, string> = {
                  image: "Kwai-Kolors/Kolors",
                  llm: "MiniMax-M2.7",
                  video: "MiniMax-Hailuo-2.3-Fast",
                  audio: "tts-1",
                };
                const model = choice?.model || DOMAIN_DEFAULT_MODEL[domain] || "Kwai-Kolors/Kolors";
                const resp = await apiFetch<{ task: { id: number } }>("/api/tasks", {
                  method: "POST",
                  json: { type: domain, prompt: node.data.prompt.trim(), model, params },
                });
                useTaskStore.getState().attach(node.id, resp.task.id);
                close();
              } finally {
                setGenerating(false);
              }
            }}
            className="flex h-8 items-center gap-1 rounded-lg bg-zinc-900 px-3 text-xs font-medium text-white hover:bg-zinc-700 disabled:opacity-40 cursor-pointer"
          >
            {generating ? "提交中…" : "生成"}
          </button>
        </div>
      </div>
      <div
        aria-label="调整面板大小"
        onPointerDown={handleResizeStart}
        className="absolute -right-1 -bottom-1 h-5 w-5 cursor-nwse-resize rounded-br-2xl"
        style={{ background: "linear-gradient(135deg, transparent 50%, #d4d4d8 50%)" }}
      />
    </div>
  );
}
