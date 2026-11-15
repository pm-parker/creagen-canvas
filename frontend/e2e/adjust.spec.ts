import { test, expect } from "@playwright/test";

/** P0-3 回归：图像调整面板——滑杆实时预览 → 导出新节点（纯前端加工全链路）。 */
test("调整面板：滑杆预览 + 导出新节点", async ({ page }) => {
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
  await panel.getByTestId("prompt-textarea").fill(`调整回归 ${T}`);
  await panel.getByTestId("composer-generate").click();
  await expect(panel).toBeHidden({ timeout: 5000 });
  await expect(
    page.locator('[data-testid="canvas-node-image"]').first().getByLabel("生成完成"),
  ).toBeVisible({ timeout: 60_000 });
  // 产物在回流的新节点（带预览图 + 加工入口）；源节点只有提示词与完成徽标
  const node = page.locator('[data-testid="canvas-node-image"]:has(img)').first();
  await node.scrollIntoViewIfNeeded();

  // 打开调整
  await node.getByRole("button", { name: "加工工具" }).click();
  await page.getByRole("button", { name: "调整…" }).dispatchEvent("click");
  await expect(page.getByText("调整图像")).toBeVisible({ timeout: 8000 });
  const preview = page.getByTestId("adjust-preview");

  // 滑杆 → 预览 filter 同步
  await page.getByTestId("adjust-brightness").fill("130");
  await expect(preview).toHaveAttribute("style", expect.stringContaining("brightness(1.3)"));

  // 导出 → 新节点（标题含"调整"）
  const nodeCount = await page.locator('[data-testid="canvas-node-image"]').count();
  await page.getByRole("button", { name: /导出为新节点/ }).click();
  await expect(
    page.locator('[data-testid="canvas-node-image"]', { hasText: "调整" }).first(),
  ).toBeVisible({ timeout: 15_000 });
  expect(await page.locator('[data-testid="canvas-node-image"]').count()).toBe(nodeCount + 1);
  test.setTimeout(150_000);
});
