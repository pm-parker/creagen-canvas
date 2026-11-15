import { useEffect, useRef, useState, type ReactNode } from "react";
import { Maximize2, Minus, Plus } from "lucide-react";
import { nextWheelZoom, zoomStep, ZOOM_MAX, ZOOM_MIN } from "@/lib/editor-zoom";

/**
 * 加工编辑器缩放底座：CSS transform 缩放 + 中键/空格拖拽平移。
 * 裁切/标注/重绘三加工器共用；归一化坐标（getBoundingClientRect）天然兼容缩放，
 * 使用显示坐标的裁切经 render-prop 拿 zoom 自行换算局部坐标。
 */
export function ZoomStage({
  children,
  maxStageWidth = 720,
  stageHeight = 360,
  testId = "zoom-stage",
}: {
  children: ReactNode | ((zoom: number) => ReactNode);
  /** 可视区（未缩放布局尺寸上限） */
  maxStageWidth?: number;
  stageHeight?: number;
  testId?: string;
}) {
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const stageRef = useRef<HTMLDivElement | null>(null);
  const panning = useRef<{ startX: number; startY: number; baseX: number; baseY: number } | null>(null);

  // 原生非被动监听：React 合成 wheel 为 passive，preventDefault 拦不住弹窗滚动
  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const handler = (e: WheelEvent) => {
      e.preventDefault();
      setZoom((z) => nextWheelZoom(z, e.deltaY));
    };
    el.addEventListener("wheel", handler, { passive: false });
    return () => el.removeEventListener("wheel", handler);
  }, []);

  // 空格按下进入平移模式（容器打标记，左键拖拽即平移）
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.code !== "Space") return;
      const target = e.target as HTMLElement;
      if (target.tagName === "INPUT" || target.tagName === "TEXTAREA") return;
      e.preventDefault();
      stageRef.current?.toggleAttribute("data-spaceheld", e.type === "keydown");
    }
    window.addEventListener("keydown", onKey);
    window.addEventListener("keyup", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("keyup", onKey);
    };
  }, []);

  function onPanPointerDown(e: React.PointerEvent) {
    const el = stageRef.current;
    if (e.button !== 1 && !el?.hasAttribute("data-spaceheld")) return;
    e.preventDefault();
    panning.current = { startX: e.clientX, startY: e.clientY, baseX: pan.x, baseY: pan.y };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  }

  function onPanPointerMove(e: React.PointerEvent) {
    if (!panning.current) return;
    setPan({
      x: panning.current.baseX + (e.clientX - panning.current.startX),
      y: panning.current.baseY + (e.clientY - panning.current.startY),
    });
  }

  function onPanPointerUp() {
    panning.current = null;
  }

  function reset() {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  }

  const btn =
    "flex h-7 w-7 items-center justify-center rounded-md bg-zinc-100 text-zinc-600 hover:bg-zinc-200 cursor-pointer disabled:opacity-40";

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1" data-testid={`${testId}-controls`}>
        <button
          aria-label="缩小"
          className={btn}
          disabled={zoom <= ZOOM_MIN}
          onClick={() => setZoom((z) => zoomStep(z, -1))}
        >
          <Minus className="h-3.5 w-3.5" />
        </button>
        <span data-testid={`${testId}-percent`} className="w-12 text-center font-mono text-xs text-zinc-500">
          {Math.round(zoom * 100)}%
        </span>
        <button
          aria-label="放大"
          className={btn}
          disabled={zoom >= ZOOM_MAX}
          onClick={() => setZoom((z) => zoomStep(z, 1))}
        >
          <Plus className="h-3.5 w-3.5" />
        </button>
        <button aria-label="重置缩放" className={btn} onClick={reset} title="重置缩放与平移">
          <Maximize2 className="h-3.5 w-3.5" />
        </button>
        <span className="ml-1 text-[10px] text-zinc-400">滚轮缩放 · 中键/空格拖拽平移</span>
      </div>
      <div
        ref={stageRef}
        data-testid={testId}
        className="relative overflow-hidden rounded-lg bg-zinc-100"
        style={{ maxWidth: maxStageWidth, height: stageHeight }}
        onPointerDown={onPanPointerDown}
        onPointerMove={onPanPointerMove}
        onPointerUp={onPanPointerUp}
      >
        <div
          data-testid={`${testId}-viewport`}
          className="absolute left-0 top-0"
          style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`, transformOrigin: "0 0" }}
        >
          {typeof children === "function" ? children(zoom) : children}
        </div>
      </div>
    </div>
  );
}
