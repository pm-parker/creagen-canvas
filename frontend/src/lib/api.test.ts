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

  it("登录 401 不当作会话过期：不清 token、不跳登录，透传错误信息", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ detail: { code: "AUTH_INVALID", message: "用户名或密码错误", hint: "" } }),
          { status: 401 },
        ),
      ),
    );

    const err = (await apiFetch("/api/auth/login", {
      method: "POST",
      json: { identifier: "bob", password: "wrong" },
    }).catch((e) => e)) as ApiError;

    expect(useAuthStore.getState().token).toBe("tok-123"); // 未被登出
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toBe("用户名或密码错误");
    expect(err.status).toBe(401);
  });

  it("改密码 401 同样豁免（双保险：后端已改 400，但不得踢会话）", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ detail: { code: "WRONG_PASSWORD", message: "原密码错误", hint: "" } }),
          { status: 401 },
        ),
      ),
    );

    await apiFetch("/api/auth/change-password", {
      method: "POST",
      json: { old_password: "a", new_password: "b" },
    }).catch(() => undefined);

    expect(useAuthStore.getState().token).toBe("tok-123");
    expect(onUnauthorized).not.toHaveBeenCalled();
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
