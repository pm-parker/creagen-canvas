import { test } from "@playwright/test";

const PAGES: Array<[string, string]> = [
  ["home", "/"],
  ["image", "/generate/image"],
  ["video", "/generate/video"],
  ["prompts", "/prompts"],
  ["tasks", "/tasks"],
  ["projects", "/projects"],
  ["assets", "/assets"],
  ["costs", "/costs"],
  ["channels", "/settings/channels"],
];

const VIEWPORTS: Array<[string, number, number]> = [
  ["1920", 1920, 1080],
  ["1280", 1280, 720],
  ["1024", 1024, 640],
];

test("三视口全页布局审计", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("用户名").fill(process.env.CANVAS_E2E_USER ?? "demo");
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForTimeout(2000);

  for (const [vn, w, h] of VIEWPORTS) {
    await page.setViewportSize({ width: w, height: h });
    for (const [name, path] of PAGES) {
      await page.goto(path);
      await page.waitForTimeout(1000);
      // 溢出检测：横向滚动条出现 = 布局溢出
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 2,
      );
      if (overflow) console.log(`OVERFLOW ${vn} ${name}: 横向溢出!`);
      if (vn === "1280") {
        await page.screenshot({ path: `e2e-screenshots/la-1280-${name}.png`, fullPage: false });
      } else if (name === "home" || name === "image") {
        await page.screenshot({ path: `e2e-screenshots/la-${vn}-${name}.png` });
      }
    }
    // 画布页（只有 1280 与 1024）
    if (vn !== "1920") {
      await page.goto("/canvas/18");
      await page.waitForTimeout(2200);
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 2,
      );
      if (overflow) console.log(`OVERFLOW ${vn} canvas`);
      if (vn === "1024") await page.screenshot({ path: `e2e-screenshots/la-${vn}-canvas.png` });
    }
  }
  console.log("DONE");
});
