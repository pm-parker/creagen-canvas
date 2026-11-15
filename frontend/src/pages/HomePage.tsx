import { useCallback, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";
import { useQuery } from "@tanstack/react-query";
import {
  Image as ImageIcon,
  Video,
  BookMarked,
  FolderKanban,
  Zap,
  Sparkles,
  RefreshCw,
  AtSign,
  LoaderCircle,
  Send,
} from "lucide-react";
import { apiFetch, ApiError } from "@/lib/api";
import { MentionTextarea, type MentionOption } from "@/components/mentions/MentionTextarea";
import { MentionPickPanel } from "@/components/mentions/MentionPickPanel";
import { parseChangelog } from "@/components/ChangelogDrawer";
import changelogRaw from "@/assets/CHANGELOG.md?raw";

const QUICK = [
  { to: "/generate/image", label: "AI 生图", desc: "文字生成图片，直接出结果", icon: ImageIcon, tone: "bg-emerald-50 text-emerald-700" },
  { to: "/generate/video", label: "AI 生视频", desc: "提示词生成短视频", icon: Video, tone: "bg-indigo-50 text-indigo-700" },
  { to: "/prompts", label: "提示词库", desc: "收藏与复用优质提示词", icon: BookMarked, tone: "bg-amber-50 text-amber-700" },
  { to: "/projects", label: "画布", desc: "多节点编排与连续迭代", icon: FolderKanban, tone: "bg-indigo-50 text-indigo-600" },
];

/** 首页精选模型（每个域按 pattern 优先，turbo 标 ✦快）。 */
const FEATURED_PATTERNS = [
  { domain: "image", patterns: [/Z-Image/i] },
  { domain: "video", patterns: [/Hailuo-2\.3/i, /M2\.7/i] },
];

interface ChannelMeta {
  id: number;
  domain: string;
  enabled: boolean;
  models: { model_id: string; display_name?: string | null; enabled: boolean }[];
}

interface ProjectMeta { id: number; title: string; updated_at: string | null }
interface TaskMeta { id: number; type: string; status: string; prompt: string; output_url?: string | null; created_at: string | null }
interface PromptMeta { id: number; title?: string | null; category?: string | null; content: string; thumb_url?: string | null }
interface AgentRunMeta {
  id: number;
  project_id: number;
  status: keyof typeof AGENT_STATUS_LABELS;
  step_count: number;
  max_steps: number;
  completion?: { done: number; total: number } | null;
}

const AGENT_TERMINAL_STATUSES = ["completed", "failed", "stopped", "incomplete"];
const AGENT_STATUS_LABELS: Record<string, string> = {
  queued: "排队中",
  planning: "思考中",
  waiting: "等待生成",
  completed: "已完成",
  failed: "失败",
  stopped: "已停止",
  incomplete: "未完成 · 可续跑",
};

/** 输出 URL 校验：仅允许本站静态路径与 http(s) 链接，防 open redirect。 */
function isSafeOutputUrl(url: string | null | undefined): url is string {
  if (!url) return false;
  return url.startsWith("/static/") || /^https?:\/\//i.test(url);
}

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

/** 灵感卡跳转：视频类目去生视频页，其余去生图页，带 prompt 预填。 */
function inspireHref(p: PromptMeta): string {
  const domain = (p.category ?? "").startsWith("video") ? "video" : "image";
  return `/generate/${domain}?prompt=${encodeURIComponent(p.content)}&pid=${p.id}`;
}

function clipText(text: string, limit: number): string {
  const normalized = text.replace(/\s+/g, " ").trim();
  return normalized.length > limit ? normalized.slice(0, limit - 1) + "…" : normalized;
}

/** JSON 串里挖第一个像正文的长字符串字段。 */
function extractTextDeep(value: unknown, depth: number): string | undefined {
  if (depth > 4 || typeof value !== "object" || !value) {
    return typeof value === "string" && value.trim().length >= 8 ? value.trim() : undefined;
  }
  const keys = ["prompt", "generative_prompt", "description", "text", "content", "title"];
  const obj = value as Record<string, unknown>;
  for (const key of keys) {
    const v = obj[key];
    if (typeof v === "string" && v.trim().length >= 8) return v.trim();
  }
  for (const v of Object.values(obj)) {
    const found = extractTextDeep(v, depth + 1);
    if (found) return found;
  }
  return undefined;
}

/** 灵感卡副标题：优先纯文本，JSON 串则提取可读片段。 */
function summarizeContent(content: string | null, limit = 80): string {
  const raw = (content ?? "").trim();
  if (!raw) return "";
  if (!raw.startsWith("{") && !raw.startsWith("[")) return clipText(raw, limit);
  try {
    const extracted = extractTextDeep(JSON.parse(raw), 0);
    if (extracted) return clipText(extracted, limit);
  } catch {
    /* fallthrough */
  }
  return clipText(raw.replace(/[{}[\]"\\]/g, " ").replace(/\s+/g, " ").trim(), limit);
}

function startOfWeek(date: Date): Date {
  const day = date.getDay();
  const mondayOffset = day === 0 ? 6 : day - 1;
  const d = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  d.setDate(d.getDate() - mondayOffset);
  return d;
}

/** 本周（周一起）生成数量。 */
function countThisWeek(createdAts: (string | null)[], now: Date): number {
  const weekStart = startOfWeek(now).getTime();
  return createdAts.filter((iso) => {
    if (!iso) return false;
    const t = new Date(iso).getTime();
    return !Number.isNaN(t) && t >= weekStart;
  }).length;
}

/** 精选模型 chips：image/video 各取一个匹配渠道模型。 */
function featuredModels(channels: ChannelMeta[], limit = 3) {
  const out: { domain: string; modelId: string; label: string; badge?: string }[] = [];
  for (const { domain, patterns } of FEATURED_PATTERNS) {
    const models = channels
      .filter((ch) => ch.enabled && ch.domain === domain)
      .flatMap((ch) => ch.models.filter((m) => m.enabled));
    if (models.length === 0) continue;
    const matched = patterns.map((re) => models.find((m) => re.test(m.model_id))).filter((m) => m !== undefined);
    for (const m of matched.length > 0 ? matched : [models[0]]) {
      out.push({
        domain,
        modelId: m.model_id,
        label: m.display_name || m.model_id,
        badge: /turbo/i.test(m.model_id) ? "✦快" : undefined,
      });
    }
  }
  return out.slice(0, limit);
}

async function startAgentRun(message: string, mentions: MentionOption[]) {
  return apiFetch<{ project_id: number; run_id: number }>("/api/agent-runs", {
    method: "POST",
    json: { message, mentions },
  });
}

/** 首页自由创作输入区：@ 引用 + 提及 chip + Agent 启动。 */
function AgentComposer() {
  const navigate = useNavigate();
  const [value, setValue] = useState("");
  const [picked, setPicked] = useState<MentionOption[]>([]);
  const [panelOpen, setPanelOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<{ code: string; message: string } | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const runsQuery = useQuery({
    queryKey: ["agent-runs", "active"],
    queryFn: () => apiFetch<{ runs: AgentRunMeta[] }>("/api/agent-runs"),
    staleTime: 10000,
  });
  const activeRun = (runsQuery.data?.runs ?? []).find((r) => !AGENT_TERMINAL_STATUSES.includes(r.status));

  const insertMention = useCallback(
    (option: MentionOption) => {
      setPanelOpen(false);
      if (picked.some((p) => p.label === option.label)) return;
      const el = textareaRef.current;
      const snippet = `${option.label} `;
      if (el) {
        const cursor = el.selectionStart ?? value.length;
        let at = value.lastIndexOf("@", Math.max(0, cursor - 1));
        if (at === -1 || cursor - at > 24) at = cursor;
        const next = value.slice(0, at) + snippet + value.slice(cursor);
        setValue(next);
        requestAnimationFrame(() => {
          el.focus();
          const pos = at + snippet.length;
          el.setSelectionRange(pos, pos);
        });
      } else {
        setValue(value + snippet);
      }
      setPicked((prev) => [...prev, option]);
    },
    [picked, value],
  );

  const submit = async () => {
    const message = value.trim();
    if (!message || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const mentions = picked.filter((p) => message.includes(p.label));
      const run = await startAgentRun(message, mentions);
      navigate(`/canvas/${run.project_id}?agent=${run.run_id}`);
    } catch (e) {
      const apiError = e instanceof ApiError ? e : null;
      setError({
        code: apiError?.code ?? "SUBMIT_FAILED",
        message: apiError?.message ?? "提交失败，请重试",
      });
      runsQuery.refetch();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section aria-label="自由创作" className="rounded-2xl border border-zinc-200 bg-white p-4 shadow-sm">
      <div className="mb-2 flex items-center gap-2 text-sm font-medium text-zinc-700">
        <Sparkles className="h-4 w-4 text-indigo-500" />
        自由创作
        <span className="text-xs font-normal text-zinc-400">说想要什么，Agent 自动规划并生成到画布</span>
      </div>
      <div className="relative">
        {panelOpen && <MentionPickPanel onPick={insertMention} picked={picked.map((p) => p.label)} />}
        <div className="rounded-xl bg-zinc-50 transition-colors focus-within:bg-white">
          <MentionTextarea
            ref={textareaRef}
            value={value}
            onChange={(v) => {
              setValue(v);
              if (v.endsWith("@")) setPanelOpen(true);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void submit();
              if (e.key === "Escape") setPanelOpen(false);
            }}
            rows={3}
            mentions={picked.map((p) => p.label)}
            placeholder="描述你想创作的内容… 输入 @ 引用素材/角色/提示词/模型（⌘/Ctrl+Enter 发送）"
          />
        </div>
        <div className="mt-2 flex items-center justify-between">
          <button
            type="button"
            onClick={() => setPanelOpen((v) => !v)}
            className={`flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs transition-colors ${panelOpen ? "bg-indigo-50 text-indigo-600" : "text-zinc-500 hover:bg-zinc-50"}`}
          >
            <AtSign className="h-3.5 w-3.5" /> 引用
          </button>
          <button
            type="button"
            onClick={() => void submit()}
            disabled={!value.trim() || submitting}
            className="flex items-center gap-1.5 rounded-lg bg-zinc-900 px-4 py-2 text-sm text-white transition-colors hover:bg-zinc-700 disabled:opacity-40"
          >
            {submitting ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
            开始创作
          </button>
        </div>
      </div>
      {activeRun && (
        <Link
          to={`/canvas/${activeRun.project_id}?agent=${activeRun.id}`}
          className="mt-3 flex items-center justify-between rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700 hover:bg-amber-100"
        >
          <span>
            有进行中的创作（{AGENT_STATUS_LABELS[activeRun.status]}
            {activeRun.completion ? ` · 交付 ${activeRun.completion.done}/${activeRun.completion.total}` : ""} · 第 {activeRun.step_count}/{activeRun.max_steps} 步）——点击查看
          </span>
          <Send className="h-3 w-3" />
        </Link>
      )}
      {error && (
        <p className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600">
          {error.message}
          {error.code === "LLM_CHANNEL_REQUIRED" && "（请先在「设置 → 模型渠道」配置 LLM 渠道）"}
        </p>
      )}
    </section>
  );
}

const SKELETON_HEIGHTS = [220, 180, 260, 200, 240, 160, 280, 190];

export function HomePage() {
  const navigate = useNavigate();
  const [inspireSeed, setInspireSeed] = useState(0);

  const projects = useQuery({
    queryKey: ["canvas", "projects"],
    queryFn: () => apiFetch<{ projects: ProjectMeta[] }>("/api/canvas/projects"),
    staleTime: 30000,
  });
  const tasks = useQuery({
    queryKey: ["tasks", "recent"],
    queryFn: () => apiFetch<{ tasks: TaskMeta[] }>("/api/tasks?limit=200"),
    staleTime: 15000,
    refetchInterval: 10000,
  });
  const inspire = useQuery({
    queryKey: ["inspire", inspireSeed],
    queryFn: () => apiFetch<{ prompts: PromptMeta[]; total: number }>("/api/prompts?has_thumb=true&sort=random&limit=12"),
    staleTime: 300000,
  });
  const inspirePrompts = useMemo(() => inspire.data?.prompts ?? [], [inspire.data]);
  const refreshInspire = useCallback(() => setInspireSeed((s) => s + 1), []);

  const channels = useQuery({
    queryKey: ["providers", "channels"],
    queryFn: () => apiFetch<{ channels: ChannelMeta[] }>("/api/providers/channels"),
    staleTime: 30000,
    retry: false,
  });
  const featured = useMemo(() => featuredModels(channels.data?.channels ?? []), [channels.data]);
  const weeklyCount = useMemo(
    () => (tasks.data && projects.data ? countThisWeek(tasks.data.tasks.map((t) => t.created_at), new Date()) : null),
    [tasks.data, projects.data],
  );
  const changelog = useMemo(() => parseChangelog(changelogRaw, 3), []);

  const recentTasks = (tasks.data?.tasks ?? []).filter((t) => t.status === "completed" && t.output_url).slice(0, 12);
  const mediaTasks = recentTasks.filter((t) => /\.(png|jpe?g|webp|mp4|mov|webm)(\?|$)/i.test(t.output_url ?? ""));

  return (
    <div className="min-h-screen bg-zinc-50">
      <header className="flex items-center justify-between border-b border-zinc-200 bg-white px-8 py-6">
        <div>
          <h1 className="text-2xl font-semibold text-zinc-900">工作台</h1>
          <p className="mt-1 flex items-center gap-2 text-sm text-zinc-500">
            从一个想法到成片
            {weeklyCount !== null && projects.data && (
              <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-xs text-emerald-600">
                本周生成 {weeklyCount} 张 · {projects.data.projects.length} 个画布
              </span>
            )}
          </p>
        </div>
        <Link
          to="/projects"
          className="flex items-center gap-1.5 rounded-lg bg-zinc-900 px-4 py-2 text-sm text-white hover:bg-zinc-700"
        >
          <FolderKanban className="h-4 w-4" /> 新建画布
        </Link>
      </header>
      <main className="mx-auto max-w-6xl space-y-8 p-8">
        <AgentComposer />
        <section>
          <h2 className="mb-3 text-sm font-medium text-zinc-500">快速开始</h2>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {QUICK.map(({ to, label, desc, icon: Icon, tone }) => (
              <Link
                key={to}
                to={to}
                className="group rounded-xl border border-zinc-200 bg-white p-4 transition-all hover:-translate-y-0.5 hover:shadow-md"
              >
                <span className={`inline-flex h-8 w-8 items-center justify-center rounded-lg ${tone}`}>
                  <Icon className="h-4 w-4" />
                </span>
                <p className="mt-2.5 text-sm font-medium text-zinc-900">{label}</p>
                <p className="mt-0.5 text-xs text-zinc-500">{desc}</p>
              </Link>
            ))}
          </div>
          {featured.length > 0 && (
            <div aria-label="精选模型" className="mt-3 flex flex-wrap items-center gap-2">
              <span className="flex items-center gap-1 text-xs text-zinc-500">
                <Zap className="h-3.5 w-3.5" /> 精选模型
              </span>
              {featured.map((m) => (
                <Link
                  key={`${m.domain}:${m.modelId}`}
                  to={`/generate/${m.domain}?model=${encodeURIComponent(m.modelId)}`}
                  className="flex items-center gap-1.5 rounded-full border border-zinc-200 bg-white px-3 py-1 text-xs text-zinc-600 transition-colors hover:border-indigo-300 hover:bg-indigo-50 hover:text-indigo-600"
                >
                  {m.domain === "image" ? <ImageIcon className="h-3 w-3" /> : <Video className="h-3 w-3" />}
                  {m.label}
                  {m.badge && <span className="rounded bg-amber-100 px-1 text-[10px] text-amber-600">{m.badge}</span>}
                </Link>
              ))}
            </div>
          )}
        </section>
        <section>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="flex items-center gap-1.5 text-sm font-medium text-zinc-500">
              <Sparkles className="h-4 w-4 text-amber-500" /> 灵感起步
              {inspire.data && (
                <span className="text-xs text-zinc-300">（库内 {inspire.data.total.toLocaleString()} 条）</span>
              )}
            </h2>
            <button
              onClick={refreshInspire}
              disabled={inspire.isFetching}
              className="flex cursor-pointer items-center gap-1 rounded-lg bg-zinc-100 px-2.5 py-1.5 text-xs text-zinc-600 hover:bg-zinc-200 disabled:opacity-50"
              aria-label="换一批灵感"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${inspire.isFetching ? "animate-spin" : ""}`} /> 换一批
            </button>
          </div>
          {inspire.isLoading ? (
            <div className="columns-2 gap-3 md:columns-3 xl:columns-4">
              {SKELETON_HEIGHTS.map((h, i) => (
                <div key={i} style={{ height: h }} className="mb-3 animate-pulse rounded-xl bg-zinc-200/60" />
              ))}
            </div>
          ) : inspirePrompts.length > 0 ? (
            <div className="columns-2 gap-3 md:columns-3 xl:columns-4 [&>*]:mb-3">
              {inspirePrompts.map((p) => (
                <Link
                  key={p.id}
                  to={inspireHref(p)}
                  className="group block break-inside-avoid overflow-hidden rounded-xl border border-zinc-200 bg-white transition-all hover:-translate-y-0.5 hover:shadow-md"
                  title={p.content}
                >
                  {p.thumb_url ? (
                    <img src={p.thumb_url} alt={p.title ?? "灵感效果图"} className="max-h-140 w-full object-cover" loading="lazy" />
                  ) : null}
                  <div className="p-2.5">
                    <p className="truncate text-xs font-medium text-zinc-900">{p.title ?? "未命名"}</p>
                    <p className="mt-0.5 line-clamp-1 text-xs text-zinc-500">{summarizeContent(p.content, 60)}</p>
                    <p className="mt-1.5 flex items-center gap-1 text-xs text-indigo-500 transition-opacity group-hover:opacity-80">
                      <Sparkles className="h-3 w-3" /> 套用这个创意 →
                    </p>
                  </div>
                </Link>
              ))}
            </div>
          ) : (
            <div className="flex flex-col items-center gap-2 rounded-2xl border border-dashed border-zinc-200 bg-white/60 py-10 text-center">
              <p className="text-sm text-zinc-400">灵感库还是空的——先去生成第一张图</p>
              <Link to="/generate/image" className="text-sm text-indigo-500 underline">
                去生图 →
              </Link>
            </div>
          )}
        </section>
        {changelog.length > 0 && (
          <section>
            <h2 className="mb-3 text-sm font-medium text-zinc-500">最近上新</h2>
            <div className="flex gap-3 overflow-x-auto pb-1">
              {changelog.map((e) => (
                <div key={e.version} className="min-w-64 max-w-72 shrink-0 rounded-xl border border-zinc-200 bg-white p-4">
                  <div className="flex items-center gap-2">
                    <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-xs font-medium text-indigo-600">v{e.version}</span>
                    {e.date && <span className="text-xs text-zinc-500">{e.date}</span>}
                  </div>
                  {e.summary && <p className="mt-2 line-clamp-2 text-xs text-zinc-500">{e.summary}</p>}
                </div>
              ))}
            </div>
          </section>
        )}
        <section>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-medium text-zinc-500">最近画布</h2>
            <Link to="/projects" className="inline-flex min-h-6 items-center text-xs text-zinc-500 hover:text-zinc-900">
              全部画布 →
            </Link>
          </div>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            {(projects.data?.projects ?? []).slice(0, 3).map((p) => (
              <button
                key={p.id}
                onClick={() => navigate(`/canvas/${p.id}`)}
                className="cursor-pointer rounded-xl border border-zinc-200 bg-white p-4 text-left transition-all hover:-translate-y-0.5 hover:shadow-md"
              >
                <p className="truncate text-sm font-medium text-zinc-900">{p.title}</p>
                <p className="mt-1 text-xs text-zinc-500">{timeAgo(p.updated_at)}编辑</p>
              </button>
            ))}
          </div>
          {(projects.data?.projects ?? []).length === 0 && (
            <p className="text-sm text-zinc-400">暂无画布，去创建一个吧</p>
          )}
        </section>
        <section>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-medium text-zinc-500">最近生成</h2>
            <Link to="/tasks" className="inline-flex min-h-6 items-center text-xs text-zinc-500 hover:text-zinc-900">
              任务中心 →
            </Link>
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6">
            {mediaTasks.map((t) => {
              const outputUrl = isSafeOutputUrl(t.output_url) ? t.output_url : null;
              if (!outputUrl) return null;
              const isVideo = /\.(mp4|webm|mov)(\?|$)/i.test(outputUrl);
              const isImage = /\.(png|jpe?g|webp)(\?|$)/i.test(outputUrl);
              return (
                <button
                  key={t.id}
                  onClick={() => {
                    if (isSafeOutputUrl(outputUrl)) window.open(outputUrl, "_blank");
                  }}
                  className="group relative aspect-square cursor-pointer overflow-hidden rounded-lg border border-zinc-200 bg-zinc-100 transition-all hover:-translate-y-0.5 hover:shadow-md dark:border-zinc-800 dark:bg-zinc-900"
                  title={t.prompt}
                >
                  {isImage ? (
                    <img src={outputUrl} alt="" loading="lazy" className="h-full w-full object-cover" />
                  ) : (
                    <video src={`${outputUrl}#t=0.5`} preload="metadata" muted className="h-full w-full object-cover" />
                  )}
                  <span className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/70 to-transparent p-2 opacity-0 transition-opacity group-hover:opacity-100">
                    <span className="line-clamp-2 text-left text-xs text-white">{t.prompt}</span>
                    <span className="mt-1 flex items-center gap-2 text-[10px] text-zinc-300">
                      <span>{isVideo ? "视频" : "图片"}</span>
                      <span>·</span>
                      <span>{timeAgo(t.created_at)}</span>
                    </span>
                  </span>
                  {isVideo && (
                    <span className="absolute top-1.5 right-1.5 rounded bg-black/60 px-1 py-0.5 text-[10px] text-white">▶</span>
                  )}
                </button>
              );
            })}
          </div>
          {mediaTasks.length === 0 && (
            <p className="text-sm text-zinc-400">还没有生成——去画布或生图页创作第一张吧</p>
          )}
          {(tasks.data?.tasks ?? []).length === 0 && <p className="text-sm text-zinc-400">还没有生成记录</p>}
        </section>
      </main>
    </div>
  );
}
