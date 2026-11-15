import { test } from "@playwright/test";

test("复现添加节点问题", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`[console.error] ${msg.text()}`);
  });
  page.on("pageerror", (err) => errors.push(`[pageerror] ${err.message}`));

  await page.goto("/register");
  await page.getByLabel("用户名").fill(`dbg${Date.now()}`);
  await page.getByLabel("邮箱").fill(`dbg${Date.now()}@x.io`);
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByLabel("邀请码").fill("letmein");
  await page.getByRole("button", { name: "注册" }).click();
  await page.waitForTimeout(1500);

  await page.goto("/projects");
  await page.getByRole("button", { name: /新建画布/ }).click();
  await page.waitForTimeout(1000);
  const card = page.getByText("未命名画布").first();
  if (await card.count()) {
    await card.click();
  } else {
    await page.goto("/canvas/1");
  }
  await page.waitForTimeout(1500);
  await page.getByTestId("add-node-text").click();
  await page.waitForTimeout(1200);

  console.log("=== ERRORS ===");
  for (const e of errors) console.log(e.slice(0, 300));
  console.log("=== node count ===");
  console.log(await page.getByTestId("canvas-node-text").count());
  await page.screenshot({ path: "e2e-screenshots/debug.png" });
});
