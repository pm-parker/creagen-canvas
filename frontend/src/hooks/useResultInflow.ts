import { useCanvasStore } from "@/stores/canvas";
import { useTaskStore } from "@/stores/tasks";

const INFLOW_OFFSET_X = 320;

/** 已回流过的 taskId 集合（幂等）。 */
const inflowed = new Set<string>();

/**
 * 生成完成回流：在源节点右侧创建结果节点 + 自动连线。
 * llm → text 节点（prompt 存完整 content，不截断）；其余 → 同类型节点（outputUrl 预览）。
 * failed 不回流。同 taskId 幂等。
 */
export function inflowResult(
  taskId: string,
  sourceNodeId: string,
  result: { status: string; output_url?: string; error?: string; result_type?: string },
): string | null {
  if (result.status !== "completed" || !result.output_url) return null;
  if (inflowed.has(taskId)) return null;
  inflowed.add(taskId);

  const store = useCanvasStore.getState();
  const source = store.nodes.find((n) => n.id === sourceNodeId);
  if (!source) return null;

  const resultType = result.result_type === "text" ? "text" : source.data.nodeType;
  const newNodeId = store.addNode(resultType, {
    x: source.position.x + INFLOW_OFFSET_X,
    y: source.position.y,
  });

  store.updateNodeData(newNodeId, {
    title: `生成 · ${source.data.title || source.data.nodeType}`,
    ...(result.result_type === "text"
      ? { prompt: result.output_url }
      : { outputUrl: result.output_url }),
  });

  // 连线 源 → 新节点
  const st = useCanvasStore.getState();
  st.onConnect({ source: sourceNodeId, target: newNodeId, sourceHandle: null, targetHandle: null });

  const ts = useTaskStore.getState();
  ts.update(Number(taskId.replace(/\D/g, "")) || 0, {});
  return newNodeId;
}
