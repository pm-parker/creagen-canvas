import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download, Plus } from "lucide-react";
import { Link } from "react-router";
import { apiFetch } from "@/lib/api";
import { copyText } from "@/lib/clipboard";

interface ProjectMeta {
  id: number;
  title: string;
}

interface Asset {
  task_id: number;
  type: string;
  url: string;
  prompt: string;
  completed_at: string | null;
}

/** 追加一个资产节点到画布（保留既有节点：取详情 → 拼接 → 全量回写）。 */
async function appendAssetToCanvas(
  projectId: number,
  asset: { type: string; title: string; outputUrl: string; prompt: string },
): Promise<void> {
  const detail = await apiFetch<{
    project: {
      nodes: Array<{
        id: number;
        type: string;
        title: string | null;
        position: { x: number; y: number };
        metadata: Record<string, unknown>;
      }>;
    };
  }>(`/api/canvas/projects/${projectId}`);
  const existing = detail.project.nodes ?? [];
  const offsetX = existing.reduce((m, n) => Math.max(m, n.position.x), -220) + 280;
  const payload = [
    ...existing.map((n) => ({
      id: n.id,
      type: n.type,
      title: n.title ?? "",
      position_x: n.position.x,
      position_y: n.position.y,
      width: null,
      height: null,
      metadata_json: JSON.stringify({
        prompt: typeof n.metadata?.prompt === "string" ? n.metadata.prompt : "",
        outputUrl: typeof n.metadata?.outputUrl === "string" ? n.metadata.outputUrl : null,
        client_id: null,
      }),
    })),
    {
      id: null,
      type: asset.type === "video" ? "video" : "image",
      title: asset.title,
      position_x: offsetX,
      position_y: 0,
      width: null,
      height: null,
      metadata_json: JSON.stringify({ prompt: asset.prompt, outputUrl: asset.outputUrl }),
    },
  ];
  await apiFetch(`/api/canvas/projects/${projectId}/nodes`, { method: "PUT", json: { nodes: payload } });
}

