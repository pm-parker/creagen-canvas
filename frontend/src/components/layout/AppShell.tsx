import { Link, useLocation, useNavigate } from "react-router";
import { Home, ImageIcon, Video, BookMarked, ListChecks, FolderKanban, Wallet, FolderOpen, Settings, LogOut, Hexagon } from "lucide-react";
import { useAuthStore } from "@/stores/auth";
import { cn } from "@/lib/utils";

// 按创作工作流排序：首页 → 画布(核心) → 快捷生成 → 素材 → 治理
const NAV = [
  { to: "/", label: "首页", icon: Home },
  { to: "/projects", label: "画布库", icon: FolderKanban },
  { to: "/generate/image", label: "生图", icon: ImageIcon },
  { to: "/generate/video", label: "生视频", icon: Video },
  { to: "/prompts", label: "提示词库", icon: BookMarked },
  { to: "/assets", label: "资产库", icon: FolderOpen },
  { to: "/tasks", label: "任务中心", icon: ListChecks },
  { to: "/costs", label: "成本看板", icon: Wallet },
  { to: "/settings/channels", label: "模型渠道", icon: Settings },
];

const NAV_ACTIVE = (to: string, path: string) =>
  to === "/" ? path === "/" : path.startsWith(to);

/** 左侧窄边栏布局：所有工作台页面统一包裹。 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);

  return (
    <div className="flex min-h-screen bg-zinc-50">
      <aside className="group/side flex w-14 shrink-0 flex-col items-center gap-1 overflow-hidden border-r border-zinc-200 bg-white py-4 transition-all duration-200 hover:w-44 hover:items-stretch hover:px-2">
        <Link to="/" className="mb-4 flex h-10 items-center px-2" aria-label="CreaGen Canvas 首页">
          <Hexagon className="h-7 w-7 shrink-0 text-zinc-900" />
          <span className="ml-2 hidden whitespace-nowrap text-sm font-semibold text-zinc-900 group-hover/side:inline">
            CreaGen Canvas
          </span>
        </Link>
        {NAV.map(({ to, label, icon: Icon }) => {
          const active = NAV_ACTIVE(to, location.pathname);
          return (
            <Link
              key={to}
              to={to}
              title={label}
              className={cn(
                "flex h-10 w-10 items-center justify-center rounded-xl transition-colors group-hover/side:w-full group-hover/side:justify-start group-hover/side:px-3",
                active
                  ? "bg-zinc-900 text-white"
                  : "text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800",
              )}
            >
              <Icon className="h-5 w-5 shrink-0" />
              <span className="ml-3 hidden whitespace-nowrap text-sm group-hover/side:inline">
                {label}
              </span>
            </Link>
          );
        })}
        <div className="mt-auto w-full">
          <button
            aria-label="退出登录"
            title={`退出 ${user?.username ?? ""}`}
            onClick={() => {
              logout();
              navigate("/login");
            }}
            className="flex h-10 w-10 items-center justify-center rounded-xl text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800 cursor-pointer group-hover/side:w-full group-hover/side:justify-start group-hover/side:px-3"
          >
            <LogOut className="h-5 w-5 shrink-0" />
            <span className="ml-3 hidden whitespace-nowrap text-sm group-hover/side:inline">
              退出登录
            </span>
          </button>
        </div>
      </aside>
      <div className="flex-1 min-w-0">{children}</div>
    </div>
  );
}
