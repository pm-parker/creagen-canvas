import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectsPage } from "../ProjectsPage";
import { useAuthStore } from "@/stores/auth";
import { apiFetch } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api")>();
  return { ...original, apiFetch: vi.fn() };
});
const mockedApiFetch = vi.mocked(apiFetch);

const MOCK_PROJECTS = [
  { id: 1, title: "新品发布短片", updated_at: "2026-09-27T10:00:00" },
  { id: 2, title: "知识科普条漫", updated_at: "2026-09-26T10:00:00" },
];

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ProjectsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ProjectsPage", () => {
  beforeEach(() => {
    mockedApiFetch.mockReset();
    useAuthStore.setState({ token: "tok", user: { id: 1, username: "u", email: "e@x", role: "user" } });
  });

  it("渲染项目卡片", async () => {
    mockedApiFetch.mockResolvedValue({ projects: MOCK_PROJECTS });
    renderPage();
    expect(await screen.findByText("新品发布短片")).toBeInTheDocument();
    expect(screen.getByText("知识科普条漫")).toBeInTheDocument();
  });

  it("新建画布：直接 POST 默认标题并刷新列表", async () => {
    const user = userEvent.setup();
    mockedApiFetch
      .mockResolvedValueOnce({ projects: MOCK_PROJECTS })
      .mockResolvedValueOnce({ project: { id: 3, title: "未命名画布" } })
      .mockResolvedValueOnce({ projects: [...MOCK_PROJECTS, { id: 3, title: "未命名画布" }] });

    renderPage();
    await screen.findByText("新品发布短片");
    await user.click(screen.getByRole("button", { name: /新建画布/ }));

    await waitFor(() =>
      expect(mockedApiFetch).toHaveBeenCalledWith(
        "/api/canvas/projects",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    expect(await screen.findByText("未命名画布")).toBeInTheDocument();
  });

  it("删除需两段确认", async () => {
    const user = userEvent.setup();
    mockedApiFetch
      .mockResolvedValueOnce({ projects: MOCK_PROJECTS })
      .mockResolvedValue({ projects: [] });

    renderPage();
    await screen.findByText("新品发布短片");
    await user.click(screen.getAllByLabelText("删除")[0]);
    // 第一段点击只出现确认按钮，未发 DELETE（允许初始 GET + 可能的 refetch，但不得出现 DELETE）
    const deleteCalls = mockedApiFetch.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === "DELETE");
    expect(deleteCalls).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() =>
      expect(mockedApiFetch).toHaveBeenCalledWith(
        "/api/canvas/projects/1",
        expect.objectContaining({ method: "DELETE" }),
      ),
    );
  });

  it("改名：inline 提交 PUT", async () => {
    const user = userEvent.setup();
    mockedApiFetch
      .mockResolvedValueOnce({ projects: MOCK_PROJECTS })
      .mockResolvedValue({ project: { id: 1, title: "改名后" } });

    renderPage();
    await screen.findByText("新品发布短片");
    await user.click(screen.getAllByLabelText("重命名")[0]);
    const input = screen.getByDisplayValue("新品发布短片");
    await user.clear(input);
    await user.type(input, "改名后");
    await user.keyboard("{Enter}");
    await waitFor(() =>
      expect(mockedApiFetch).toHaveBeenCalledWith(
        "/api/canvas/projects/1",
        expect.objectContaining({ method: "PUT" }),
      ),
    );
  });
});
