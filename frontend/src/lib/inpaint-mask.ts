/**
 * J4 局部重绘蒙版 stroke 模型（纯逻辑，与 DOM 解耦）。
 * 坐标体系：归一化（0-1）；每笔 = 一次按下-拖动-抬起的圆点序列 + 归一化半径。
 * 撤销 = 弹出最后一笔（渲染侧全量重绘，避免局部擦除的 alpha 累积问题）。
 */

export interface MaskPoint {
  x: number;
  y: number;
}

export interface MaskStroke {
  points: MaskPoint[];
  /** 笔刷半径（相对图宽 0.01~0.10） */
  radius: number;
}

export interface MaskModel {
  strokes: MaskStroke[];
  /** 是否处于一次按下的进行中（true=后续点并入最后一笔） */
  drawing: boolean;
}

export function newStrokes(): MaskModel {
  return { strokes: [], drawing: false };
}

/** 追加一个点：未在笔画中则开新笔（携带当前笔刷半径）。 */
export function addStrokePoint(model: MaskModel, p: MaskPoint, radius = 0.035): MaskModel {
  if (!model.drawing) {
    return { strokes: [...model.strokes, { points: [p], radius }], drawing: true };
  }
  const strokes = model.strokes.slice(0, -1);
  const last = model.strokes[model.strokes.length - 1];
  strokes.push({ ...last, points: [...last.points, p] });
  return { ...model, strokes };
}

/** 抬起：结束当前笔画（下一次落点开新笔）。 */
export function endStroke(model: MaskModel): MaskModel {
  return { ...model, drawing: false };
}

export function undoStroke(model: MaskModel): MaskModel {
  if (model.strokes.length === 0) return model;
  return { strokes: model.strokes.slice(0, -1), drawing: false };
}

export function hasMaskContent(model: MaskModel): boolean {
  return model.strokes.some((s) => s.points.length > 0);
}

/** 笔刷半径换算：百分比 clamp 到 [0.01, 0.10] 后 ×画布宽。 */
export function brushRadius(pct: number, canvasWidth: number): number {
  const clamped = Math.min(0.1, Math.max(0.01, pct));
  return clamped * canvasWidth;
}
