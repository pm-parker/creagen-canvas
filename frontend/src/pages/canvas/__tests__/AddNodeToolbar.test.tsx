import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, beforeEach } from "vitest";
import { AddNodeToolbar } from "../AddNodeToolbar";
import { useCanvasStore } from "@/stores/canvas";

describe("AddNodeToolbar", () => {
  beforeEach(() => {
    useCanvasStore.getState().reset();
    useCanvasStore.getState().setLoaded(true);
  });

  it("点击四类按钮分别在视口中心添加节点", async () => {
    const user = userEvent.setup();
    render(<AddNodeToolbar />);

    await user.click(screen.getByTestId("add-node-text"));
    await user.click(screen.getByTestId("add-node-image"));
    await user.click(screen.getByTestId("add-node-video"));
    await user.click(screen.getByTestId("add-node-audio"));

    const s = useCanvasStore.getState();
    expect(s.nodes).toHaveLength(4);
    expect(s.nodes.map((n) => n.data.nodeType)).toEqual(["text", "image", "video", "audio"]);
    expect(s.dirty).toBe(true);
  });
});
