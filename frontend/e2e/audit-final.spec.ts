import { test } from "@playwright/test";

test("修复点终验：生成页模型/404/渠道编辑", async ({ page }) => {
  const T = Date.now();
  await page.goto("/register");
  await page.getByLabel("用户名").fill(`fx${T}`);
  await page.getByLabel("邮箱").fill(`fx${T}@x.io`);
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByLabel("邀请码").fill("gan-2026");
  await page.getByRole("button", { name: "注册" }).click();
  await page.waitForTimeout(2000);

  // 1. 生图页模型下拉
  await page.goto("/generate/image");
  await page.waitForTimeout(1500);
  const selects = await page.locator("select").all();
  for (const s of selects) {
    const opts = await s.locator("option").allTextContents();
    console.log("IMG_SELECT:", JSON.stringify(opts.slice(0, 6)));
  }
  // 填提示词后生成按钮是否可点
  await page.locator("#quick-prompt").fill("测试");
  const genDisabled = await page.getByRole("button", { name: /生成/ }).isDisabled();
  console.log("GEN_DISABLED_AFTER_PROMPT:", genDisabled);

  // 2. 404
  await page.goto("/nonexistent-xyz");
  await page.waitForTimeout(800);
  console.log("404_HAS_TEXT:", (await page.content()).includes("404"));

  // 3. 渠道编辑按钮
  await page.goto("/settings/channels");
  await page.waitForTimeout(1200);
  console.log("EDIT_BTN:", await page.getByLabel("编辑渠道").first().isVisible().catch(() => false));
  await page.getByLabel("编辑渠道").first().click();
  await page.waitForTimeout(500);
  console.log("EDIT_FORM:", await page.getByLabel("渠道名称").first().isVisible().catch(() => false));
});
