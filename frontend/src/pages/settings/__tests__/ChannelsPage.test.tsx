import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ChannelsPage } from "../ChannelsPage";
import { apiFetch } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api")>();
  return { ...original, apiFetch: vi.fn() };
});
const mockedApiFetch = vi.mocked(apiFetch);

const MOCK_CHANNELS = [
  {
    id: 1,
    domain: "llm",
    name: "主力大模型",
    base_url: "https://api.example.com/v1",
    key_masked: "sk-***5678",
    enabled: true,
    sort_order: 0,
    models: [],
  },
];

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ChannelsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ChannelsPage", () => {
  beforeEach(() => {
    mockedApiFetch.mockReset();
    mockedApiFetch.mockResolvedValue({ channels: MOCK_CHANNELS });
  });

  it("渲染渠道列表（掩码 key + 域徽标）", async () => {
    renderPage();
    expect(await screen.findByText("主力大模型")).toBeInTheDocument();
    expect(screen.getByText("sk-***5678")).toBeInTheDocument();
    expect(screen.getByText("LLM")).toBeInTheDocument();
  });

  it("新建对话框提交完整字段", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("主力大模型");
    await user.click(screen.getByRole("button", { name: /新建渠道/ }));

    await user.selectOptions(screen.getByLabelText("渠道域"), "image");
    await user.type(screen.getByLabelText("名称"), "生图渠道");
    await user.type(screen.getByLabelText("Base URL"), "https://img.example.com/v1");
    await user.type(screen.getByLabelText("API Key"), "sk-new-key-9999");
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(mockedApiFetch).toHaveBeenCalledWith(
        "/api/providers/channels",
        expect.objectContaining({
          method: "POST",
          json: expect.objectContaining({ domain: "image", key: "sk-new-key-9999" }),
        }),
      ),
    );
  });

  it("新建对话框：勾选支持图像编辑时提交 capabilities=[edit]", async () => {
    const user = userEvent.setup();
    mockedApiFetch.mockImplementation((path: string) => {
      if (path === "/api/providers/channels") return Promise.resolve({ channels: MOCK_CHANNELS });
      return Promise.resolve({});
    });

    renderPage();
    await screen.findByText("主力大模型");
    await user.click(screen.getByRole("button", { name: /新建渠道/ }));

    await user.selectOptions(screen.getByLabelText("渠道域"), "image");
    await user.type(screen.getByLabelText("名称"), "重绘渠道");
    await user.type(screen.getByLabelText("Base URL"), "https://img.example.com/v1");
    await user.type(screen.getByLabelText("API Key"), "sk-ed-8888");
    await user.click(screen.getByLabelText("支持图像编辑"));
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(mockedApiFetch).toHaveBeenCalledWith(
        "/api/providers/channels",
        expect.objectContaining({
          json: expect.objectContaining({ capabilities: ["edit"] }),
        }),
      ),
    );
  });

  it("连通性测试展示成功延迟", async () => {
    const user = userEvent.setup();
    mockedApiFetch
      .mockResolvedValueOnce({ channels: MOCK_CHANNELS })
      .mockResolvedValueOnce({ ok: true, latency_ms: 233, models_found: 12 });

    renderPage();
    await screen.findByText("主力大模型");
    await user.click(screen.getByRole("button", { name: /测试连通/ }));

    expect(await screen.findByText(/233ms/)).toBeInTheDocument();
  });

  it("连通性测试失败展示错误与提示", async () => {
    const user = userEvent.setup();
    mockedApiFetch
      .mockResolvedValueOnce({ channels: MOCK_CHANNELS })
      .mockResolvedValueOnce({ ok: false, code: "PROVIDER_UNREACHABLE", message: "无法连接" });

    renderPage();
    await screen.findByText("主力大模型");
    await user.click(screen.getByRole("button", { name: /测试连通/ }));
    expect(await screen.findByText(/无法连接/)).toBeInTheDocument();
  });

  it("BUG-A 回归：编辑渠道保存必须走 PATCH（后端仅注册 patch，PUT 会 405）", async () => {
    const user = userEvent.setup();
    mockedApiFetch.mockImplementation((path: string) => {
      if (path === "/api/providers/channels") return Promise.resolve({ channels: MOCK_CHANNELS });
      return Promise.resolve({});
    });

    renderPage();
    await screen.findByText("主力大模型");
    await user.click(screen.getByRole("button", { name: "编辑渠道" }));

    await user.clear(screen.getByLabelText("渠道名称"));
    await user.type(screen.getByLabelText("渠道名称"), "改名渠道");
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => {
      const call = mockedApiFetch.mock.calls.find(
        (c) => typeof c[0] === "string" && c[0] === "/api/providers/channels/1",
      );
      expect(call).toBeDefined();
      expect((call![1] as { method?: string }).method).toBe("PATCH");
      expect((call![1] as { json?: Record<string, unknown> }).json).toMatchObject({
        name: "改名渠道",
      });
    });
  });
});
