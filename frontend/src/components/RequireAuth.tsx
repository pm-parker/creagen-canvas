import { Navigate, Outlet, useLocation } from "react-router";
import { AppShell } from "@/components/layout/AppShell";
import { useAuthStore } from "@/stores/auth";

/** 未登录访问受保护路由 → 跳登录并记录来源；登录后回跳。 */
export function RequireAuth() {
  const token = useAuthStore((s) => s.token);
  const location = useLocation();

  if (!token) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }
  return (
    <AppShell>
      <Outlet />
    </AppShell>
  );
}
