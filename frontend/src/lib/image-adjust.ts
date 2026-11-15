/**
 * 图像调整模型（纯逻辑）：亮度/对比度/饱和度/色温(sepia)/模糊(blur)。
 * 预览 = img style.filter；导出 = canvas ctx.filter 烘焙（同一字符串保证一致）。
 * CSS filter 语义：brightness(1.2)=+20%；sepia 近似暖色（色温）；blur 单位 px。
 */

export interface AdjustValues {
  /** 50~200，100=中性 */
  brightness: number;
  /** 50~200，100=中性 */
  contrast: number;
  /** 0~200，100=中性 */
  saturate: number;
  /** 0~100 暖色强度，0=中性 */
  sepia: number;
  /** 0~12 px，0=中性 */
  blur: number;
}

export const DEFAULT_ADJUST: AdjustValues = {
  brightness: 100,
  contrast: 100,
  saturate: 100,
  sepia: 0,
  blur: 0,
};

const RANGES: Record<keyof AdjustValues, [number, number]> = {
  brightness: [50, 200],
  contrast: [50, 200],
  saturate: [0, 200],
  sepia: [0, 100],
  blur: [0, 12],
};

export function clampAdjust(v: AdjustValues): AdjustValues {
  const out = { ...v };
  for (const key of Object.keys(RANGES) as Array<keyof AdjustValues>) {
    const [min, max] = RANGES[key];
    out[key] = Math.min(max, Math.max(min, v[key]));
  }
  return out;
}

/** 与默认值的容差比较（滑杆抖动 1 以内视为中性）。 */
export function isDefaultAdjust(v: AdjustValues): boolean {
  return buildFilterString(v) === "";
}

/** 构建 CSS filter 串：仅输出偏离默认的项；全默认返回空串。 */
export function buildFilterString(v: AdjustValues): string {
  const parts: string[] = [];
  const n = (x: number) => String(parseFloat(x.toFixed(2)));
  if (Math.abs(v.brightness - 100) >= 1) parts.push(`brightness(${n(v.brightness / 100)})`);
  if (Math.abs(v.contrast - 100) >= 1) parts.push(`contrast(${n(v.contrast / 100)})`);
  if (Math.abs(v.saturate - 100) >= 1) parts.push(`saturate(${n(v.saturate / 100)})`);
  if (v.sepia >= 1) parts.push(`sepia(${n(v.sepia / 100)})`);
  if (v.blur >= 0.5) parts.push(`blur(${n(v.blur)}px)`);
  return parts.join(" ");
}
