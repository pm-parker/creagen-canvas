/** 图片宫格切分：把 w×h 图均分为 grid×grid 块，返回每块源坐标（像素）。 */

export function gridSplitCells(
  width: number,
  height: number,
  grid: 2 | 3 | 4,
): Array<{ sx: number; sy: number; sw: number; sh: number }> {
  if (![2, 3, 4].includes(grid)) return [];
  if (width <= 0 || height <= 0) return [];
  const sw = Math.floor(width / grid);
  const sh = Math.floor(height / grid);
  if (sw <= 0 || sh <= 0) return [];
  const cells: Array<{ sx: number; sy: number; sw: number; sh: number }> = [];
  for (let row = 0; row < grid; row++) {
    for (let col = 0; col < grid; col++) {
      const sx = col * sw;
      const sy = row * sh;
      // 末行/末列吸收余数像素，保证完整覆盖
      const w = col === grid - 1 ? width - sx : sw;
      const h = row === grid - 1 ? height - sy : sh;
      cells.push({ sx, sy, sw: w, sh: h });
    }
  }
  return cells;
}

/** 自定义行列切分（P0-4）：cols×rows 各 1~4；末行/末列吸收余数像素。 */
export function gridSplitRC(
  width: number,
  height: number,
  cols: number,
  rows: number,
): Array<{ sx: number; sy: number; sw: number; sh: number }> {
  if (!Number.isInteger(cols) || !Number.isInteger(rows)) return [];
  if (cols < 1 || cols > 4 || rows < 1 || rows > 4) return [];
  if (width <= 0 || height <= 0) return [];
  const sw = Math.floor(width / cols);
  const sh = Math.floor(height / rows);
  if (sw <= 0 || sh <= 0) return [];
  const cells: Array<{ sx: number; sy: number; sw: number; sh: number }> = [];
  for (let row = 0; row < rows; row++) {
    for (let col = 0; col < cols; col++) {
      const sx = col * sw;
      const sy = row * sh;
      const w = col === cols - 1 ? width - sx : sw;
      const h = row === rows - 1 ? height - sy : sh;
      cells.push({ sx, sy, sw: w, sh: h });
    }
  }
  return cells;
}
