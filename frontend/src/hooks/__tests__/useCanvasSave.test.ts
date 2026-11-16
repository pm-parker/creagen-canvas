import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useCanvasSave } from "../useCanvasSave";
import { useCanvasStore, type CanvasNode } from "@/stores/canvas";

/** fetch mock：按 URL 路由返回 Response-like 对象或抛错。 */
type Route = (url: string, init: RequestInit) => unknown;

function jsonResponse(data: unknown, status = 200): Response {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: async () => data,
  } as unknown as Response;
}

function conflictResponse(): Response {
  return jsonResponse(
    {
      detail: {
        code: "REVISION_CONFLICT",
        message: "画布已在其他窗口被修改",
        hint: "客户端将基于此状态三方合并后自动重试",
        current_revision: 8,
        nodes: [
          {
            id: 2,
            type: "image",
            title: "Agent 节点",
            position: { x: 9, y: 9 },
            width: 300,
            height: 200,
            metadata: { client_id: "agent-x", outputUrl: "/agent.png", prompt: "q" },
          },
        ],
        connections: [],
      },
    },
    409,
  );
}

function mockFetch(routes: Route[], fallbackError = "boom") {
  const calls: Array<{ url: string; method?: string; body: unknown }> = [];
  const impl = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    let body: unknown = undefined;
    try {
      body = init.body ? JSON.parse(String(init.body)) : undefined;
    } catch {
      body = init.body;
    }
    calls.push({ url, method: init.method, body });
    for (const r of routes) {
      const out = r(url, init);
      if (out !== undefined) return out as Response;
    }
    return Promise.reject(new TypeError(fallbackError));
  });
  vi.stubGlobal("fetch", impl);
  return { calls, impl };
}

function markDirty() {
  act(() => {
    useCanvasStore.setState({ loaded: true, dirty: true });
  });
}

