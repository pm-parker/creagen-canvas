import { test, expect } from "@playwright/test";

/**
 * 提示词面板生成流回归（修复前：面板提交的任务无人订阅 SSE → 节点卡"生成中"不回流）。
 * 链路：建画布 → 加图片节点 → 面板写提示词 → 面板内点「生成」→ 任务完成 → 节点完成徽标 + 结果回流。
 */
test("提示词面板提交生成：任务完成回流不卡死", async ({ page }) => {
  const T = Date.now();

  await page.goto("/login");
  await page.getByLabel("用户名").fill(process.env.CANVAS_E2E_USER ?? "demo");
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForTimeout(1500);

  // 1. 建画布并进入（新建后自动跳转画布编辑器）
  await page.goto("/projects");
  await page.getByRole("button", { name: "新建画布" }).click();
  await expect(page.getByTestId("canvas-editor")).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(2000);

  // 2. 加图片节点 → 打开提示词面板
  await page.getByTestId("add-node-image").click();
  await page.waitForTimeout(600);
  await page.locator('[data-testid="canvas-node-image"]').first().click();
  const panel = page.getByTestId("prompt-panel");
  await expect(panel).toBeVisible({ timeout: 8000 });
  await panel.getByTestId("prompt-textarea").fill(`面板生成回归：一只白色猫 ${T}`);

  // 3. 面板内提交（修复前该任务无人订阅，节点永远转圈）
  await panel.getByTestId("composer-generate").click();
  await expect(panel).toBeHidden({ timeout: 5000 });

  // 4. 节点进入生成中（spinner）或直接完成（真实渠道 5-30s）
  const node = page.locator('[data-testid="canvas-node-image"]').first();
  await expect(node.getByLabel("生成中")).toBeVisible({ timeout: 10_000 });

  // 5. 完成徽标（SSE 回流）；免费 Kolors 通常 <30s
  await expect(node.getByLabel("生成完成")).toBeVisible({ timeout: 60_000 });
  test.setTimeout(120_000);
});
