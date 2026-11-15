import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch, setUnauthorizedHandler } from "./api";
import { useAuthStore } from "@/stores/auth";

describe("apiFetch", () => {
  beforeEach(() => {
    useAuthStore.setState({ token: "tok-123", user: null });
    vi.stubGlobal(
      "location",
      Object.assign(new URL("http://localhost/canvas/1"), { assign: vi.fn() }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    useAuthStore.setState({ token: null, user: null });
  });

  it("注入 Authorization 头并序列化 json body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await apiFetch("/api/tasks", { method: "POST", json: { prompt: "hi" } });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(headers["Authorization"]).toBe("Bearer tok-123");
    expect(headers["Content-Type"]).toBe("application/json");
    expect(init.body).toBe(JSON.stringify({ prompt: "hi" }));
  });

  it("后端错误 envelope 映射为 ApiError{code,message,hint}", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ detail: { code: "PROVIDER_NOT_CONFIGURED", message: "未配置", hint: "去设置页" } }),
          { status: 422 },
        ),
      ),
    );

    const err = (await apiFetch("/api/tasks", { method: "POST" }).catch((e) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe("PROVIDER_NOT_CONFIGURED");
    expect(err.hint).toBe("去设置页");
    expect(err.status).toBe(422);
  });

  it("401 清 token 并跳登录（Review Focus #2）", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 401 })));

    await apiFetch("/api/tasks").catch(() => undefined);

    expect(useAuthStore.getState().token).toBeNull();
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("HTTP 200 内嵌 JSON 错误同样映射（对齐后端 HTTP200+JSON错误兼容契约）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: { code: "X", message: "m", hint: "" } }), { status: 200 }),
      ),
    );
    const err = (await apiFetch("/api/tasks").catch((e) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe("X");
  });
});
