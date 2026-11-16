import { describe, expect, it } from "vitest";
import type { Edge } from "@xyflow/react";
import type { CanvasNode } from "@/stores/canvas";
import {
  mergeSaveResponse,
  threeWayMerge,
  type CanvasMergeState,
  type Tombstones,
} from "./canvasMerge";

/** 构造 RF 节点（client_id 即节点 id）。 */
function node(
  id: string,
  over: Partial<CanvasNode["data"]> & { x?: number; y?: number } = {},
): CanvasNode {
  return {
    id,
    type: "canvas",
    position: { x: over.x ?? 0, y: over.y ?? 0 },
    data: {
      nodeType: over.nodeType ?? "text",
      title: over.title ?? `节点${id}`,
      prompt: over.prompt ?? "p",
      ...(over.metadata !== undefined ? { metadata: over.metadata } : {}),
      ...(over.width !== undefined ? { width: over.width } : {}),
      ...(over.height !== undefined ? { height: over.height } : {}),
      ...(over.outputUrl !== undefined ? { outputUrl: over.outputUrl } : {}),
    },
  };
}

function edge(source: string, target: string): Edge {
  return { id: `e-${source}-${target}`, source, target };
}

function state(nodes: CanvasNode[], edges: Edge[] = []): CanvasMergeState {
  return { nodes, edges };
}

