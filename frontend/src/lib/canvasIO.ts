import type { Edge, Node } from "@xyflow/react";

export interface CanvasExportFile {
  version: 1;
  app: "creagen-canvas";
  exported_at: string;
  project: { title: string; viewport: { x: number; y: number; zoom: number } };
  nodes: Array<{
    id: string;
    type: string;
    title: string;
    position: { x: number; y: number };
    prompt: string;
    outputUrl?: string;
  }>;
  connections: Array<{ source: string; target: string }>;
}

/** 从 RF 节点/边组装导出文件（排除运行时字段，保留创作内容）。 */
export function buildExport(
  title: string,
  viewport: { x: number; y: number; zoom: number },
  nodes: Node[],
  edges: Edge[],
): CanvasExportFile {
  return {
    version: 1,
    app: "creagen-canvas",
    exported_at: new Date().toISOString(),
    project: { title, viewport },
    nodes: nodes.map((n) => {
      const data = n.data as { nodeType?: string; title?: string; prompt?: string; outputUrl?: string };
      return {
        id: String(n.id),
        type: data.nodeType ?? "text",
        title: data.title ?? "",
        position: { x: n.position.x, y: n.position.y },
        prompt: data.prompt ?? "",
        ...(data.outputUrl ? { outputUrl: data.outputUrl } : {}),
      };
    }),
    connections: edges.map((e) => ({ source: String(e.source), target: String(e.target) })),
  };
}

/** 解析导入文件：校验 app 标识与结构，映射回 RF 节点/边。非法抛 Error。 */
export function parseImport(raw: string): {
  title: string;
  viewport: { x: number; y: number; zoom: number };
  nodes: Node[];
  edges: Edge[];
} {
  const data = JSON.parse(raw) as Partial<CanvasExportFile>;
  if (data.app !== "creagen-canvas" || data.version !== 1) {
    throw new Error("不是有效的 CreaGen Canvas 导出文件");
  }
  if (!Array.isArray(data.nodes) || !Array.isArray(data.connections)) {
    throw new Error("文件缺少 nodes/connections");
  }
  const nodes: Node[] = data.nodes.map((n) => ({
    id: n.id,
    type: "canvas",
    position: { x: n.position?.x ?? 0, y: n.position?.y ?? 0 },
    data: {
      nodeType: (n.type as "text" | "image" | "video" | "audio") ?? "text",
      title: n.title ?? "",
      prompt: n.prompt ?? "",
      ...(n.outputUrl ? { outputUrl: n.outputUrl } : {}),
    },
  }));
  const validIds = new Set(nodes.map((n) => n.id));
  const edges: Edge[] = data.connections
    .filter((c) => validIds.has(c.source) && validIds.has(c.target))
    .map((c, i) => ({ id: `imp-${i}`, source: c.source, target: c.target }));
  return {
    title: data.project?.title ?? "导入的画布",
    viewport: data.project?.viewport ?? { x: 0, y: 0, zoom: 1 },
    nodes,
    edges,
  };
}
