import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, beforeEach } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { RequireAuth } from "../RequireAuth";
import { useAuthStore } from "@/stores/auth";

/** 登录路由探针：暴露当前 location 的 pathname+search，用于断言 next 参数。 */
function LoginProbe() {
  const location = useLocation();
  return (
    <div>
      登录页
      <span data-testid="login-pathname">{location.pathname}</span>
      <span data-testid="login-search">{location.search}</span>
    </div>
  );
}

function renderAt(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route element={<RequireAuth />}>
            <Route path="/projects" element={<div>受保护内容</div>} />
          </Route>
          <Route path="/login" element={<LoginProbe />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("RequireAuth 未登录重定向", () => {
  beforeEach(() => {
    useAuthStore.setState({ token: null, user: null });
  });

  it("重定向 /login 并携带 next=当前路径+查询串", () => {
    renderAt("/projects?tab=all");

    expect(screen.getByText("登录页")).toBeInTheDocument();
    expect(screen.queryByText("受保护内容")).not.toBeInTheDocument();
    expect(screen.getByTestId("login-pathname").textContent).toBe("/login");
    expect(screen.getByTestId("login-search").textContent).toBe(
      `?next=${encodeURIComponent("/projects?tab=all")}`,
    );
  });

  it("已登录（有 token）正常渲染子路由", () => {
    useAuthStore.setState({ token: "tok", user: null });
    renderAt("/projects");
    expect(screen.getByText("受保护内容")).toBeInTheDocument();
  });
});
