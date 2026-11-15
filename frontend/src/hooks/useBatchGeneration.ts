import { useCallback, useState } from "react";
import { apiFetch } from "@/lib/api";
import { addRow, removeRow, settleBatchRow, type BatchRow } from "./batchTable";

/** 批量生成 hook：行管理 + 逐行提交 + 轮询收敛。 */
export function useBatchGeneration(domain: "image" | "video", model: string) {
  const [rows, setRows] = useState<BatchRow[]>(() => addRow([], ""));

  const updatePrompt = useCallback((id: string, prompt: string) => {
    setRows((rs) => rs.map((r) => (r.id === id ? { ...r, prompt } : r)));
  }, []);

  const add = useCallback(() => setRows((rs) => addRow(rs, "")), []);
  const remove = useCallback((id: string) => setRows((rs) => removeRow(rs, id)), []);

  const run = useCallback(async () => {
    const pending = rows.filter((r) => r.status === "idle" && r.prompt.trim());
    if (pending.length === 0) return;
    setRows((rs) =>
      rs.map((r) => (pending.some((p) => p.id === r.id) ? { ...r, status: "queued" } : r)),
    );
    for (const row of pending) {
      try {
        const resp = await apiFetch<{ task: { id: number } }>("/api/tasks", {
          method: "POST",
          json: {
            type: domain,
            prompt: row.prompt,
            model,
            params: domain === "image" ? { image_count: 1 } : { duration: 5 },
          },
        });
        const taskId = resp.task.id;
        setRows((rs) => rs.map((r) => (r.id === row.id ? { ...r, taskId } : r)));
        void poll(taskId, row.id);
      } catch (e) {
        const message = e instanceof Error ? e.message : "提交失败";
        setRows((rs) => rs.map((r) => (r.id === row.id ? { ...r, status: "failed" as const, error: message } : r)));
      }
    }
  }, [rows, domain, model]);

  async function poll(taskId: number, _rowId: string) {
    for (let i = 0; i < 60; i++) {
      await new Promise((r) => setTimeout(r, 2500));
      try {
        const detail = await apiFetch<{ task: { status: string; output_url?: string; error_msg?: string } }>(
          `/api/tasks/${taskId}`,
        );
        const t = detail.task;
        if (t.status === "completed" || t.status === "failed") {
          setRows((rs) =>
            settleBatchRow(rs, taskId, {
              status: t.status as BatchRow["status"],
              outputUrl: t.output_url,
              error: t.error_msg ?? undefined,
            }),
          );
          return;
        }
      } catch {
        /* 网络抖动继续轮询 */
      }
    }
    setRows((rs) =>
      settleBatchRow(rs, taskId, { status: "failed", error: "轮询超时" }),
    );
  }

  return { rows, updatePrompt, add, remove, run, isRunning: rows.some((r) => r.status === "queued" || r.status === "generating") };
}

/** hook 签名备注：model 由调用方（面板 ModelSelect）给出，空串由后端通配渠道处理。 */
