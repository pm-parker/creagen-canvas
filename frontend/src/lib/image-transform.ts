/**
 * 图像几何变换与导出（纯逻辑，P1-1）：旋转 90°×n / 翻转 / 缩放 / 格式。
 * 预览用 CSS transform；导出用 canvas 矩阵变换（applyTransform 在组件内，
 * 纯函数只做尺寸/格式计算，DOM 无关便于测试）。
 */

export type RotateQuarter = 0 | 1 | 2 | 3; // 顺时针 90° 的倍数

export interface TransformOptions {
  rotate: RotateQuarter;
  flipH: boolean;
  flipV: boolean;
}

export const DEFAULT_TRANSFORM: TransformOptions = { rotate: 0, flipH: false, flipV: false };

export const EXPORT_MAX = 4096;

export function isDefaultTransform(t: TransformOptions): boolean {
  return t.rotate === 0 && !t.flipH && !t.flipV;
}

/** 旋转后画布尺寸：奇数个 90° 交换宽高。 */
export function rotatedSize(w: number, h: number, rotate: RotateQuarter): { w: number; h: number } {
  return rotate % 2 === 1 ? { w: h, h: w } : { w, h };
}

export interface ExportSizeTarget {
  width?: number;
  height?: number;
  keepRatio?: boolean;
}

/** 导出尺寸：无目标原尺寸；单边按比例；keepRatio（默认 true）时双给以宽为准；clamp 1~4096。 */
export function computeExportSize(w: number, h: number, target: ExportSizeTarget): { width: number; height: number } {
  const clamp = (v: number) => Math.min(EXPORT_MAX, Math.max(1, Math.round(v)));
  if (target.width === undefined && target.height === undefined) {
    return { width: clamp(w), height: clamp(h) };
  }
  if (target.width !== undefined && target.height !== undefined && target.keepRatio === false) {
    return { width: clamp(target.width), height: clamp(target.height) };
  }
  if (target.width !== undefined) {
    const tw = clamp(target.width);
    return { width: tw, height: clamp((h * tw) / w) };
  }
  const th = clamp(target.height!);
  return { width: clamp((w * th) / h), height: th };
}

export type ExportFormat = "png" | "jpeg" | "webp";

/** 导出 MIME：PNG 无质量；JPG/WebP 质量 1~100 → 0.1~1。 */
export function exportMime(format: ExportFormat, quality: number): { mime: string; quality?: number } {
  if (format === "png") return { mime: "image/png" };
  const q = Math.min(1, Math.max(0.1, quality / 100));
  return { mime: `image/${format}`, quality: q };
}
