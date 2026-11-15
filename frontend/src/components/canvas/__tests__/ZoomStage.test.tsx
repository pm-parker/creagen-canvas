import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ZoomStage } from "../ZoomStage";

describe("ZoomStage", () => {
  it("放大/缩小按钮改变缩放并显示百分比", async () => {
    const user = userEvent.setup();
    render(
      <ZoomStage>
        <img alt="" />
      </ZoomStage>,
    );
    const percent = screen.getByTestId("zoom-stage-percent");
    expect(percent.textContent).toBe("100%");

    await user.click(screen.getByLabelText("放大"));
    expect(percent.textContent).toBe("125%");
    await user.click(screen.getByLabelText("缩小"));
    expect(percent.textContent).toBe("100%");
  });

  it("滚轮上滚放大且 transform 应用到视口", () => {
    render(
      <ZoomStage>
        <img alt="" />
      </ZoomStage>,
    );
    const stage = screen.getByTestId("zoom-stage");
    fireEvent.wheel(stage, { deltaY: -120 });
    const viewport = screen.getByTestId("zoom-stage-viewport");
    expect(viewport.style.transform).toContain("scale(1.14"); // 1.25^0.6 ≈ 1.143
    fireEvent.wheel(stage, { deltaY: 120 });
    expect(screen.getByTestId("zoom-stage-percent").textContent).toBe("100%");
  });

  it("重置恢复 100% 与零平移；到达边界时按钮禁用", async () => {
    const user = userEvent.setup();
    render(
      <ZoomStage>
        <img alt="" />
      </ZoomStage>,
    );
    // 连点放大到 4x 上限
    for (let i = 0; i < 10; i++) await user.click(screen.getByLabelText("放大"));
    expect(screen.getByLabelText("放大")).toBeDisabled();
    await user.click(screen.getByLabelText("重置缩放"));
    expect(screen.getByTestId("zoom-stage-percent").textContent).toBe("100%");
    expect(screen.getByTestId("zoom-stage-viewport").style.transform).toBe(
      "translate(0px, 0px) scale(1)",
    );
  });
});
