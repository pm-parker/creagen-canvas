import { describe, expect, it } from "vitest";
import { ZOOM_MAX, ZOOM_MIN, clampZoom, nextWheelZoom, zoomStep } from "./editor-zoom";

describe("editor-zoom", () => {
  it("clampZoom 边界与正常值", () => {
    expect(ZOOM_MIN).toBe(0.5);
    expect(ZOOM_MAX).toBe(4);
    expect(clampZoom(0.2)).toBe(0.5);
    expect(clampZoom(9)).toBe(4);
    expect(clampZoom(1.75)).toBe(1.75);
  });

  it("zoomStep 按 1.25 倍步进并 clamp", () => {
    expect(zoomStep(1, 1)).toBeCloseTo(1.25);
    expect(zoomStep(1, -1)).toBeCloseTo(0.8);
    // 步进越界即 clamp
    expect(zoomStep(4, 1)).toBe(4);
    expect(zoomStep(0.5, -1)).toBe(0.5);
  });

  it("nextWheelZoom：滚轮上滚放大、下滚缩小", () => {
    expect(nextWheelZoom(1, -100)).toBeGreaterThan(1);
    expect(nextWheelZoom(1, 100)).toBeLessThan(1);
    expect(nextWheelZoom(1, 0)).toBe(1);
    // 小步长多次累计不越界
    let z = 1;
    for (let i = 0; i < 50; i++) z = nextWheelZoom(z, 100);
    expect(z).toBeGreaterThanOrEqual(0.5);
  });
});
