import { describe, expect, it } from "vitest";
import {
  DEFAULT_TRANSFORM,
  computeExportSize,
  exportMime,
  isDefaultTransform,
  rotatedSize,
} from "./image-transform";

describe("image-transform（P1-1 变换导出）", () => {
  it("默认变换 isDefault；任何偏离即非默认", () => {
    expect(DEFAULT_TRANSFORM).toEqual({ rotate: 0, flipH: false, flipV: false });
    expect(isDefaultTransform(DEFAULT_TRANSFORM)).toBe(true);
    expect(isDefaultTransform({ rotate: 1, flipH: false, flipV: false })).toBe(false);
    expect(isDefaultTransform({ rotate: 0, flipH: true, flipV: false })).toBe(false);
  });

  it("rotatedSize：奇数个 90° 交换宽高", () => {
    expect(rotatedSize(1024, 768, 0)).toEqual({ w: 1024, h: 768 });
    expect(rotatedSize(1024, 768, 1)).toEqual({ w: 768, h: 1024 });
    expect(rotatedSize(1024, 768, 2)).toEqual({ w: 1024, h: 768 });
    expect(rotatedSize(1024, 768, 3)).toEqual({ w: 768, h: 1024 });
  });

  it("computeExportSize：无目标原尺寸；只给宽/高按比例；keepRatio 以宽为准", () => {
    expect(computeExportSize(1000, 500, {})).toEqual({ width: 1000, height: 500 });
    expect(computeExportSize(1000, 500, { width: 500 })).toEqual({ width: 500, height: 250 });
    expect(computeExportSize(1000, 500, { height: 100 })).toEqual({ width: 200, height: 100 });
    expect(computeExportSize(1000, 500, { width: 300, height: 999, keepRatio: true })).toEqual({
      width: 300,
      height: 150,
    });
    expect(computeExportSize(1000, 500, { width: 300, height: 999, keepRatio: false })).toEqual({
      width: 300,
      height: 999,
    });
  });

  it("computeExportSize：值域 clamp 1~4096 且取整", () => {
    expect(computeExportSize(1000, 500, { width: 0 })).toEqual({ width: 1, height: 1 });
    expect(computeExportSize(1000, 500, { width: 9999 })).toEqual({ width: 4096, height: 2048 });
    expect(computeExportSize(999, 333, { width: 100 })).toEqual({ width: 100, height: 33 });
  });

  it("exportMime：PNG 无质量参数；JPG/WebP 质量 1~100 映射 0.1~1", () => {
    expect(exportMime("png", 90)).toEqual({ mime: "image/png" });
    expect(exportMime("jpeg", 90)).toEqual({ mime: "image/jpeg", quality: 0.9 });
    expect(exportMime("webp", 100)).toEqual({ mime: "image/webp", quality: 1 });
    expect(exportMime("jpeg", 0).quality).toBeCloseTo(0.1);
    expect(exportMime("jpeg", 200).quality).toBe(1);
  });
});
