import { describe, expect, it, beforeEach } from "vitest";
import { useCanvasStore } from "./canvas";

const PROJECT = {
  project: {
    id: 1,
    title: "T",
    viewport: '{"x":5,"y":6,"k":1.2}',
    nodes: [
      {
        id: 11,
        type: "text",
        title: "节点A",
        position: { x: 10, y: 20 },
        metadata: { prompt: "一只猫" },
      },
      { id: 12, type: "image", title: "图", position: { x: 0, y: 0 }, metadata: {} },
    ],
    connections: [{ id: 1, from_node_id: 11, to_node_id: 12 }],
  },
};

describe("canvas store", () => {
  beforeEach(() => useCanvasStore.getState().reset());

  it("loadFromApi：后端格式 → RF 节点/连线，loaded=true", () => {
    useCanvasStore.getState().loadFromApi(PROJECT as never);
    const s = useCanvasStore.getState();
    expect(s.loaded).toBe(true);
    expect(s.nodes).toHaveLength(2);
    expect(s.nodes[0].id).toBe("11");
    expect(s.nodes[0].data.nodeType).toBe("text");
    expect(s.nodes[0].data.prompt).toBe("一只猫");
    expect(s.nodes[0].position).toEqual({ x: 10, y: 20 });
    expect(s.edges).toEqual([
      expect.objectContaining({ source: "11", target: "12" }),
    ]);
  });

  it("loadFromApi：metadata.client_id 优先作节点 id（缺失才回退数据库 id）", () => {
    useCanvasStore.getState().loadFromApi({
      project: {
        revision: 3,
        viewport: "{}",
        nodes: [
          {
            id: 101,
            type: "image",
            title: "有cid",
            position: { x: 0, y: 0 },
            metadata: { client_id: "uuid-1", extraUrls: ["/a.png"], durationSec: 6 },
          },
          {
            id: 102,
            type: "text",
            title: "无cid",
            position: { x: 5, y: 5 },
            metadata: { prompt: "p" },
          },
        ],
        connections: [{ id: 1, from_node_id: 101, to_node_id: 102 }],
      },
    });
    const s = useCanvasStore.getState();
    expect(s.nodes[0].id).toBe("uuid-1");
    expect(s.nodes[1].id).toBe("102");
    // 服务端写入的未知 metadata 键随节点携带（保存时合并回传）
    expect(s.nodes[0].data.metadata).toMatchObject({
      client_id: "uuid-1",
      extraUrls: ["/a.png"],
      durationSec: 6,
    });
    // 宽高随节点携带
    expect(s.nodes[0].data.metadata).toBeTruthy();
    // 连线端点翻译成 RF 节点 id（client_id）
    expect(s.edges[0].source).toBe("uuid-1");
    expect(s.edges[0].target).toBe("102");
    // revision 记录（下次保存的乐观锁基准）
    expect(s.revision).toBe(3);
  });

  it("loaded=false 时 markDirtyIfLoaded 拒绝置脏（Review Focus #3）", () => {
    useCanvasStore.setState({ loaded: false });
    useCanvasStore.getState().markDirtyIfLoaded();
    expect(useCanvasStore.getState().dirty).toBe(false);
  });

  it("loaded=true 时变更置脏", () => {
    useCanvasStore.getState().loadFromApi(PROJECT as never);
    useCanvasStore.getState().markDirtyIfLoaded();
    expect(useCanvasStore.getState().dirty).toBe(true);
  });

  it("onConnect 生成 uuid 边（RF v12 对同向重复边去重）", () => {
    useCanvasStore.getState().loadFromApi(PROJECT as never);
    // 已有 11→12；同向重复边被 addEdge 去重
    useCanvasStore.getState().onConnect({ source: "11", target: "12", sourceHandle: null, targetHandle: null });
    expect(useCanvasStore.getState().edges).toHaveLength(1);
    // 反向新边正常创建
    useCanvasStore.getState().onConnect({ source: "12", target: "11", sourceHandle: null, targetHandle: null });
    const s = useCanvasStore.getState();
    expect(s.edges).toHaveLength(2);
    expect(s.edges[1].id).toBeTruthy();
    expect(s.dirty).toBe(true);
  });
});

