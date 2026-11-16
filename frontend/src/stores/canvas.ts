import { uuid } from "@/lib/uuid";
import { create } from "zustand";
import {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type Edge,
  type Node,
  type OnConnect,
  type OnEdgesChange,
  type OnNodesChange,
} from "@xyflow/react";
import { toFlowViewport } from "@/lib/viewport";
import {
  mergeSaveResponse,
  sameCanvasState,
  threeWayMerge,
  type CanvasMergeState,
  type MergeConflict,
} from "@/lib/canvasMerge";

/** 边删除墓碑键（与 canvasMerge.edgeKey 同构：source→target）。 */
export function edgeTombstoneKey(e: { source: string; target: string }): string {
  return `${e.source}→${e.target}`;
}

const HISTORY_LIMIT = 50;

interface HistoryEntry {
  nodes: CanvasNode[];
  edges: Edge[];
}

export type CanvasNodeType = "text" | "image" | "video" | "audio";
export const CANVAS_NODE_TYPES: CanvasNodeType[] = ["text", "image", "video", "audio"];

export interface CanvasNodeData extends Record<string, unknown> {
  nodeType: CanvasNodeType;
  title: string;
  prompt: string;
  /** 生成回流结果（image 真实 URL / llm 完整文本已写入 prompt） */
  outputUrl?: string;
  /** 节点尺寸（后端 width/height；保存时原样回传） */
  width?: number;
  height?: number;
  /** 服务端回传的完整 metadata（保存时合并回传，未知键不丢） */
  metadata?: Record<string, unknown>;
}

export interface CanvasNode extends Node<CanvasNodeData> {}

/** 后端同步响应里的节点形状（宽/高/client_id 在顶层字段）。 */
export interface ServerCanvasNode {
  id: number;
  client_id?: string | null;
  type?: string;
  title?: string;
  position?: { x: number; y: number };
  width?: number | null;
  height?: number | null;
  metadata?: Record<string, unknown> | null;
}

/** 后端连线（端点是数据库节点 id）。 */
export interface ServerCanvasConnection {
  from_node_id: number;
  to_node_id: number;
}

/** 后端节点 → RF 节点：metadata.client_id 优先作节点 id（无则回退数据库 id）。 */
export function serverNodeToCanvas(n: ServerCanvasNode): CanvasNode {
  const meta = (n.metadata ?? {}) as Record<string, unknown>;
  const cid = typeof meta.client_id === "string" && meta.client_id ? meta.client_id : String(n.id);
  return {
    id: cid,
    type: "canvas",
    position: n.position ?? { x: 0, y: 0 },
    data: {
      nodeType: (n.type as CanvasNodeType) ?? "text",
      title: n.title ?? "",
      prompt: typeof meta.prompt === "string" ? meta.prompt : "",
      outputUrl: typeof meta.outputUrl === "string" ? meta.outputUrl : undefined,
      width: n.width ?? undefined,
      height: n.height ?? undefined,
      metadata: meta,
    },
  };
}

/** 冲突列表合并（按 id+reason 去重）。 */
function mergeConflicts(a: MergeConflict[], b: MergeConflict[]): MergeConflict[] {
  const out = [...a];
  for (const c of b) {
    if (!out.some((x) => x.id === c.id && x.reason === c.reason)) out.push(c);
  }
  return out;
}

/** 后端节点数组 → 三方合并用的状态（节点用 client_id，边端点翻译成 RF id）。 */
function serverStateToMergeState(
  serverNodes: ServerCanvasNode[],
  serverConnections: ServerCanvasConnection[],
): CanvasMergeState {
  const nodes = serverNodes.map(serverNodeToCanvas);
  const idByDbId = new Map<number, string>();
  nodes.forEach((n, i) => idByDbId.set(serverNodes[i].id, n.id));
  const edges: Edge[] = serverConnections.map((c) => {
    const source = idByDbId.get(c.from_node_id) ?? String(c.from_node_id);
    const target = idByDbId.get(c.to_node_id) ?? String(c.to_node_id);
    return { id: `e-${source}-${target}`, source, target };
  });
  return { nodes, edges };
}

