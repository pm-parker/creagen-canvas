import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AdjustDialog } from "../AdjustDialog";
import { useCanvasStore } from "@/stores/canvas";

let uploadSeq = 0;
vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn().mockImplementation(async () => ({ url: `/outputs/uploads/fake-${++uploadSeq}.png` })),
}));

describe("AdjustDialog", () => {
  it("拖动亮度滑杆 → 预览 filter 同步；重置清空", () => {
    render(<AdjustDialog nodeId="n1" src="/outputs/images/a.png" onClose={() => undefined} />);
    const preview = screen.getByTestId("adjust-preview") as HTMLImageElement;
    expect(preview.style.filter).toBe(""); // 默认无 filter

    fireEvent.change(screen.getByLabelText("亮度"), { target: { value: "130" } });
    expect(preview.style.filter).toContain("brightness(1.3)");

    fireEvent.change(screen.getByLabelText("色温（暖）"), { target: { value: "40" } });
    expect(preview.style.filter).toContain("sepia(0.4)");

    fireEvent.click(screen.getByRole("button", { name: "重置" }));
    expect(preview.style.filter).toBe("");
  });

  it("默认（未调整）时重置禁用", () => {
    render(<AdjustDialog nodeId="n1" src="/outputs/images/a.png" onClose={() => undefined} />);
    expect(screen.getByRole("button", { name: "重置" })).toBeDisabled();
  });

  it("导出：烘焙上传 → 新节点 + 连线", async () => {
    // jsdom 无 canvas 实现：stub getContext/toDataURL，只验证接线（上传→新节点→连线）
    const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    const origGetContext = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.toDataURL = vi.fn(() => "data:image/png;base64,x");
    HTMLCanvasElement.prototype.getContext = vi.fn(() => ({
      filter: "none",
      drawImage: vi.fn(),
    })) as unknown as HTMLCanvasElement["getContext"];
    const before = useCanvasStore.getState().nodes.length;
    render(<AdjustDialog nodeId="n1" src="/outputs/images/a.png" onClose={() => undefined} />);
    fireEvent.change(screen.getByLabelText("亮度"), { target: { value: "110" } });
    fireEvent.click(screen.getByRole("button", { name: /导出为新节点/ }));
    await vi.waitFor(() => {
      expect(useCanvasStore.getState().nodes.length).toBe(before + 1);
    });
    const added = useCanvasStore.getState().nodes[useCanvasStore.getState().nodes.length - 1];
    expect(added.data.outputUrl).toMatch(/^\/outputs\/uploads\/fake-\d+\.png$/);
    expect(added.data.title).toContain("调整");
    expect(useCanvasStore.getState().edges.some((e) => e.source === "n1" && e.target === added.id)).toBe(true);
    HTMLCanvasElement.prototype.toDataURL = origToDataURL;
    HTMLCanvasElement.prototype.getContext = origGetContext;
  });
});

describe("AdjustDialog 批量模式（P1-4）", () => {
  it("批量导出：逐目标新节点+连线+汇总", async () => {
    // jsdom 无 canvas：stub 导出链路所需接口
    const origGetContext = HTMLCanvasElement.prototype.getContext;
    const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    const OrigImage = window.Image;
    HTMLCanvasElement.prototype.getContext = vi.fn(() => ({ filter: "none", drawImage: vi.fn() })) as unknown as HTMLCanvasElement["getContext"];
    HTMLCanvasElement.prototype.toDataURL = vi.fn(() => "data:image/png;base64,x");
    // jsdom 不加载真实图片：stub Image 让 onload 立即触发
    class FakeImage {
      onload: (() => void) | null = null;
      naturalWidth = 100;
      naturalHeight = 100;
      src = "";
      constructor() {
        setTimeout(() => this.onload?.(), 0);
      }
    }
    vi.stubGlobal("Image", FakeImage);

    // 预置两个源节点
    const store = useCanvasStore.getState();
    const idA = store.addNode("image", { x: 0, y: 0 });
    store.updateNodeData(idA, { outputUrl: "/outputs/a.png" });
    const idB = store.addNode("image", { x: 500, y: 0 });
    store.updateNodeData(idB, { outputUrl: "/outputs/b.png" });

    const before = useCanvasStore.getState().nodes.length;
    render(
      <AdjustDialog
        nodeId={idA}
        src="/outputs/a.png"
        batchTargets={[
          { nodeId: idA, src: "/outputs/a.png" },
          { nodeId: idB, src: "/outputs/b.png" },
        ]}
        onClose={() => undefined}
      />,
    );
    fireEvent.change(screen.getByLabelText("亮度"), { target: { value: "110" } });
    fireEvent.click(screen.getByRole("button", { name: /导出 2 张/ }));
    await vi.waitFor(() => {
      expect(screen.getByText(/已导出 2 张/)).toBeInTheDocument();
    });
    expect(useCanvasStore.getState().nodes.length).toBe(before + 2);
    const added = useCanvasStore.getState().nodes.slice(-2);
    expect(added.map((n) => n.data.title)).toEqual(["批量调整", "批量调整"]);

    HTMLCanvasElement.prototype.getContext = origGetContext;
    HTMLCanvasElement.prototype.toDataURL = origToDataURL;
    vi.unstubAllGlobals();
    window.Image = OrigImage;
  });
});
