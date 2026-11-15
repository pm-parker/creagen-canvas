import { test } from "@playwright/test";

test("F3 资产加入画布（admin 有数据）", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("用户名").fill(process.env.CANVAS_E2E_USER ?? "demo");
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForTimeout(2000);

  await page.goto("/assets");
  await page.waitForTimeout(1500);
  const card = page.locator("div.group.relative").first();
  await card.hover();
  await page.waitForTimeout(400);
  const addBtn = page.getByLabel("加入画布").first();
  console.log("按钮可见:", await addBtn.isVisible());
  await addBtn.click();
  await page.waitForTimeout(800);
  console.log("弹层:", await page.getByText("加入画布").nth(1).isVisible().catch(() => false));
  await page.locator("div.w-80 button").filter({ hasText: /画布|测试|形状/ }).first().click();
  await page.waitForTimeout(1500);
  console.log("成功提示:", await page.getByText(/已加入/).isVisible().catch(() => false));
  await page.screenshot({ path: "e2e-screenshots/f3-added.png" });

  // 验证节点真的进了画布
  await page.goto("/projects");
  await page.waitForTimeout(800);
  await page.locator("a[href^='/canvas/']").first().click();
  await page.waitForTimeout(2000);
  console.log("画布节点数:", await page.locator(".react-flow__node").count());
});
