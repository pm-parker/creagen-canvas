import { test } from "@playwright/test";

test("打磨终验：全页复截", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("用户名").fill(process.env.CANVAS_E2E_USER ?? "demo");
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForTimeout(2000);
  for (const [name, path] of [
    ["f-home", "/"],
    ["f-image", "/generate/image"],
    ["f-costs", "/costs"],
  ] as const) {
    await page.goto(path);
    await page.waitForTimeout(1400);
    await page.screenshot({ path: `e2e-screenshots/${name}.png` });
  }
});
