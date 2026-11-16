import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch, ApiError } from "@/lib/api";
import { buildNodePayload, collectDeletedClientIds, mapEdgesByNodeOrder } from "@/lib/savePayload";
import { type CanvasMergeState } from "@/lib/canvasMerge";
import { useCanvasStore, type ServerCanvasConnection, type ServerCanvasNode } from "@/stores/canvas";

export type SaveStatus = "saved" | "saving" | "dirty" | "error";

const DEBOUNCE_MS = 1200;
const BASE_RETRY_MS = 5000;
const MAX_RETRY_MS = 30000;
/** 响应合并后发现仍有在途改动时，立即续存的最大轮数（防服务端反复改写导致死循环） */
const MAX_FOLLOW_UP_SAVES = 3;

interface NodesResponse {
  nodes: Array<{ id: number; client_id?: string | null }>;
  revision: number;
}

/** 409 detail 里的服务端全量状态（与成功响应同形状）。 */
interface ConflictState {
  current_revision: number;
  nodes: ServerCanvasNode[];
  connections: ServerCanvasConnection[];
}

/** 读取 ApiError 携带的 409 服务端状态（detail 透传在 cause 上）。 */
function conflictStateOf(err: unknown): ConflictState | null {
  const cause = (err as { cause?: unknown } | null)?.cause;
  if (typeof cause !== "object" || cause === null) return null;
  const c = cause as Partial<ConflictState>;
  if (typeof c.current_revision !== "number" || !Array.isArray(c.nodes)) return null;
  return {
    current_revision: c.current_revision,
    nodes: c.nodes as ServerCanvasNode[],
    connections: Array.isArray(c.connections) ? (c.connections as ConflictState["connections"]) : [],
  };
}

