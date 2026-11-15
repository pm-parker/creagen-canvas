import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MultiAngleDialog } from "../MultiAngleDialog";

vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn().mockResolvedValue({ task: { id: 1 } }),
}));

function renderDialog() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MultiAngleDialog nodeId="n1" nodeType="image" prompt="测试" onClose={() => undefined} />
    </QueryClientProvider>,
  );
}

describe("MultiAngleDialog（P1-2）", () => {
  afterEach(() => vi.restoreAllMocks());

  it("提交后 unmount 清理轮询 interval（修复前泄漏）", async () => {
    const user = userEvent.setup();
    const setSpy = vi.spyOn(globalThis, "setInterval");
    const clearSpy = vi.spyOn(globalThis, "clearInterval");
    const created: unknown[] = setSpy.mock.results.map((r) => r.value);

    const { unmount } = renderDialog();
    await user.click(screen.getByRole("button", { name: /生成 3 个角度/ }));
    await waitFor(() => {
      expect(setSpy).toHaveBeenCalled();
    });
    unmount();
    // 卸载必须清理组件创建的 interval 句柄
    for (const r of setSpy.mock.results) {
      if (!created.includes(r.value)) {
        expect(clearSpy).toHaveBeenCalledWith(r.value);
      }
    }
  });

  it("自定义角度输入框存在且可添加为预设", async () => {
    const user = userEvent.setup();
    renderDialog();
    const input = screen.getByLabelText("自定义角度");
    await user.type(input, "俯视45度侧面");
    await user.click(screen.getByRole("button", { name: "添加" }));
    expect(screen.getByText("俯视45度侧面").closest("button")).toHaveClass("bg-zinc-900"); // 默认选中态
  });
});
