import { describe, expect, it } from "vitest";
import { toBackendViewport, toFlowViewport } from "./viewport";

describe("viewport 转换（Review Focus #5）", () => {
  it("后端 {x,y,k} → React Flow {x,y,zoom}", () => {
    expect(toFlowViewport({ x: 10, y: -20, k: 1.5 })).toEqual({ x: 10, y: -20, zoom: 1.5 });
  });

  it("React Flow {x,y,zoom} → 后端 {x,y,k}", () => {
    expect(toBackendViewport({ x: 0, y: 0, zoom: 0.8 })).toEqual({ x: 0, y: 0, k: 0.8 });
  });

  it("双向互逆（含小数与缩放边界）", () => {
    for (const vp of [
      { x: 0, y: 0, zoom: 1 },
      { x: -123.5, y: 456.25, zoom: 0.2 },
      { x: 999, y: -1, zoom: 2 },
    ]) {
      expect(toFlowViewport(toBackendViewport(vp))).toEqual(vp);
    }
  });

  it("后端缺省 k 视为 1；非法输入回退默认视口", () => {
    expect(toFlowViewport({ x: 1, y: 2 })).toEqual({ x: 1, y: 2, zoom: 1 });
    expect(toFlowViewport(null)).toEqual({ x: 0, y: 0, zoom: 1 });
    expect(toFlowViewport("bad" as unknown as { x: number; y: number; k: number })).toEqual({
      x: 0,
      y: 0,
      zoom: 1,
    });
  });
});
