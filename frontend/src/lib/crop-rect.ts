/**
 * 裁切选区计算（纯函数，与 DOM 解耦便于测试）。
 * 坐标体系：displayX/Y 为画布显示坐标，scale 为 显示/原图 缩放比。
 */

export interface CropRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** 由拖拽起点/终点构造归一化选区（支持任意方向拖拽），并 clamp 到画布范围。 */
export function normalizeRect(
  startX: number,
  startY: number,
  endX: number,
  endY: number,
  maxW: number,
  maxH: number,
): CropRect {
  const x1 = Math.max(0, Math.min(startX, endX));
  const y1 = Math.max(0, Math.min(startY, endY));
  const x2 = Math.min(maxW, Math.max(startX, endX));
  const y2 = Math.min(maxH, Math.max(startY, endY));
  return { x: x1, y: y1, w: Math.max(1, Math.round(x2 - x1)), h: Math.max(1, Math.round(y2 - y1)) };
}

/** 显示坐标 → 原图像素坐标（裁切用），并 clamp 到原图边界。 */
export function toImageRect(rect: CropRect, scale: number, imgW: number, imgH: number): CropRect {
  const sx = Math.max(0, Math.min(Math.round(rect.x / scale), imgW - 1));
  const sy = Math.max(0, Math.min(Math.round(rect.y / scale), imgH - 1));
  const sw = Math.max(1, Math.min(Math.round(rect.w / scale), imgW - sx));
  const sh = Math.max(1, Math.min(Math.round(rect.h / scale), imgH - sy));
  return { x: sx, y: sy, w: sw, h: sh };
}

/** 等比缩放系数：图片适配显示区域（不超过 1，避免放大模糊）。 */
export function fitScale(imgW: number, imgH: number, maxW: number, maxH: number): number {
  if (imgW <= 0 || imgH <= 0) return 1;
  return Math.min(maxW / imgW, maxH / imgH, 1);
}

/** 比例锁定（P0-4）：把选区约束到目标宽高比（ratio=w/h；null=自由）。
 * 主导轴规则：当前宽高比大于目标 → 以宽为准收敛高；反之以高为准收敛宽。
 * 越界时按画布边界收缩（保持左上锚点），最小 1px。
 */
export function constrainToRatio(
  rect: CropRect,
  ratio: number | null,
  maxW: number,
  maxH: number,
): CropRect {
  if (ratio === null || ratio <= 0) return rect;
  let w = rect.w;
  let h = rect.h;
  if (w / h > ratio) h = w / ratio;
  else w = h * ratio;
  const maxFitW = maxW - rect.x;
  const maxFitH = maxH - rect.y;
  if (w > maxFitW) {
    w = maxFitW;
    h = w / ratio;
  }
  if (h > maxFitH) {
    h = maxFitH;
    w = h * ratio;
  }
  return { x: rect.x, y: rect.y, w: Math.max(1, Math.round(w)), h: Math.max(1, Math.round(h)) };
}
