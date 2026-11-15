import { test } from "@playwright/test";

// 第一轮：admin 账号（有真实数据）全页面截图
test("R1 数据态全页截图", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("用户名").fill(process.env.CANVAS_E2E_USER ?? "demo");
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForTimeout(2000);

  const pages: Array<[string, string]> = [
    ["01-home", "/"],
    ["02-image", "/generate/image"],
    ["03-video", "/generate/video"],
    ["04-prompts", "/prompts"],
    ["05-tasks", "/tasks"],
    ["06-projects", "/projects"],
    ["07-assets", "/assets"],
    ["08-costs", "/costs"],
    ["09-channels", "/settings/channels"],
  ];
  for (const [name, path] of pages) {
    await page.goto(path);
    await page.waitForTimeout(1500);
    await page.screenshot({ path: `e2e-screenshots/r1-${name}.png`, fullPage: true });
  }

  // 画布：进入第一个项目
  await page.goto("/projects");
  await page.waitForTimeout(1000);
  const card = page.locator("a[href^='/canvas/']").first();
  if (await card.isVisible().catch(() => false)) {
    await card.click();
    await page.waitForTimeout(2000);
    await page.screenshot({ path: "e2e-screenshots/r1-10-canvas.png", fullPage: false });
  }
});
