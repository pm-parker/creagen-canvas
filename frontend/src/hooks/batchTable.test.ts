import { describe, expect, it } from "vitest";
import { createBatchRows, type BatchRow, settleBatchRow } from "./batchTable";

describe("批量创作表", () => {
  it("createRows：按数量生成空行，id 唯一", () => {
    const rows = createBatchRows(3);
    expect(rows).toHaveLength(3);
    expect(new Set(rows.map((r) => r.id)).size).toBe(3);
    expect(rows.every((r) => r.status === "idle" && r.prompt === "")).toBe(true);
  });

  it("settleBatchRow：只更新目标行", () => {
    const rows: BatchRow[] = [
      { id: "a", prompt: "p1", status: "generating", taskId: 11 },
      { id: "b", prompt: "p2", status: "idle" },
    ];
    const next = settleBatchRow(rows, 11, { status: "completed", outputUrl: "/x.png" });
    expect(next[0].status).toBe("completed");
    expect(next[0].outputUrl).toBe("/x.png");
    expect(next[1].status).toBe("idle");
  });

  it("settleBatchRow：taskId 不存在时返回原数组（不可变）", () => {
    const rows: BatchRow[] = [{ id: "a", prompt: "p", status: "idle" }];
    const next = settleBatchRow(rows, 999, { status: "failed", error: "x" });
    expect(next).toBe(rows); // 未变化直接返回原引用，便于跳过重渲染
  });

  it("可生成判定：prompt 非空且未在生成中", () => {
    const rows: BatchRow[] = [
      { id: "a", prompt: "有内容", status: "idle" },
      { id: "b", prompt: "", status: "idle" },
      { id: "c", prompt: "生成中", status: "generating" },
    ];
    expect(rows.filter((r) => r.status === "idle" && r.prompt.trim()).map((r) => r.id)).toEqual(["a"]);
  });
});
