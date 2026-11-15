import { useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Crop, Loader2 } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { useCanvasStore } from "@/stores/canvas";
import { useModelChoice } from "@/components/canvas/ModelSelect";
import { constrainToRatio, fitScale, normalizeRect, toImageRect, type CropRect } from "@/lib/crop-rect";
import { ZoomStage } from "@/components/canvas/ZoomStage";
import { useTaskStore } from "@/stores/tasks";
import { AnnotatePane } from "./AnnotatePane";
import { InpaintPane } from "./InpaintPane";

const VIEW_MAX_W = 640;
const VIEW_MAX_H = 320;

/** 图片加工对话框：mode="crop" 拖拽选区裁切；mode="annotate" 标注画板（笔/矩形/箭头/文字）。 */
export function ImageEditorDialog({
  nodeId,
  src,
  mode = "crop",
  onClose,
}: {
  nodeId: string;
  src: string;
  mode?: "crop" | "annotate" | "inpaint";
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [imgSize, setImgSize] = useState<{ w: number; h: number } | null>(null);
  const [rect, setRect] = useState<CropRect | null>(null);
  // P0-4 比例锁定：null=自由；拖拽选区时收敛到目标宽高比
  const [ratio, setRatio] = useState<number | null>(null);
  const [annotated, setAnnotated] = useState<{ dataUrl: string; count: number } | null>(null);
  const [inpaint, setInpaint] = useState<{ maskDataUrl: string } | null>(null);
  const [inpaintPrompt, setInpaintPrompt] = useState("");
  const [submitted, setSubmitted] = useState<number | null>(null);
  const dragStart = useRef<{ x: number; y: number } | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  // ZoomStage 当前缩放（render-prop 同步）：显示坐标÷zoom 还原布局坐标
  const zoomRef = useRef(1);
  const addNode = useCanvasStore((s) => s.addNode);
  const updateNodeData = useCanvasStore((s) => s.updateNodeData);
  const onConnect = useCanvasStore((s) => s.onConnect);

  const scale = imgSize ? fitScale(imgSize.w, imgSize.h, VIEW_MAX_W, VIEW_MAX_H) : 1;

  function onImgLoad(e: React.SyntheticEvent<HTMLImageElement>) {
    const img = e.currentTarget;
    imgRef.current = img;
    setImgSize({ w: img.naturalWidth, h: img.naturalHeight });
  }

  function displayPos(e: React.MouseEvent<HTMLDivElement>) {
    const box = e.currentTarget.getBoundingClientRect();
    const z = zoomRef.current || 1;
    return { x: (e.clientX - box.left) / z, y: (e.clientY - box.top) / z };
  }

  function onPointerDown(e: React.MouseEvent<HTMLDivElement>) {
    if (!imgSize || busy) return;
    dragStart.current = displayPos(e);
    setRect(null);
  }

  function onPointerMove(e: React.MouseEvent<HTMLDivElement>) {
    if (!dragStart.current || !imgSize) return;
    const p = displayPos(e);
    setRect(
      constrainToRatio(
        normalizeRect(dragStart.current.x, dragStart.current.y, p.x, p.y, imgSize.w * scale, imgSize.h * scale),
        ratio,
        imgSize.w * scale,
        imgSize.h * scale,
      ),
    );
  }

  function onPointerUp() {
    dragStart.current = null;
  }

  async function doExportAnnotated() {
    if (!annotated || busy) return;
    setBusy(true);
    setError("");
    try {
      const up = await apiFetch<{ url: string }>("/api/uploads", {
        method: "POST",
        json: { data_url: annotated.dataUrl },
      });
      const source = useCanvasStore.getState().nodes.find((n) => n.id === nodeId);
      const parentPos = source?.position ?? { x: 0, y: 0 };
      const id = addNode("image", { x: parentPos.x + 320, y: parentPos.y });
      updateNodeData(id, {
        title: `标注版 (${annotated.count} 处)`,
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

  async function doInpaint() {
    if (!inpaint || !inpaintPrompt.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      const maskUp = await apiFetch<{ url: string }>("/api/uploads", {
        method: "POST",
        json: { data_url: inpaint.maskDataUrl },
      });
      const choice = useModelChoice.getState().choice["image"];
      const body = {
        type: "image",
        prompt: inpaintPrompt.trim(),
        model: choice?.model || "Kwai-Kolors/Kolors",
        params: {
          mode: "edit",
          image: src,
          mask: maskUp.url,
          ...(choice?.channelId ? { channel_id: choice.channelId } : {}),
        },
      };
      const resp = await apiFetch<{ task: { id: number } }>("/api/tasks", { method: "POST", json: body });
      // 回流：attach 到源节点——GenerateButton 订阅接管，完成后产物自动连线回流画布
      useTaskStore.getState().attach(nodeId, resp.task.id);
      setSubmitted(resp.task.id);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "提交失败");
    } finally {
      setBusy(false);
    }
  }

  async function doCrop() {
    if (!rect || !imgSize || !imgRef.current || busy) return;
    setBusy(true);
    setError("");
    try {
      const r = toImageRect(rect, scale, imgSize.w, imgSize.h);
      const canvas = document.createElement("canvas");
      canvas.width = r.w;
      canvas.height = r.h;
      const ctx = canvas.getContext("2d");
      if (!ctx) throw new Error("canvas 不可用");
      ctx.drawImage(imgRef.current, r.x, r.y, r.w, r.h, 0, 0, r.w, r.h);
      const dataUrl = canvas.toDataURL("image/png");
      const up = await apiFetch<{ url: string }>("/api/uploads", {
        method: "POST",
        json: { data_url: dataUrl },
      });
      // 新节点放在源节点右侧，并连一条线（加工产物 → 源可追溯）
      const source = useCanvasStore.getState().nodes.find((n) => n.id === nodeId);
      const parentPos = source?.position ?? { x: 0, y: 0 };
      const id = addNode("image", { x: parentPos.x + 320, y: parentPos.y });
      updateNodeData(id, {
        title: `裁切 ${r.w}×${r.h}`,
        outputUrl: up.url,
        prompt: source?.data.prompt ?? "",
      });
      onConnect({ source: nodeId, target: id, sourceHandle: null, targetHandle: null });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "裁切失败");
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
          <h3 className="text-lg font-semibold">裁切图片</h3>
          {imgSize && (
            <span className="text-xs text-zinc-400">
              原图 {imgSize.w}×{imgSize.h}
              {rect ? ` · 选区 ${Math.round(rect.w / scale)}×${Math.round(rect.h / scale)}` : ""}
            </span>
          )}
        </div>
        {mode === "crop" ? (
          <p className="text-xs text-zinc-500">在图片上拖拽框选区域，松开后点击「裁切为新节点」。按 Esc 取消。</p>
        ) : mode === "annotate" ? (
          <p className="text-xs text-zinc-500">选择工具在图上标注，完成后点击「导出标注图」生成新节点。</p>
        ) : (
          <p className="text-xs text-zinc-500">
            涂抹要重绘的区域并填写重绘指令。需要渠道开启「支持图像编辑」（/images/edits 协议）。
          </p>
        )}

        {mode === "annotate" ? (
          <AnnotatePane
            src={src}
            onChange={(strokes, dataUrl) => setAnnotated(dataUrl ? { dataUrl, count: strokes.length } : null)}
          />
        ) : mode === "inpaint" ? (
          <div className="space-y-2">
            <InpaintPane
              src={src}
              onChange={(maskDataUrl, _meta, hasMask) =>
                setInpaint(hasMask && maskDataUrl ? { maskDataUrl } : null)
              }
            />
            <textarea
              value={inpaintPrompt}
              onChange={(e) => setInpaintPrompt(e.target.value)}
              placeholder="重绘指令，如：把这块改成夜空"
              rows={2}
              maxLength={1000}
              className="w-full rounded-lg border border-zinc-200 p-2 text-sm outline-none focus:border-zinc-400"
            />
            {submitted && (
              <p className="text-xs text-emerald-600">
                已提交任务 #{submitted}，完成后结果自动回流为源节点右侧的新节点。
              </p>
            )}
          </div>
        ) : (
        <ZoomStage testId="crop-zoom">
          {(zoom) => {
            zoomRef.current = zoom;
            return (
              <div
                className="relative mx-auto cursor-crosshair select-none overflow-hidden bg-zinc-100"
                style={{
                  width: imgSize ? imgSize.w * scale : 320,
                  height: imgSize ? imgSize.h * scale : 200,
                }}
                onMouseDown={onPointerDown}
                onMouseMove={onPointerMove}
                onMouseUp={onPointerUp}
                data-testid="crop-stage"
              >
                <img src={src} onLoad={onImgLoad} alt="待裁切" className="block max-w-none" draggable={false} />
                {rect && (
                  <>
                    <div
                      className="pointer-events-none absolute border-2 border-white ring-1 ring-zinc-900/60"
                      style={{ left: rect.x, top: rect.y, width: rect.w, height: rect.h }}
                    />
                    <div
                      className="pointer-events-none absolute inset-0 bg-zinc-900/40"
                      style={{
                        clipPath: `polygon(0 0, 100% 0, 100% 100%, 0 100%, 0 0, ${rect.x}px ${rect.y}px, ${rect.x}px ${rect.y + rect.h}px, ${rect.x + rect.w}px ${rect.y + rect.h}px, ${rect.x + rect.w}px ${rect.y}px)`,
                      }}
                    />
                  </>
                )}
              </div>
            );
          }}
        </ZoomStage>
        )}
        {mode === "crop" && (
          <div className="flex flex-wrap items-center gap-1.5" data-testid="crop-ratio-group">
            <span className="text-xs text-zinc-400">比例</span>
            {([
              { label: "自由", value: null },
              { label: "1:1", value: 1 },
              { label: "16:9", value: 16 / 9 },
              { label: "9:16", value: 9 / 16 },
            ] as Array<{ label: string; value: number | null }>).map(({ label, value }) => (
              <button
                key={label}
                onClick={() => {
                  setRatio(value);
                  // 已有选区时立即收敛到新比例
                  if (rect && imgSize) {
                    setRect(
                      constrainToRatio(rect, value, imgSize.w * scale, imgSize.h * scale),
                    );
                  }
                }}
                className={
                  "h-7 rounded-md px-2 text-xs cursor-pointer " +
                  (ratio === value
                    ? "bg-zinc-900 text-white"
                    : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200")
                }
              >
                {label}
              </button>
            ))}
          </div>
        )}

        {error && <p className="text-xs text-red-600">{error}</p>}

        <div className="flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={onClose}>
            取消
          </Button>
          {mode === "annotate" ? (
            <Button size="sm" onClick={doExportAnnotated} disabled={!annotated || busy}>
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              {busy ? "处理中…" : `导出标注图${annotated ? `（${annotated.count} 处标注）` : ""}`}
            </Button>
          ) : mode === "inpaint" ? (
            <Button size="sm" onClick={doInpaint} disabled={!inpaint || !inpaintPrompt.trim() || busy}>
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              {busy ? "提交中…" : "局部重绘"}
            </Button>
          ) : (
            <Button size="sm" onClick={doCrop} disabled={!rect || busy}>
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Crop className="h-4 w-4" />}
              {busy ? "处理中…" : "裁切为新节点"}
            </Button>
          )}
        </div>
      </div>
    </div>
  , document.body);
}
