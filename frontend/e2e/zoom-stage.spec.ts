import { test, expect } from "@playwright/test";

/** P0-1 回归：加工编辑器缩放底座——三个加工器均有缩放控件且 transform 生效。 */
test("裁切弹窗：缩放控件与 transform 生效", async ({ page }) => {
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

  // 建一个带图的节点：图片节点生成一次（走免费 Kolors）
  await page.getByTestId("add-node-image").click();
  await page.waitForTimeout(500);
  await page.locator('[data-testid="canvas-node-image"]').first().click();
  const panel = page.getByTestId("prompt-panel");
  await expect(panel).toBeVisible({ timeout: 8000 });
  await panel.getByTestId("prompt-textarea").fill(`缩放回归 ${T}`);
  await panel.getByTestId("composer-generate").click();
  await expect(panel).toBeHidden({ timeout: 5000 });
  await expect(
    page.locator('[data-testid="canvas-node-image"]').first().getByLabel("生成完成"),
  ).toBeVisible({ timeout: 60_000 });
  // 产物在回流的新节点（带预览图 + 加工入口）；源节点只有提示词与完成徽标
  const node = page.locator('[data-testid="canvas-node-image"]:has(img)').first();
  await node.scrollIntoViewIfNeeded();

  // 打开裁切 → 缩放控件存在且生效
  await node.getByRole("button", { name: "加工工具" }).click();
  await page.getByRole("button", { name: "裁切…" }).dispatchEvent("click");
  const controls = page.getByTestId("crop-zoom-controls");
  await expect(controls).toBeVisible({ timeout: 8000 });
  await expect(page.getByTestId("crop-zoom-percent")).toHaveText("100%");

  await controls.getByLabel("放大").click();
  await controls.getByLabel("放大").click();
  await expect(page.getByTestId("crop-zoom-viewport")).toHaveAttribute(
    "style",
    expect.stringContaining("scale(1.56"),
  ); // 1.25^2 = 1.5625
  // 重置
  await controls.getByLabel("重置缩放").click();
  await expect(page.getByTestId("crop-zoom-percent")).toHaveText("100%");
  test.setTimeout(120_000);
});
