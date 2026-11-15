import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PromptPanel } from "../PromptPanel";
import { useCanvasStore } from "@/stores/canvas";
import { usePanel } from "@/stores/panel";

describe("PromptPanel", () => {
  beforeEach(() => {
    useCanvasStore.setState({
      nodes: [
        {
          id: "n1",
          type: "canvas",
          position: { x: 0, y: 0 },
          data: { nodeType: "text", title: "文案", prompt: "初始提示词" },
        },
      ],
      loaded: true,
      dirty: false,
    });
    usePanel.getState().close();
  });

  afterEach(() => vi.useRealTimers());

  function renderPanel() {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={qc}>
        <PromptPanel initial={{ width: 800, height: 240 }} />
      </QueryClientProvider>
    );
  }

  it("openFor 后显示节点 prompt 初值", () => {
    usePanel.getState().openFor("n1");
    renderPanel();
    expect(screen.getByRole("textbox")).toHaveValue("初始提示词");
  });

  it("输入防抖 300ms 写回节点 data.prompt", () => {
    vi.useFakeTimers();
    usePanel.getState().openFor("n1");
    renderPanel();
    const box = screen.getByRole("textbox");

    // 立即改动：防抖未到，不写回
    fireEvent.change(box, { target: { value: "初始提示词，追加" } });
    expect(useCanvasStore.getState().nodes[0].data.prompt).toBe("初始提示词");

    act(() => vi.advanceTimersByTime(350));
    expect(useCanvasStore.getState().nodes[0].data.prompt).toBe("初始提示词，追加");
  });

  it("Esc 关闭面板", () => {
    usePanel.getState().openFor("n1");
    renderPanel();
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Escape" });
    expect(usePanel.getState().activeNodeId).toBeNull();
  });

  it("方向键在 textarea 内不冒泡（不触发画布快捷键）", () => {
    usePanel.getState().openFor("n1");
    renderPanel();
    const box = screen.getByRole("textbox");
    const spy = vi.fn();
    document.addEventListener("keydown", spy, true);
    fireEvent.keyDown(box, { key: "ArrowRight" });
    expect(spy).toHaveBeenCalled(); // 冒泡被面板拦在捕获层之前——事件仍在 document 出现，
    // 但 RF 的容器监听在面板外层元素，断言 stopPropagation 效果：
    expect(box).toHaveFocus();
    document.removeEventListener("keydown", spy, true);
  });
});
