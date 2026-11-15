/**
 * 批量调整规划（P1-4 纯逻辑）：框选多图节点套同一 AdjustValues。
 * 计划项 = nodeId + src + 已烘焙 filter 串；执行侧（组件）逐项 fetch→canvas→upload。
 */

import { buildFilterString, type AdjustValues } from "./image-adjust";

export interface BatchTarget {
  nodeId: string;
  src: string;
}

export interface BatchPlanItem extends BatchTarget {
  filter: string;
}

/** 默认参数 → 空计划（无谓烘焙不执行）；非默认 → 逐目标生成。 */
export function planBatchAdjust(targets: BatchTarget[], values: AdjustValues): BatchPlanItem[] {
  const filter = buildFilterString(values);
  if (!filter) return [];
  return targets.map((t) => ({ ...t, filter }));
}

export interface BatchItemResult {
  nodeId: string;
  ok: boolean;
  error?: string;
}

export function summarizeBatchResult(results: BatchItemResult[]): { ok: number; failed: number } {
  return {
    ok: results.filter((r) => r.ok).length,
    failed: results.filter((r) => !r.ok).length,
  };
}
