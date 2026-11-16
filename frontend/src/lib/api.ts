import { useAuthStore } from "@/stores/auth";

/** 结构化错误：与后端 errors.py 契约对齐（code/message/hint）。 */
export class ApiError extends Error {
  code: string;
  hint: string;
  status: number;

  constructor(code: string, message: string, hint: string, status: number, cause?: unknown) {
    super(message);
    this.code = code;
    this.hint = hint;
    this.status = status;
    if (cause !== undefined) {
      // 后端 detail 全量透传（如 409 REVISION_CONFLICT 携带的 current_revision/nodes/
      // connections）——调用方可从 cause 读取，无需额外 GET
      (this as { cause?: unknown }).cause = cause;
    }
  }
}

type UnauthorizedHandler = () => void;
let unauthorizedHandler: UnauthorizedHandler | null = null;

/** 由路由层注册：401 时的跳转行为（避免本模块依赖 router）。 */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null) {
  unauthorizedHandler = handler;
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

  if (resp.status === 401) {
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
    throw new ApiError(
      detail.code ?? "API_ERROR",
      detail.message ?? "请求失败",
      detail.hint ?? "",
      resp.status,
      detail,
    );
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
