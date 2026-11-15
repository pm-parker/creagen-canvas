import { test } from "@playwright/test";

test("细节打磨 pass：全页截图（admin 数据态）", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("用户名").fill(process.env.CANVAS_E2E_USER ?? "demo");
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForTimeout(2000);
  for (const [name, path] of [
    ["01-home", "/"],
    ["02-image", "/generate/image"],
    ["04-prompts", "/prompts"],
    ["05-tasks", "/tasks"],
    ["07-assets", "/assets"],
    ["08-costs", "/costs"],
    ["09-channels", "/settings/channels"],
  ] as const) {
    await page.goto(path);
    await page.waitForTimeout(1400);
    await page.screenshot({ path: `e2e-screenshots/pol-${name}.png`, fullPage: name !== "04-prompts" });
  }
  await page.goto("/canvas/18");
  await page.waitForTimeout(2500);
  await page.locator(".react-flow__node").first().hover();
  await page.waitForTimeout(300);
  // 打开提示词面板（点节点）
  await page.locator(".react-flow__node").first().click();
  await page.waitForTimeout(1000);
  await page.screenshot({ path: "e2e-screenshots/pol-10-canvas.png" });
});
