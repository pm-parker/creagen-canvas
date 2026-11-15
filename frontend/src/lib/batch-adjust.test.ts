import { describe, expect, it } from "vitest";
import { planBatchAdjust, summarizeBatchResult } from "./batch-adjust";
import { DEFAULT_ADJUST } from "./image-adjust";
import type { AdjustValues } from "./image-adjust";

describe("batch-adjust（P1-4 批量调整规划）", () => {
  const targets = [
    { nodeId: "a", src: "/outputs/1.png" },
    { nodeId: "b", src: "/outputs/2.png" },
    { nodeId: "c", src: "/outputs/3.png" },
  ];

  it("planBatchAdjust：默认参数返回空计划（无谓烘焙不执行）", () => {
    expect(planBatchAdjust(targets, { ...DEFAULT_ADJUST })).toEqual([]);
  });

  it("planBatchAdjust：非默认参数逐一生成计划项（带 filter 串）", () => {
    const v: AdjustValues = { ...DEFAULT_ADJUST, brightness: 120 };
    const plan = planBatchAdjust(targets, v);
    expect(plan).toHaveLength(3);
    expect(plan[0]).toEqual({ nodeId: "a", src: "/outputs/1.png", filter: "brightness(1.2)" });
    expect(plan[2].nodeId).toBe("c");
  });

  it("summarizeBatchResult：成功/失败计数", () => {
    const s = summarizeBatchResult([
      { nodeId: "a", ok: true },
      { nodeId: "b", ok: false, error: "401" },
      { nodeId: "c", ok: true },
    ]);
    expect(s).toEqual({ ok: 2, failed: 1 });
  });
});
