import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsPage } from "../../SettingsPage";
import { apiFetch } from "@/lib/api";
import { useAuthStore } from "@/stores/auth";

vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api")>();
  return { ...original, apiFetch: vi.fn() };
});
const mockedApiFetch = vi.mocked(apiFetch);

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Routes>
          <Route path="/" element={<SettingsPage />} />
          <Route path="/login" element={<div>登录页</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function fillAndSubmit() {
  const user = userEvent.setup();
  renderPage();
  await user.type(await screen.findByLabelText("当前密码"), "{{SECRET_e1tq7gbe}}");
  await user.type(screen.getByLabelText("新密码"), "{{SECRET_v9er8p8r}}");
  await user.type(screen.getByLabelText("确认新密码"), "{{SECRET_v9er8p8r}}");
  await user.click(screen.getByRole("button", { name: "修改密码" }));
}

describe("SettingsPage 修改密码", () => {
  beforeEach(() => {
    mockedApiFetch.mockReset();
    useAuthStore.setState({
      token: "old-token",
      user: { id: 1, username: "u", email: "u@x.io", role: "user" },
    });
  });

  it("成功：存储响应中的新 token（会话延续）并显示成功提示", async () => {
    mockedApiFetch.mockResolvedValue({ access_token: "new-token", token_type: "bearer" });

    await fillAndSubmit();

    await waitFor(() => {
      expect(useAuthStore.getState().token).toBe("new-token");
    });
    expect(useAuthStore.getState().user?.username).toBe("u"); // 未被登出
    expect(await screen.findByText(/密码已修改/)).toBeInTheDocument();
    expect(screen.queryByText("登录页")).not.toBeInTheDocument(); // 未跳登录
  });

  it("原密码错误（400 WRONG_PASSWORD）：显示「原密码错误」且不清 token、不登出", async () => {
    mockedApiFetch.mockRejectedValue(
      Object.assign(new Error("原密码错误"), { code: "WRONG_PASSWORD", hint: "", status: 400 }),
    );

    await fillAndSubmit();

    expect(await screen.findByText("原密码错误")).toBeInTheDocument();
    expect(useAuthStore.getState().token).toBe("old-token"); // token 保留
    expect(useAuthStore.getState().user?.username).toBe("u");
    expect(screen.queryByText("登录页")).not.toBeInTheDocument();
  });
});
