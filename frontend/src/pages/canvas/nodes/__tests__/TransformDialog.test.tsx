import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TransformDialog } from "../TransformDialog";

describe("TransformDialog（P1-1）", () => {
  it("右转一次 90°，四次回到 0°（修复前 +4 兜底缺 %4 → 450°）", () => {
    render(<TransformDialog nodeId="n1" src="/outputs/images/a.png" onClose={() => undefined} />);
    const preview = screen.getByTestId("transform-preview");
    expect(preview.style.transform).toContain("rotate(0deg)");
    fireEvent.click(screen.getByLabelText("右转90度"));
    expect(preview.style.transform).toContain("rotate(90deg)");
    fireEvent.click(screen.getByLabelText("右转90度"));
    expect(preview.style.transform).toContain("rotate(180deg)");
    fireEvent.click(screen.getByLabelText("右转90度"));
    expect(preview.style.transform).toContain("rotate(270deg)");
    fireEvent.click(screen.getByLabelText("右转90度"));
    expect(preview.style.transform).toContain("rotate(0deg)");
  });

  it("左转从 0 回绕到 270°；翻转与重置", () => {
    render(<TransformDialog nodeId="n1" src="/outputs/images/a.png" onClose={() => undefined} />);
    const preview = screen.getByTestId("transform-preview");
    fireEvent.click(screen.getByLabelText("左转90度"));
    expect(preview.style.transform).toContain("rotate(270deg)");
    fireEvent.click(screen.getByLabelText("水平翻转"));
    expect(preview.style.transform).toContain("scaleX(-1)");
    fireEvent.click(screen.getByLabelText("重置变换"));
    expect(preview.style.transform).toBe("rotate(0deg) scaleX(1) scaleY(1)");
  });
});
