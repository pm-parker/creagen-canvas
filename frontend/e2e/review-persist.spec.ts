import { test, expect } from "@playwright/test";

test("画布 outputUrl 刷新持久化", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("用户名").fill(process.env.CANVAS_E2E_USER ?? "demo");
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForTimeout(2000);

  // 进入 project 18（有刚加入的图节点）
  await page.goto("/canvas/18");
  await page.waitForTimeout(2000);
  const imgs = await page.locator(".react-flow__node img").count();
  console.log("刷新后图片节点 img 数:", imgs);
  const src = imgs > 0 ? await page.locator(".react-flow__node img").first().getAttribute("src") : "";
  console.log("img src:", src?.slice(0, 60));
  expect(imgs).toBeGreaterThan(0);
});
