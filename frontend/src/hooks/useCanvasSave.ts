import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch, ApiError } from "@/lib/api";
import { buildNodePayload, mapEdgesByNodeOrder } from "@/lib/savePayload";
import { useCanvasStore } from "@/stores/canvas";

export type SaveStatus = "saved" | "saving" | "dirty" | "error";

const DEBOUNCE_MS = 1200;
const BASE_RETRY_MS = 5000;
const MAX_RETRY_MS = 30000;

interface NodesResponse {
  nodes: Array<{ id: number; client_id?: string | null }>;
  revision: number;
}

/**
 * 画布保存管线：loaded 守卫 → 防抖 1.2s → PUT nodes（携带 base_revision 乐观锁）
 * → 按 client_id 映射 → PUT connections。
 * 409 REVISION_CONFLICT：重取服务端节点合并进本地（服务端新增并入、服务端写入的
 * 元数据保留）后用最新 revision 重试一次——不静默覆盖他端改动。
 * 失败保留 dirty 指数退避重试（5s→30s 封顶）；切画布/卸载时取消重试
 * （防把项目 A 的节点写进项目 B）。
 */
export function useCanvasSave(projectId: string | undefined) {
  const nodes = useCanvasStore((s) => s.nodes);
  const edges = useCanvasStore((s) => s.edges);
  const dirty = useCanvasStore((s) => s.dirty);

  const [status, setStatus] = useState<SaveStatus>("saved");
  const [retryCount, setRetryCount] = useState(0);
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const retryCountRef = useRef(0);
  /** 当前生效的项目：切项目后旧的重试/在途保存不得再写 */
  const activeProject = useRef<string | undefined>(projectId);

  /** PUT nodes（带乐观锁）→ 合并服务端返回 → PUT connections */
  const putNodes = useCallback(async (pid: string): Promise<NodesResponse> => {
    const store = useCanvasStore.getState();
    const resp = await apiFetch<NodesResponse>(`/api/canvas/projects/${pid}/nodes`, {
      method: "PUT",
      json: { nodes: buildNodePayload(store.nodes), base_revision: store.revision },
    });
    if (activeProject.current !== pid) return resp;
    // 服务端最新节点并回本地（补齐服务端新增 / 服务端写入的元数据）
    useCanvasStore.getState().mergeServerNodes(resp.nodes);
    useCanvasStore.getState().setRevision(resp.revision);
    const connPayload = mapEdgesByNodeOrder(useCanvasStore.getState().edges, resp.nodes);
    await apiFetch(`/api/canvas/projects/${pid}/connections`, {
      method: "PUT",
      json: { connections: connPayload },
    });
    return resp;
  }, []);

  const doSave = useCallback(async () => {
    const store = useCanvasStore.getState();
    const pid = activeProject.current;
    if (!store.loaded || pid === undefined) return;
    setStatus("saving");
    try {
      await putNodes(pid);
      if (activeProject.current !== pid) return; // 保存期间切了项目：不写回状态
      useCanvasStore.getState().clearDirty();
      retryCountRef.current = 0;
      setRetryCount(0);
      setStatus("saved");
      // 自动版本快照：距上次 >5 分钟时创建（对标 BeefTV 快照节流）
      try {
        const last = Number(localStorage.getItem(`cc-last-snap-${pid}`) ?? 0);
        if (Date.now() - last > 5 * 60 * 1000) {
          await apiFetch(`/api/canvas/projects/${pid}/snapshots`, { method: "POST", json: {} });
          localStorage.setItem(`cc-last-snap-${pid}`, String(Date.now()));
        }
      } catch {
        /* 快照失败不影响保存 */
      }
    } catch (err) {
      // 409 冲突：拉最新状态合并进本地后，用最新 revision 重试一次（不静默覆盖）
      if (err instanceof ApiError && err.status === 409 && err.code === "REVISION_CONFLICT") {
        try {
          const fresh = await apiFetch<{
            project: {
              revision?: number;
              nodes: Array<{
                id: number;
                type?: string;
                title?: string;
                position?: { x: number; y: number };
                width?: number | null;
                height?: number | null;
                metadata?: Record<string, unknown> | null;
              }>;
            };
          }>(`/api/canvas/projects/${pid}`);
          if (activeProject.current !== pid) return;
          useCanvasStore.getState().mergeServerNodes(fresh.project.nodes);
          useCanvasStore.getState().setRevision(fresh.project.revision ?? 0);
          await putNodes(pid);
          if (activeProject.current !== pid) return;
          useCanvasStore.getState().clearDirty();
          retryCountRef.current = 0;
          setRetryCount(0);
          setStatus("saved");
          return;
        } catch {
          /* 合并重试失败 → 走常规退避 */
        }
      }
      retryCountRef.current += 1;
      const next = retryCountRef.current;
      setRetryCount(next);
      const delay = Math.min(BASE_RETRY_MS * 2 ** (next - 1), MAX_RETRY_MS);
      if (retryTimer.current) clearTimeout(retryTimer.current);
      retryTimer.current = setTimeout(() => void doSave(), delay);
      setStatus("error");
    }
  }, [putNodes]);

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

  // 切画布：取消挂起的防抖与重试（防把项目 A 的节点写进项目 B）
  useEffect(() => {
    if (activeProject.current === projectId) return;
    activeProject.current = projectId;
    if (debounceTimer.current) {
      clearTimeout(debounceTimer.current);
      debounceTimer.current = null;
    }
    if (retryTimer.current) {
      clearTimeout(retryTimer.current);
      retryTimer.current = null;
    }
    retryCountRef.current = 0;
    setRetryCount(0);
    setStatus("saved");
  }, [projectId]);

  // 卸载：清理挂起的定时器
  useEffect(
    () => () => {
      if (debounceTimer.current) clearTimeout(debounceTimer.current);
      if (retryTimer.current) clearTimeout(retryTimer.current);
    },
    [],
  );

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
