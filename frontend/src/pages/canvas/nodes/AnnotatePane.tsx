import { useEffect, useRef, useState } from "react";
import { Eraser, Square, Type } from "lucide-react";
import { Pencil } from "lucide-react";
import { ArrowUpRight } from "lucide-react";
import { ZoomStage } from "@/components/canvas/ZoomStage";
import {
  ANNOTATE_COLORS,
  drawStroke,
  normalizeRectAnnotate,
  redrawAll,
  type AnnotateTool,
  type Stroke,
} from "@/lib/annotate";

const VIEW_MAX_W = 640;
const VIEW_MAX_H = 320;

/** 标注画板：在图片上绘制笔/矩形/箭头/文字，产出笔画列表（归一化坐标）。 */
export function AnnotatePane({
  src,
  onChange,
}: {
  src: string;
  onChange: (strokes: Stroke[], flatDataUrl: string | null, meta: { w: number; h: number } | null) => void;
}) {
  const [tool, setTool] = useState<AnnotateTool>("pen");
  const [color, setColor] = useState<string>(ANNOTATE_COLORS[0]);
  // P1-3 笔宽/字号三档（相对图宽/图高比例）
  const [penWidth, setPenWidth] = useState(0.004);
  const [fontSize, setFontSize] = useState(0.05);
  const [strokes, setStrokes] = useState<Stroke[]>([]);
  const [imgSize, setImgSize] = useState<{ w: number; h: number } | null>(null);
  const [textInput, setTextInput] = useState<{ nx: number; ny: number; value: string } | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const mountedRef = useRef(true);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const drawing = useRef(false);
  const dragStart = useRef<{ nx: number; ny: number } | null>(null);

  const scale = imgSize ? Math.min(VIEW_MAX_W / imgSize.w, VIEW_MAX_H / imgSize.h, 1) : 1;

  // 图片加载后按笔画重绘
  function handleImgLoad(e: React.SyntheticEvent<HTMLImageElement>) {
    const img = e.currentTarget;
    imgRef.current = img;
    setImgSize({ w: img.naturalWidth, h: img.naturalHeight });
    requestAnimationFrame(() => {
      const cv = canvasRef.current;
      if (cv) {
        cv.width = img.naturalWidth;
        cv.height = img.naturalHeight;
        const ctx = cv.getContext("2d");
        if (ctx) redrawAll(ctx, strokes, cv.width, cv.height);
      }
    });
  }

  function repaint(list: Stroke[]) {
    const cv = canvasRef.current;
    if (!cv) return;
    const ctx = cv.getContext("2d");
    if (ctx) redrawAll(ctx, list, cv.width, cv.height);
  }

  function normPos(e: React.MouseEvent<HTMLCanvasElement>) {
    const cv = e.currentTarget;
    const box = cv.getBoundingClientRect();
    return {
      nx: Math.min(1, Math.max(0, (e.clientX - box.left) / box.width)),
      ny: Math.min(1, Math.max(0, (e.clientY - box.top) / box.height)),
    };
  }

  function onDown(e: React.MouseEvent<HTMLCanvasElement>) {
    if (tool === "text") {
      const p = normPos(e);
      setTextInput({ nx: p.nx, ny: p.ny, value: "" });
      return;
    }
    drawing.current = true;
    dragStart.current = normPos(e);
    if (tool === "pen") {
      const p = normPos(e);
      setStrokes((list) => [
        ...list,
        { tool: "pen", color, width: penWidth, points: [{ x: p.nx, y: p.ny }] },
      ]);
    }
  }

  function onMove(e: React.MouseEvent<HTMLCanvasElement>) {
    if (!drawing.current) return;
    const p = normPos(e);
    if (tool === "pen") {
      setStrokes((list) => {
        const last = list[list.length - 1];
        if (!last || last.tool !== "pen") return list;
        last.points.push({ x: p.nx, y: p.ny });
        return [...list];
      });
      const cv = canvasRef.current;
      const ctx0 = cv?.getContext("2d");
      if (cv && ctx0) {
        const last = strokes[strokes.length - 1];
        if (last && last.tool === "pen" && last.points.length >= 2) {
          const a = last.points[last.points.length - 2];
          const b = last.points[last.points.length - 1];
          drawStroke(ctx0, { tool: "pen", color: last.color, width: last.width, points: [a, b] }, cv.width, cv.height);
        }
      }
    } else if (dragStart.current) {
      // rect/arrow 实时预览：重绘已有 + 当前预览
      const r = normalizeRectAnnotate(dragStart.current.nx, dragStart.current.ny, p.nx, p.ny);
      const preview: Stroke =
        tool === "rect"
          ? { tool: "rect", color, width: penWidth, ...r }
          : { tool: "arrow", color, width: penWidth, x1: dragStart.current.nx, y1: dragStart.current.ny, x2: p.nx, y2: p.ny };
      const cv = canvasRef.current;
      const ctx = cv?.getContext("2d");
      if (cv && ctx) {
        redrawAll(ctx, strokes, cv.width, cv.height);
        drawStroke(ctx, preview, cv.width, cv.height);
      }
    }
  }

  function onUp(e: React.MouseEvent<HTMLCanvasElement>) {
    if (!drawing.current) return;
    drawing.current = false;
    const p = normPos(e);
    if (tool === "rect" || tool === "arrow") {
      const r = normalizeRectAnnotate(dragStart.current?.nx ?? p.nx, dragStart.current?.ny ?? p.ny, p.nx, p.ny);
      const stroke: Stroke =
        tool === "rect"
          ? { tool: "rect", color, width: penWidth, ...r }
          : {
              tool: "arrow",
              color,
              width: penWidth,
              x1: dragStart.current?.nx ?? p.nx,
              y1: dragStart.current?.ny ?? p.ny,
              x2: p.nx,
              y2: p.ny,
            };
      setStrokes((list) => [...list, stroke]);
    }
    dragStart.current = null;
  }

  function commitText() {
    if (!textInput || !textInput.value.trim()) {
      setTextInput(null);
      return;
    }
    const stroke: Stroke = {
      tool: "text",
      color,
      width: 0.004,
      x: textInput.nx,
      y: textInput.ny,
      text: textInput.value.trim(),
      size: fontSize,
    };
    const list = [...strokes, stroke];
    setStrokes(list);
    repaint(list);
    setTextInput(null);
  }

  function undo() {
    const list = strokes.slice(0, -1);
    setStrokes(list);
    repaint(list);
  }

  function clearAll() {
    setStrokes([]);
    repaint([]);
  }

  // 产物上抛：扁平化 dataURL（懒生成）
  function emit() {
    const img = imgRef.current;
    const cv = canvasRef.current;
    if (!img || !cv || !imgSize) return;
    const out = document.createElement("canvas");
    out.width = imgSize.w;
    out.height = imgSize.h;
    const ctx = out.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(img, 0, 0);
    ctx.drawImage(cv, 0, 0);
    onChange(strokes, out.toDataURL("image/png"), imgSize);
  }

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (mountedRef.current) emit();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strokes]);

  const btn = (active: boolean) =>
    `flex h-8 w-8 items-center justify-center rounded-lg cursor-pointer ${
      active ? "bg-zinc-900 text-white" : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200"
    }`;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <button aria-label="画笔" title="画笔" className={btn(tool === "pen")} onClick={() => setTool("pen")}>
          <Pencil className="h-4 w-4" />
        </button>
        <button aria-label="矩形" title="矩形" className={btn(tool === "rect")} onClick={() => setTool("rect")}>
          <Square className="h-4 w-4" />
        </button>
        <button aria-label="箭头" title="箭头" className={btn(tool === "arrow")} onClick={() => setTool("arrow")}>
          <ArrowUpRight className="h-4 w-4" />
        </button>
        <button aria-label="文字" title="文字" className={btn(tool === "text")} onClick={() => setTool("text")}>
          <Type className="h-4 w-4" />
        </button>
        <span className="mx-1 h-5 w-px bg-zinc-200" />
        {ANNOTATE_COLORS.map((c) => (
          <button
            key={c}
            aria-label={`颜色 ${c}`}
            onClick={() => setColor(c)}
            className={`h-6 w-6 rounded-full border-2 cursor-pointer ${
              color === c ? "border-zinc-900" : "border-transparent"
            }`}
            style={{ background: c }}
          />
        ))}
        <span className="mx-1 h-5 w-px bg-zinc-200" />
        {(["细", "中", "粗"] as const).map((label, i) => (
          <button
            key={label}
            aria-label={`笔宽${label}`}
            title={`笔宽${label}`}
            onClick={() => setPenWidth([0.002, 0.004, 0.007][i])}
            className={
              "h-7 rounded-md px-1.5 text-xs cursor-pointer " +
              (penWidth === [0.002, 0.004, 0.007][i]
                ? "bg-zinc-900 text-white"
                : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200")
            }
          >
            {label}
          </button>
        ))}
        <span className="mx-1 h-5 w-px bg-zinc-200" />
        {(["小", "中", "大"] as const).map((label, i) => (
          <button
            key={label}
            aria-label={`字号${label}`}
            title={`字号${label}（文字工具）`}
            onClick={() => setFontSize([0.03, 0.05, 0.08][i])}
            className={
              "h-7 rounded-md px-1.5 text-xs cursor-pointer " +
              (fontSize === [0.03, 0.05, 0.08][i]
                ? "bg-zinc-900 text-white"
                : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200")
            }
          >
            {label}
          </button>
        ))}
        <span className="mx-1 h-5 w-px bg-zinc-200" />
        <button aria-label="撤销一笔" title="撤销一笔" className={btn(false)} onClick={undo} disabled={!strokes.length}>
          <Eraser className="h-4 w-4" />
        </button>
        <button
          aria-label="清空标注"
          title="清空标注"
          className="rounded-lg bg-zinc-100 px-2 py-1.5 text-xs text-zinc-600 hover:bg-zinc-200 cursor-pointer"
          onClick={clearAll}
          disabled={!strokes.length}
        >
          清空
        </button>
      </div>

      <ZoomStage testId="annotate-zoom">
        <div className="relative mx-auto select-none" style={{ width: imgSize ? imgSize.w * scale : 320 }}>
          <img src={src} onLoad={handleImgLoad} alt="标注底图" className="block max-w-none" draggable={false} />
          <canvas
            ref={canvasRef}
            className="absolute left-0 top-0 h-full w-full cursor-crosshair"
            onMouseDown={onDown}
            onMouseMove={onMove}
            onMouseUp={onUp}
            data-testid="annotate-canvas"
          />
          {textInput && (
            <input
              autoFocus
              value={textInput.value}
              onChange={(e) => setTextInput({ ...textInput, value: e.target.value })}
              onKeyDown={(e) => {
                if (e.key === "Enter") commitText();
                if (e.key === "Escape") setTextInput(null);
              }}
              onBlur={commitText}
              placeholder="输入文字回车"
              className="absolute z-10 rounded border border-zinc-400 px-1 py-0.5 text-sm shadow"
              style={{
                left: `${textInput.nx * 100}%`,
                top: `calc(${textInput.ny * 100}% - 12px)`,
                width: 140,
              }}
            />
          )}
        </div>
      </ZoomStage>
    </div>
  );
}
