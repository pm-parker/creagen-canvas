import { describe, expect, it } from "vitest";
import { gridSplitCells, gridSplitRC } from "./grid-split";

describe("宫格切分坐标计算", () => {
  it("4 宫格（2x2）", () => {
    const cells = gridSplitCells(1024, 1024, 2);
    expect(cells).toHaveLength(4);
    expect(cells[0]).toEqual({ sx: 0, sy: 0, sw: 512, sh: 512 });
    expect(cells[3]).toEqual({ sx: 512, sy: 512, sw: 512, sh: 512 });
  });

  it("9 宫格（3x3）覆盖全图无缝", () => {
    const cells = gridSplitCells(900, 600, 3);
    expect(cells).toHaveLength(9);
    const totalArea = cells.reduce((acc, c) => acc + c.sw * c.sh, 0);
    expect(totalArea).toBe(900 * 600);
    // 每块等分
    expect(cells.every((c) => c.sw === 300 && c.sh === 200)).toBe(true);
  });

  it("非法宫格数返回空", () => {
    expect(gridSplitCells(100, 100, 5 as 2)).toEqual([]); // 只支持 2/3/4（即 4/9/16 宫格）
  });
});

describe("gridSplitRC（P0-4 自定义行列）", () => {
  it("2x3 六块：宽 2 等分 / 高 3 等分", () => {
    const cells = gridSplitRC(1000, 900, 2, 3);
    expect(cells).toHaveLength(6);
    expect(cells[0]).toEqual({ sx: 0, sy: 0, sw: 500, sh: 300 });
    expect(cells[5]).toEqual({ sx: 500, sy: 600, sw: 500, sh: 300 });
  });

  it("行列非法（<1 或 >4）返回空", () => {
    expect(gridSplitRC(100, 100, 0, 2)).toEqual([]);
    expect(gridSplitRC(100, 100, 2, 5)).toEqual([]);
  });
});
