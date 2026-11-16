import { useCanvasSave } from "@/hooks/useCanvasSave";
import { useCanvasStore } from "@/stores/canvas";

/** 保存状态指示 + 保存冲突非阻塞提示（三方合并已按本地版本落地）。 */
export function SaveIndicator({ projectId }: { projectId: string | undefined }) {
  const { status, retryCount } = useCanvasSave(projectId);
  const conflicts = useCanvasStore((s) => s.conflicts);

  const text =
    status === "saving"
      ? "保存中…"
      : status === "saved"
        ? conflicts.length > 0
          ? `已保存（${conflicts.length} 处冲突按本地版本合并）`
          : "已保存"
        : status === "error"
          ? `未保存（第 ${retryCount} 次重试）`
          : "未保存";

  const tone =
    status === "saved"
      ? conflicts.length > 0
        ? "text-amber-600"
        : "text-zinc-400"
      : status === "error"
        ? "text-red-600"
        : "text-zinc-600";

  return (
    <span
      data-testid="save-indicator"
      className={`text-xs ${tone}`}
      role="status"
      title={
        conflicts.length > 0
          ? `与远端修改冲突的节点（已按本地版本保存）：${conflicts
              .map((c) => c.id)
              .join("、")}`
          : undefined
      }
    >
      {text}
    </span>
  );
}
