import { useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Loader2, SlidersHorizontal } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { useCanvasStore } from "@/stores/canvas";
import {
  DEFAULT_ADJUST,
  buildFilterString,
  clampAdjust,
  isDefaultAdjust,
  type AdjustValues,
} from "@/lib/image-adjust";
import {
  planBatchAdjust,
  summarizeBatchResult,
  type BatchItemResult,
  type BatchTarget,
} from "@/lib/batch-adjust";

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const el = new window.Image();
    el.crossOrigin = "anonymous";
    el.onload = () => resolve(el);
    el.onerror = () => reject(new Error("图片加载失败"));
    el.src = src;
  });
}

const SLIDERS: Array<{
  key: keyof AdjustValues;
  label: string;
  min: number;
  max: number;
  step: number;
}> = [
  { key: "brightness", label: "亮度", min: 50, max: 200, step: 1 },
  { key: "contrast", label: "对比度", min: 50, max: 200, step: 1 },
  { key: "saturate", label: "饱和度", min: 0, max: 200, step: 1 },
  { key: "sepia", label: "色温（暖）", min: 0, max: 100, step: 1 },
  { key: "blur", label: "模糊", min: 0, max: 12, step: 0.5 },
];

/**
 * 图像调整面板：滑杆实时预览（img style.filter）→ 导出烘焙为新节点（canvas ctx.filter）。
 * 纯前端加工，零后端依赖；预览与导出共用同一 filter 串保证一致。
 */