/**
 * 画布保存管线：loaded 守卫 → 防抖 1.2s → PUT nodes（携带 base_revision 乐观锁
 * 与 deleted_client_ids 显式删除）→ 三方合并响应 → PUT connections。
 *
 * 在途改动不丢：PUT 前快照当时发送的状态（sentSnapshot）；响应到达时以
 * base=sentSnapshot、local=当前本地、server=响应 做 threeWayMerge——请求飞行期间
 * 用户做的删除/编辑/新增全部保留，不会被响应回滚；若合并结果 ≠ 服务端状态
 * （仍有未上传的本地改动）则重新置脏并再保存一轮（下一轮的 deleted_client_ids
 * 会带上飞行期间删掉的节点）。
 * 409 REVISION_CONFLICT：用 detail 携带的服务端状态做同样的三方合并，随后用
 * current_revision 重试一次（免额外 GET，不静默覆盖他端改动）。
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
  /** 连续"在途改动续存"轮数（超过则交还防抖/退避，避免打爆服务端） */
  const followUpsRef = useRef(0);
  /** 当前生效的项目：切项目后旧的重试/在途保存不得再写 */
  const activeProject = useRef<string | undefined>(projectId);

  /** 防抖排程下一轮保存（复用同一个 timer 槽，新排程覆盖旧的）。 */
  const scheduleDebounce = useCallback(() => {
    if (debounceTimer.current) clearTimeout(debounceTimer.current);
    debounceTimer.current = setTimeout(() => void doSaveRef.current(), DEBOUNCE_MS);
  }, []);

  /**
   * PUT nodes（带乐观锁 + 显式删除）→ 三方合并响应（保住在途本地改动）→
   * PUT connections。返回是否仍有未上传的本地改动（需要再保存一轮）。
   */
  const putNodes = useCallback(async (pid: string): Promise<boolean> => {
    const store = useCanvasStore.getState();
    // 发送快照：响应合并的 base（服务端状态正是这份快照落库后的结果）
    const sentSnapshot: CanvasMergeState = {
      nodes: store.nodes.map((n) => structuredClone(n)),
      edges: store.edges.map((e) => ({ ...e })),
    };
    // 显式删除（C② 墓碑）：用户删掉的节点（含在途删除）+ base 快照里有而本地已无的
    // 节点（兜底，防墓碑遗漏）——服务端按 client_id 精确删除，Agent 建的节点同样可删
    const deletedClientIds = [
      ...new Set([
        ...store.activeTombstones().nodes,
        ...collectDeletedClientIds(store.base?.nodes ?? null, store.nodes),
      ]),
    ];
    const resp = await apiFetch<NodesResponse>(`/api/canvas/projects/${pid}/nodes`, {
      method: "PUT",
      json: {
        nodes: buildNodePayload(sentSnapshot.nodes),
        base_revision: store.revision,
        deleted_client_ids: deletedClientIds,
      },
    });
    if (activeProject.current !== pid) return false;
    // 响应落盘：base=发送快照 × local=当前 × server=响应 → 飞行期间的
    // 删除/编辑/新增保留；返回是否仍有未上传的本地改动
    const pendingLocal = useCanvasStore
      .getState()
      .applyServerStateWithBase(sentSnapshot, resp.nodes, resp.revision);
    const connPayload = mapEdgesByNodeOrder(useCanvasStore.getState().edges, resp.nodes);
    await apiFetch(`/api/canvas/projects/${pid}/connections`, {
      method: "PUT",
      json: { connections: connPayload },
    });
    return pendingLocal && activeProject.current === pid;
  }, []);

  /** 409 冲突：base 快照 × 本地 × 409 携带的服务端状态 → 三方合并落盘。 */
  const mergeFromConflict = useCallback((conflict: ConflictState): void => {
    useCanvasStore
      .getState()
      .applyServerState(conflict.nodes, conflict.connections, conflict.current_revision);
  }, []);

  const doSave = useCallback(async () => {
    const store = useCanvasStore.getState();
    const pid = activeProject.current;
    if (!store.loaded || pid === undefined) return;
    setStatus("saving");
    // 新保存周期开始：清掉上一周期的冲突提示（本周期若再冲突会重新记录）
    useCanvasStore.getState().clearConflicts();
    /** 失败退避重试（指数退避 5s→30s 封顶）。 */
    const scheduleRetry = () => {
      retryCountRef.current += 1;
      const next = retryCountRef.current;
      setRetryCount(next);
      const delay = Math.min(BASE_RETRY_MS * 2 ** (next - 1), MAX_RETRY_MS);
      if (retryTimer.current) clearTimeout(retryTimer.current);
      retryTimer.current = setTimeout(() => void doSave(), delay);
      setStatus("error");
    };
    let pendingLocal = false;
    try {
      pendingLocal = await putNodes(pid);
    } catch (err) {
      // 409 冲突：三方合并（base × 本地 × 409 携带的服务端状态）后用最新 revision 重试一次
      const conflict =
        err instanceof ApiError && err.status === 409 && err.code === "REVISION_CONFLICT"
          ? conflictStateOf(err)
          : null;
      if (conflict === null) {
        if (activeProject.current !== pid) return;
        scheduleRetry();
        return;
      }
      try {
        if (activeProject.current !== pid) return;
        mergeFromConflict(conflict);
        pendingLocal = await putNodes(pid);
      } catch {
        if (activeProject.current !== pid) return;
        scheduleRetry();
        return;
      }
    }
    if (activeProject.current !== pid) return; // 保存期间切了项目：不写回状态
    if (pendingLocal) {
      // 在途改动（飞行期间的删除/编辑）尚未上传 → 保持脏并再保存一轮
      // （下一轮 deleted_client_ids 会带上飞行期间删掉的节点）
      if (followUpsRef.current < MAX_FOLLOW_UP_SAVES) {
        followUpsRef.current += 1;
        useCanvasStore.getState().markDirtyIfLoaded();
        setStatus("dirty");
        scheduleDebounce();
        return;
      }
      // 续存轮数用尽：保留脏标记，等用户下一次编辑触发防抖（不再自动轰炸）
      setStatus("error");
      return;
    }
    followUpsRef.current = 0;
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
  }, [putNodes, mergeFromConflict, scheduleDebounce]);

  const doSaveRef = useRef(doSave);
  doSaveRef.current = doSave;

  // dirty 变化触发防抖保存（doSaveRef 持最新回调，不进依赖数组避免触发器重排）
  useEffect(() => {
    if (!dirty) return;
    if (debounceTimer.current) clearTimeout(debounceTimer.current);
    setStatus("saving");
    debounceTimer.current = setTimeout(() => void doSaveRef.current(), DEBOUNCE_MS);
    return () => {
      if (debounceTimer.current) clearTimeout(debounceTimer.current);
    };
  }, [dirty, nodes, edges]);

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
    followUpsRef.current = 0;
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
