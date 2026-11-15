/** 批量创作表状态机（对标 BeefTV 批量创作表，简化为面板形态）。 */

export type BatchRowStatus = "idle" | "queued" | "generating" | "completed" | "failed";

export interface BatchRow {
  id: string;
  prompt: string;
  status: BatchRowStatus;
  taskId?: number;
  outputUrl?: string;
  error?: string;
}

let seq = 0;

export function createBatchRows(n: number): BatchRow[] {
  return Array.from({ length: n }, () => ({
    id: `br-${Date.now()}-${seq++}-${Math.random().toString(36).slice(2, 8)}`,
    prompt: "",
    status: "idle" as const,
  }));
}

/** 按 taskId 更新行状态；无匹配时返回原引用（跳过无效渲染）。 */
export function settleBatchRow(
  rows: BatchRow[],
  taskId: number,
  patch: Partial<Pick<BatchRow, "status" | "outputUrl" | "error">>,
): BatchRow[] {
  const idx = rows.findIndex((r) => r.taskId === taskId);
  if (idx === -1) return rows;
  const next = [...rows];
  next[idx] = { ...next[idx], ...patch };
  return next;
}

export function addRow(rows: BatchRow[], prompt = ""): BatchRow[] {
  return [...rows, ...createBatchRows(1).map((r) => ({ ...r, prompt }))];
}

export function removeRow(rows: BatchRow[], id: string): BatchRow[] {
  return rows.filter((r) => r.id !== id);
}
