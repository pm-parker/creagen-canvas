
export interface SseEvent {
  type: string;
  data: Record<string, unknown>;
}

export interface ConsumeOptions {
  /** 流意外断开时的终态兜底（内部 GET /api/tasks/{id}） */
  fallback: () => Promise<SseEvent | undefined>;
  signal?: AbortSignal;
}

function _token(): string | null {
  try {
    const raw = localStorage.getItem("cc-auth");
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { state?: { token?: string } };
    return parsed.state?.token ?? null;
  } catch {
    return null;
  }
}

/**
 * 消费任务 SSE 流（fetch ReadableStream——EventSource 无法带 Authorization 头）。
 * 终态（completed/failed）或流断开后结束；断开时调用 fallback 兜底取终态。
 */
export async function consumeSseStream(
  taskId: number,
  onEvent: (type: string, data: Record<string, unknown>) => void,
  opts: ConsumeOptions,
): Promise<void> {
  const headers: Record<string, string> = {};
  const token = _token();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  let resp: Response;
  try {
    resp = await fetch(`/api/tasks/stream?task_id=${taskId}`, { headers, signal: opts.signal });
  } catch {
    await _fallback(opts, onEvent);
    return;
  }

  if (!resp.ok || !resp.body) {
    await _fallback(opts, onEvent);
    return;
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let terminal = false;

  function handleLine(line: string) {
    if (!line.startsWith("data: ")) return;
    try {
      const payload = JSON.parse(line.slice(6)) as SseEvent;
      onEvent(payload.type, payload.data ?? {});
      if (payload.type === "completed" || payload.type === "failed") terminal = true;
    } catch {
      /* 非 JSON 行忽略（含 keepalive 注释帧） */
    }
  }

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        handleLine(line);
        if (terminal) return; // 终态后不再读取
      }
    }
  } catch {
    /* 流中途网络错误（重置/断网）：不能让异常冒泡——走兑底拉终态 */
  }

  // 任何非终态退出（干净断开或异常）都兑底一次
  if (!terminal) await _fallback(opts, onEvent);
}

async function _fallback(
  opts: ConsumeOptions,
  onEvent: (t: string, d: Record<string, unknown>) => void,
) {
  try {
    const evt = await opts.fallback();
    if (evt) onEvent(evt.type, evt.data);
  } catch {
    /* 兑底也失败：静默结束，上层可重试/轮询（不能变成 unhandled rejection） */
  }
}
