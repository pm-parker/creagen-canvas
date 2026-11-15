import { useCanvasSave } from "@/hooks/useCanvasSave";

export function SaveIndicator({ projectId }: { projectId: string | undefined }) {
  const { status, retryCount } = useCanvasSave(projectId);

  const text =
    status === "saving"
      ? "保存中…"
      : status === "saved"
        ? "已保存"
        : status === "error"
          ? `未保存（第 ${retryCount} 次重试）`
          : "未保存";

  const tone =
    status === "saved"
      ? "text-zinc-400"
      : status === "error"
        ? "text-red-600"
        : "text-zinc-600";

  return (
    <span data-testid="save-indicator" className={`text-xs ${tone}`} role="status">
      {text}
    </span>
  );
}
