import type { Edge } from "@xyflow/react";
import type { CanvasNode } from "@/stores/canvas";

/**
 * 画布三方合并（base = 上次同步的服务端状态，local = 本地当前，server = 服务端最新）。
 *
 * 节点按 client_id（即 RF 节点 id）对齐，边按 source→target 对齐：
 * - 本地删除且服务端未改 → 保持删除（不复活用户删掉的节点）
 * - 本地删除但服务端已改 → 保留服务端版本并记冲突
 * - 服务端删除且本地未改 → 删除
 * - 服务端删除但本地已改 → 保留本地（重试保存会重建）并记冲突
 * - 仅一侧修改 → 取该侧；两侧都改 → 取本地并记冲突
 * - 任一侧新增 → 保留（Agent 回流节点等）
 * 指向合并结果中不存在节点的边一律丢弃。
 */

/** 冲突条目（非阻塞提示：保存管线 console.warn + store.conflicts）。 */
export interface MergeConflict {
  /** 节点 client_id */
  id: string;
  reason: "both-edited" | "local-deleted-server-edited" | "server-deleted-local-edited";
}

/**
 * 删除墓碑（C②）：用户本地删掉的节点/边 id，跨保存轮次保持删除意图。
 *
 * - 墓碑项绝不从服务端复活——即使服务端在 base 之后改过它（此时仅记冲突供日志，
 *   不再沿用 round-2「保留服务端版本」的规则）；
 * - 连到墓碑节点的边一并丢弃；
 * - 墓碑只在服务端确认删除（PUT 响应中缺席）或本地撤销删除（节点又出现）时清除
 *   （见 stores/canvas.ts activeTombstones / confirmServerDeletions）。
 */
export interface Tombstones {
  nodes: ReadonlySet<string>;
  edges: ReadonlySet<string>;
}

export const NO_TOMBSTONES: Tombstones = { nodes: new Set(), edges: new Set() };

/** 参与合并的一方状态。 */
export interface CanvasMergeState {
  nodes: CanvasNode[];
  edges: Edge[];
}

export interface ThreeWayMergeResult extends CanvasMergeState {
  conflicts: MergeConflict[];
}

/** 递归按 key 排序的稳定 JSON（键序无关的深比较）。 */
function canonical(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value) ?? "null";
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  const record = value as Record<string, unknown>;
  const keys = Object.keys(record).sort();
  return `{${keys.map((k) => `${JSON.stringify(k)}:${canonical(record[k])}`).join(",")}}`;
}

/** 两份状态是否等价（节点/边内容级比较，忽略顺序与选中态）。 */
export function sameCanvasState(a: CanvasMergeState, b: CanvasMergeState): boolean {
  if (a.nodes.length !== b.nodes.length || a.edges.length !== b.edges.length) return false;
  const nodeKey = (n: CanvasNode) => nodeSignature(n);
  const na = a.nodes.map(nodeKey).sort().join("|");
  const nb = b.nodes.map(nodeKey).sort().join("|");
  if (na !== nb) return false;
  const ekey = (e: Edge) => `${e.source}→${e.target}`;
  const ea = a.edges.map(ekey).sort().join("|");
  const eb = b.edges.map(ekey).sort().join("|");
  return ea === eb;
}

/**
 * 保存响应合并（base = 本次发送的快照，local = 当前本地，server = 响应里的服务端状态）。
 *
 * 与 threeWayMerge 的区别：**节点/边的存在性以本地为准**。
 * - 本地节点 → 保留（服务端同 id 节点只用于回填服务端写入的字段）；
 * - 本地已删的节点（请求在途期间删除）→ **不复活**（服务端回显不算“服务端改动”）；
 * - 服务端独有的新节点（Agent 回流等）→ 并入本地；
 * - 本地独有的节点（请求在途期间新增）→ 保留（下一轮保存上传）。
 * 服务端写入的字段（outputUrl/extraUrls/durationSec/尺寸）以响应为准回填。
 */
