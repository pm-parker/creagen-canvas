import { test } from "@playwright/test";

test("V3a 历史切换 + 渠道测试点击", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("用户名").fill(process.env.CANVAS_E2E_USER ?? "demo");
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForTimeout(2000);

  // 渠道测试连通点击反馈
  await page.goto("/settings/channels");
  await page.waitForTimeout(1200);
  await page.getByRole("button", { name: "测试连通" }).first().click();
  await page.waitForTimeout(2500);
  const feedback = await page.locator("text=/✓|✗/").first().textContent().catch(() => "");
  console.log("V3a-1 测试连通反馈:", feedback);

  // 历史切换：canvas/18 有历史的节点
  await page.goto("/canvas/18");
  await page.waitForTimeout(2500);
  await page.locator(".react-flow__node").nth(1).hover();
  await page.waitForTimeout(400);
  const hist = page.locator("[data-testid=history-picker]").first();
  const visible = await hist.isVisible().catch(() => false);
  console.log("V3a-2 历史选择器可见:", visible);
  if (visible) {
    await hist.locator("button").first().click();
    await page.waitForTimeout(800);
    const menuItems = await page.locator("[data-testid=history-picker] div.absolute button, .absolute.left-0.top-6 button").count();
    console.log("V3a-2b 历史菜单项:", menuItems);
  }
});