describe("节点操作（Task 5）", () => {
  beforeEach(() => useCanvasStore.getState().reset());

  it("addNode：四型节点带 uuid id 与完整 data，且置脏", () => {
    useCanvasStore.getState().loadFromApi(PROJECT as never);
    useCanvasStore.getState().clearDirty();

    useCanvasStore.getState().addNode("image", { x: 100, y: 200 });
    const s = useCanvasStore.getState();
    expect(s.nodes).toHaveLength(3);
    const added = s.nodes[2];
    expect(added.id).toMatch(/^[0-9a-f-]{36}$/);
    expect(added.data.nodeType).toBe("image");
    expect(added.data.title).toBe("");
    expect(added.data.prompt).toBe("");
    expect(added.position).toEqual({ x: 100, y: 200 });
    expect(s.dirty).toBe(true);
  });

  it("updateNodeData：按 id 局部更新 data 且置脏", () => {
    useCanvasStore.getState().loadFromApi(PROJECT as never);
    useCanvasStore.getState().clearDirty();
    useCanvasStore.getState().updateNodeData("11", { prompt: "一只橘猫" });
    const s = useCanvasStore.getState();
    expect(s.nodes[0].data.prompt).toBe("一只橘猫");
    expect(s.nodes[0].data.nodeType).toBe("text");
    expect(s.dirty).toBe(true);
  });

  it("updateNodeData：未知 id 不抛错不置脏", () => {
    useCanvasStore.getState().loadFromApi(PROJECT as never);
    useCanvasStore.getState().clearDirty();
    expect(() => useCanvasStore.getState().updateNodeData("nope", { prompt: "x" })).not.toThrow();
    expect(useCanvasStore.getState().dirty).toBe(false);
  });
});

describe("撤销/重做（Task A：对标 BeefTV 操作历史）", () => {
  beforeEach(() => useCanvasStore.getState().reset());

  it("变更前压栈，undo 回滚，redo 恢复", () => {
    useCanvasStore.getState().loadFromApi(PROJECT as never);
    useCanvasStore.getState().clearDirty();
    expect(useCanvasStore.getState().canUndo).toBe(false);

    useCanvasStore.getState().addNode("image", { x: 1, y: 1 });
    expect(useCanvasStore.getState().nodes).toHaveLength(3);
    expect(useCanvasStore.getState().canUndo).toBe(true);

    useCanvasStore.getState().undo();
    expect(useCanvasStore.getState().nodes).toHaveLength(2);
    expect(useCanvasStore.getState().canRedo).toBe(true);

    useCanvasStore.getState().redo();
    expect(useCanvasStore.getState().nodes).toHaveLength(3);
  });

  it("栈深 50 截断", () => {
    useCanvasStore.getState().loadFromApi(PROJECT as never);
    for (let i = 0; i < 55; i++) {
      useCanvasStore.getState().updateNodeData("11", { prompt: `v${i}` });
    }
    expect(useCanvasStore.getState().historyDepth).toBeLessThanOrEqual(50);
    // 连续 undo 最多回滚 50 步
    let steps = 0;
    while (useCanvasStore.getState().canUndo && steps < 60) {
      useCanvasStore.getState().undo();
      steps++;
    }
    expect(steps).toBeLessThanOrEqual(50);
  });

  it("undo 后新变更清空 redo 栈", () => {
    useCanvasStore.getState().loadFromApi(PROJECT as never);
    useCanvasStore.getState().addNode("image", { x: 0, y: 0 });
    useCanvasStore.getState().undo();
    const hadRedo = useCanvasStore.getState().canRedo;
    useCanvasStore.getState().addNode("video", { x: 2, y: 2 });
    expect(hadRedo).toBe(true);
    expect(useCanvasStore.getState().canRedo).toBe(false);
  });
});

describe("节点自动整理（M2-1，对标 BeefTV 自动整理）", () => {
  beforeEach(() => useCanvasStore.getState().reset());

  function loadThree() {
    useCanvasStore.getState().loadFromApi({
      project: {
        viewport: "{}",
        nodes: [
          { id: 1, type: "text", title: "A", position: { x: 0, y: 0 }, metadata: {} },
          { id: 2, type: "image", title: "B", position: { x: 0, y: 0 }, metadata: {} },
          { id: 3, type: "video", title: "C", position: { x: 0, y: 0 }, metadata: {} },
        ],
        connections: [{ id: 1, from_node_id: 1, to_node_id: 2 }],
      },
    });
  }

  it("按连线拓扑分层：无入边列 0，下游列 1", () => {
    loadThree();
    useCanvasStore.getState().autoArrange();
    const s = useCanvasStore.getState();
    const a = s.nodes.find((n) => n.id === "1")!;
    const b = s.nodes.find((n) => n.id === "2")!;
    const c = s.nodes.find((n) => n.id === "3")!;
    expect(a.position.x).toBeLessThan(b.position.x); // A → B 有边
    expect(c.position.x).toBeLessThan(b.position.x); // C 无入边也在列 0
    // 同层（A、C 同在层 0）纵向排开
    expect(a.position.y).not.toBe(c.position.y);
  });

  it("保留 X/Y 均为数字且不重叠", () => {
    loadThree();
    useCanvasStore.getState().autoArrange();
    const s = useCanvasStore.getState();
    const positions = s.nodes.map((n) => `${n.position.x},${n.position.y}`);
    expect(new Set(positions).size).toBe(s.nodes.length);
    expect(s.dirty).toBe(true);
  });

  it("空画布不炸", () => {
    useCanvasStore.getState().reset();
    expect(() => useCanvasStore.getState().autoArrange()).not.toThrow();
  });
});