interface CanvasState {
  nodes: CanvasNode[];
  edges: Edge[];
  loaded: boolean;
  dirty: boolean;
  /** React Flow 视口（保存到 project.viewport） */
  viewport: { x: number; y: number; zoom: number };
  undoStack: HistoryEntry[];
  redoStack: HistoryEntry[];
  canUndo: boolean;
  canRedo: boolean;
  historyDepth: number;
  undo: () => void;
  redo: () => void;
  pushHistory: () => void;
  /** 从后端 project 响应整体装载（nodes/connections 全量替换语义） */
  loadFromApi: (resp: unknown) => void;
  /** 保存响应落盘：base=发送快照 × local=当前 × server=响应（存在性以本地为准），
   *  返回本地是否在请求在途期间发生过改动（删除/编辑/新增 → 需再保存一轮）。 */
  applyServerStateWithBase: (
    mergeBase: CanvasMergeState | null,
    serverNodes: ServerCanvasNode[],
    revision: number,
  ) => boolean;
  /** 409 携带的服务端状态落盘（以当前 base 快照为基准三方合并）。 */
  applyServerState: (
    serverNodes: ServerCanvasNode[],
    serverConnections: ServerCanvasConnection[],
    revision: number,
  ) => void;
  /** 上次同步的服务端状态（三方合并的 base 快照）：loadFromApi / 保存成功后更新 */
  base: { nodes: CanvasNode[]; edges: Edge[] } | null;
  /** 409 三方合并后的落盘结果（保留选中态），并更新 base/revision */
  applyMergedState: (merged: {
    nodes: CanvasNode[];
    edges: Edge[];
    revision: number;
    conflicts: MergeConflict[];
  }) => void;
  /** 三方合并产生的冲突提示（非阻塞；下次成功保存后清空） */
  conflicts: MergeConflict[];
  clearConflicts: () => void;
  /**
   * 删除墓碑（C②）：用户本地删掉的节点/边 client_id 集合（含在途删除）。
   * 墓碑随每次保存作为 deleted_client_ids 发送；只有服务端确认删除（PUT 响应
   * 中缺席）或本地撤销删除时才清除。切项目/loadFromApi 重置。
   */
  tombstones: { nodes: string[]; edges: string[] };
  /** 本地删除节点（含批量）→ 记墓碑；undo 恢复或重新出现 → 撤销墓碑 */
  tombstoneNodes: (ids: string[]) => void;
  tombstoneEdges: (keys: string[]) => void;
  /** 保存响应到达：缺席即确认删除 → 清除对应墓碑。 */
  confirmServerDeletions: (serverNodes: ServerCanvasNode[]) => void;
  /** 三方合并 / 响应合并前调用：墓碑与当前本地状态对齐（本地撤销删除的撤墓碑）。 */
  activeTombstones: () => { nodes: ReadonlySet<string>; edges: ReadonlySet<string> };
  /** 记录保存成功的 base_revision（乐观锁：下次保存随请求携带） */
  revision: number | null;
  setRevision: (rev: number) => void;
  reset: () => void;
  setLoaded: (v: boolean) => void;
  markDirtyIfLoaded: () => void;
  clearDirty: () => void;
  setViewport: (vp: { x: number; y: number; zoom: number }) => void;
  onNodesChange: OnNodesChange;
  onEdgesChange: OnEdgesChange;
  onConnect: OnConnect;
  setNodes: (nodes: CanvasNode[]) => void;
  setEdges: (edges: Edge[]) => void;
  /** 新增指定类型节点（position 由调用方经 screenToFlowPosition 换算） */
  addNode: (type: CanvasNodeType, position: { x: number; y: number }) => string;
  /** 拓扑分层自动整理（对标 BeefTV 节点自动整理） */
  autoArrange: () => void;
  updateNodeData: (id: string, patch: Partial<CanvasNodeData>) => void;
}

