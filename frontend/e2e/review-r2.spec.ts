import { test } from "@playwright/test";

test("R2 修复后全页复验", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("用户名").fill(process.env.CANVAS_E2E_USER ?? "demo");
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForTimeout(2000);

  const pages: Array<[string, string]> = [
    ["01-home", "/"],
    ["02-image", "/generate/image"],
    ["04-prompts", "/prompts"],
    ["05-tasks", "/tasks"],
    ["07-assets", "/assets"],
  ];
  for (const [name, path] of pages) {
    await page.goto(path);
    await page.waitForTimeout(1500);
    await page.screenshot({ path: `e2e-screenshots/r2-${name}.png`, fullPage: true });
  }
  // 画布
  await page.goto("/projects");
  await page.waitForTimeout(800);
  const card = page.locator("a[href^='/canvas/']").first();
  if (await card.isVisible().catch(() => false)) {
    await card.click();
    await page.waitForTimeout(2000);
    await page.screenshot({ path: "e2e-screenshots/r2-10-canvas.png" });
  }
});