export function AssetsPage() {
  const [copied, setCopied] = useState(-1);
  const [addTo, setAddTo] = useState<Asset | null>(null);
  const [addBusy, setAddBusy] = useState<number | null>(null);
  const [addedTo, setAddedTo] = useState<{ id: number; title: string } | null>(null);
  const projects = useQuery({
    queryKey: ["canvas", "projects"],
    queryFn: () => apiFetch<{ projects: ProjectMeta[] }>("/api/canvas/projects"),
  });

  async function doAdd(p: ProjectMeta) {
    if (!addTo) return;
    setAddBusy(p.id);
    try {
      await appendAssetToCanvas(p.id, {
        type: addTo.type,
        title: addTo.prompt || "资产",
        outputUrl: addTo.url,
        prompt: addTo.prompt,
      });
      setAddedTo({ id: p.id, title: p.title });
      setAddTo(null);
      setTimeout(() => setAddedTo(null), 4000);
    } finally {
      setAddBusy(null);
    }
  }
  const assets = useQuery({
    queryKey: ["assets"],
    queryFn: () => apiFetch<{ assets: Asset[]; total: number }>("/api/assets?limit=120"),
    refetchInterval: 30_000,
  });

  const list = assets.data?.assets ?? [];
  const images = list.filter((a) => a.type === "image");
  const others = list.filter((a) => a.type !== "image");

  return (
    <div className="min-h-screen bg-zinc-50">
      <header className="border-b border-zinc-200 bg-white px-8 py-6">
        <h1 className="text-xl font-semibold">资产库</h1>
        <p className="mt-1 text-sm text-zinc-500">你生成过的全部产物（共 {assets.data?.total ?? 0} 项）</p>
      </header>
      <main className="mx-auto max-w-6xl space-y-6 p-8">
        {assets.isLoading && <p className="text-sm text-zinc-500">加载中…</p>}
        {images.length > 0 && (
          <section>
            <h2 className="mb-3 text-sm font-medium text-zinc-500">图片</h2>
            <div className="columns-2 gap-4 lg:columns-4 [&>*]:mb-4">
              {images.map((a) => (
                <div
                  key={a.task_id}
                  className="group relative break-inside-avoid overflow-hidden rounded-xl border border-zinc-200 bg-white shadow-xs hover:shadow-md"
                >
                  <button
                    aria-label="加入画布"
                    title="加入画布"
                    onClick={() => setAddTo(a)}
                    className="absolute right-2 top-2 z-10 hidden items-center gap-1 rounded-lg bg-zinc-900/90 px-2.5 py-1.5 text-xs text-white cursor-pointer hover:bg-zinc-700 group-hover:flex"
                  >
                    <Plus className="h-3.5 w-3.5" /> 加入画布
                  </button>
                  <img
                    src={a.url}
                    alt={a.prompt}
                    loading="lazy"
                    onError={(e) => {
                      const el = e.target as HTMLImageElement;
                      el.style.display = "none";
                      const ph = el.nextElementSibling as HTMLElement | null;
                      if (ph) ph.style.display = "flex";
                    }}
                    className="w-full transition-transform group-hover:scale-[1.02]"
                  />
                  <div
                    style={{ display: "none" }}
                    className="aspect-square w-full flex-col items-center justify-center bg-zinc-100 text-xs text-zinc-400"
                  >
                    原图已过期
                  </div>
                  <a
                    href={a.url}
                    target="_blank"
                    rel="noreferrer"
                    aria-label="查看原图"
                    className="absolute inset-0"
                  />
                  <p className="truncate px-3 py-2 text-xs text-zinc-500">{a.prompt || "未命名"}</p>
                </div>
              ))}
            </div>
          </section>
        )}
        {others.length > 0 && (
          <section>
            <h2 className="mb-3 text-sm font-medium text-zinc-500">视频与文案</h2>
            <div className="space-y-2">
              {others.map((a) => {
                const isText = !/\.(mp4|webm|mov)(\?|$)/i.test(a.url);
                return (
                  <div
                    key={a.task_id}
                    className="flex items-center justify-between gap-3 rounded-lg border border-zinc-200 bg-white px-4 py-2.5"
                  >
                    <span className="min-w-0 flex-1 truncate text-sm">{a.prompt || a.url}</span>
                    {isText ? (
                      <button
                        className="flex shrink-0 cursor-pointer items-center gap-1 text-xs text-zinc-500 underline hover:text-zinc-700"
                        onClick={() => {
                          void copyText(a.url).then((ok) => {
                            setCopied(ok ? a.task_id : -1);
                            if (ok) setTimeout(() => setCopied(-1), 1500);
                          });
                        }}
                      >
                        {copied === a.task_id ? "已复制" : "复制全文"}
                      </button>
                    ) : (
                      <a
                        href={a.url}
                        download
                        target="_blank"
                        rel="noreferrer"
                        className="flex shrink-0 items-center gap-1 text-xs text-zinc-500 underline"
                      >
                        <Download className="h-3 w-3" /> 下载
                      </a>
                    )}
                  </div>
                );
              })}
            </div>
          </section>
        )}
        {!assets.isLoading && list.length === 0 && (
          <p className="text-sm text-zinc-400">还没有资产。去生图页或画布生成一些吧。</p>
        )}
      </main>

      {addTo && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-zinc-900/40"
          onClick={() => setAddTo(null)}
        >
          <div
            className="w-80 rounded-2xl bg-white p-5 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <p className="text-sm font-medium text-zinc-900">加入画布</p>
            <p className="mt-1 truncate text-xs text-zinc-400">{addTo.prompt || addTo.url}</p>
            <div className="mt-3 max-h-64 space-y-1 overflow-y-auto">
              {(projects.data?.projects ?? []).length === 0 && (
                <p className="py-4 text-center text-xs text-zinc-400">
                  还没有画布，去
                  <Link to="/projects" className="text-zinc-600 underline">
                    新建一个
                  </Link>
                  吧
                </p>
              )}
              {(projects.data?.projects ?? []).map((p) => (
                <button
                  key={p.id}
                  disabled={addBusy !== null}
                  onClick={() => doAdd(p)}
                  className="flex w-full cursor-pointer items-center justify-between rounded-lg px-3 py-2 text-left text-sm text-zinc-700 hover:bg-zinc-100 disabled:opacity-50"
                >
                  <span className="truncate">{p.title || "未命名画布"}</span>
                  {addBusy === p.id && <span className="text-xs text-zinc-400">加入中…</span>}
                </button>
              ))}
            </div>
            <button
              onClick={() => setAddTo(null)}
              className="mt-3 w-full cursor-pointer rounded-lg border border-zinc-200 py-1.5 text-xs text-zinc-500 hover:bg-zinc-50"
            >
              取消
            </button>
          </div>
        </div>
      )}

      {addedTo && (
        <div className="fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-full bg-zinc-900 px-4 py-2 text-xs text-white shadow-lg">
          已加入「{addedTo.title || "未命名画布"}」·{" "}
          <Link to={`/canvas/${addedTo.id}`} className="underline">
            打开画布
          </Link>
        </div>
      )}
    </div>
  );
}