describe("useCanvasSave（保存管线回归）", () => {
  beforeEach(() => {
    useCanvasStore.getState().reset();
    useCanvasStore.setState({ loaded: true, dirty: false });
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("保存请求携带 base_revision 与 deleted_client_ids，成功后记录新 revision", async () => {
    const { calls } = mockFetch([
      (url) =>
        url.endsWith("/nodes")
          ? jsonResponse({
              nodes: [
                {
                  id: 1,
                  type: "text",
                  title: "t",
                  position: { x: 0, y: 0 },
                  metadata: { client_id: "uuid-a", prompt: "p" },
                },
              ],
              revision: 7,
            })
          : undefined,
      () => jsonResponse({}),
    ]);
    useCanvasStore.setState({
      revision: 6,
      nodes: [
        {
          id: "uuid-a",
          type: "canvas",
          position: { x: 0, y: 0 },
          data: { nodeType: "text", title: "t", prompt: "p" },
        },
      ],
    });
    const { result } = renderHook(() => useCanvasSave("1"));
    markDirty();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1300);
    });
    expect(result.current.status).toBe("saved");
    const nodesPut = calls.find((c) => c.url.endsWith("/nodes"));
    expect(nodesPut?.body).toMatchObject({ base_revision: 6 });
    expect(useCanvasStore.getState().revision).toBe(7);
    expect(useCanvasStore.getState().dirty).toBe(false);
  });

  it("409 REVISION_CONFLICT：用 detail 携带的服务端状态三方合并后重试一次成功，不发 GET", async () => {
    let nodesAttempt = 0;
    const { calls } = mockFetch([
      (url) => {
        if (!url.endsWith("/nodes")) return undefined;
        nodesAttempt++;
        if (nodesAttempt === 1) return conflictResponse();
        // 重试成功：回显本地 + 409 并入的 agent 节点（与本地一致 → 不再触发续存）
        return jsonResponse({
          nodes: [
            {
              id: 1,
              type: "text",
              title: "t",
              position: { x: 0, y: 0 },
              metadata: { client_id: "uuid-a", prompt: "p" },
            },
            {
              id: 2,
              type: "image",
              title: "Agent 节点",
              position: { x: 9, y: 9 },
              width: 300,
              height: 200,
              metadata: { client_id: "agent-x", outputUrl: "/agent.png", prompt: "q" },
            },
          ],
          revision: 9,
        });
      },
      () => jsonResponse({}),
    ]);
    // base = 上次同步状态（仅 uuid-a）
    const baseNode: CanvasNode = {
      id: "uuid-a",
      type: "canvas",
      position: { x: 0, y: 0 },
      data: { nodeType: "text", title: "t", prompt: "p" },
    };
    useCanvasStore.setState({
      revision: 3,
      base: { nodes: [baseNode], edges: [] },
      nodes: [baseNode],
    });
    const { result } = renderHook(() => useCanvasSave("1"));
    markDirty();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(result.current.status).toBe("saved");
    // 409 → 合并重试；若合并仍留下在途差异则自动续存一轮（防抖窗口内完成）
    // 不发 GET（服务端状态由 409 detail 携带）
    expect(calls.filter((c) => c.method === "GET" || c.method === undefined)).toEqual([]);
    // 重试用了 409 的 current_revision
    const puts = calls.filter((c) => c.url.endsWith("/nodes"));
    expect(puts[1]?.body).toMatchObject({ base_revision: 8 });
    // 服务端新增的 agent 节点并入本地且未被删除
    const ids = useCanvasStore.getState().nodes.map((n) => n.id);
    expect(ids).toContain("uuid-a");
    expect(ids).toContain("agent-x");
  });

  it("409 双侧修改：本地胜出并记入 store.conflicts（非阻塞提示）", async () => {
    const localTitle = "本地标题";
    const conflictBoth = jsonResponse(
      {
        detail: {
          code: "REVISION_CONFLICT",
          message: "画布已在其他窗口被修改",
          hint: "",
          current_revision: 5,
          nodes: [
            {
              id: 9,
              type: "text",
              title: "服务端标题",
              position: { x: 0, y: 0 },
              metadata: { client_id: "uuid-a", prompt: "p" },
            },
          ],
          connections: [],
        },
      },
      409,
    );
    let nodesAttempt = 0;
    mockFetch([
      (url) => {
        if (!url.endsWith("/nodes")) return undefined;
        nodesAttempt++;
        if (nodesAttempt === 1) return conflictBoth;
        // 重试成功：回显本地胜出的内容（与本地一致 → 不再触发续存）
        return jsonResponse({
          nodes: [
            {
              id: 1,
              type: "text",
              title: localTitle,
              position: { x: 0, y: 0 },
              metadata: { client_id: "uuid-a", prompt: "p" },
            },
          ],
          revision: 6,
        });
      },
      () => jsonResponse({}),
    ]);
    const baseNode: CanvasNode = {
      id: "uuid-a",
      type: "canvas",
      position: { x: 0, y: 0 },
      data: { nodeType: "text", title: "旧标题", prompt: "p" },
    };
    useCanvasStore.setState({
      revision: 3,
      base: { nodes: [baseNode], edges: [] },
      nodes: [{ ...baseNode, data: { ...baseNode.data, title: "本地标题" } }],
    });
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { result } = renderHook(() => useCanvasSave("1"));
    markDirty();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(result.current.status).toBe("saved");
    expect(nodesAttempt).toBeGreaterThanOrEqual(2);
    // 本地胜出
    expect(useCanvasStore.getState().nodes[0]?.data.title).toBe("本地标题");
    // 409 合并阶段的冲突提示保留（重试成功不清空用户应看到的冲突）
    expect(useCanvasStore.getState().conflicts).toContainEqual({
      id: "uuid-a",
      reason: "both-edited",
    });
    expect(warn).toHaveBeenCalled();
  });

  it("失败排程重试后切项目：重试定时器被取消，不把项目 A 的节点写进项目 B", async () => {
    const { calls } = mockFetch([
      (url) => {
        if (url.endsWith("/projects/1/nodes")) return conflictResponse();
        if (url.endsWith("/projects/2/nodes")) {
          return jsonResponse({ nodes: [], revision: 1 });
        }
        return undefined;
      },
      () => jsonResponse({}),
    ]);
    useCanvasStore.setState({
      revision: 3,
      nodes: [
        {
          id: "uuid-a",
          type: "canvas",
          position: { x: 0, y: 0 },
          data: { nodeType: "text", title: "A", prompt: "p" },
        },
      ],
    });
    const { rerender, unmount } = renderHook(
      ({ pid }: { pid: string | undefined }) => useCanvasSave(pid),
      { initialProps: { pid: "1" } },
    );
    markDirty();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1300);
    });
    // 第一次保存 + 合并重试都拿到 409 → 走退避排程（5s 后重试项目 1）
    const beforeSwitch = calls.length;
    expect(calls.filter((c) => c.url.endsWith("/projects/1/nodes")).length).toBeGreaterThan(0);
    // 切换到项目 B（同一 hook 实例换 projectId）
    rerender({ pid: "2" });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    // 项目 A 的挂起重试必须被取消：切换后不得再有任何节点保存请求
    // （否则 A 的节点会被写进 B 的画布）
    const afterSwitch = calls.slice(beforeSwitch);
    expect(afterSwitch.filter((c) => c.url.endsWith("/nodes"))).toEqual([]);
    // 卸载清理
    unmount();
  });

  it("round-3：保存请求在途期间删除节点 → 响应后仍保持删除，且下一轮保存把它放进 deleted_client_ids", async () => {
    // 挂起第一次 PUT 的响应，模拟请求在途期间用户删了节点
    let resolveFirst!: (v: Response) => void;
    const firstPut = new Promise<Response>((res) => {
      resolveFirst = res;
    });
    let nodesAttempt = 0;
    const { calls } = mockFetch([
      (url) => {
        if (!url.endsWith("/nodes")) return undefined;
        nodesAttempt++;
        if (nodesAttempt === 1) return firstPut;
        // 第二轮：回显服务端真实状态（只有 uuid-a，uuid-b 已被删）
        return jsonResponse({
          nodes: [
            {
              id: 1,
              type: "text",
              title: "t",
              position: { x: 0, y: 0 },
              metadata: { client_id: "uuid-a", prompt: "p" },
            },
          ],
          revision: 8,
        });
      },
      () => jsonResponse({}),
    ]);
    const mk = (id: string): CanvasNode => ({
      id,
      type: "canvas",
      position: { x: 0, y: 0 },
      data: { nodeType: "text", title: "t", prompt: "p" },
    });
    useCanvasStore.setState({
      revision: 6,
      base: { nodes: [mk("uuid-a"), mk("uuid-b")], edges: [] },
      nodes: [mk("uuid-a"), mk("uuid-b")],
    });
    const { result } = renderHook(() => useCanvasSave("1"));
    markDirty();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1300);
    });
    expect(nodesAttempt).toBe(1); // 第一次 PUT 已发出、在途
    // 在途期间删除 uuid-b（第一轮响应包含它——若直接套用响应会复活）
    act(() => {
      useCanvasStore.setState({
        nodes: [mk("uuid-a")],
        dirty: true,
      });
    });
    // 送达第一轮响应（回显两个节点）
    await act(async () => {
      resolveFirst(
        jsonResponse({
          nodes: [
            { id: 1, type: "text", title: "t", position: { x: 0, y: 0 }, metadata: { client_id: "uuid-a", prompt: "p" } },
            { id: 2, type: "text", title: "t", position: { x: 0, y: 0 }, metadata: { client_id: "uuid-b", prompt: "p" } },
          ],
          revision: 7,
        }),
      );
      await vi.advanceTimersByTimeAsync(5000);
    });
    // 删除保持：uuid-b 不复活
    const ids = useCanvasStore.getState().nodes.map((n) => n.id);
    expect(ids).toEqual(["uuid-a"]);
    // 自动续存一轮：下一轮 PUT 把 uuid-b 放进 deleted_client_ids（显式删除）
    const puts = calls.filter((c) => c.url.endsWith("/nodes"));
    expect(puts.length).toBeGreaterThanOrEqual(2);
    const followUp = puts[puts.length - 1]?.body as { deleted_client_ids?: string[] };
    expect(followUp?.deleted_client_ids).toContain("uuid-b");
    expect(result.current.status).toBe("saved");
  });

  it("round-3：保存请求在途期间编辑节点 → 响应后编辑保留（不被响应回滚）", async () => {
    let resolveFirst!: (v: Response) => void;
    const firstPut = new Promise<Response>((res) => {
      resolveFirst = res;
    });
    let nodesAttempt = 0;
    const { calls } = mockFetch([
      (url) => {
        if (!url.endsWith("/nodes")) return undefined;
        nodesAttempt++;
        if (nodesAttempt === 1) return firstPut;
        return jsonResponse({
          nodes: [
            {
              id: 1,
              type: "text",
              title: "在途新标题",
              position: { x: 0, y: 0 },
              metadata: { client_id: "uuid-a", prompt: "p" },
            },
          ],
          revision: 8,
        });
      },
      () => jsonResponse({}),
    ]);
    const mk = (title: string): CanvasNode => ({
      id: "uuid-a",
      type: "canvas",
      position: { x: 0, y: 0 },
      data: { nodeType: "text", title, prompt: "p" },
    });
    useCanvasStore.setState({
      revision: 6,
      base: { nodes: [mk("旧标题")], edges: [] },
      nodes: [mk("旧标题")],
    });
    const { result } = renderHook(() => useCanvasSave("1"));
    markDirty();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1300);
    });
    expect(nodesAttempt).toBe(1);
    // 在途期间编辑标题
    act(() => {
      useCanvasStore.setState({ nodes: [mk("在途新标题")], dirty: true });
    });
    await act(async () => {
      // 第一轮响应回显的是发送时的内容（旧标题）——不得回滚本地编辑
      resolveFirst(
        jsonResponse({
          nodes: [
            { id: 1, type: "text", title: "旧标题", position: { x: 0, y: 0 }, metadata: { client_id: "uuid-a", prompt: "p" } },
          ],
          revision: 7,
        }),
      );
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(useCanvasStore.getState().nodes[0]?.data.title).toBe("在途新标题");
    // 续存一轮把编辑真正上传
    const puts = calls.filter((c) => c.url.endsWith("/nodes"));
    expect(puts.length).toBeGreaterThanOrEqual(2);
    const lastMeta = JSON.parse(
      String(
        (puts[puts.length - 1]?.body as { nodes: Array<{ metadata_json: string }> }).nodes[0]
          .metadata_json,
      ),
    );
    expect(lastMeta.client_id).toBe("uuid-a");
    expect(result.current.status).toBe("saved");
  });

  it("常规失败进入指数退避重试，并在成功后恢复", async () => {
    let nodesAttempt = 0;
    mockFetch([
      (url) => {
        if (!url.endsWith("/nodes")) return undefined;
        nodesAttempt++;
        if (nodesAttempt <= 1) throw new TypeError("network down");
        return jsonResponse({ nodes: [], revision: 2 });
      },
      () => jsonResponse({}),
    ]);
    useCanvasStore.setState({ revision: 0, nodes: [] });
    const { result } = renderHook(() => useCanvasSave("1"));
    markDirty();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1300);
    });
    expect(result.current.status).toBe("error");
    expect(result.current.retryCount).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5100);
    });
    expect(result.current.status).toBe("saved");
    expect(useCanvasStore.getState().revision).toBe(2);
  });
});

