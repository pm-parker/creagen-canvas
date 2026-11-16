import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { useAuthStore } from "@/stores/auth";
import { LoginPage } from "../LoginPage";

vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api")>();
  return { ...original, apiFetch: vi.fn() };
});

import { apiFetch } from "@/lib/api";
const mockedApiFetch = vi.mocked(apiFetch);

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={["/login"]}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/" element={<div>项目库首页</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("LoginPage", () => {
  beforeEach(() => {
    mockedApiFetch.mockReset();
    useAuthStore.setState({ token: null, user: null });
  });

  it("登录成功：存 token、拉取用户、跳转项目库", async () => {
    const user = userEvent.setup();
    mockedApiFetch.mockImplementation((path) => {
      if (path === "/api/auth/login") {
        return Promise.resolve({ access_token: "tok-1", token_type: "bearer" });
      }
      if (path === "/api/auth/me") {
        return Promise.resolve({ id: 1, username: "bob", email: "b@x.io", role: "user" });
      }
      return Promise.reject(new Error("unexpected " + String(path)));
    });

    renderLogin();
    await user.type(screen.getByLabelText("用户名或邮箱"), "bob");
    await user.type(screen.getByLabelText("密码"), "password123");
    await user.click(screen.getByRole("button", { name: "登录" }));

    await waitFor(() => expect(screen.getByText("项目库首页")).toBeInTheDocument());
    expect(useAuthStore.getState().token).toBe("tok-1");
    expect(useAuthStore.getState().user?.username).toBe("bob");
  });

  it("登录失败显示后端 message 且不跳转", async () => {
    const user = userEvent.setup();
    mockedApiFetch.mockRejectedValue(
      Object.assign(new Error("用户名或密码错误"), { code: "AUTH_INVALID", hint: "", status: 401 }),
    );

    renderLogin();
    await user.type(screen.getByLabelText("用户名或邮箱"), "bob");
    await user.type(screen.getByLabelText("密码"), "wrong");
    await user.click(screen.getByRole("button", { name: "登录" }));

    expect(await screen.findByText("用户名或密码错误")).toBeInTheDocument();
    expect(screen.queryByText("项目库首页")).not.toBeInTheDocument();
  });

  it("登录 401 不触发全局登出：apiFetch 豁免 /api/auth/login（见 api.test.ts）", async () => {
    // 与 api.test.ts 的「登录 401 不触发登出」互为表里：这里钉住 UI 行为——
    // 登录页 401 后不会设置任何 token。
    const user = userEvent.setup();
    mockedApiFetch.mockRejectedValue(
      Object.assign(new Error("用户名或密码错误"), { code: "AUTH_INVALID", hint: "", status: 401 }),
    );

    renderLogin();
    await user.type(screen.getByLabelText("用户名或邮箱"), "bob");
    await user.type(screen.getByLabelText("密码"), "wrong");
    await user.click(screen.getByRole("button", { name: "登录" }));

    expect(await screen.findByText("用户名或密码错误")).toBeInTheDocument();
    expect(useAuthStore.getState().token).toBeNull();
  });

  it("?next= 回跳：登录成功后跳到来源路径（含查询串）", async () => {
    const user = userEvent.setup();
    mockedApiFetch.mockImplementation((path) => {
      if (path === "/api/auth/login") {
        return Promise.resolve({ access_token: "tok-2", token_type: "bearer" });
      }
      if (path === "/api/auth/me") {
        return Promise.resolve({ id: 1, username: "bob", email: "b@x.io", role: "user" });
      }
      return Promise.reject(new Error("unexpected " + String(path)));
    });

    render(
      <MemoryRouter initialEntries={["/login?next=%2Fshare%2Fab12%3Fx%3D1"]}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/share/:token" element={<div>分享页</div>} />
        </Routes>
      </MemoryRouter>,
    );
    await user.type(screen.getByLabelText("用户名或邮箱"), "bob");
    await user.type(screen.getByLabelText("密码"), "password123");
    await user.click(screen.getByRole("button", { name: "登录" }));

    await waitFor(() => expect(screen.getByText("分享页")).toBeInTheDocument());
  });

  it("?next=//evil.com 拒绝：仍跳默认首页，防开放重定向", async () => {
    const user = userEvent.setup();
    mockedApiFetch.mockImplementation((path) => {
      if (path === "/api/auth/login") {
        return Promise.resolve({ access_token: "tok-3", token_type: "bearer" });
      }
      if (path === "/api/auth/me") {
        return Promise.resolve({ id: 1, username: "bob", email: "b@x.io", role: "user" });
      }
      return Promise.reject(new Error("unexpected " + String(path)));
    });

    render(
      <MemoryRouter initialEntries={["/login?next=%2F%2Fevil.com%2Fphish"]}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<div>项目库首页</div>} />
          <Route path="*" element={<div>其它页</div>} />
        </Routes>
      </MemoryRouter>,
    );
    await user.type(screen.getByLabelText("用户名或邮箱"), "bob");
    await user.type(screen.getByLabelText("密码"), "password123");
    await user.click(screen.getByRole("button", { name: "登录" }));

    await waitFor(() => expect(screen.getByText("项目库首页")).toBeInTheDocument());
  });
});
