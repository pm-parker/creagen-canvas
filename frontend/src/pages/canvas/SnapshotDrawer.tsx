import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { History } from "lucide-react";
import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api";

interface SnapshotMeta {
  id: number;
  version: number;
  label: string;
  created_at: string | null;
}

/** 画布版本历史抽屉：列表 / 恢复（对标 BeefTV 版本记录）。 */
export function SnapshotDrawer({
  projectId,
  onRestored,
}: {
  projectId: string;
  onRestored: () => void;
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const snapshots = useQuery({
    queryKey: ["snapshots", projectId],
    queryFn: () =>
      apiFetch<{ snapshots: SnapshotMeta[] }>(`/api/canvas/projects/${projectId}/snapshots`),
    enabled: open,
  });

  const restore = useMutation({
    mutationFn: (sid: number) =>
      apiFetch(`/api/canvas/projects/${projectId}/snapshots/${sid}/restore`, { method: "POST" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["canvas", "project", projectId] });
      onRestored();
      setOpen(false);
    },
  });

  return (
    <div className="relative">
      <button
        aria-label="版本历史"
        data-testid="snapshot-drawer-button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 rounded-full bg-white/95 px-2.5 py-1 text-xs text-zinc-600 shadow-sm hover:bg-zinc-50 cursor-pointer"
      >
        <History className="h-3.5 w-3.5" /> 历史
      </button>
      {open && (
        <div className="absolute right-0 top-8 z-20 w-72 rounded-xl border border-zinc-200 bg-white p-3 shadow-xl" data-testid="snapshot-list">
          <p className="mb-2 text-xs font-medium text-zinc-500">
            版本历史（保留最近 20 份，恢复前自动备份当前）
          </p>
          {snapshots.isLoading && <p className="text-xs text-zinc-400">加载中…</p>}
          {(snapshots.data?.snapshots ?? []).length === 0 && (
            <p className="text-xs text-zinc-400">还没有版本。画布有改动并保存后自动累积。</p>
          )}
          <div className="max-h-64 space-y-1 overflow-y-auto">
            {(snapshots.data?.snapshots ?? []).map((s) => (
              <div key={s.id} className="flex items-center justify-between rounded-lg px-2 py-1.5 hover:bg-zinc-50">
                <div className="min-w-0">
                  <p className="truncate text-xs text-zinc-800">
                    v{s.version} · {s.label}
                  </p>
                  <p className="text-[10px] text-zinc-400">{s.created_at?.slice(0, 16).replace("T", " ")}</p>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={busy}
                  onClick={async () => {
                    setBusy(true);
                    await restore.mutateAsync(s.id);
                    setBusy(false);
                  }}
                >
                  恢复
                </Button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
