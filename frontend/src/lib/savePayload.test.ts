import { describe, expect, it } from "vitest";
import { buildNodePayload, collectDeletedClientIds, mapEdgesByNodeOrder } from "./savePayload";
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
        width: null,
        height: null,
        metadata_json: JSON.stringify({ prompt: "提示词uuid-a", outputUrl: null, client_id: "uuid-a" }),
      },
      {
        type: "image",
        title: "节点uuid-b",
        position_x: -5,
        position_y: 0,
        width: null,
        height: null,
        metadata_json: JSON.stringify({ prompt: "提示词uuid-b", outputUrl: null, client_id: "uuid-b" }),
      },
    ]);
  });

  it("空数组合法（清空画布）", () => {
    expect(buildNodePayload([])).toEqual([]);
  });

  it("保留服务端写入的 metadata 键与 width/height（合并而非丢弃）", () => {
    const node: CanvasNode = {
      id: "uuid-1",
      type: "canvas",
      position: { x: 1, y: 2 },
      data: {
        nodeType: "image",
        title: "节点uuid-1",
        prompt: "新提示词",
        width: 280,
        height: 180,
        metadata: {
          client_id: "uuid-1",
          extraUrls: ["/outputs/extra1.png", "/outputs/extra2.png"],
          durationSec: 6,
          someServerKey: "keep-me",
        },
      },
    };
    const [payload] = buildNodePayload([node]);
    expect(payload.width).toBe(280);
    expect(payload.height).toBe(180);
    const meta = JSON.parse(payload.metadata_json) as Record<string, unknown>;
    // 服务端写入的未知键必须原样保留
    expect(meta.extraUrls).toEqual(["/outputs/extra1.png", "/outputs/extra2.png"]);
    expect(meta.durationSec).toBe(6);
    expect(meta.someServerKey).toBe("keep-me");
    // 前端自管键仍由本地状态覆盖
    expect(meta.prompt).toBe("新提示词");
    expect(meta.client_id).toBe("uuid-1");
  });

  it("未携带尺寸/元数据时：width/height 为 null（服务端保留既有值）", () => {
    const [payload] = buildNodePayload([mkNode("uuid-c", 0, 0)]);
    expect(payload.width).toBeNull();
    expect(payload.height).toBeNull();
    expect(JSON.parse(payload.metadata_json)).toEqual({
      prompt: "提示词uuid-c",
      outputUrl: null,
      client_id: "uuid-c",
    });
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

describe("collectDeletedClientIds（round-3：显式删除 base 感知）", () => {
  it("base 有、本地没有 → 收集为 deleted_client_ids（含 Agent 节点）", () => {
    const base = [{ id: "uuid-a" }, { id: "agent-x" }, { id: "uuid-b" }];
    const current = [{ id: "uuid-a" }];
    expect(collectDeletedClientIds(base, current)).toEqual(["agent-x", "uuid-b"]);
  });

  it("base 为空/未加载 → 不删（服务端在客户端加载后新建的节点不受影响）", () => {
    expect(collectDeletedClientIds(null, [])).toEqual([]);
    expect(collectDeletedClientIds([], [])).toEqual([]);
  });

  it("本地新增的节点不算删除；去重", () => {
    const base = [{ id: "a" }, { id: "a" }];
    const current = [{ id: "a" }, { id: "new-1" }];
    expect(collectDeletedClientIds(base, current)).toEqual([]);
  });
});
