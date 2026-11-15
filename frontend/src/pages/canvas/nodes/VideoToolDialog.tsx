import { useRef, useState } from "react";
import { Film, Loader2, Scissors } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { useCanvasStore } from "@/stores/canvas";

/**
 * 视频轻加工（P2）：取帧（指定时间点 → 图片节点）/ 截取（起止区间 → 新视频节点）。
 * 后端 ffmpeg 同步处理：copy 快路径秒级，重编码有耗时——按钮态提示。
 */
export function VideoToolDialog({
  nodeId,
  src,
  mode,
  onClose,
}: {
  nodeId: string;
  src: string;
  mode: "frame" | "trim";
  onClose: () => void;
}) {
  const [time, setTime] = useState("0");
  const [start, setStart] = useState("0");
  const [end, setEnd] = useState("3");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const addNode = useCanvasStore((s) => s.addNode);
  const updateNodeData = useCanvasStore((s) => s.updateNodeData);
  const onConnect = useCanvasStore((s) => s.onConnect);

  async function submit() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const isFrame = mode === "frame";
      const resp = await apiFetch<{ url: string }>("/api/media/" + (isFrame ? "frame" : "trim"), {
        method: "POST",
        json: isFrame
          ? { url: src, time: Number(time) || 0 }
          : { url: src, start: Number(start) || 0, end: Number(end) || 1 },
      });
      const source = useCanvasStore.getState().nodes.find((n) => n.id === nodeId);
      const parentPos = source?.position ?? { x: 0, y: 0 };
      const id = addNode(isFrame ? "image" : "video", {
        x: parentPos.x + 320,
        y: parentPos.y,
      });
      updateNodeData(id, {
        title: isFrame ? `封面帧 ${Number(time).toFixed(1)}s` : `截取 ${start}s~${end}s`,
        outputUrl: resp.url,
        prompt: source?.data.prompt ?? "",
      });
      onConnect({ source: nodeId, target: id, sourceHandle: null, targetHandle: null });
      onClose();
    } catch (err) {
      const e = err as { message?: string; hint?: string };
      setError(`${e.message ?? "处理失败"}${e.hint ? `：${e.hint}` : ""}`);
    } finally {
      setBusy(false);
    }
  }

  const inputCls =
    "h-8 w-24 rounded-lg border border-zinc-200 px-2 text-sm outline-none focus:border-zinc-400";

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="max-h-[92vh] max-w-2xl space-y-3 overflow-y-auto rounded-2xl bg-white p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-semibold">{mode === "frame" ? "提取帧" : "截取视频"}</h3>
          <span className="max-w-64 truncate text-xs text-zinc-400" title={src}>
            {src}
          </span>
        </div>
        <p className="text-xs text-zinc-500">
          {mode === "frame"
            ? "指定时间点提取一帧作为图片节点（封面可用 0s）。"
            : "截取起止区间生成新视频节点（优先无损 copy，不对齐时自动重编码）。"}
        </p>

        <div className="flex max-h-56 items-center justify-center overflow-hidden rounded-lg bg-zinc-900">
          <video
            ref={videoRef}
            src={src}
            controls
            className="max-h-56 max-w-full"
            data-testid="video-preview"
          />
        </div>

        {mode === "frame" ? (
          <div className="flex items-center gap-2 text-sm text-zinc-600">
            <span>时间点（秒）</span>
            <input
              type="number"
              min={0}
              step={0.1}
              value={time}
              aria-label="时间点秒"
              onChange={(e) => setTime(e.target.value)}
              className={inputCls}
            />
            <button
              onClick={() => setTime(String(videoRef.current?.currentTime ?? 0))}
              className="rounded-md bg-zinc-100 px-2 py-1 text-xs text-zinc-600 hover:bg-zinc-200 cursor-pointer"
            >
              取当前播放位置
            </button>
          </div>
        ) : (
          <div className="flex items-center gap-2 text-sm text-zinc-600">
            <span>区间（秒）</span>
            <input
              type="number"
              min={0}
              step={0.1}
              value={start}
              aria-label="起始秒"
              onChange={(e) => setStart(e.target.value)}
              className={inputCls}
            />
            <span>至</span>
            <input
              type="number"
              min={0.5}
              step={0.1}
              value={end}
              aria-label="结束秒"
              onChange={(e) => setEnd(e.target.value)}
              className={inputCls}
            />
          </div>
        )}

        {error && <p className="text-xs text-red-600">{error}</p>}

        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose}>
            取消
          </Button>
          <Button size="sm" onClick={submit} disabled={busy}>
            {busy ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : mode === "frame" ? (
              <Film className="h-4 w-4" />
            ) : (
              <Scissors className="h-4 w-4" />
            )}
            {busy ? "处理中…" : mode === "frame" ? "提取为新节点" : "截取为新节点"}
          </Button>
        </div>
      </div>
    </div>
  );
}