describe("useCanvasSave（C② 墓碑：在途删除走 deleted_client_ids）", () => {
  beforeEach(() => {
    useCanvasStore.getState().reset();
    useCanvasStore.setState({ loaded: true, dirty: false });
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("在途删除的 Agent 节点：响应回显不复活，且以墓碑身份进入下一轮 deleted_client_ids", async () => {
    let resolveFirst!: (v: Response) => void;
    const firstPut = new Promise<Response>((res) => {
      resolveFirst = res;
    });
    let nodesAttempt = 0;
    const { calls } = mockFetch([
      (url) => {
        if (!url.endsWith("/nodes")) return undefined;
        nodesAttempt++;
        if (nodesAttempt === 1) return firstPut;
        return jsonResponse({ nodes: [], revision: 8 });
      },
      () => jsonResponse({}),
    ]);
    const mk = (id: string): CanvasNode => ({
      id,
      type: "canvas",
      position: { x: 0, y: 0 },
      data: { nodeType: "image", title: "t", prompt: "p" },
    });
    useCanvasStore.setState({
      revision: 6,
      base: { nodes: [mk("agent-x")], edges: [] },
      nodes: [mk("agent-x")],
    });
    const { result } = renderHook(() => useCanvasSave("1"));
    markDirty();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1300);
    });
    expect(nodesAttempt).toBe(1);
    // 在途期间删除 Agent 节点（记入墓碑）
    act(() => {
      useCanvasStore.getState().onNodesChange([{ type: "remove", id: "agent-x" }]);
    });
    expect(useCanvasStore.getState().tombstones.nodes).toEqual(["agent-x"]);
    await act(async () => {
      resolveFirst(
        jsonResponse({
          nodes: [
            {
              id: 5,
              type: "image",
              title: "t",
              position: { x: 0, y: 0 },
              metadata: { client_id: "agent-x", prompt: "p", outputUrl: "/done.png" },
            },
          ],
          revision: 7,
        }),
      );
      await vi.advanceTimersByTimeAsync(5000);
    });
    // 墓碑：Agent 回显不复活
    expect(useCanvasStore.getState().nodes.map((n) => n.id)).toEqual([]);
    // 下一轮保存把墓碑作为 deleted_client_ids 发送
    const puts = calls.filter((c) => c.url.endsWith("/nodes"));
    expect(puts.length).toBeGreaterThanOrEqual(2);
    const followUp = puts[puts.length - 1]?.body as { deleted_client_ids?: string[] };
    expect(followUp?.deleted_client_ids).toContain("agent-x");
    // 服务端确认删除后（第二轮响应为空）→ 墓碑清除
    expect(useCanvasStore.getState().tombstones.nodes).toEqual([]);
    expect(result.current.status).toBe("saved");
  });
});
