import type { Edge } from "@xyflow/react";
import type { CanvasNode } from "@/stores/canvas";

export interface BackendNodePayload {
  type: string;
  title: string;
  position_x: number;
  position_y: number;
  /** 节点尺寸（服务端 width/height）：缺省 null，服务端保留既有值 */
  width: number | null;
  height: number | null;
  metadata_json: string;
}

export interface BackendConnectionPayload {
  from_node_id: number;
  to_node_id: number;
}

/** 服务端返回的节点（client_id 可能在顶层，也可能在 metadata 里）。 */
export interface ServerNode {
  id: number;
  client_id?: string | null;
  metadata?: { client_id?: string | null } | null;
}

/** 只含 id 的节点（base 快照 / 本地节点都满足）。 */
export interface NodeIdLike {
  id: string;
}

/**
 * RF 节点 → 后端 bulk sync 字段。
 *
 * - metadata.client_id = 节点 id（前端 uuid / 服务端回传的 client_id）→ 服务端稳定 upsert
 * - width/height 一并上传（尺寸丢失修复）
 * - 服务端返回的未知 metadata 键（extraUrls/durationSec/_created_rev 等）原样带回，
 *   只覆盖前端自己维护的 prompt/outputUrl/client_id（合并而非丢弃）
 */
export function buildNodePayload(nodes: CanvasNode[]): BackendNodePayload[] {
  return nodes.map((n) => {
    const meta: Record<string, unknown> = { ...(n.data.metadata ?? {}) };
    const outputUrl =
      n.data.outputUrl ?? (typeof meta.outputUrl === "string" ? meta.outputUrl : null);
    const merged = {
      ...meta,
      prompt: n.data.prompt ?? "",
      outputUrl,
      client_id: n.id,
    };
    return {
      type: n.data.nodeType,
      title: n.data.title,
      position_x: n.position.x,
      position_y: n.position.y,
      width: n.data.width ?? null,
      height: n.data.height ?? null,
      metadata_json: JSON.stringify(merged),
    };
  });
}

/**
 * 收集"base 快照里有、本地已删掉"的节点 id（= client_id）。
 *
 * 服务端按此精确删除——含 Agent 建的节点（用户有权删自己画布上的任何节点）。
 * 只删"客户端本就知道"的节点：服务端在客户端加载之后新建的节点（base 里没有）
 * 不会被误删。
 */
export function collectDeletedClientIds(
  baseNodes: NodeIdLike[] | null | undefined,
  currentNodes: NodeIdLike[],
): string[] {
  if (!baseNodes || baseNodes.length === 0) return [];
  const present = new Set(currentNodes.map((n) => n.id));
  const out: string[] = [];
  for (const n of baseNodes) {
    if (!present.has(n.id) && !out.includes(n.id)) out.push(n.id);
  }
  return out;
}

/**
 * 连线翻译：服务端 sync_nodes 全量替换后会重排 id，但响应数组顺序与提交顺序一致。
 * 以 client_id → 后端 id 建映射，把 uuid 边翻译为后端 id 边；
 * 对不上（异常丢失）的边丢弃而非错连。
 */
export function mapEdgesByNodeOrder(edges: Edge[], serverNodes: ServerNode[]): BackendConnectionPayload[] {
  const byClientId = new Map<string, number>();
  for (const sn of serverNodes) {
    const cid = sn.client_id ?? sn.metadata?.client_id ?? null;
    if (cid) byClientId.set(cid, sn.id);
  }
  const out: BackendConnectionPayload[] = [];
  for (const e of edges) {
    const from = byClientId.get(e.source);
    const to = byClientId.get(e.target);
    if (from !== undefined && to !== undefined) {
      out.push({ from_node_id: from, to_node_id: to });
    }
  }
  return out;
}
