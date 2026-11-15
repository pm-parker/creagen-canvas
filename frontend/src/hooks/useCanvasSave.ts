import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "@/lib/api";
import { buildNodePayload, mapEdgesByNodeOrder } from "@/lib/savePayload";
import { useCanvasStore } from "@/stores/canvas";

export type SaveStatus = "saved" | "saving" | "dirty" | "error";

const DEBOUNCE_MS = 1200;
const BASE_RETRY_MS = 5000;
const MAX_RETRY_MS = 30000;

/**
 * 画布保存管线：loaded 守卫 → 防抖 1.2s → PUT nodes（全量）→ 按 client_id 映射 → PUT connections。
 * 失败保留 dirty 指数退避重试（5s→30s 封顶）。
 */
export function useCanvasSave(projectId: string | undefined) {
  const nodes = useCanvasStore((s) => s.nodes);
  const edges = useCanvasStore((s) => s.edges);
  const dirty = useCanvasStore((s) => s.dirty);

  const [status, setStatus] = useState<SaveStatus>("saved");
  const [retryCount, setRetryCount] = useState(0);
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const doSave = useCallback(async () => {
    const store = useCanvasStore.getState();
    if (!store.loaded || projectId === undefined) return;
    setStatus("saving");
    try {
      const payload = buildNodePayload(store.nodes);
      const resp = await apiFetch<{ nodes: Array<{ id: number; client_id?: string | null }> }>(
        `/api/canvas/projects/${projectId}/nodes`,
        { method: "PUT", json: { nodes: payload } },
      );
      const connPayload = mapEdgesByNodeOrder(store.edges, resp.nodes);
      await apiFetch(`/api/canvas/projects/${projectId}/connections`, {
        method: "PUT",
        json: { connections: connPayload },
      });
      store.clearDirty();
      setRetryCount(0);
      setStatus("saved");
      // 自动版本快照：距上次 >5 分钟时创建（对标 BeefTV 快照节流）
      try {
        const last = Number(localStorage.getItem(`cc-last-snap-${projectId}`) ?? 0);
        if (Date.now() - last > 5 * 60 * 1000) {
          await apiFetch(`/api/canvas/projects/${projectId}/snapshots`, { method: "POST", json: {} });
          localStorage.setItem(`cc-last-snap-${projectId}`, String(Date.now()));
        }
      } catch {
        /* 快照失败不影响保存 */
      }
    } catch {
      setRetryCount((n) => {
        const next = n + 1;
        const delay = Math.min(BASE_RETRY_MS * 2 ** (next - 1), MAX_RETRY_MS);
        if (retryTimer.current) clearTimeout(retryTimer.current);
        retryTimer.current = setTimeout(() => void doSave(), delay);
        return next;
      });
      setStatus("error");
    }
  }, [projectId]);

  // dirty 变化触发防抖保存
  useEffect(() => {
    if (!dirty) return;
    if (debounceTimer.current) clearTimeout(debounceTimer.current);
    setStatus("saving");
    debounceTimer.current = setTimeout(() => void doSave(), DEBOUNCE_MS);
    return () => {
      if (debounceTimer.current) clearTimeout(debounceTimer.current);
    };
  }, [dirty, nodes, edges, doSave]);

  // 未保存时离开页面提醒
  useEffect(() => {
    function beforeUnload(e: BeforeUnloadEvent) {
      if (useCanvasStore.getState().dirty) {
        e.preventDefault();
      }
    }
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, []);

  return { status, retryCount };
}
