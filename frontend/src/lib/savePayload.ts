import type { Edge } from "@xyflow/react";
import type { CanvasNode } from "@/stores/canvas";

export interface BackendNodePayload {
  type: string;
  title: string;
  position_x: number;
  position_y: number;
  metadata_json: string;
}

export interface BackendConnectionPayload {
  from_node_id: number;
  to_node_id: number;
}

/** RF 节点 → 后端 bulk sync 字段；前端 uuid 存入 metadata_json.client_id 供保存后映射。 */
export function buildNodePayload(nodes: CanvasNode[]): BackendNodePayload[] {
  return nodes.map((n) => ({
    type: n.data.nodeType,
    title: n.data.title,
    position_x: n.position.x,
    position_y: n.position.y,
    metadata_json: JSON.stringify({
      prompt: n.data.prompt,
      outputUrl: n.data.outputUrl ?? null,
      client_id: n.id,
    }),
  }));
}

/**
 * 连线翻译：服务端 sync_nodes 全量替换后会重排 id，但响应数组顺序与提交顺序一致。
 * 以 client_id → 后端新 id 建映射，把 uuid 边翻译为后端 id 边；
 * 对不上（异常丢失）的边丢弃而非错连。
 */
export function mapEdgesByNodeOrder(
  edges: Edge[],
  serverNodes: Array<{ id: number; client_id?: string | null }>,
): BackendConnectionPayload[] {
  const byClientId = new Map<string, number>();
  for (const sn of serverNodes) {
    if (sn.client_id) byClientId.set(sn.client_id, sn.id);
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
