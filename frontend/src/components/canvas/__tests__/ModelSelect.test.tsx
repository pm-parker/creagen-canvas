import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ModelSelect } from "../ModelSelect";
import { apiFetch } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api")>();
  return { ...original, apiFetch: vi.fn() };
});
const mockedApiFetch = vi.mocked(apiFetch);

function renderSelect(domain = "llm") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ModelSelect domain={domain} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ModelSelect", () => {
  beforeEach(() => {
    mockedApiFetch.mockReset();
    localStorage.clear();
  });

  it("无渠道时显示去设置引导", async () => {
    mockedApiFetch.mockResolvedValue({ channels: [] });
    renderSelect();
    expect(await screen.findByText(/尚未配置渠道/)).toBeInTheDocument();
  });

  it("有渠道时渲染渠道与模型级联", async () => {
    mockedApiFetch.mockResolvedValue({
      channels: [
        {
          id: 1,
          domain: "llm",
          name: "主渠道",
          enabled: true,
          models: [
            { model_id: "gpt-x", display_name: "GPT-X", enabled: true },
            { model_id: "gpt-y", display_name: "GPT-Y", enabled: true },
          ],
        },
      ],
    });
    renderSelect();
    expect(await screen.findByLabelText("渠道选择-llm")).toBeInTheDocument();
    expect(screen.getByLabelText("模型选择-llm")).toBeInTheDocument();
  });
});
