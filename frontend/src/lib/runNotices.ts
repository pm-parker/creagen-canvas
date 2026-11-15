import { useAuthStore } from "@/stores/auth";

/** /api/agent-runs/notices 推送的运行通知（终态 toast 通道）。 */
export interface RunNotice {
  run_id: number;
  title: string;
  body: string;
}

/**
 * 订阅用户级 agent 运行通知流（SSE，fetch 流解析避免 EventSource 无法带 Authorization）。
 * 只派发 type === "run_notice" 的事件；流结束/出错时静默返回，重连由调用方负责。
 */
export async function subscribeRunNotices(
  onNotice: (notice: RunNotice) => void,
  opts: { signal?: AbortSignal } = {},
): Promise<void> {
  const headers: Record<string, string> = {};
  const token = useAuthStore.getState().token;
  if (token) headers.Authorization = `Bearer ${token}`;

  let res: Response;
  try {
    res = await fetch("/api/agent-runs/notices", { headers, signal: opts.signal });
  } catch {
    return;
  }
  if (!res.ok || !res.body) return;

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return;
      buf += decoder.decode(value, { stream: true });
      const blocks = buf.split("\n\n");
      buf = blocks.pop() ?? "";
      for (const block of blocks) {
        const dataLine = block.split("\n").find((line) => line.startsWith("data: "));
        if (!dataLine) continue;
        try {
          const evt = JSON.parse(dataLine.slice(6));
          if (evt.type === "run_notice" && evt.data) onNotice(evt.data as RunNotice);
        } catch {
          // 忽略非 JSON 心跳/残帧
        }
      }
    }
  } catch {
    // AbortError 或流中断——调用方决定是否重连
  }
}
