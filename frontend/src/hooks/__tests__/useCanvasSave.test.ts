import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useCanvasSave } from "../useCanvasSave";
import { useCanvasStore } from "@/stores/canvas";

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
        hint: "已自动合并服务端最新内容后重试",
        current_revision: 8,
      },
    },
    409,
  );
}

function mockFetch(routes: Route[], fallbackError = "boom") {
  const calls: Array<{ url: string; body: unknown }> = [];
  const impl = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    let body: unknown = undefined;
    try {
      body = init.body ? JSON.parse(String(init.body)) : undefined;
    } catch {
      body = init.body;
    }
    calls.push({ url, body });
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

  it("保存请求携带 base_revision，成功后记录新 revision", async () => {
    const { calls } = mockFetch([
      (url) =>
        url.endsWith("/nodes")
          ? jsonResponse({ nodes: [{ id: 1, client_id: "uuid-a" }], revision: 7 })
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

  it("409 REVISION_CONFLICT：重载合并后用最新 revision 重试一次成功，不清空服务端新增", async () => {
    let nodesAttempt = 0;
    const { calls } = mockFetch([
      (url) => {
        if (!url.endsWith("/nodes")) return undefined;
        nodesAttempt++;
        if (nodesAttempt === 1) return conflictResponse();
        return jsonResponse({
          nodes: [
            { id: 1, client_id: "uuid-a", metadata: { client_id: "uuid-a" } },
            { id: 2, client_id: "agent-x", metadata: { client_id: "agent-x" } },
          ],
          revision: 9,
        });
      },
      (url) => {
        if (url.includes("/projects/1") && !url.includes("/nodes")) {
          return jsonResponse({
            project: {
              revision: 8,
              nodes: [
                {
                  id: 2,
                  type: "image",
                  title: "Agent 节点",
                  position: { x: 9, y: 9 },
                  metadata: { client_id: "agent-x", outputUrl: "/agent.png" },
                },
              ],
            },
          });
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
    expect(nodesAttempt).toBe(2);
    // 重试用了重载后的 revision
    const puts = calls.filter((c) => c.url.endsWith("/nodes"));
    expect(puts[1]?.body).toMatchObject({ base_revision: 8 });
    // 服务端新增的 agent 节点并入本地且未被删除
    const ids = useCanvasStore.getState().nodes.map((n) => n.id);
    expect(ids).toContain("uuid-a");
    expect(ids).toContain("agent-x");
  });

  it("失败排程重试后切项目：重试定时器被取消，不把项目 A 的节点写进项目 B", async () => {
    const { calls } = mockFetch([
      (url) => {
        if (url.endsWith("/projects/1/nodes")) return conflictResponse();
        if (url.endsWith("/projects/1")) {
          return jsonResponse({ project: { revision: 8, nodes: [] } });
        }
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
