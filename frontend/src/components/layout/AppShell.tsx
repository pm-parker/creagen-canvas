import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { useQuery } from "@tanstack/react-query";
import {
  Clapperboard,
  BookBookmark,
  FolderKanban,
  FolderOpen,
  House,
  Hexagon,
  Image,
  ListChecks,
  LogOut,
  Settings,
  SlidersHorizontal,
  Video,
  Wallet,
  X,
} from "lucide-react";
import { useAuthStore } from "@/stores/auth";
import { apiFetch } from "@/lib/api";
import { cn } from "@/lib/utils";

interface NavItem {
  to: string;
  label: string;
  sub: string;
  icon: React.ComponentType<{ className?: string }>;
}

interface NavGroup {
  group: string;
  items: NavItem[];
}

/** 按创作工作流分组：创作 → 内容 → 管理（与 0.16 侧边栏一致）。 */
const NAV_GROUPS: NavGroup[] = [
  {
    group: "创作",
    items: [
      { to: "/projects", label: "画布库", sub: "无限画布创作", icon: FolderKanban },
      { to: "/generate/image", label: "生图", sub: "文字生成图片", icon: Image },
      { to: "/generate/video", label: "生视频", sub: "提示词生成视频", icon: Video },
      { to: "/storyboards", label: "分镜", sub: "剧本到成片管线", icon: Clapperboard },
      { to: "/characters", label: "角色库", sub: "跨片角色资产", icon: Clapperboard },
    ],
  },
  {
    group: "内容",
    items: [
      { to: "/prompts", label: "提示词库", sub: "灵感与复用", icon: BookBookmark },
      { to: "/assets", label: "资产库", sub: "全部生成产物", icon: FolderOpen },
    ],
  },
  {
    group: "管理",
    items: [
      { to: "/tasks", label: "任务中心", sub: "队列与状态", icon: ListChecks },
      { to: "/costs", label: "成本看板", sub: "渠道花费统计", icon: Wallet },
      { to: "/settings/channels", label: "模型渠道", sub: "接入与配置", icon: Settings },
      { to: "/settings", label: "系统设置", sub: "主题与账号", icon: SlidersHorizontal },
    ],
  },
];

const FLAT_ITEMS = NAV_GROUPS.flatMap((g) => g.items);

/**
 * 激活判定：精确命中，或位于其子路径下且不落入更长的兄弟导航项。
 * （如 /settings/channels 命中「模型渠道」而不再命中「系统设置」。）
 */
const NAV_ACTIVE = (to: string, path: string) =>
  to === "/" ? path === "/" : path === to ? true : path.startsWith(`${to}/`) ? !FLAT_ITEMS.some((o) => o.to !== to && o.to.length > to.length && path.startsWith(o.to)) : false;

/** 当前部署的 bundle 文件名（来自页面里加载的 script）。 */
const currentBundleName = () =>
  document.querySelector<HTMLScriptElement>(`script[src*="assets/index-"]`)?.src?.split("/").pop() ?? "";

/** 抓取站点首页 HTML 里的最新 bundle 文件名。 */
async function fetchLatestBundleName(): Promise<string> {
  try {
    const html = await (await fetch(`/`, { cache: `no-store` })).text();
    return html.match(/assets\/(index-[\w-]+\.js)/)?.[1] ?? "";
  } catch {
    return "";
  }
}

const VERSION_CHECK_INTERVAL_MS = 300000;