export function mergeSaveResponse(
  base: CanvasMergeState,
  local: CanvasMergeState,
  server: CanvasMergeState,
  tombstones: Tombstones = NO_TOMBSTONES,
): ThreeWayMergeResult {
  const conflicts: MergeConflict[] = [];
  const baseById = new Map(base.nodes.map((n) => [n.id, n]));
  const serverById = new Map(server.nodes.map((n) => [n.id, n]));
  const merged: CanvasNode[] = [];
  const present = new Set<string>();

  for (const ln of local.nodes) {
    const sn = serverById.get(ln.id);
    const bn = baseById.get(ln.id);
    if (!sn) {
      // 服务端没有（本地新增或服务端已删）→ 本地保留
      merged.push(ln);
      present.add(ln.id);
      continue;
    }
    const localChanged = !bn || nodeSignature(ln) !== nodeSignature(bn);
    const serverChanged = nodeSignature(sn) !== nodeSignature(bn ?? sn);
    if (localChanged && serverChanged) {
      conflicts.push({ id: ln.id, reason: "both-edited" });
    }
    // 保留本地内容（请求在途期间的编辑），并回填服务端写入的字段
    merged.push(enrichWithServer(ln, sn));
    present.add(ln.id);
  }

  // 服务端独有节点：本地没有的（Agent 回流等）→ 并入；本地删掉的 → 保持删除。
  // 墓碑项（用户明确删过，含在途删除）绝不因服务端回显复活。
  for (const sn of server.nodes) {
    if (present.has(sn.id)) continue;
    if (tombstones.nodes.has(sn.id)) continue; // 墓碑：不复活
    if (!baseById.has(sn.id)) {
      merged.push(sn);
      present.add(sn.id);
    }
  }

  // 边：本地为准 + 服务端新增（墓碑边不复活）
  const localEdgeKeys = new Set(local.edges.map(edgeKey));
  const mergedEdges: Edge[] = [...local.edges];
  for (const se of server.edges) {
    if (tombstones.edges.has(edgeKey(se))) continue;
    if (!localEdgeKeys.has(edgeKey(se))) mergedEdges.push(se);
  }
  const edges = mergedEdges.filter((e) => present.has(e.source) && present.has(e.target));

  return { nodes: merged, edges, conflicts };
}

/** 本地节点 + 服务端写入字段回填（服务端字段优先，客户端独有键保留）。 */
function enrichWithServer(local: CanvasNode, server: CanvasNode): CanvasNode {
  return {
    ...local,
    data: {
      ...local.data,
      outputUrl: server.data.outputUrl ?? local.data.outputUrl,
      width: server.data.width ?? local.data.width,
      height: server.data.height ?? local.data.height,
      metadata: { ...(local.data.metadata ?? {}), ...(server.data.metadata ?? {}) },
    },
  };
}

/**
 * 节点内容签名：type/title/position/width/height/metadata。
 * 忽略服务端托管的 metadata._created_rev；本地把 prompt/outputUrl 维护在 data 上、
 * 服务端放在 metadata 里——统一折叠进 metadata 视图后比较。
 */
export function nodeSignature(n: CanvasNode): string {
  const meta: Record<string, unknown> = { ...(n.data.metadata ?? {}) };
  delete meta._created_rev;
  meta.prompt = n.data.prompt ?? (typeof meta.prompt === "string" ? meta.prompt : "");
  meta.outputUrl = n.data.outputUrl ?? meta.outputUrl ?? null;
  meta.client_id = n.id;
  return canonical({
    type: n.data.nodeType,
    title: n.data.title,
    position: [n.position.x, n.position.y],
    width: n.data.width ?? null,
    height: n.data.height ?? null,
    metadata: meta,
  });
}

function edgeKey(e: { source: string; target: string }): string {
  return `${e.source}→${e.target}`;
}

