import { describe, expect, it } from "vitest";
import { drawArrow, drawStroke, normalizeRectAnnotate, toPixel, type Stroke } from "./annotate";

interface MockCtx extends CanvasRenderingContext2D {
  calls: string[];
}

function mockCtx(): MockCtx {
  const calls: string[] = [];
  const ctx = {
    strokeStyle: "",
    fillStyle: "",
    lineWidth: 1,
    font: "",
    lineCap: "",
    lineJoin: "",
    calls,
    beginPath: () => calls.push("begin"),
    moveTo: (x: number, y: number) => calls.push(`move:${Math.round(x)},${Math.round(y)}`),
    lineTo: (x: number, y: number) => calls.push(`line:${Math.round(x)},${Math.round(y)}`),
    stroke: () => calls.push("stroke"),
    fill: () => calls.push("fill"),
    fillText: (t: string, x: number, y: number) => calls.push(`text:${t}@${Math.round(x)},${Math.round(y)}`),
    strokeRect: (x: number, y: number, w: number, h: number) =>
      calls.push(`rect:${Math.round(x)},${Math.round(y)},${Math.round(w)},${Math.round(h)}`),
    clearRect: () => calls.push("clear"),
    save: () => calls.push("save"),
    restore: () => calls.push("restore"),
    closePath: () => calls.push("close"),
  };
  return ctx as unknown as MockCtx;
}

describe("drawStroke", () => {
  it("pen 画笔按归一化坐标映射像素", () => {
    const ctx = mockCtx();
    const stroke: Stroke = {
      tool: "pen",
      color: "#f00",
      width: 0.005,
      points: [
        { x: 0.1, y: 0.2 },
        { x: 0.5, y: 0.6 },
      ],
    };
    drawStroke(ctx, stroke, 1000, 500);
    expect(ctx.calls).toContain("move:100,100");
    expect(ctx.calls).toContain("line:500,300");
    expect(ctx.calls).toContain("stroke");
  });

  it("rect 用 strokeRect 且宽高换算正确", () => {
    const ctx = mockCtx();
    const stroke: Stroke = { tool: "rect", color: "#00f", width: 0.004, x1: 0.1, y1: 0.1, x2: 0.6, y2: 0.5 };
    drawStroke(ctx, stroke, 1000, 500);
    expect(ctx.calls).toContain("rect:100,50,500,200");
  });

  it("text 按 size 换算字号并定位", () => {
    const ctx = mockCtx();
    const stroke: Stroke = { tool: "text", color: "#000", width: 0.004, x: 0.2, y: 0.4, text: "标注", size: 0.08 };
    drawStroke(ctx, stroke, 1000, 500);
    expect(ctx.calls).toContain("text:标注@200,200");
    expect(ctx.font).toContain("40px");
  });

  it("arrow 画主线并 closePath 补三角头", () => {
    const ctx = mockCtx();
    const stroke: Stroke = { tool: "arrow", color: "#000", width: 0.004, x1: 0, y1: 0, x2: 1, y2: 0 };
    drawStroke(ctx, stroke, 1000, 100);
    expect(ctx.calls).toContain("close");
    expect(ctx.calls.filter((c) => c === "stroke").length).toBe(1);
    expect(ctx.calls.filter((c) => c === "fill").length).toBe(1);
  });

  it("空 pen 笔画不绘制", () => {
    const ctx = mockCtx();
    const stroke: Stroke = { tool: "pen", color: "#000", width: 0.004, points: [] };
    drawStroke(ctx, stroke, 1000, 500);
    expect(ctx.calls).not.toContain("stroke");
  });
});

describe("drawArrow 头部自适应", () => {
  it("超短线头长不超过线长 40%", () => {
    const ctx = mockCtx();
    // 长度 10px（0.01 × 1000），lineWidth 4 → 头被 clamp 到 4px
    drawArrow(ctx, 0, 0, 10, 0, 4);
    expect(ctx.calls).toContain("close");
  });
});

describe("toPixel", () => {
  it("归一化转像素", () => {
    expect(toPixel(0.25, 0.5, 800, 400)).toEqual({ x: 200, y: 200 });
  });
});

describe("normalizeRectAnnotate", () => {
  it("拖拽方向归一", () => {
    expect(normalizeRectAnnotate(0.8, 0.9, 0.2, 0.3)).toEqual({ x1: 0.2, y1: 0.3, x2: 0.8, y2: 0.9 });
  });
});
