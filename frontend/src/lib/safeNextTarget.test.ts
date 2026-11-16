import { describe, expect, it } from "vitest";
import { safeNextTarget } from "./safeNextTarget";

describe("safeNextTarget（开放重定向防护）", () => {
  it("无 next → 默认首页", () => {
    expect(safeNextTarget(null)).toBe("/");
    expect(safeNextTarget("")).toBe("/");
  });

  it("合法同源相对路径原样返回（含查询串）", () => {
    expect(safeNextTarget("/projects")).toBe("/projects");
    expect(safeNextTarget("/share/abc?x=1&y=2")).toBe("/share/abc?x=1&y=2");
  });

  it("拒绝协议相对与绝对 URL → 回退首页", () => {
    expect(safeNextTarget("//evil.com/phish")).toBe("/");
    expect(safeNextTarget("/\\evil.com")).toBe("/");
    expect(safeNextTarget("https://evil.com")).toBe("/");
    expect(safeNextTarget("evil.com")).toBe("/");
  });
});