export function AdjustDialog({
  nodeId,
  src,
  batchTargets,
  onClose,
}: {
  nodeId: string;
  src: string;
  /** P1-4 批量模式：框选多图节点套同一组参数（传入时以批量导出为准） */
  batchTargets?: BatchTarget[];
  onClose: () => void;
}) {
  const [values, setValues] = useState<AdjustValues>({ ...DEFAULT_ADJUST });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [batchProgress, setBatchProgress] = useState<{ done: number; total: number } | null>(null);
  const [batchSummary, setBatchSummary] = useState("");
  const isBatch = Array.isArray(batchTargets) && batchTargets.length > 0;
  const imgRef = useRef<HTMLImageElement | null>(null);
  const addNode = useCanvasStore((s) => s.addNode);
  const updateNodeData = useCanvasStore((s) => s.updateNodeData);
  const onConnect = useCanvasStore((s) => s.onConnect);

  const filter = buildFilterString(values);

  function set(key: keyof AdjustValues, v: number) {
    setValues((prev) => clampAdjust({ ...prev, [key]: v }));
  }

  /** 批量导出：逐目标 fetch→烘焙→上传→源节点右侧新节点+连线；失败逐项记录不中断。 */
  async function exportBatch() {
    const targets = batchTargets ?? [];
    const plan = planBatchAdjust(targets, values);
    if (plan.length === 0 || busy) return;
    setBusy(true);
    setError("");
    setBatchSummary("");
    setBatchProgress({ done: 0, total: plan.length });
    const results: BatchItemResult[] = [];
    for (let i = 0; i < plan.length; i++) {
      const item = plan[i];
      try {
        const img = await loadImage(item.src);
        const canvas = document.createElement("canvas");
        canvas.width = img.naturalWidth;
        canvas.height = img.naturalHeight;
        const ctx = canvas.getContext("2d");
        if (!ctx) throw new Error("canvas 不可用");
        ctx.filter = item.filter;
        ctx.drawImage(img, 0, 0);
        const up = await apiFetch<{ url: string }>("/api/uploads", {
          method: "POST",
          json: { data_url: canvas.toDataURL("image/png") },
        });
        const source = useCanvasStore.getState().nodes.find((n) => n.id === item.nodeId);
        const parentPos = source?.position ?? { x: 0, y: 0 };
        const id = addNode("image", { x: parentPos.x + 320, y: parentPos.y });
        updateNodeData(id, {
          title: "批量调整",
          outputUrl: up.url,
          prompt: source?.data.prompt ?? "",
        });
        onConnect({ source: item.nodeId, target: id, sourceHandle: null, targetHandle: null });
        results.push({ nodeId: item.nodeId, ok: true });
      } catch (err) {
        results.push({
          nodeId: item.nodeId,
          ok: false,
          error: err instanceof Error ? err.message : "导出失败",
        });
      }
      setBatchProgress({ done: i + 1, total: plan.length });
    }
    const s = summarizeBatchResult(results);
    setBatchSummary(
      s.failed > 0 ? `成功 ${s.ok} 张，失败 ${s.failed} 张（可重试）` : `已导出 ${s.ok} 张新节点`,
    );
    setBusy(false);
  }

  async function exportAdjusted() {
    if (isBatch) {
      await exportBatch();
      return;
    }
    const img = imgRef.current;
    if (!img || busy) return;
    setBusy(true);
    setError("");
    try {
      const canvas = document.createElement("canvas");
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      const ctx = canvas.getContext("2d");
      if (!ctx) throw new Error("canvas 不可用");
      ctx.filter = filter || "none";
      ctx.drawImage(img, 0, 0);
      const up = await apiFetch<{ url: string }>("/api/uploads", {
        method: "POST",
        json: { data_url: canvas.toDataURL("image/png") },
      });
      const source = useCanvasStore.getState().nodes.find((n) => n.id === nodeId);
      const parentPos = source?.position ?? { x: 0, y: 0 };
      const id = addNode("image", { x: parentPos.x + 320, y: parentPos.y });
      updateNodeData(id, {
        title: `调整 · ${SLIDERS.filter((s) => values[s.key] !== DEFAULT_ADJUST[s.key]).map((s) => s.label).join("/") || "原图"}`,
        outputUrl: up.url,
        prompt: source?.data.prompt ?? "",
      });
      onConnect({ source: nodeId, target: id, sourceHandle: null, targetHandle: null });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "导出失败");
    } finally {
      setBusy(false);
    }
  }

  return createPortal(
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="max-h-[92vh] max-w-3xl space-y-3 overflow-y-auto rounded-2xl bg-white p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-semibold">
            {isBatch ? `批量调整（${batchTargets!.length} 张）` : "调整图像"}
          </h3>
          <span className="text-xs text-zinc-400">{filter || "未调整"}</span>
        </div>
        <p className="text-xs text-zinc-500">拖动滑杆实时预览，导出后生成新节点（原图不受影响）。</p>

        <div className="flex max-h-64 items-center justify-center overflow-hidden rounded-lg bg-zinc-100">
          <img
            ref={imgRef}
            src={src}
            alt="调整预览"
            crossOrigin="anonymous"
            className="max-h-64 max-w-full object-contain"
            style={{ filter: filter || undefined }}
            data-testid="adjust-preview"
          />
        </div>

        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {SLIDERS.map(({ key, label, min, max, step }) => (
            <label key={key} className="flex items-center gap-2 text-xs text-zinc-600">
              <span className="w-16 shrink-0">{label}</span>
              <input
                type="range"
                min={min}
                max={max}
                step={step}
                value={values[key]}
                aria-label={label}
                onChange={(e) => set(key, Number(e.target.value))}
                className="flex-1"
                data-testid={`adjust-${key}`}
              />
              <span className="w-10 text-right font-mono text-zinc-400">{values[key]}</span>
            </label>
          ))}
        </div>

        {error && <p className="text-xs text-red-600">{error}</p>}
        {batchSummary && <p className="text-xs text-emerald-600">{batchSummary}</p>}

        <div className="flex justify-end gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setValues({ ...DEFAULT_ADJUST })}
            disabled={isDefaultAdjust(values)}
          >
            重置
          </Button>
          <Button variant="ghost" size="sm" onClick={onClose}>
            取消
          </Button>
          <Button size="sm" onClick={exportAdjusted} disabled={busy || !filter}>
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <SlidersHorizontal className="h-4 w-4" />}
            {busy
              ? batchProgress
                ? `导出中 ${batchProgress.done}/${batchProgress.total}`
                : "导出中…"
              : isBatch
                ? `导出 ${batchTargets!.length} 张`
                : "导出为新节点"}
          </Button>
        </div>
      </div>
    </div>
  , document.body);
}