describe("threeWayMerge（保存冲突三方合并）", () => {
  it("本地删除的节点不复活（服务端未改）", () => {
    const base = state([node("a"), node("b")]);
    const local = state([node("a")]); // 用户删了 b
    const server = state([node("a"), node("b")]); // 服务端没动 b
    const out = threeWayMerge(base, local, server);
    expect(out.nodes.map((n) => n.id)).toEqual(["a"]);
    expect(out.conflicts).toEqual([]);
  });

  it("本地删除但服务端已改 → 保留服务端版本并记冲突", () => {
    const base = state([node("a"), node("b")]);
    const local = state([node("a")]); // 用户删了 b
    const server = state([node("a"), node("b", { title: "服务端改标题" })]); // Agent 回写等
    const out = threeWayMerge(base, local, server);
    expect(out.nodes.map((n) => n.id).sort()).toEqual(["a", "b"]);
    const b = out.nodes.find((n) => n.id === "b")!;
    expect(b.data.title).toBe("服务端改标题");
    expect(out.conflicts).toEqual([{ id: "b", reason: "local-deleted-server-edited" }]);
  });

  it("服务端新增节点（Agent 回流）保留", () => {
    const base = state([node("a")]);
    const local = state([node("a", { title: "本地改" })]);
    const server = state([node("a"), node("agent-1", { nodeType: "image" })]);
    const out = threeWayMerge(base, local, server);
    expect(out.nodes.map((n) => n.id)).toEqual(["a", "agent-1"]);
    expect(out.conflicts).toEqual([]);
  });

  it("本地新增节点保留", () => {
    const base = state([node("a")]);
    const local = state([node("a"), node("mine")]);
    const server = state([node("a")]);
    const out = threeWayMerge(base, local, server);
    expect(out.nodes.map((n) => n.id)).toEqual(["a", "mine"]);
  });

  it("双侧都改 → 本地胜出并记冲突", () => {
    const base = state([node("a")]);
    const local = state([node("a", { title: "本地标题", x: 10 })]);
    const server = state([node("a", { title: "服务端标题", x: 99 })]);
    const out = threeWayMerge(base, local, server);
    expect(out.nodes).toHaveLength(1);
    expect(out.nodes[0].data.title).toBe("本地标题");
    expect(out.nodes[0].position.x).toBe(10);
    expect(out.conflicts).toEqual([{ id: "a", reason: "both-edited" }]);
  });

  it("仅服务端改 → 服务端胜出（宽高/服务端元数据采纳）", () => {
    const base = state([node("a")]);
    const local = state([node("a")]);
    const server = state([
      node("a", { title: "新标题", width: 320, height: 200, metadata: { outputUrl: "/o.png", extraUrls: ["/o.png", "/e.png"] } }),
    ]);
    const out = threeWayMerge(base, local, server);
    expect(out.nodes[0].data.title).toBe("新标题");
    expect(out.nodes[0].data.width).toBe(320);
    expect(out.nodes[0].data.metadata).toMatchObject({ outputUrl: "/o.png" });
    expect(out.conflicts).toEqual([]);
  });

  it("仅本地改 → 本地胜出", () => {
    const base = state([node("a")]);
    const local = state([node("a", { prompt: "本地提示词" })]);
    const server = state([node("a")]);
    const out = threeWayMerge(base, local, server);
    expect(out.nodes[0].data.prompt).toBe("本地提示词");
    expect(out.conflicts).toEqual([]);
  });

  it("服务端删除且本地未改 → 删除", () => {
    const base = state([node("a"), node("b")]);
    const local = state([node("a"), node("b")]);
    const server = state([node("a")]); // 另一端删了 b
    const out = threeWayMerge(base, local, server);
    expect(out.nodes.map((n) => n.id)).toEqual(["a"]);
    expect(out.conflicts).toEqual([]);
  });

  it("服务端删除但本地已改 → 保留本地并记冲突（重试将重建）", () => {
    const base = state([node("a"), node("b")]);
    const local = state([node("a"), node("b", { title: "本地改" })]);
    const server = state([node("a")]);
    const out = threeWayMerge(base, local, server);
    expect(out.nodes.map((n) => n.id)).toEqual(["a", "b"]);
    expect(out.conflicts).toEqual([{ id: "b", reason: "server-deleted-local-edited" }]);
  });

  it("指向已删除节点的边被丢弃；服务端新增边保留", () => {
    const base = state([node("a"), node("b")], [edge("a", "b")]);
    const local = state([node("a")]); // 删 b（连带 a→b 边）
    const server = state(
      [node("a"), node("b"), node("agent-1")],
      [edge("a", "b"), edge("agent-1", "a")], // 服务端（Agent）连了一条新边
    );
    const out = threeWayMerge(base, local, server);
    expect(out.nodes.map((n) => n.id).sort()).toEqual(["a", "agent-1"]);
    // a→b 指向已删除的 b：丢弃；agent-1→a 指向存在节点：保留
    expect(out.edges).toEqual([edge("agent-1", "a")]);
  });

  it("本地新增边保留；服务端删除且本地未动的边移除", () => {
    const base = state([node("a"), node("b")], [edge("a", "b")]);
    const local = state([node("a"), node("b"), node("c")], [edge("a", "b"), edge("b", "c")]);
    const server = state([node("a"), node("b"), node("c")]); // 另一端删了 a→b
    const out = threeWayMerge(base, local, server);
    expect(out.edges).toEqual([edge("b", "c")]);
  });
});

