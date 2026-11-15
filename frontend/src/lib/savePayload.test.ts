import { describe, expect, it } from "vitest";
import { buildNodePayload, mapEdgesByNodeOrder } from "./savePayload";
import type { CanvasNode } from "@/stores/canvas";

function mkNode(id: string, x: number, y: number): CanvasNode {
  return {
    id,
    type: "canvas",
    position: { x, y },
    data: { nodeType: "image", title: `节点${id}`, prompt: `提示词${id}` },
  };
}

describe("buildNodePayload", () => {
  it("RF 节点 → 后端 bulk 字段，uuid 存入 metadata_json.client_id", () => {
    const payload = buildNodePayload([mkNode("uuid-a", 10, 20), mkNode("uuid-b", -5, 0)]);
    expect(payload).toEqual([
      {
        type: "image",
        title: "节点uuid-a",
        position_x: 10,
        position_y: 20,
        metadata_json: JSON.stringify({ prompt: "提示词uuid-a", outputUrl: null, client_id: "uuid-a" }),
      },
      {
        type: "image",
        title: "节点uuid-b",
        position_x: -5,
        position_y: 0,
        metadata_json: JSON.stringify({ prompt: "提示词uuid-b", outputUrl: null, client_id: "uuid-b" }),
      },
    ]);
  });

  it("空数组合法（清空画布）", () => {
    expect(buildNodePayload([])).toEqual([]);
  });
});

describe("mapEdgesByNodeOrder（Review Focus #4：id 漂移）", () => {
  it("服务端按提交顺序返回新 id：按数组位置映射 uuid→后端 id", () => {
    const edges = [
      { id: "e1", source: "uuid-a", target: "uuid-b" },
      { id: "e2", source: "uuid-b", target: "uuid-c" },
    ];
    // 提交顺序 [a, b, c]；服务端重排后新 id 为 [301, 302, 303]
    const serverNodes = [
      { id: 301, client_id: "uuid-a" },
      { id: 302, client_id: "uuid-b" },
      { id: 303, client_id: "uuid-c" },
    ];
    expect(mapEdgesByNodeOrder(edges, serverNodes)).toEqual([
      { from_node_id: 301, to_node_id: 302 },
      { from_node_id: 302, to_node_id: 303 },
    ]);
  });

  it("服务端响应丢失某节点（client_id 对不上）时该边被丢弃而非错连", () => {
    const edges = [{ id: "e1", source: "uuid-a", target: "uuid-b" }];
    const serverNodes = [{ id: 301, client_id: "uuid-a" }]; // uuid-b 丢失
    expect(mapEdgesByNodeOrder(edges, serverNodes)).toEqual([]);
  });

  it("空边返回空", () => {
    expect(mapEdgesByNodeOrder([], [{ id: 1, client_id: "x" }])).toEqual([]);
  });
});
