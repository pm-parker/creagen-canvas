import { describe, expect, it } from "vitest";
import { constrainToRatio, fitScale, normalizeRect, toImageRect } from "./crop-rect";

describe("normalizeRect", () => {
  it("任意方向拖拽 → 正矩形", () => {
    const r = normalizeRect(100, 80, 40, 30, 800, 600);
    expect(r).toEqual({ x: 40, y: 30, w: 60, h: 50 });
  });

  it("clamp 到画布范围（负坐标/越界）", () => {
    const r = normalizeRect(-10, -5, 9999, 9999, 800, 600);
    expect(r).toEqual({ x: 0, y: 0, w: 800, h: 600 });
  });

  it("最小尺寸 1px", () => {
    const r = normalizeRect(50, 50, 50, 50, 800, 600);
    expect(r.w).toBe(1);
    expect(r.h).toBe(1);
  });
});

describe("toImageRect", () => {
  it("显示坐标按 scale 映射回原图", () => {
    // 显示 0.5x：选区 (20,10,100,60) → 原图 (40,20,200,120)
    const r = toImageRect({ x: 20, y: 10, w: 100, h: 60 }, 0.5, 800, 600);
    expect(r).toEqual({ x: 40, y: 20, w: 200, h: 120 });
  });

  it("选区越界 clamp 到原图边界", () => {
    const r = toImageRect({ x: 700, y: 500, w: 400, h: 300 }, 1, 800, 600);
    expect(r).toEqual({ x: 700, y: 500, w: 100, h: 100 });
  });
});

describe("fitScale", () => {
  it("大图缩小适配", () => {
    expect(fitScale(2000, 1000, 720, 460)).toBe(0.36);
  });

  it("小图不放大（cap 1）", () => {
    expect(fitScale(200, 100, 720, 460)).toBe(1);
  });

  it("非法尺寸返回 1", () => {
    expect(fitScale(0, 100, 720, 460)).toBe(1);
  });
});

describe("constrainToRatio（P0-4 比例锁定）", () => {
  it("ratio=null 原样返回", () => {
    const r = { x: 10, y: 10, w: 200, h: 80 };
    expect(constrainToRatio(r, null, 800, 600)).toEqual(r);
  });

  it("1:1 以宽为准收敛高", () => {
    const r = constrainToRatio({ x: 10, y: 10, w: 200, h: 80 }, 1, 800, 600);
    expect(r.w).toBe(200);
    expect(r.h).toBe(200);
    expect(r.x).toBe(10);
    expect(r.y).toBe(10);
  });

  it("16:9 以宽为准；9:16 以高为准（窄高选区）", () => {
    const wide = constrainToRatio({ x: 0, y: 0, w: 320, h: 100 }, 16 / 9, 800, 600);
    expect(wide.h).toBe(180);
    const tall = constrainToRatio({ x: 0, y: 0, w: 100, h: 320 }, 9 / 16, 800, 600);
    expect(tall.w).toBe(180);
  });

  it("越界时以画布边界反向收缩", () => {
    // 选区贴底：1:1 收敛高时越界 → 保持底部对齐收缩宽
    const r = constrainToRatio({ x: 700, y: 500, w: 100, h: 100 }, 1, 800, 600);
    expect(r.x + r.w).toBeLessThanOrEqual(800);
    expect(r.y + r.h).toBeLessThanOrEqual(600);
    expect(Math.abs(r.w - r.h)).toBeLessThanOrEqual(1);
  });
});
