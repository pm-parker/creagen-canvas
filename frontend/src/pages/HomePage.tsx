import { Link } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { Image as ImageIcon, Video, BookMarked, FolderKanban, ArrowRight } from "lucide-react";
import { apiFetch } from "@/lib/api";

const QUICK = [
  { to: "/generate/image", label: "AI 生图", desc: "文字生成图片，直接出结果", icon: ImageIcon, tone: "bg-emerald-50 text-emerald-700" },
  { to: "/generate/video", label: "AI 生视频", desc: "提示词生成短视频", icon: Video, tone: "bg-indigo-50 text-indigo-700" },
  { to: "/prompts", label: "提示词库", desc: "收藏与复用优质提示词", icon: BookMarked, tone: "bg-amber-50 text-amber-700" },
  { to: "/projects", label: "画布", desc: "多节点编排与连续迭代", icon: FolderKanban, tone: "bg-indigo-50 text-indigo-600" },
];

interface ProjectMeta { id: number; title: string; updated_at: string | null }
interface TaskMeta { id: number; type: string; status: string; prompt: string; created_at: string | null }

function timeAgo(iso: string | null): string {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "刚刚";
  if (m < 60) return `${m} 分钟前`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} 小时前`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d} 天前`;
  return new Date(iso).toLocaleDateString("zh-CN");
}

export function HomePage() {
  const projects = useQuery({
    queryKey: ["canvas", "projects"],
    queryFn: () => apiFetch<{ projects: ProjectMeta[] }>("/api/canvas/projects"),
  });
  const tasks = useQuery({
    queryKey: ["tasks", "recent"],
    queryFn: () => apiFetch<{ tasks: TaskMeta[] }>("/api/tasks?limit=6"),
    refetchInterval: 10_000,
  });

  return (
    <div className="min-h-screen bg-zinc-50">
      <header className="border-b border-zinc-200 bg-white px-8 py-6">
        <h1 className="text-2xl font-semibold text-zinc-900">工作台</h1>
        <p className="mt-1 text-sm text-zinc-500">AI 创作画布 —— 从一句话开始，到完整作品</p>
      </header>
      <main className="mx-auto max-w-6xl space-y-8 p-8">
        <section>
          <h2 className="mb-3 text-sm font-medium text-zinc-500">快速开始</h2>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {QUICK.map(({ to, label, desc, icon: Icon, tone }) => (
              <Link
                key={to}
                to={to}
                className="group rounded-2xl border border-zinc-200 bg-white p-5 shadow-xs transition-all hover:-translate-y-0.5 hover:shadow-md"
              >
                <span className={`mb-3 inline-flex h-10 w-10 items-center justify-center rounded-xl ${tone}`}>
                  <Icon className="h-5 w-5" />
                </span>
                <p className="font-medium text-zinc-900">{label}</p>
                <p className="mt-1 text-xs text-zinc-500">{desc}</p>
                <ArrowRight className="mt-3 h-4 w-4 text-zinc-300 transition-transform group-hover:translate-x-1 group-hover:text-zinc-500" />
              </Link>
            ))}
          </div>
        </section>

        <section>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-medium text-zinc-500">最近画布</h2>
            <Link to="/projects" className="text-xs text-zinc-500 hover:text-zinc-900">全部画布 →</Link>
          </div>
          {projects.data?.projects?.length ? (
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              {projects.data.projects.slice(0, 4).map((p) => (
                <Link key={p.id} to={`/canvas/${p.id}`} className="rounded-xl border border-zinc-200 bg-white p-4 hover:shadow-sm">
                  <p className="truncate text-sm font-medium">{p.title}</p>
                  <p className="mt-1 text-xs text-zinc-400">{timeAgo(p.updated_at)}</p>
                </Link>
              ))}
            </div>
          ) : (
            <p className="text-sm text-zinc-400">暂无画布，去创建一个吧</p>
          )}
        </section>

        <section>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-medium text-zinc-500">最近生成</h2>
            <Link to="/tasks" className="text-xs text-zinc-500 hover:text-zinc-900">任务中心 →</Link>
          </div>
          {(tasks.data?.tasks?.length ?? 0) > 0 ? (
            <div className="space-y-2">
              {tasks.data!.tasks.slice(0, 5).map((t) => (
                <div key={t.id} className="flex items-center gap-3 rounded-lg border border-zinc-200 bg-white px-4 py-2.5">
                  <span className={`h-2 w-2 rounded-full ${t.status === "completed" ? "bg-emerald-500" : t.status === "failed" ? "bg-red-500" : "bg-amber-400"}`} />
                  <span className="max-w-[40%] truncate text-sm">{t.prompt}</span>
                  <span className="text-xs text-zinc-400">{t.type}</span>
                  <span
                    className={`ml-auto text-xs ${
                      t.status === "failed" ? "text-red-500" : t.status === "completed" ? "text-emerald-600" : "text-zinc-400"
                    }`}
                  >
                    {t.status === "completed" ? "已完成" : t.status === "failed" ? "失败" : t.status === "generating" ? "生成中" : "排队中"}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-zinc-400">还没有生成记录</p>
          )}
        </section>
      </main>
    </div>
  );
}
