import { describe, expect, it } from "vitest";
import { buildGenerationParams } from "../useGenerate";
import type { CanvasNode } from "@/stores/canvas";

function mkNode(id: string, nodeType: string, prompt: string, outputUrl?: string): CanvasNode {
  return {
    id,
    type: "canvas",
    position: { x: 0, y: 0 },
    data: {
      nodeType: nodeType as "text" | "image" | "video" | "audio",
      title: "",
      prompt,
      ...(outputUrl ? { outputUrl } : {}),
    } as CanvasNode["data"],
  };
}

describe("buildGenerationParams", () => {
  it("image：prompt + 模型；上游参考图 url 进 reference_urls", () => {
    const upstream = [mkNode("ref1", "image", "", "/outputs/a.png")];
    const body = buildGenerationParams(mkNode("n1", "image", "赛博朋克城市"), "flux-x", upstream);
    expect(body.type).toBe("image");
    expect(body.model).toBe("flux-x");
    expect(body.params.reference_urls).toEqual(["/outputs/a.png"]);
  });

  it("llm：prompt 为 messages 主体", () => {
    const body = buildGenerationParams(mkNode("n2", "llm", "写一段开头"), "gpt-x", []);
    expect(body.type).toBe("llm");
    expect(body.prompt).toBe("写一段开头");
  });

  it("video：带上游参考时 duration 默认 5", () => {
    const body = buildGenerationParams(mkNode("n3", "video", "奔跑的猫"), "minimax-h3", []);
    expect(body.type).toBe("video");
    expect(body.params.duration).toBe(5);
  });

  it("无 prompt 不允许提交（disabled 判定）", () => {
    const node = mkNode("n4", "image", "  ");
    expect(buildGenerationParams.canSubmit(node)).toBe(false);
    expect(buildGenerationParams.canSubmit(mkNode("n5", "image", "ok"))).toBe(true);
  });
});
