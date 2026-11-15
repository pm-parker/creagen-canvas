import { test, expect } from "@playwright/test";

/** P1-1 回归：变换与导出——旋转预览 + 导出新节点。 */
test("变换与导出：右转90预览与导出", async ({ page }) => {
  const T = Date.now();
  await page.goto("/login");
  await page.getByLabel("用户名").fill(process.env.CANVAS_E2E_USER ?? "demo");
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForTimeout(1500);

  await page.goto("/projects");
  await page.getByRole("button", { name: "新建画布" }).click();
  await expect(page.getByTestId("canvas-editor")).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(1500);
  await page.getByTestId("add-node-image").click();
  await page.waitForTimeout(500);
  await page.locator('[data-testid="canvas-node-image"]').first().click();
  const panel = page.getByTestId("prompt-panel");
  await expect(panel).toBeVisible({ timeout: 8000 });
  await panel.getByTestId("prompt-textarea").fill(`变换回归 ${T}`);
  await panel.getByTestId("composer-generate").click();
  await expect(
    page.locator('[data-testid="canvas-node-image"]').first().getByLabel("生成完成"),
  ).toBeVisible({ timeout: 60_000 });

  const node = page.locator('[data-testid="canvas-node-image"]:has(img)').first();
  await node.scrollIntoViewIfNeeded();
  await node.getByRole("button", { name: "加工工具" }).click();
  await page.getByRole("button", { name: "变换与导出…" }).dispatchEvent("click");
  await expect(page.getByText("变换与导出")).toBeVisible({ timeout: 8000 });

  // 右转 90 → 预览 transform 更新
  await page.getByLabel("右转90度").click();
  const preview = page.getByTestId("transform-preview");
  await expect(preview).toHaveAttribute("style", expect.stringContaining("rotate(90deg)"));

  // 导出 JPG + 质量滑杆可见
  await page.getByRole("button", { name: "JPG" }).click();
  await expect(page.getByLabel("导出质量")).toBeVisible();

  const count = await page.locator('[data-testid="canvas-node-image"]').count();
  await page.getByRole("button", { name: /导出为新节点/ }).click();
  await expect(
    page.locator('[data-testid="canvas-node-image"]', { hasText: "变换 · 旋转90°" }).first(),
  ).toBeVisible({ timeout: 15_000 });
  expect(await page.locator('[data-testid="canvas-node-image"]').count()).toBe(count + 1);
  test.setTimeout(150_000);
});
