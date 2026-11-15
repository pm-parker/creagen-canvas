import { useRef, useState } from "react";
import { Brush, Undo2 } from "lucide-react";
import { ZoomStage } from "@/components/canvas/ZoomStage";
import {
  addStrokePoint,
  brushRadius,
  endStroke,
  hasMaskContent,
  newStrokes,
  undoStroke,
  type MaskModel,
} from "@/lib/inpaint-mask";

const MASK_COLOR = "rgba(239,68,68,0.55)"; // 红色半透明覆盖 = 待重绘区

/**
 * 局部重绘蒙版画板：笔刷涂抹（尺寸可调、笔画级撤销）→ 黑白 mask PNG dataURL（白=重绘区）。
 * 蒙版以 stroke 模型驱动，重绘走全量重刷（见 lib/inpaint-mask）。
 */
export function InpaintPane({
  src,
  onChange,
}: {
  src: string;
  onChange: (maskDataUrl: string | null, meta: { w: number; h: number } | null, hasMask: boolean) => void;
}) {
  const [imgSize, setImgSize] = useState<{ w: number; h: number } | null>(null);
  const [model, setModel] = useState<MaskModel>(() => newStrokes());
  const [brushPct, setBrushPct] = useState(3.5); // 相对图宽百分比 1~10
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  // ref 驱动（同步）：mousedown 后紧跟着的 mousemove 必须立即并入同一笔，
  // 不能依赖 React state 异步 flush（e2e dispatchEvent 背靠背事件会踩中）
  const modelRef = useRef(model);
  const drawingRef = useRef(false);

  function commit(next: MaskModel) {
    modelRef.current = next;
    setModel(next);
    repaint(next);
  }

  function handleImgLoad(e: React.SyntheticEvent<HTMLImageElement>) {
    const img = e.currentTarget;
    imgRef.current = img;
    setImgSize({ w: img.naturalWidth, h: img.naturalHeight });
    requestAnimationFrame(() => {
      const cv = canvasRef.current;
      if (cv) {
        cv.width = img.naturalWidth;
        cv.height = img.naturalHeight;
      }
    });
  }

  /** 全量重绘当前模型（撤销/清空后必须整体刷新）。 */
  function repaint(m: MaskModel) {
    const cv = canvasRef.current;
    const ctx = cv?.getContext("2d");
    if (!cv || !ctx) return;
    ctx.clearRect(0, 0, cv.width, cv.height);
    ctx.fillStyle = MASK_COLOR;
    for (const stroke of m.strokes) {
      const r = brushRadius(stroke.radius, cv.width);
      for (const p of stroke.points) {
        ctx.beginPath();
        ctx.arc(p.x * cv.width, p.y * cv.height, r, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  }

  /** 导出黑白 mask PNG：涂抹区→白，其余→黑（OpenAI edits 语义）。 */
  function exportMask(): string | null {
    const cv = canvasRef.current;
    if (!cv) return null;
    const out = document.createElement("canvas");
    out.width = cv.width;
    out.height = cv.height;
    const ctx = out.getContext("2d");
    if (!ctx) return null;
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, out.width, out.height);
    ctx.drawImage(cv, 0, 0);
    const imgData = ctx.getImageData(0, 0, out.width, out.height);
    const d = imgData.data;
    for (let i = 0; i < d.length; i += 4) {
      if (d[i + 3] > 0) {
        d[i] = 255;
        d[i + 1] = 255;
        d[i + 2] = 255;
        d[i + 3] = 255;
      }
    }
    ctx.putImageData(imgData, 0, 0);
    return out.toDataURL("image/png");
  }

  function notify(m: MaskModel) {
    onChange(exportMask(), imgSize, hasMaskContent(m));
  }

  function normPos(e: React.MouseEvent<HTMLCanvasElement>) {
    const cv = e.currentTarget;
    const box = cv.getBoundingClientRect();
    return {
      x: Math.min(1, Math.max(0, (e.clientX - box.left) / box.width)),
      y: Math.min(1, Math.max(0, (e.clientY - box.top) / box.height)),
    };
  }

  function onDown(e: React.MouseEvent<HTMLCanvasElement>) {
    drawingRef.current = true;
    commit(addStrokePoint(modelRef.current, normPos(e), brushPct / 100));
  }

  function onMove(e: React.MouseEvent<HTMLCanvasElement>) {
    if (!drawingRef.current) return;
    commit(addStrokePoint(modelRef.current, normPos(e), brushPct / 100));
  }

  function onUp() {
    if (!drawingRef.current) return;
    drawingRef.current = false;
    const next = endStroke(modelRef.current);
    commit(next);
    notify(next);
  }

  function undo() {
    const next = undoStroke(modelRef.current);
    commit(next);
    notify(next);
  }

  function clearMask() {
    const next = newStrokes();
    commit(next);
    notify(next);
  }

  const scale = imgSize
    ? Math.min(640 / imgSize.w, 320 / imgSize.h, 1)
    : 1;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex items-center gap-1 text-xs text-zinc-600">
          <Brush className="h-4 w-4" /> 涂抹需要重绘的区域
        </span>
        <label className="flex items-center gap-1 text-xs text-zinc-600" data-testid="brush-size">
          笔刷
          <input
            type="range"
            min={1}
            max={10}
            step={0.5}
            value={brushPct}
            onChange={(e) => setBrushPct(Number(e.target.value))}
            aria-label="笔刷尺寸"
            className="w-24"
          />
          <span className="w-8 font-mono text-zinc-500">{brushPct}%</span>
        </label>
        <button
          aria-label="撤销一笔"
          title="撤销一笔"
          onClick={undo}
          disabled={model.strokes.length === 0}
          className="ml-auto flex h-7 w-7 items-center justify-center rounded-md bg-zinc-100 text-zinc-600 hover:bg-zinc-200 disabled:opacity-40 cursor-pointer"
        >
          <Undo2 className="h-3.5 w-3.5" />
        </button>
        <button
          aria-label="清除蒙版"
          onClick={clearMask}
          className="rounded-md bg-zinc-100 px-2 py-1 text-xs text-zinc-600 hover:bg-zinc-200 cursor-pointer"
        >
          清除涂抹
        </button>
      </div>
      <ZoomStage testId="inpaint-zoom">
        <div className="relative mx-auto select-none" style={{ width: imgSize ? imgSize.w * scale : 320 }}>
          <img src={src} onLoad={handleImgLoad} alt="重绘底图" className="block max-w-none" draggable={false} />
          <canvas
            ref={canvasRef}
            className="absolute left-0 top-0 h-full w-full cursor-crosshair"
            onMouseDown={onDown}
            onMouseMove={onMove}
            onMouseUp={onUp}
            onMouseLeave={onUp}
            data-testid="inpaint-canvas"
          />
        </div>
      </ZoomStage>
    </div>
  );
}