/** 新版本横幅：页面可见时每 5 分钟对比 bundle 文件名，发现新版本提示刷新。 */
function NewVersionBanner() {
  const [stale, setStale] = useState(false);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    const current = currentBundleName();
    if (!current) return;
    let cancelled = false;
    const check = async () => {
      if (cancelled || document.visibilityState !== "visible") return;
      const latest = await fetchLatestBundleName();
      if (latest && latest !== current) setStale(true);
    };
    const timer = window.setInterval(() => void check(), VERSION_CHECK_INTERVAL_MS);
    const onVisible = () => {
      if (document.visibilityState === "visible") void check();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  if (!stale || dismissed) return null;

  return (
    <div
      data-testid="version-banner"
      className="fixed inset-x-0 top-0 z-[70] flex items-center justify-center gap-3 bg-amber-500 px-4 py-1.5 text-sm text-zinc-950 shadow-md"
    >
      <span>✨ 新版本已就绪，刷新后生效</span>
      <button
        onClick={() => window.location.reload()}
        className="rounded-md bg-zinc-950 px-3 py-0.5 text-xs font-medium text-amber-50 hover:bg-zinc-800 cursor-pointer"
      >
        立即刷新
      </button>
      <button
        onClick={() => setDismissed(true)}
        aria-label="暂缓提醒"
        className="text-xs text-zinc-800 underline underline-offset-2 hover:text-zinc-950 cursor-pointer"
      >
        稍后
      </button>
    </div>
  );
}

/** 侧边栏底部版本徽章：点击弹出当前版本与更新要点。 */
function VersionBadge() {
  const [open, setOpen] = useState(false);
  const { data } = useQuery({
    queryKey: ["app-version"],
    queryFn: () => apiFetch<{ version?: string }>("/api/health"),
    staleTime: Infinity,
  });
  const version = data?.version ?? "";

  return (
    <>
      <NewVersionBanner />
      <button
        onClick={() => setOpen(true)}
        title="当前版本与更新日志"
        className="mx-auto flex h-6 items-center rounded-full bg-zinc-100 px-2 text-[10px] font-medium text-zinc-500 hover:bg-zinc-200 hover:text-zinc-700 cursor-pointer group-hover/side:mx-0"
      >
        {version ? `v${version}` : "v?"}
      </button>
      {open && (
        <div
          role="dialog"
          aria-label="版本信息"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          onClick={() => setOpen(false)}
        >
          <div
            className="w-full max-w-md space-y-3 rounded-2xl bg-white p-6 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold">当前版本 {version ? `v${version}` : "未知"}</h3>
              <button
                onClick={() => setOpen(false)}
                className="text-zinc-400 hover:text-zinc-600 cursor-pointer"
                aria-label="关闭"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <p className="text-xs leading-relaxed text-zinc-500">
              开源自托管多用户 AI 创作画布。完整更新日志见仓库 CHANGELOG.md 与 GitHub Releases
              页面；升级后建议刷新页面加载最新前端资源。
            </p>
            <div className="rounded-xl bg-zinc-50 p-3 text-xs text-zinc-600">
              <p className="font-medium text-zinc-800">最近更新</p>
              <ul className="mt-1.5 list-disc space-y-1 pl-4">
                <li>画布只读分享与一键复制</li>
                <li>首页灵感起步墙（提示词库 9,000+ 效果图）</li>
                <li>视频字幕烧录 / 视频放大工具</li>
              </ul>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

/** 左侧窄边栏布局（hover 展开、分组导航、amber 高亮）：所有工作台页面统一包裹。 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);

  return (
    <div className="flex min-h-screen bg-zinc-50">
      <aside className="group/side sticky top-0 z-40 flex h-dvh w-14 shrink-0 flex-col items-center gap-1 overflow-x-hidden overflow-y-auto border-r border-zinc-200 bg-white py-4 transition-all duration-200 hover:w-48 hover:items-stretch hover:px-2">
        <Link to="/" className="mb-3 flex h-10 items-center px-2" aria-label="CreaGen Canvas 首页">
          <Hexagon className="h-7 w-7 shrink-0 text-zinc-900" />
          <span className="ml-2 hidden whitespace-nowrap text-sm font-semibold text-zinc-900 group-hover/side:inline">
            CreaGen Canvas
          </span>
        </Link>
        <Link
          to="/"
          data-nav-item="/"
          title="首页 · 工作台"
          className={cn(
            "flex h-10 w-10 items-center justify-center rounded-xl transition-colors group-hover/side:w-full group-hover/side:justify-start group-hover/side:px-3",
            NAV_ACTIVE("/", location.pathname)
              ? "bg-amber-500 text-zinc-950"
              : "text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800",
          )}
        >
          <House className="h-5 w-5 shrink-0" />
          <span className="ml-3 hidden whitespace-nowrap text-sm group-hover/side:inline">首页</span>
        </Link>
        {NAV_GROUPS.map(({ group, items }) => (
          <div
            key={group}
            className="flex w-full flex-col items-center group-hover/side:items-stretch"
          >
            <p className="hidden px-3 pb-1 pt-2 text-[10px] font-medium uppercase tracking-wider text-zinc-300 group-hover/side:block">
              {group}
            </p>
            {items.map(({ to, label, sub, icon: Icon }) => {
              const active = NAV_ACTIVE(to, location.pathname);
              return (
                <Link
                  key={to}
                  to={to}
                  data-nav-item={to}
                  title={`${label} · ${sub}`}
                  className={cn(
                    "flex h-10 w-10 items-center justify-center rounded-xl transition-colors group-hover/side:w-full group-hover/side:justify-start group-hover/side:px-3",
                    active
                      ? "bg-amber-500 text-zinc-950"
                      : "text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800",
                  )}
                >
                  <Icon className="h-5 w-5 shrink-0" />
                  <span className="ml-3 hidden min-w-0 flex-1 group-hover/side:block">
                    <span className="block truncate text-sm leading-tight">{label}</span>
                    <span
                      className={cn(
                        "block truncate text-[10px] leading-tight",
                        active ? "text-zinc-300" : "text-zinc-400",
                      )}
                    >
                      {sub}
                    </span>
                  </span>
                </Link>
              );
            })}
          </div>
        ))}
        <div className="mt-auto flex w-full flex-col items-center gap-2 group-hover/side:items-stretch">
          <VersionBadge />
          <button
            aria-label="退出登录"
            title={`退出 ${user?.username ?? ""}`}
            onClick={() => {
              logout();
              navigate("/login");
            }}
            className="flex h-10 w-10 items-center justify-center rounded-xl text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-600 group-hover/side:w-full group-hover/side:justify-start group-hover/side:px-3 cursor-pointer"
          >
            <LogOut className="h-5 w-5 shrink-0" />
            <span className="ml-3 hidden whitespace-nowrap text-sm group-hover/side:inline">
              退出登录
            </span>
          </button>
        </div>
      </aside>
      <main className="min-w-0 flex-1">{children}</main>
    </div>
  );
}
