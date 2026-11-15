import { test } from "@playwright/test";

test("切分入口接线验证", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("用户名").fill(process.env.CANVAS_E2E_USER ?? "demo");
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForTimeout(2000);
  await page.goto("/canvas/18");
  await page.waitForTimeout(2500);
  await page.locator(".react-flow__node").first().hover();
  await page.getByLabel("宫格切分").first().click();
  await page.waitForTimeout(800);
  console.log("切分弹窗:", await page.getByRole("heading", { name: "宫格切分" }).isVisible().catch(() => false));
});
