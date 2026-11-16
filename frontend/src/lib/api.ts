import { useAuthStore } from "@/stores/auth";

/** 结构化错误：与后端 errors.py 契约对齐（code/message/hint）。 */
export class ApiError extends Error {
  code: string;
  hint: string;
  status: number;

  constructor(code: string, message: string, hint: string, status: number) {
    super(message);
    this.code = code;
    this.hint = hint;
    this.status = status;
  }
}

type UnauthorizedHandler = () => void;
let unauthorizedHandler: UnauthorizedHandler | null = null;

/** 由路由层注册：401 时的跳转行为（避免本模块依赖 router）。 */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null) {
  unauthorizedHandler = handler;
}

/** 401 不触发「登录过期」处理的路由：
 * - /api/auth/login：密码错误返回 401 是业务错误，由登录页展示
 *   「用户名或密码错误」，不能当作会话过期去清 token / 跳登录。
 * - /api/auth/change-password：已改为 400（不在此列），但历史版本返回
 *   401，同样不应踢会话，双保险豁免。 */
const NO_SESSION_EXPIRE_PATHS = ["/api/auth/login", "/api/auth/change-password"];

function isSessionExpireExempt(path: string): boolean {
  return NO_SESSION_EXPIRE_PATHS.includes(path);
}

interface ApiFetchOptions extends Omit<RequestInit, "body"> {
  json?: unknown;
}

export async function apiFetch<T = unknown>(path: string, opts: ApiFetchOptions = {}): Promise<T> {
  const { json, headers, ...rest } = opts;
  const token = useAuthStore.getState().token;

  const init: RequestInit = {
    ...rest,
    headers: {
      ...(json !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    ...(json !== undefined ? { body: JSON.stringify(json) } : {}),
  };

  const resp = await fetch(path, init);

  if (resp.status === 401 && !isSessionExpireExempt(path)) {
    useAuthStore.getState().logout();
    unauthorizedHandler?.();
    throw new ApiError("AUTH_REQUIRED", "登录已过期，请重新登录", "", 401);
  }

  // 不信任 content-type：统一尝试 JSON 解析（后端兼容契约：HTTP 200 内嵌 JSON 业务错误也统一处理）
  let data: unknown = undefined;
  let parsed = false;
  try {
    data = await resp.json();
    parsed = true;
  } catch {
    // 非 JSON 响应（204/文本）——按无 envelope 处理
  }

  if (parsed && _looksLikeEnvelopeError(data)) {
    const detail = (data as { detail: { code?: string; message?: string; hint?: string } }).detail;
    throw new ApiError(detail.code ?? "API_ERROR", detail.message ?? "请求失败", detail.hint ?? "", resp.status);
  }

  if (!resp.ok) {
    const detail = (data as { detail?: unknown } | undefined)?.detail;
    if (typeof detail === "string") {
      throw new ApiError("API_ERROR", detail, "", resp.status);
    }
    throw new ApiError("HTTP_ERROR", `请求失败（${resp.status}）`, "", resp.status);
  }

  return data as T;
}

function _looksLikeEnvelopeError(data: unknown): boolean {
  if (typeof data !== "object" || data === null) return false;
  const detail = (data as { detail?: unknown }).detail;
  return (
    typeof detail === "object" &&
    detail !== null &&
    "code" in detail &&
    "message" in detail
  );
}
