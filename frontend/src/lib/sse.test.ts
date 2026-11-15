import { afterEach, describe, expect, it, vi } from "vitest";
import { consumeSseStream } from "./sse";

function sseResponse(chunks: string[], closeEarly = false): Response {
  const encoder = new TextEncoder();
  let i = 0;
  const stream = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (i < chunks.length) {
        controller.enqueue(encoder.encode(chunks[i++]));
      } else if (!closeEarly) {
        // 保持流开放（模拟服务端持续连接），仅发终态帧后由解析器主动停止
        controller.close();
      } else {
        controller.close();
      }
    },
  });
  return new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

describe("consumeSseStream", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("按顺序解析 phase 与 completed 帧后停止", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse([
          'data: {"type":"phase","data":{"phase":"generating"}}\n\n',
          'data: {"type":"completed","data":{"output_url":"/x.png"}}\n\n',
        ]),
      ),
    );
    const events: Array<[string, Record<string, unknown>]> = [];
    await consumeSseStream(1, (t, d) => events.push([t, d]), { fallback: async () => undefined });
    expect(events.map((e) => e[0])).toEqual(["phase", "completed"]);
    expect(events[1][1]).toEqual({ output_url: "/x.png" });
  });

  it("流提前断开时兜底拉终态（Review Focus #3）", async () => {
    const fetchMock = vi.fn();
    // 第一次：SSE 流（空，立即断开）；第二次：兜底 GET 终态
    fetchMock.mockResolvedValueOnce(sseResponse([], true)).mockResolvedValueOnce(
      new Response(
        JSON.stringify({ task: { status: "completed", output_url: "/fallback.png" } }),
        { status: 200 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const events: Array<[string, Record<string, unknown>]> = [];
    await consumeSseStream(2, (t, d) => events.push([t, d]), {
      fallback: async () => ({ type: "completed", data: { output_url: "/fallback.png" } }),
    });

    expect(events[events.length - 1][0]).toBe("completed");
    expect(events[events.length - 1][1]).toEqual({ output_url: "/fallback.png" });
  });

  it("请求带 Authorization 头", async () => {
    const fetchMock = vi.fn().mockResolvedValue(sseResponse([], true));
    vi.stubGlobal("fetch", fetchMock);
    const token = "tok-sse";
    vi.stubGlobal("localStorage", {
      getItem: vi.fn(() => JSON.stringify({ state: { token } })),
    });
    await consumeSseStream(3, () => undefined, { fallback: async () => undefined });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>)["Authorization"]).toContain("Bearer");
  });

  it("流中途网络错误（read 抛错）仍走兑底拉终态", async () => {
    const fetchMock = vi.fn();
    // SSE 流：发一帧 phase 后 read() 抛错（断网/连接重置）
    const encoder = new TextEncoder();
    let i = 0;
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        if (i === 0) {
          i++;
          controller.enqueue(
            encoder.encode('data: {"type":"phase","data":{"phase":"generating"}}\n\n'),
          );
        } else {
          controller.error(new TypeError("network error"));
        }
      },
    });
    fetchMock
      .mockResolvedValueOnce(new Response(stream, { status: 200 }))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ task: { status: "completed", output_url: "/fallback2.png" } }),
          { status: 200 },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    const events: Array<[string, Record<string, unknown>]> = [];
    await consumeSseStream(7, (t, d) => events.push([t, d]), {
      fallback: async () => ({ type: "completed", data: { output_url: "/fallback2.png" } }),
    });
    // 修复前：read 抛错直接 reject，兑底永不执行
    expect(events.map((e) => e[0])).toEqual(["phase", "completed"]);
    expect(events[1][1]).toEqual({ output_url: "/fallback2.png" });
  });

  it("兑底自身失败也不抛（上层可重试/轮询）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse(['data: {"type":"phase","data":{"phase":"generating"}}\n\n'], true),
      ),
    );
    const events: Array<[string, Record<string, unknown>]> = [];
    await expect(
      consumeSseStream(8, (t, d) => events.push([t, d]), {
        fallback: async () => {
          throw new TypeError("network error");
        },
      }),
    ).resolves.toBeUndefined();
    expect(events.map((e) => e[0])).toEqual(["phase"]);
  });
});
