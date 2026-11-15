import { useState } from "react";
import { History } from "lucide-react";
import { useTaskStore } from "@/stores/tasks";

/** 节点生成历史选择器：展示该节点历次生成结果，点击切换 outputUrl。 */
export function GenerationHistoryPicker({
  nodeId,
  activeUrl,
  onSelect,
}: {
  nodeId: string;
  activeUrl?: string;
  onSelect: (url: string) => void;
}) {
  // selector 内禁止 ?? []：每次返回新引用会导致 zustand v5 严格比较无限重渲（React #185）
  const history = useTaskStore((s) => s.generationHistory[nodeId]) ?? [];
  const [open, setOpen] = useState(false);

  if (history.length === 0) return null;

  return (
    <div className="relative">
      <button
        aria-label="生成历史"
        data-testid="history-picker"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 rounded-full bg-zinc-100 px-2 py-0.5 text-xs text-zinc-600 hover:bg-zinc-200 cursor-pointer"
      >
        <History className="h-3 w-3" /> 历史 {history.length}
      </button>
      {open && (
        <div className="absolute left-0 top-6 z-20 max-h-52 w-56 overflow-y-auto rounded-xl border border-zinc-200 bg-white p-1 shadow-lg">
          {history.map((h) => (
            <button
              key={h.taskId}
              onClick={() => {
                onSelect(h.outputUrl);
                setOpen(false);
              }}
              className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs hover:bg-zinc-100 cursor-pointer ${
                h.outputUrl === activeUrl ? "bg-zinc-100" : ""
              }`}
            >
              <span className="truncate flex-1">{h.prompt || `任务 #${h.taskId}`}</span>
              <span className="text-zinc-400">#{h.taskId}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
