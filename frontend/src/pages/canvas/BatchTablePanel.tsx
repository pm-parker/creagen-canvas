import { useState } from "react";
import { Panel, ReactFlowProvider } from "@xyflow/react";
import { Plus, Table2, Trash2 } from "lucide-react";
import { useBatchGeneration } from "@/hooks/useBatchGeneration";
import { useDomainChannels } from "@/components/canvas/ModelSelect";
import { Button } from "@/components/ui/button";
function BatchModelSelect({
  domain,
  value,
  onChange,
}: {
  domain: "image" | "video";
  value: string;
  onChange: (v: string) => void;
}) {
  const { channels, isLoading } = useDomainChannels(domain);
  if (isLoading) return <span className="text-xs text-zinc-400">…</span>;
  const opts: Array<{ label: string; value: string }> = [];
  for (const c of channels) {
    if (c.models.length === 0) {
      opts.push({ label: c.name, value: "" });
      continue;
    }
    for (const m of c.models) {
      opts.push({ label: c.name + " / " + m.display_name, value: m.model_id });
    }
  }
  return (
    <select
      aria-label="批量创作模型"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="h-8 w-full rounded-lg border border-zinc-200 bg-white px-2 text-xs"
    >
      {opts.length === 0 && <option value="">（无可用模型）</option>}
      {opts.map((o) => (
        <option key={o.value + o.label} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}


const STATUS_TONE: Record<string, string> = {
  idle: "text-zinc-400",
  queued: "text-amber-600",
  generating: "text-amber-600",
  completed: "text-emerald-600",
  failed: "text-red-600",
};

function BatchPanelInner({ domain, onClose }: { domain: "image" | "video"; onClose: () => void }) {
  const [model, setModel] = useState("");
  const { rows, updatePrompt, add, remove, run, isRunning } = useBatchGeneration(domain, model);

  return (
    <div className="w-[560px] max-w-[92vw] rounded-2xl border border-zinc-200 bg-white p-4 shadow-xl">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold">
          <Table2 className="h-4 w-4" /> 批量创作表（{domain === "image" ? "生图" : "生视频"}）
        </h3>
        <div className="flex items-center gap-2">
          <div className="w-40">
            <BatchModelSelect domain={domain} value={model} onChange={setModel} />
          </div>
          <Button variant="ghost" size="sm" onClick={onClose}>
            关闭
          </Button>
        </div>
      </div>
      <div className="max-h-[50vh] space-y-2 overflow-y-auto pr-1">
        {rows.map((r) => (
          <div key={r.id} className="flex items-center gap-2">
            <span className={`w-16 shrink-0 text-xs ${STATUS_TONE[r.status]}`}>
              {r.status === "completed"
                ? "完成"
                : r.status === "failed"
                  ? "失败"
                  : r.status === "generating"
                    ? "生成中"
                    : r.status === "queued"
                      ? "排队"
                      : "待填"}
            </span>
            <input
              value={r.prompt}
              onChange={(e) => updatePrompt(r.id, e.target.value)}
              placeholder="提示词…"
              className="h-8 flex-1 rounded-lg border border-zinc-200 px-2 text-sm outline-none focus:border-zinc-400"
            />
            <button
              aria-label="删除行"
              onClick={() => remove(r.id)}
              className="text-zinc-400 hover:text-red-500 cursor-pointer"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        ))}
      </div>
      <div className="mt-3 flex items-center justify-between">
        <button
          onClick={add}
          className="flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-800 cursor-pointer"
        >
          <Plus className="h-3.5 w-3.5" /> 添加一行
        </button>
        <Button size="sm" onClick={run} disabled={isRunning || rows.every((r) => !r.prompt.trim())}>
          {isRunning
            ? "批量生成中…"
            : `批量生成（${rows.filter((r) => r.status === "idle" && r.prompt.trim()).length}）`}
        </Button>
      </div>
    </div>
  );
}

export function BatchTablePanel({ domain, onClose }: { domain: "image" | "video"; onClose: () => void }) {
  return (
    <Panel position="top-center">
      <ReactFlowProvider>
        <BatchPanelInner domain={domain} onClose={onClose} />
      </ReactFlowProvider>
    </Panel>
  );
}
