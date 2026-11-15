import { useRef, useState } from "react";
import { createPortal } from "react-dom";
import { FlipHorizontal, FlipVertical, Loader2, RotateCcw, RotateCw, Download } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { useCanvasStore } from "@/stores/canvas";
import {
  DEFAULT_TRANSFORM,
  computeExportSize,
  exportMime,
  isDefaultTransform,
  rotatedSize,
  type ExportFormat,
  type TransformOptions,
} from "@/lib/image-transform";

/**
 * 变换与导出（P1-1）：旋转/翻转预览（CSS）+ 缩放/格式导出（canvas 两步法：
 * 先翻转进临时画布，再平移旋转绘制——与 CSS 预览 transform 顺序一致）。
 */
export function TransformDialog({ nodeId, src, onClose }: { nodeId: string; src: string; onClose: () => void }) {
  const [t, setT] = useState<TransformOptions>({ ...DEFAULT_TRANSFORM });
  const [keepRatio, setKeepRatio] = useState(true);
  const [widthInput, setWidthInput] = useState("");
  const [heightInput, setHeightInput] = useState("");
  const [format, setFormat] = useState<ExportFormat>("png");
  const [quality, setQuality] = useState(90);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const imgRef = useRef<HTMLImageElement | null>(null);
  const addNode = useCanvasStore((s) => s.addNode);
  const updateNodeData = useCanvasStore((s) => s.updateNodeData);
  const onConnect = useCanvasStore((s) => s.onConnect);

  const cssTransform = `rotate(${t.rotate * 90}deg) scaleX(${t.flipH ? -1 : 1}) scaleY(${t.flipV ? -1 : 1})`;

  function rotate(dir: 1 | -1) {
    setT((prev) => ({ ...prev, rotate: ((((prev.rotate + dir) % 4) + 4) % 4) as 0 | 1 | 2 | 3 }));
  }

  async function exportTransformed() {
    const img = imgRef.current;
    if (!img || busy) return;
    setBusy(true);
    setError("");
    try {
      const W = img.naturalWidth;
      const H = img.naturalHeight;
      // 第一步：翻转（如有）进临时画布
      let source: CanvasImageSource = img;
      if (t.flipH || t.flipV) {
        const tmp = document.createElement("canvas");
        tmp.width = W;
        tmp.height = H;
        const tctx = tmp.getContext("2d");
        if (!tctx) throw new Error("canvas 不可用");
        tctx.translate(t.flipH ? W : 0, t.flipV ? H : 0);
        tctx.scale(t.flipH ? -1 : 1, t.flipV ? -1 : 1);
        tctx.drawImage(img, 0, 0);
        source = tmp;
      }
      // 第二步：按 90° 旋转绘制到导出画布
      const rot = rotatedSize(W, H, t.rotate);
      const size = computeExportSize(rot.w, rot.h, {
        width: widthInput ? Number(widthInput) : undefined,
        height: heightInput ? Number(heightInput) : undefined,
        keepRatio,
      });
      const canvas = document.createElement("canvas");
      canvas.width = size.width;
      canvas.height = size.height;
      const ctx = canvas.getContext("2d");
      if (!ctx) throw new Error("canvas 不可用");
      ctx.scale(size.width / rot.w, size.height / rot.h);
      if (t.rotate === 1) {
        ctx.translate(rot.w, 0);
        ctx.rotate(Math.PI / 2);
      } else if (t.rotate === 2) {
        ctx.translate(rot.w, rot.h);
        ctx.rotate(Math.PI);
      } else if (t.rotate === 3) {
        ctx.translate(0, rot.h);
        ctx.rotate(-Math.PI / 2);
      }
      ctx.drawImage(source, 0, 0);

      const { mime, quality: q } = exportMime(format, quality);
      const dataUrl = canvas.toDataURL(mime, q);
      const up = await apiFetch<{ url: string }>("/api/uploads", {
        method: "POST",
        json: { data_url: dataUrl },
      });
      const node = useCanvasStore.getState().nodes.find((n) => n.id === nodeId);
      const parentPos = node?.position ?? { x: 0, y: 0 };
      const id = addNode("image", { x: parentPos.x + 320, y: parentPos.y });
      const bits: string[] = [];
      if (t.rotate) bits.push(`旋转${t.rotate * 90}°`);
      if (t.flipH) bits.push("水平翻转");
      if (t.flipV) bits.push("垂直翻转");
      if (widthInput || heightInput) bits.push(`${size.width}×${size.height}`);
      if (format !== "png") bits.push(format.toUpperCase());
      updateNodeData(id, {
        title: `变换 · ${bits.join("/") || "原图"}`,
        outputUrl: up.url,
        prompt: node?.data.prompt ?? "",
      });
      onConnect({ source: nodeId, target: id, sourceHandle: null, targetHandle: null });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "导出失败");
    } finally {
      setBusy(false);
    }
  }

  const toolBtn =
    "flex h-8 w-8 items-center justify-center rounded-md bg-zinc-100 text-zinc-600 hover:bg-zinc-200 cursor-pointer";

  return createPortal(
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="max-h-[92vh] max-w-3xl space-y-3 overflow-y-auto rounded-2xl bg-white p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-semibold">变换与导出</h3>
          <span className="text-xs text-zinc-400">{cssTransform === "rotate(0deg) scaleX(1) scaleY(1)" ? "未变换" : cssTransform}</span>
        </div>

        <div className="flex max-h-64 items-center justify-center overflow-hidden rounded-lg bg-zinc-100">
          <img
            ref={imgRef}
            src={src}
            alt="变换预览"
            className="max-h-64 max-w-full object-contain transition-transform"
            style={{ transform: cssTransform }}
            data-testid="transform-preview"
          />
        </div>

        <div className="flex items-center gap-1.5">
          <button aria-label="左转90度" title="左转 90°" className={toolBtn} onClick={() => rotate(-1)}>
            <RotateCcw className="h-4 w-4" />
          </button>
          <button aria-label="右转90度" title="右转 90°" className={toolBtn} onClick={() => rotate(1)}>
            <RotateCw className="h-4 w-4" />
          </button>
          <button
            aria-label="水平翻转"
            title="水平翻转"
            className={toolBtn}
            onClick={() => setT((p) => ({ ...p, flipH: !p.flipH }))}
          >
            <FlipHorizontal className="h-4 w-4" />
          </button>
          <button
            aria-label="垂直翻转"
            title="垂直翻转"
            className={toolBtn}
            onClick={() => setT((p) => ({ ...p, flipV: !p.flipV }))}
          >
            <FlipVertical className="h-4 w-4" />
          </button>
          <button
            aria-label="重置变换"
            className={`${toolBtn} px-2 text-xs`}
            disabled={isDefaultTransform(t)}
            onClick={() => setT({ ...DEFAULT_TRANSFORM })}
          >
            重置
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-600">
          <span>导出尺寸</span>
          <input
            type="number"
            min={1}
            max={4096}
            placeholder="原宽"
            aria-label="导出宽"
            value={widthInput}
            onChange={(e) => setWidthInput(e.target.value)}
            className="h-8 w-20 rounded-lg border border-zinc-200 px-2"
          />
          <span>×</span>
          <input
            type="number"
            min={1}
            max={4096}
            placeholder="原高"
            aria-label="导出高"
            value={heightInput}
            onChange={(e) => setHeightInput(e.target.value)}
            className="h-8 w-20 rounded-lg border border-zinc-200 px-2"
          />
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={keepRatio} onChange={(e) => setKeepRatio(e.target.checked)} />
            保持比例
          </label>
        </div>

        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <span className="text-zinc-600">格式</span>
          {(["png", "jpeg", "webp"] as const).map((f) => (
            <button
              key={f}
              onClick={() => setFormat(f)}
              className={
                "h-7 rounded-md px-2 cursor-pointer " +
                (format === f ? "bg-zinc-900 text-white" : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200")
              }
            >
              {f === "jpeg" ? "JPG" : f.toUpperCase()}
            </button>
          ))}
          {format !== "png" && (
            <label className="ml-2 flex items-center gap-1 text-zinc-600">
              质量
              <input
                type="range"
                min={1}
                max={100}
                value={quality}
                aria-label="导出质量"
                onChange={(e) => setQuality(Number(e.target.value))}
                className="w-24"
              />
              <span className="w-8 font-mono text-zinc-400">{quality}</span>
            </label>
          )}
        </div>

        {error && <p className="text-xs text-red-600">{error}</p>}

        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose}>
            取消
          </Button>
          <Button size="sm" onClick={exportTransformed} disabled={busy}>
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
            {busy ? "导出中…" : "导出为新节点"}
          </Button>
        </div>
      </div>
    </div>
  , document.body);
}
