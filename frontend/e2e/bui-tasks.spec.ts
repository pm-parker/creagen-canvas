import { test } from "@playwright/test";

test("任务中心 SVG icon + 徽章", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("用户名").fill(process.env.CANVAS_E2E_USER ?? "demo");
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForTimeout(2000);
  await page.goto("/tasks");
  await page.waitForTimeout(1500);
  console.log("SVG icon 数:", await page.locator("svg.h-4.w-4").count());
  console.log("完成徽章:", await page.getByText(/\/\d+/).first().isVisible().catch(() => false));
  await page.screenshot({ path: "e2e-screenshots/bui-tasks.png" });
});
