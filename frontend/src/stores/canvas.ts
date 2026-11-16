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

/** 后端节点 → RF 节点：metadata.client_id 优先作节点 id（无则回退数据库 id）。 */
function serverNodeToCanvas(n: ServerCanvasNode): CanvasNode {
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
  /** 从后端同步响应合并节点：服务端新增/服务端胜出的节点并入，本地未编辑节点
   * 采纳服务端最新内容（宽度/元数据），本地仍在编辑的节点保留本地值 */
  mergeServerNodes: (nodes: Array<ServerCanvasNode>) => void;
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
    set({ nodes, edges, viewport, loaded: true, dirty: false, revision: p.revision ?? null, undoStack: [], redoStack: [], canUndo: false, canRedo: false, historyDepth: 0 });
  },

  mergeServerNodes: (serverNodes) => {
    const incoming = serverNodes.map(serverNodeToCanvas);
    const byId = new Map(incoming.map((n) => [n.id, n]));
    const cur = get().nodes;
    // 本地节点保留（可能尚未保存）；服务端独有字段（outputUrl/尺寸/metadata）并入
    const merged = cur.map((local) => {
      const remote = byId.get(local.id);
      if (!remote) return local;
      return {
        ...local,
        data: {
          ...local.data,
          outputUrl: local.data.outputUrl ?? remote.data.outputUrl,
          width: local.data.width ?? remote.data.width,
          height: local.data.height ?? remote.data.height,
          metadata: { ...(remote.data.metadata ?? {}), ...(local.data.metadata ?? {}) },
        },
      };
    });
    // 服务端新增（Agent 回流等）：追加
    const localIds = new Set(cur.map((n) => n.id));
    for (const n of incoming) if (!localIds.has(n.id)) merged.push(n);
    set({ nodes: merged });
  },

  setRevision: (rev) => set({ revision: rev }),

  reset: () =>
    set({
      nodes: [],
      edges: [],
      loaded: false,
      dirty: false,
      revision: null,
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
    set({ nodes: applyNodeChanges(changes, get().nodes) as CanvasNode[] });
    get().markDirtyIfLoaded();
  },
  onEdgesChange: (changes) => {
    get().pushHistory();
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
