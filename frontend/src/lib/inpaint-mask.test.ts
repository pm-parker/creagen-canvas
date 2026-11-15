import { describe, expect, it } from "vitest";
import { addStrokePoint, brushRadius, endStroke, hasMaskContent, newStrokes, undoStroke } from "./inpaint-mask";

describe("inpaint-mask（J4 蒙版 stroke 模型）", () => {
  it("newStrokes 空模型；未落笔前 addStrokePoint 开新笔，同笔内追加", () => {
    let s = newStrokes();
    expect(s.strokes.length).toBe(0);
    expect(hasMaskContent(s)).toBe(false);
    s = addStrokePoint(s, { x: 0.1, y: 0.2 });
    expect(s.strokes.length).toBe(1);
    expect(s.strokes[0].points.length).toBe(1);
    expect(s.drawing).toBe(true);
    s = addStrokePoint(s, { x: 0.12, y: 0.21 });
    expect(s.strokes[0].points.length).toBe(2);
    expect(s.strokes.length).toBe(1);
  });

  it("抬起结束一笔；再次落下开新笔；undoStroke 撤销最后一笔", () => {
    let s = newStrokes();
    s = addStrokePoint(s, { x: 0.1, y: 0.1 });
    s = endStroke(s);
    expect(s.drawing).toBe(false);
    s = addStrokePoint(s, { x: 0.3, y: 0.3 });
    s = endStroke(s);
    expect(s.strokes.length).toBe(2);
    s = undoStroke(s);
    expect(s.strokes.length).toBe(1);
    expect(s.strokes[0].points[0]).toEqual({ x: 0.1, y: 0.1 });
    s = undoStroke(s);
    expect(s.strokes.length).toBe(0);
    expect(hasMaskContent(s)).toBe(false);
    // 空模型再撤不炸
    expect(undoStroke(s)).toBe(s);
  });

  it("hasMaskContent：有点即 true", () => {
    let s = newStrokes();
    expect(hasMaskContent(s)).toBe(false);
    s = addStrokePoint(s, { x: 0.5, y: 0.5 });
    expect(hasMaskContent(s)).toBe(true);
  });

  it("brushRadius：百分比(0.01~0.10)×画布宽，越界 clamp", () => {
    expect(brushRadius(0.035, 1000)).toBe(35);
    expect(brushRadius(0.01, 1000)).toBe(10);
    expect(brushRadius(0.1, 1000)).toBe(100);
    expect(brushRadius(0.5, 1000)).toBe(100); // clamp 上限
    expect(brushRadius(0.001, 1000)).toBe(10); // clamp 下限
  });
});