describe("mergeSaveResponse（round-3：保存响应合并在途改动）", () => {
  const mk = (id: string, title = "t"): CanvasNode => ({
    id,
    type: "canvas",
    position: { x: 0, y: 0 },
    data: { nodeType: "text", title, prompt: "p" },
  });

  it("在途删除：base 有、本地删、服务端回显 → 不复活", () => {
    const base = { nodes: [mk("a"), mk("b")], edges: [] };
    const local = { nodes: [mk("a")], edges: [] };
    const server = { nodes: [mk("a"), mk("b")], edges: [] }; // 响应回显两个
    const out = mergeSaveResponse(base, local, server);
    expect(out.nodes.map((n) => n.id)).toEqual(["a"]);
    expect(out.conflicts).toEqual([]);
  });

  it("在途编辑：本地改了、响应回显旧值 → 编辑保留并回填服务端字段", () => {
    const base = { nodes: [mk("a", "旧")], edges: [] };
    const local = { nodes: [{ ...mk("a", "新"), data: { ...mk("a", "新").data } }], edges: [] };
    const server = {
      nodes: [
        {
          ...mk("a", "旧"),
          data: { ...mk("a", "旧").data, outputUrl: "/s.png", width: 280, height: 180, metadata: { extraUrls: ["/x"] } },
        },
      ],
      edges: [],
    };
    const out = mergeSaveResponse(base, local, server);
    expect(out.nodes[0]?.data.title).toBe("新"); // 在途编辑保留
    expect(out.nodes[0]?.data.outputUrl).toBe("/s.png"); // 服务端字段回填
    expect(out.nodes[0]?.data.width).toBe(280);
    expect(out.nodes[0]?.data.metadata).toMatchObject({ extraUrls: ["/x"] });
  });

  it("在途新增：本地新节点保留（下一轮保存上传）；服务端新增（Agent 回流）并入", () => {
    const base = { nodes: [mk("a")], edges: [] };
    const local = { nodes: [mk("a"), mk("local-new")], edges: [] };
    const server = { nodes: [mk("a"), mk("agent-x")], edges: [] };
    const out = mergeSaveResponse(base, local, server);
    expect(out.nodes.map((n) => n.id).sort()).toEqual(["a", "agent-x", "local-new"]);
  });

  it("服务端写入但本地未改：服务端版本胜出（宽高/元数据采纳）", () => {
    const base = { nodes: [mk("a")], edges: [] };
    const server = {
      nodes: [{ ...mk("a"), data: { ...mk("a").data, outputUrl: "/done.png" } }],
      edges: [],
    };
    const out = mergeSaveResponse(base, { nodes: [mk("a")], edges: [] }, server);
    expect(out.nodes[0]?.data.outputUrl).toBe("/done.png");
    expect(out.conflicts).toEqual([]);
  });
});

describe("删除墓碑（C②：tombstones 绝不复活）", () => {
  const mk = (id: string, title = "t"): CanvasNode => ({
    id,
    type: "canvas",
    position: { x: 0, y: 0 },
    data: { nodeType: "text", title, prompt: "p" },
  });

  it("墓碑节点即使服务端在 base 之后改过也不复活（仅记冲突）", () => {
    const base = state([node("a"), node("b")]);
    const local = state([node("a")]); // 用户删了 b
    const server = state([node("a"), node("b", { title: "Agent 改了" })]);
    const tombs: Tombstones = { nodes: new Set(["b"]), edges: new Set() };
    const out = threeWayMerge(base, local, server, tombs);
    expect(out.nodes.map((n) => n.id)).toEqual(["a"]); // 不复活
    expect(out.conflicts).toEqual([{ id: "b", reason: "local-deleted-server-edited" }]);
  });

  it("保存响应回显墓碑节点（在途删除）→ 不复活", () => {
    const base = state([mk("a"), mk("b")]);
    const local = state([mk("a")]);
    const server = state([mk("a"), mk("b")]); // 响应回显两个（发送时 b 还在）
    const tombs: Tombstones = { nodes: new Set(["b"]), edges: new Set() };
    const out = mergeSaveResponse(base, local, server, tombs);
    expect(out.nodes.map((n) => n.id)).toEqual(["a"]);
  });

  it("连到墓碑节点的边被丢弃；墓碑边不复活", () => {
    const base = state([node("a"), node("b")], [edge("a", "b")]);
    const local = state([node("a")], []); // 删 b 及其边
    const server = state([node("a"), node("b")], [edge("a", "b"), edge("b", "a")]);
    const tombs: Tombstones = { nodes: new Set(["b"]), edges: new Set() };
    const out = threeWayMerge(base, local, server, tombs);
    expect(out.nodes.map((n) => n.id)).toEqual(["a"]);
    expect(out.edges).toEqual([]); // a→b / b→a 都因墓碑丢弃
  });

  it("墓碑边单独生效（节点都在但用户删了那条边）", () => {
    const base = state([node("a"), node("b")], [edge("a", "b")]);
    const local = state([node("a"), node("b")], []);
    const server = state([node("a"), node("b")], [edge("a", "b")]); // Agent 又改了 b
    const tombs: Tombstones = { nodes: new Set(), edges: new Set(["a→b"]) };
    const out = threeWayMerge(base, local, server, tombs);
    expect(out.edges).toEqual([]);
  });
});
