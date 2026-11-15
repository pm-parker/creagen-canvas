/** 加工编辑器缩放纯逻辑（ZoomStage 底座，与 DOM 解耦便于测试）。 */

export const ZOOM_MIN = 0.5;
export const ZOOM_MAX = 4;
const STEP = 1.25;

/** 步进倍率下的最小/最大值（保证按钮步进能到达边界）。 */
function clampZoom(z: number): number {
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z));
}

/** 按钮步进：dir=1 放大 / dir=-1 缩小（×1.25），越界取边界。 */
export function zoomStep(current: number, dir: 1 | -1): number {
  return clampZoom(dir === 1 ? current * STEP : current / STEP);
}

/** 滚轮：deltaY<0（上滚）放大；小步长随 |deltaY| 缩放。 */
export function nextWheelZoom(current: number, deltaY: number): number {
  if (deltaY === 0) return clampZoom(current);
  const factor = Math.pow(STEP, (-deltaY / 100) * 0.5);
  return clampZoom(current * factor);
}

export { clampZoom };
