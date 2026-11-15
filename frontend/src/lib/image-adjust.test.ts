import { describe, expect, it } from "vitest";
import { DEFAULT_ADJUST, buildFilterString, clampAdjust, isDefaultAdjust, type AdjustValues } from "./image-adjust";

describe("image-adjust（调整面板 filter 模型）", () => {
  it("默认值全部中性且 isDefault 为 true", () => {
    expect(DEFAULT_ADJUST).toEqual({ brightness: 100, contrast: 100, saturate: 100, sepia: 0, blur: 0 });
    expect(isDefaultAdjust(DEFAULT_ADJUST)).toBe(true);
  });

  it("buildFilterString：默认值输出空串（无 filter 开销）", () => {
    expect(buildFilterString(DEFAULT_ADJUST)).toBe("");
  });

  it("buildFilterString：仅输出偏离默认的项，值域 clamp", () => {
    const v: AdjustValues = { brightness: 120, contrast: 100, saturate: 80, sepia: 30, blur: 2 };
    expect(buildFilterString(v)).toBe("brightness(1.2) saturate(0.8) sepia(0.3) blur(2px)");
  });

  it("clampAdjust 越界值拉回安全区间", () => {
    const clamped = clampAdjust({ brightness: 500, contrast: -10, saturate: 0, sepia: 200, blur: 100 });
    expect(clamped.brightness).toBe(200);
    expect(clamped.contrast).toBe(50);
    expect(clamped.saturate).toBe(0);
    expect(clamped.sepia).toBe(100);
    expect(clamped.blur).toBe(12);
  });

  it("blur 为 0 时不输出 blur 项（与默认等价）", () => {
    const v: AdjustValues = { ...DEFAULT_ADJUST, blur: 0.0001 };
    expect(buildFilterString(v)).not.toContain("blur");
  });
});