/** 三方合并（纯函数）。local 顺序优先，服务端新增节点追加在尾部。 */
export function threeWayMerge(
  base: CanvasMergeState,
  local: CanvasMergeState,
  server: CanvasMergeState,
  tombstones: Tombstones = NO_TOMBSTONES,
): ThreeWayMergeResult {
  const conflicts: MergeConflict[] = [];
  const baseById = new Map(base.nodes.map((n) => [n.id, n]));
  const serverById = new Map(server.nodes.map((n) => [n.id, n]));
  const merged: CanvasNode[] = [];
  const present = new Set<string>();

  // 1) 本地节点：占住 local 顺序中的位置
  for (const ln of local.nodes) {
    const bn = baseById.get(ln.id);
    const sn = serverById.get(ln.id);
    const localChanged = !bn || nodeSignature(ln) !== nodeSignature(bn);
    if (sn === undefined) {
      if (!bn) {
        merged.push(ln); // 本地新增
        present.add(ln.id);
      } else if (localChanged) {
        // 服务端已删 + 本地已改 → 保留本地（重试将重建）
        merged.push(ln);
        present.add(ln.id);
        conflicts.push({ id: ln.id, reason: "server-deleted-local-edited" });
      }
      // 服务端已删 + 本地未改 → 采纳删除（不 push）
    } else if (!bn) {
      // base 未知：本地新增（服务端也建了同 id → 双方新增，本地优先）
      merged.push(ln);
      present.add(ln.id);
      if (nodeSignature(ln) !== nodeSignature(sn)) {
        conflicts.push({ id: ln.id, reason: "both-edited" });
      }
    } else {
      const serverChanged = nodeSignature(sn) !== nodeSignature(bn);
      if (serverChanged && localChanged) {
        merged.push(ln); // 双方都改 → 本地胜出
        present.add(ln.id);
        conflicts.push({ id: ln.id, reason: "both-edited" });
      } else if (serverChanged) {
        merged.push(sn); // 仅服务端改 → 服务端胜出
        present.add(ln.id);
      } else {
        merged.push(ln); // 仅本地改 / 都没改 → 本地
        present.add(ln.id);
      }
    }
  }

  // 2) 服务端独有节点
  for (const sn of server.nodes) {
    if (present.has(sn.id)) continue;
    const bn = baseById.get(sn.id);
    if (tombstones.nodes.has(sn.id)) {
      // 墓碑（C②）：用户删过 → 绝不复活；服务端还改过它的话仅记冲突供日志
      if (bn && nodeSignature(sn) !== nodeSignature(bn)) {
        conflicts.push({ id: sn.id, reason: "local-deleted-server-edited" });
      }
      continue;
    }
    if (!bn) {
      merged.push(sn); // 服务端新增（Agent 回流等）
      present.add(sn.id);
    } else if (nodeSignature(sn) !== nodeSignature(bn)) {
      // 本地已删 + 服务端已改 → 保留服务端版本，记冲突
      merged.push(sn);
      present.add(sn.id);
      conflicts.push({ id: sn.id, reason: "local-deleted-server-edited" });
    }
    // 本地已删 + 服务端未改 → 保持删除（不复活）
  }

  // 3) 边（presence 语义：base 有/无 × local 有/无 × server 有/无）
  const baseEdgeKeys = new Set(base.edges.map(edgeKey));
  const localEdges = new Map(local.edges.map((e) => [edgeKey(e), e]));
  const serverEdges = new Map(server.edges.map((e) => [edgeKey(e), e]));
  const mergedEdges: Edge[] = [];
  for (const [key, e] of localEdges) {
    if (baseEdgeKeys.has(key) && !serverEdges.has(key)) continue; // 服务端已删且本地未动
    mergedEdges.push(e);
  }
  for (const [key, e] of serverEdges) {
    if (localEdges.has(key)) continue; // 已并入
    if (tombstones.edges.has(key)) continue; // 墓碑边：用户删过 → 不复活
    if (baseEdgeKeys.has(key)) continue; // 本地已删 → 不复活
    mergedEdges.push(e); // 服务端新增边
  }

  // 4) 指向已不存在节点的边丢弃
  const edges = mergedEdges.filter((e) => present.has(e.source) && present.has(e.target));

  return { nodes: merged, edges, conflicts };
}
