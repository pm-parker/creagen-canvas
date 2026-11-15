import type { CanvasNode } from "@/stores/canvas";

export interface GenerationBody {
  type: string;
  prompt: string;
  model: string;
  params: Record<string, unknown>;
}

/** 提交可行性：prompt 非空。 */
export function canSubmit(node: CanvasNode): boolean {
  return node.data.prompt.trim().length > 0;
}

buildGenerationParams.canSubmit = canSubmit;

/**
 * 组装生成提交 body。
 * 上游（连入的参考节点）中带 outputUrl 的图片按顺序作为 reference_urls。
 */
export function buildGenerationParams(
  node: CanvasNode,
  model: string,
  upstream: CanvasNode[],
  excludedSourceIds: string[] = [],
): GenerationBody {
  const referenceUrls = upstream
    .filter((n) => !excludedSourceIds.includes(n.id))
    .filter((n) => n.data.nodeType === "image" && n.data.outputUrl)
    .map((n) => n.data.outputUrl as string);

  const params: Record<string, unknown> = {};
  if (node.data.nodeType === "video") params.duration = 5;
  if (node.data.nodeType === "image") params.image_count = 1;
  if (referenceUrls.length > 0) params.reference_urls = referenceUrls;

  // 文字节点生成 = LLM 域（后端四域：image|video|llm|audio）
  const domain = node.data.nodeType === "text" ? "llm" : node.data.nodeType;

  return { type: domain, prompt: node.data.prompt.trim(), model, params };
}
