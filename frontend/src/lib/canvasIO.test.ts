import { describe, expect, it } from "vitest";
import { buildExport, parseImport } from "./canvasIO";

const SAMPLE = {
  version: 1 as const,
  app: "creagen-canvas" as const,
  exported_at: "2026-09-27T00:00:00Z",
  project: { title: "画布A", viewport: { x: 1, y: 2, zoom: 1.2 } },
  nodes: [
    { id: "a", type: "text", title: "A", position: { x: 0, y: 0 }, prompt: "p-a" },
    { id: "b", type: "image", title: "B", position: { x: 100, y: 0 }, prompt: "p-b", outputUrl: "/outputs/b.png" },
  ],
  connections: [{ source: "a", target: "b" }],
};

describe("画布导入导出", () => {
  it("导出结构完整且可回读", () => {
    const exported = buildExport("画布A", { x: 1, y: 2, zoom: 1.2 }, [
      { id: "a", position: { x: 0, y: 0 }, data: { nodeType: "text", title: "A", prompt: "p-a" } },
      { id: "b", position: { x: 100, y: 0 }, data: { nodeType: "image", title: "B", prompt: "p-b", outputUrl: "/outputs/b.png" } },
    ] as never as Parameters<typeof buildExport>[2], [{ id: "e1", source: "a", target: "b" }]);
    const json = JSON.stringify(exported);
    const parsed = parseImport(json);
    expect(parsed.title).toBe("画布A");
    expect(parsed.nodes).toHaveLength(2);
    expect(parsed.nodes[1].data.outputUrl).toBe("/outputs/b.png");
    expect(parsed.edges).toHaveLength(1);
  });

  it("parseImport 拒绝非本应用文件", () => {
    expect(() => parseImport(JSON.stringify({ hello: 1 }))).toThrow(/不是有效/);
  });

  it("导入时悬空连线被丢弃", () => {
    const raw = JSON.stringify({
      ...SAMPLE,
      connections: [{ source: "a", target: "ghost" }],
    });
    const parsed = parseImport(raw);
    expect(parsed.edges).toHaveLength(0);
  });

  it("导出→解析→再导出 幂等", () => {
    const round1 = buildExport("T", { x: 0, y: 0, zoom: 1 }, [
      { id: "x", position: { x: 1, y: 2 }, data: { nodeType: "video", title: "V", prompt: "v" } },
    ] as never as Parameters<typeof buildExport>[2], []);
    const parsed = parseImport(JSON.stringify(round1));
    const round2 = buildExport(parsed.title, parsed.viewport, parsed.nodes, parsed.edges);
    expect(round2.nodes).toEqual(round1.nodes);
  });
});