export const useCanvasStore = create<CanvasState>()((set, get) => ({
  nodes: [],
  edges: [],
  loaded: false,
  dirty: false,
  revision: null,
  base: null,
  conflicts: [],
  tombstones: { nodes: [], edges: [] },
  viewport: { x: 0, y: 0, zoom: 1 },
  undoStack: [],
  redoStack: [],
  canUndo: false,
  canRedo: false,
  historyDepth: 0,

  pushHistory: () => {
    const { nodes, edges, undoStack } = get();
    const next = [...undoStack, { nodes, edges }].slice(-HISTORY_LIMIT);
    set({ undoStack: next, redoStack: [], canUndo: true, canRedo: false, historyDepth: next.length });
  },

  undo: () => {
    const { undoStack, nodes, edges } = get();
    if (undoStack.length === 0) return;
    const prev = undoStack[undoStack.length - 1];
    const nextUndo = undoStack.slice(0, -1);
    set({
      nodes: prev.nodes,
      edges: prev.edges,
      undoStack: nextUndo,
      redoStack: [...get().redoStack, { nodes, edges }],
      canUndo: nextUndo.length > 0,
      canRedo: true,
      dirty: true,
    });
  },

  redo: () => {
    const { redoStack, nodes, edges } = get();
    if (redoStack.length === 0) return;
    const nextEntry = redoStack[redoStack.length - 1];
    const nextRedo = redoStack.slice(0, -1);
    set({
      nodes: nextEntry.nodes,
      edges: nextEntry.edges,
      redoStack: nextRedo,
      undoStack: [...get().undoStack, { nodes, edges }],
      canUndo: true,
      canRedo: nextRedo.length > 0,
      dirty: true,
    });
  },

  loadFromApi: (resp) => {
    const data = resp as {
      project: {
        revision?: number;
        viewport?: string;
        nodes?: Array<ServerCanvasNode & { position: { x: number; y: number } }>;
        connections?: Array<{ id: number; from_node_id: number; to_node_id: number }>;
      };
    };
    const p = data.project;
    const nodes: CanvasNode[] = (p.nodes ?? []).map(serverNodeToCanvas);
    // 连线端点是数据库 id：翻译成 RF 节点 id（client_id）
    const idByDbId = new Map<number, string>();
    (p.nodes ?? []).forEach((n) => idByDbId.set(n.id, serverNodeToCanvas(n).id));
    const edges: Edge[] = (p.connections ?? []).map((c) => {
      const source = idByDbId.get(c.from_node_id) ?? String(c.from_node_id);
      const target = idByDbId.get(c.to_node_id) ?? String(c.to_node_id);
      return { id: `e-${source}-${target}`, source, target };
    });
    let viewport = { x: 0, y: 0, zoom: 1 };
    try {
      viewport = toFlowViewport(JSON.parse(p.viewport ?? "{}"));
    } catch {
      /* 缺省视口 */
    }
    set({
      nodes,
      edges,
      viewport,
      loaded: true,
      dirty: false,
      revision: p.revision ?? null,
      base: { nodes: nodes.map((n) => structuredClone(n)), edges: edges.map((e) => ({ ...e })) },
      conflicts: [],
      tombstones: { nodes: [], edges: [] },
      undoStack: [],
      redoStack: [],
      canUndo: false,
      canRedo: false,
      historyDepth: 0,
    });
  },

  /** 409 冲突：当前 base 快照 × 本地 × 服务端状态 → 三方合并落盘（冲突提示保留）。 */
  applyServerState: (serverNodes, serverConnections, revision) => {
    const state = get();
    const base: CanvasMergeState = state.base ?? { nodes: [], edges: [] };
    const server = serverStateToMergeState(serverNodes, serverConnections);
    const merged = threeWayMerge(
      base,
      { nodes: state.nodes, edges: state.edges },
      server,
      state.activeTombstones(),
    );
    get().applyMergedState({
      nodes: merged.nodes,
      edges: merged.edges,
      revision,
      conflicts: mergeConflicts(state.conflicts, merged.conflicts),
    });
    // base = 服务端当前状态（重试保存的 deleted_client_ids 基准）：本地已删但
    // 服务端仍有的节点（含 _created_rev 较新的 Agent 节点）→ 重试时显式删除
    set({
      base: {
        nodes: server.nodes.map((n) => structuredClone(n)),
        edges: server.edges.map((e) => ({ ...e })),
      },
    });
    if (merged.conflicts.length > 0) {
      console.warn(
        "[canvas] 保存冲突已按本地版本合并：",
        merged.conflicts.map((c) => `${c.id} (${c.reason})`).join(", "),
      );
    }
  },

  /** 保存响应落盘：base=发送快照 × local=当前 × server=响应（存在性以本地为准）。 */
  applyServerStateWithBase: (mergeBase, serverNodes, revision) => {
    const state = get();
    const base: CanvasMergeState = mergeBase ?? { nodes: [], edges: [] };
    const server = serverStateToMergeState(serverNodes, []);
    // 在途改动检测：本地是否在请求发出后发生过变化（删除/编辑/新增）
    const changedDuringFlight = !sameCanvasState(
      { nodes: state.nodes, edges: state.edges },
      base,
    );
    const merged = mergeSaveResponse(
      base,
      { nodes: state.nodes, edges: state.edges },
      server,
      state.activeTombstones(),
    );
    // 保留选中态：与当前重合的 id 继承当前选中标记
    const curSelected = new Set(state.nodes.filter((n) => n.selected).map((n) => n.id));
    const withSelection = merged.nodes.map((n) =>
      curSelected.has(n.id) && !n.selected ? { ...n, selected: true } : n,
    );
    set({
      nodes: withSelection,
      edges: merged.edges,
      revision,
      // base = 服务端最新状态（响应）：下一轮保存的 deleted_client_ids 以它为基准
      // （在途期间删掉的节点 → 显式删除，服务端精确删）
      base: {
        nodes: server.nodes.map((n) => structuredClone(n)),
        edges: merged.edges.map((e) => ({ ...e })),
      },
      // 冲突提示累加（409 合并阶段记录的冲突不被保存响应抹掉）
      conflicts: mergeConflicts(state.conflicts, merged.conflicts),
    });
    // 墓碑维护：响应里缺席的节点 = 服务端已删除 → 清除对应墓碑
    get().confirmServerDeletions(serverNodes);
    return changedDuringFlight;
  },

  applyMergedState: ({ nodes, edges, revision, conflicts }) => {
    // 保留选中态：与当前重合的 id 继承当前选中标记
    const curSelected = new Set(get().nodes.filter((n) => n.selected).map((n) => n.id));
    const withSelection = nodes.map((n) =>
      curSelected.has(n.id) && !n.selected ? { ...n, selected: true } : n,
    );
    set({
      nodes: withSelection,
      edges,
      revision,
      base: {
        nodes: nodes.map((n) => structuredClone(n)),
        edges: edges.map((e) => ({ ...e })),
      },
      conflicts,
    });
  },

  clearConflicts: () => set({ conflicts: [] }),

  tombstoneNodes: (ids) => {
    if (ids.length === 0) return;
    const cur = new Set(get().tombstones.nodes);
    for (const id of ids) cur.add(id);
    set({ tombstones: { nodes: [...cur], edges: get().tombstones.edges } });
  },

  tombstoneEdges: (keys) => {
    if (keys.length === 0) return;
    const cur = new Set(get().tombstones.edges);
    for (const k of keys) cur.add(k);
    set({ tombstones: { nodes: get().tombstones.nodes, edges: [...cur] } });
  },

  /** 服务端确认删除：响应中缺席的节点墓碑清除（已删完，无需再发）；
   *  仍回显的（删除未生效）保留墓碑，下一轮继续删。 */
  confirmServerDeletions: (serverNodes) => {
    const t = get().tombstones;
    if (t.nodes.length === 0) return;
    const alive = new Set(
      serverNodes.map((n) => {
        const meta = (n.metadata ?? {}) as Record<string, unknown>;
        return typeof meta.client_id === "string" && meta.client_id ? meta.client_id : String(n.id);
      }),
    );
    const kept = t.nodes.filter((id) => alive.has(id));
    if (kept.length !== t.nodes.length) {
      set({ tombstones: { nodes: kept, edges: t.edges } });
    }
  },

  /** 合并前墓碑对齐：本地又出现的节点/边（undo 恢复等）→ 撤销墓碑。 */
  activeTombstones: () => {
    const { nodes, edges, tombstones } = get();
    const presentNodes = new Set(nodes.map((n) => n.id));
    const presentEdges = new Set(edges.map(edgeTombstoneKey));
    return {
      nodes: new Set(tombstones.nodes.filter((id) => !presentNodes.has(id))),
      edges: new Set(tombstones.edges.filter((k) => !presentEdges.has(k))),
    };
  },

  setRevision: (rev) => set({ revision: rev }),

  reset: () =>
    set({
      nodes: [],
      edges: [],
      loaded: false,
      dirty: false,
      revision: null,
      base: null,
      conflicts: [],
      tombstones: { nodes: [], edges: [] },
      viewport: { x: 0, y: 0, zoom: 1 },
      undoStack: [],
      redoStack: [],
      canUndo: false,
      canRedo: false,
      historyDepth: 0,
    }),
  setLoaded: (v) => set({ loaded: v }),
  markDirtyIfLoaded: () => {
    if (get().loaded) set({ dirty: true });
  },
  clearDirty: () => set({ dirty: false }),
  setViewport: (vp) => set({ viewport: vp }),

  onNodesChange: (changes) => {
    get().pushHistory();
    // 删除墓碑（C②）：remove 变更（Delete/Backspace、批量删除）记墓碑；
    // undo 恢复（add 回来的同 id 节点）在 activeTombstones 对齐时自动撤销
    const removed = changes
      .filter((c): c is { type: "remove"; id: string } => c.type === "remove")
      .map((c) => c.id);
    if (removed.length > 0) get().tombstoneNodes(removed);
    set({ nodes: applyNodeChanges(changes, get().nodes) as CanvasNode[] });
    get().markDirtyIfLoaded();
  },
  onEdgesChange: (changes) => {
    get().pushHistory();
    // 删除墓碑（C②）：remove 变更按当前状态里的边换算成 source→target 键
    const removedIds = changes
      .filter((c): c is { type: "remove"; id: string } => c.type === "remove")
      .map((c) => c.id);
    if (removedIds.length > 0) {
      const keyById = new Map(get().edges.map((e) => [e.id, edgeTombstoneKey(e)]));
      get().tombstoneEdges(
        removedIds.map((id) => keyById.get(id)).filter((k): k is string => k !== undefined),
      );
    }
    set({ edges: applyEdgeChanges(changes, get().edges) });
    get().markDirtyIfLoaded();
  },
  onConnect: (connection: Connection) => {
    get().pushHistory();
    set({ edges: addEdge({ ...connection, id: uuid() }, get().edges) });
    get().markDirtyIfLoaded();
  },
  setNodes: (nodes) => set({ nodes }),
  setEdges: (edges) => set({ edges }),
  autoArrange: () => {
    const { nodes, edges } = get();
    if (nodes.length === 0) return;
    get().pushHistory();

    // BFS 分层：入度 0 为第 0 层，target 层 = max(source 层 + 1)
    const layerOf = new Map<string, number>();
    const incoming = new Map<string, number>();
    for (const e of edges) incoming.set(e.target, (incoming.get(e.target) ?? 0) + 1);
    const queue = nodes.filter((n) => !incoming.has(n.id)).map((n) => n.id);
    for (const id of queue) layerOf.set(id, 0);
    let guard = 0;
    while (queue.length > 0 && guard < nodes.length * 4) {
      guard++;
      const id = queue.shift()!;
      const layer = layerOf.get(id) ?? 0;
      for (const e of edges.filter((x) => x.source === id)) {
        const nextLayer = layer + 1;
        const cur = layerOf.get(e.target);
        if (cur === undefined || cur < nextLayer) {
          layerOf.set(e.target, nextLayer);
          queue.push(e.target);
        }
      }
    }

    // 无入度兜底（环/孤立层）
    for (const n of nodes) {
      if (!layerOf.has(n.id)) layerOf.set(n.id, 0);
    }

    // 层内纵向排布
    const COL_GAP = 360;
    const ROW_GAP = 240;
    const byLayer = new Map<number, string[]>();
    for (const n of nodes) {
      const layer = layerOf.get(n.id) ?? 0;
      byLayer.set(layer, [...(byLayer.get(layer) ?? []), n.id]);
    }
    const posById = new Map<string, { x: number; y: number }>();
    for (const [layer, ids] of [...byLayer.entries()].sort((a, b) => a[0] - b[0])) {
      ids.forEach((id, i) => posById.set(id, { x: layer * COL_GAP, y: i * ROW_GAP }));
    }
    set({
      nodes: nodes.map((n) => ({ ...n, position: posById.get(n.id) ?? n.position })),
    });
    get().markDirtyIfLoaded();
  },
  addNode: (type, position) => {
    get().pushHistory();
    const id = uuid();
    set({
      nodes: [
        ...get().nodes,
        { id, type: "canvas", position, data: { nodeType: type, title: "", prompt: "" } },
      ],
    });
    get().markDirtyIfLoaded();
    return id;
  },
  updateNodeData: (id, patch) => {
    get().pushHistory();
    const nodes = get().nodes;
    const idx = nodes.findIndex((n) => n.id === id);
    if (idx === -1) return;
    const next = [...nodes];
    next[idx] = { ...next[idx], data: { ...next[idx].data, ...patch } };
    set({ nodes: next });
    get().markDirtyIfLoaded();
  },
}));
