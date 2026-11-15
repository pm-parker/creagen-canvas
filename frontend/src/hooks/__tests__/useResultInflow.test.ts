import { describe, expect, it, beforeEach } from "vitest";
import { useCanvasStore } from "@/stores/canvas";
import { useTaskStore } from "@/stores/tasks";
import { inflowResult } from "../useResultInflow";

import type { CanvasNodeType } from "@/stores/canvas";

const SOURCE = {
  id: "src-1",
  type: "canvas",
  position: { x: 100, y: 100 },
  data: { nodeType: "image" as CanvasNodeType, title: "源图", prompt: "p" },
};

function setup() {
  useCanvasStore.setState({ nodes: [SOURCE], edges: [], loaded: true, dirty: false });
  useTaskStore.setState({ runs: {} });
}

describe("inflowResult（Task 5：结果回流）", () => {
  beforeEach(setup);

  it("image 任务：源右侧创建 image 节点并自动连线", () => {
    const nodeId = inflowResult("task-1", "src-1", {
      status: "completed",
      output_url: "/outputs/gen.png",
    });
    expect(nodeId).toBeTruthy();
    const s = useCanvasStore.getState();
    expect(s.nodes).toHaveLength(2);
    const created = s.nodes[1];
    expect(created.data.nodeType).toBe("image");
    expect(created.data.outputUrl).toBe("/outputs/gen.png");
    expect(created.position.x).toBeGreaterThanOrEqual(SOURCE.position.x + 200);
    expect(s.edges.some((e) => e.source === "src-1" && e.target === created.id)).toBe(true);
    expect(s.dirty).toBe(true);
  });

  it("llm 长文本（>220 字）完整写入不截断（Review Focus #2）", () => {
    const content = "段".repeat(500);
    inflowResult("task-2", "src-1", { status: "completed", output_url: content, result_type: "text" });
    const created = useCanvasStore.getState().nodes[1];
    expect(created.data.prompt).toBe(content);
    expect(created.data.prompt.length).toBe(500);
  });

  it("同 taskId 重复回调幂等（只回流一次）", () => {
    inflowResult("task-3", "src-1", { status: "completed", output_url: "/x.png" });
    const after1 = useCanvasStore.getState().nodes.length;
    inflowResult("task-3", "src-1", { status: "completed", output_url: "/x.png" });
    expect(useCanvasStore.getState().nodes.length).toBe(after1);
  });

  it("failed 任务不回流", () => {
    expect(inflowResult("task-4", "src-1", { status: "failed", error: "boom" })).toBeNull();
    expect(useCanvasStore.getState().nodes).toHaveLength(1);
  });
});
