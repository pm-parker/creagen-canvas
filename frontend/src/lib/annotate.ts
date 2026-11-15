/**
 * 标注引擎（纯逻辑，与 DOM 解耦）。
 * 坐标体系：全部使用归一化坐标（0-1，相对图片宽高），绘制时乘以画布像素尺寸。
 */

export type AnnotateTool = "pen" | "rect" | "arrow" | "text";

export interface BaseStroke {
  tool: AnnotateTool;
  color: string;
  width: number; // 相对图片宽度比例（如 0.004）
}

export interface PenStroke extends BaseStroke {
  tool: "pen";
  points: Array<{ x: number; y: number }>;
}

export interface ShapeStroke extends BaseStroke {
  tool: "rect" | "arrow";
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export interface TextStroke extends BaseStroke {
  tool: "text";
  x: number;
  y: number;
  text: string;
  size: number; // 相对图片高度比例
}

export type Stroke = PenStroke | ShapeStroke | TextStroke;

export const ANNOTATE_COLORS = ["#ef4444", "#f59e0b", "#10b981", "#3b82f6", "#ffffff"] as const;

/** 画一条笔画到画布（W/H 为画布像素尺寸）。 */
export function drawStroke(ctx: CanvasRenderingContext2D, stroke: Stroke, w: number, h: number): void {
  ctx.save();
  ctx.strokeStyle = stroke.color;
  ctx.fillStyle = stroke.color;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.lineWidth = Math.max(1, stroke.width * w);

  if (stroke.tool === "pen") {
    const pts = stroke.points;
    if (pts.length === 0) {
      ctx.restore();
      return;
    }
    ctx.beginPath();
    ctx.moveTo(pts[0].x * w, pts[0].y * h);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x * w, pts[i].y * h);
    if (pts.length === 1) ctx.lineTo(pts[0].x * w + 0.01, pts[0].y * h);
    ctx.stroke();
  } else if (stroke.tool === "rect") {
    ctx.strokeRect(stroke.x1 * w, stroke.y1 * h, (stroke.x2 - stroke.x1) * w, (stroke.y2 - stroke.y1) * h);
  } else if (stroke.tool === "arrow") {
    drawArrow(ctx, stroke.x1 * w, stroke.y1 * h, stroke.x2 * w, stroke.y2 * h, ctx.lineWidth);
  } else if (stroke.tool === "text") {
    ctx.font = `bold ${Math.max(10, stroke.size * h)}px sans-serif`;
    ctx.fillText(stroke.text, stroke.x * w, stroke.y * h);
  }
  ctx.restore();
}

/** 箭头：主线 + 实心三角头部（头长随线宽与线长自适应）。 */
export function drawArrow(
  ctx: CanvasRenderingContext2D,
  x1: number,
  y1: number,
  x2: number,
  y2: number,
  lineWidth: number,
): void {
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);
  ctx.stroke();

  const angle = Math.atan2(y2 - y1, x2 - x1);
  const len = Math.hypot(x2 - x1, y2 - y1);
  const head = Math.min(Math.max(lineWidth * 3.5, 8), len * 0.4);
  ctx.beginPath();
  ctx.moveTo(x2, y2);
  ctx.lineTo(x2 - head * Math.cos(angle - Math.PI / 6), y2 - head * Math.sin(angle - Math.PI / 6));
  ctx.lineTo(x2 - head * Math.cos(angle + Math.PI / 6), y2 - head * Math.sin(angle + Math.PI / 6));
  ctx.closePath();
  ctx.fill();
}

/** 重绘全部笔画（撤销/清空后全量刷新用）。 */
export function redrawAll(ctx: CanvasRenderingContext2D, strokes: Stroke[], w: number, h: number): void {
  ctx.clearRect(0, 0, w, h);
  for (const s of strokes) drawStroke(ctx, s, w, h);
}

/** 归一化坐标 → 画布像素坐标。 */
export function toPixel(nx: number, ny: number, w: number, h: number): { x: number; y: number } {
  return { x: nx * w, y: ny * h };
}

/** 标注矩形归一化（任意方向拖拽 → 正矩形）。 */
export function normalizeRectAnnotate(
  x1: number,
  y1: number,
  x2: number,
  y2: number,
): { x1: number; y1: number; x2: number; y2: number } {
  return {
    x1: Math.min(x1, x2),
    y1: Math.min(y1, y2),
    x2: Math.max(x1, x2),
    y2: Math.max(y1, y2),
  };
}
