import { test, expect } from "@playwright/test";

/**
 * BUG-A 回归：渠道编辑保存必须走 PATCH。
 * 修复前：前端 PUT → 后端仅注册 patch → 405，勾选「支持图像编辑」无法保存。
 * 链路：新建测试渠道（带/不带 edit 能力）→ UI 编辑勾选 → 保存 → 断言能力徽标变化 → 清理。
 */
test("渠道编辑：勾选支持图像编辑保存后徽标出现", async ({ page }) => {
  const T = Date.now();
  const chName = `e2e编辑${T}`;

  await page.goto("/login");
  await page.getByLabel("用户名").fill(process.env.CANVAS_E2E_USER ?? "demo");
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForTimeout(1500);

  await page.goto("/settings/channels");
  await page.waitForTimeout(1500);

  // 1. 新建一个普通 image 渠道
  await page.getByRole("button", { name: /新建渠道/ }).click();
  await page.locator("#ch-domain").selectOption("image");
  await page.locator("#ch-name").fill(chName);
  await page.locator("#ch-url").fill("https://api.siliconflow.cn/v1");
  await page.locator("#ch-key").fill("sk-e2e-fake-key-0000");
  await page.getByRole("button", { name: "保存" }).click();
  await expect(page.getByText(chName).first()).toBeVisible({ timeout: 10_000 });

  // 定位到该渠道的卡片范围
  const card = page.locator("div.rounded-xl.border", { hasText: chName }).first();
  await expect(card).toBeVisible();

  // 2. 编辑：勾选支持图像编辑 → 保存（修复前此处 405）
  await card.getByRole("button", { name: "编辑渠道" }).click();
  await card.locator("input[name='edit_cap']").check();
  await card.getByRole("button", { name: "保存" }).click();

  // 3. 保存成功：表单收起 + 「可编辑」徽标出现（修复前两断言都不过）
  await expect(card.locator("input[name='edit_cap']")).toBeHidden({ timeout: 10_000 });
  await expect(card.getByText("可编辑")).toBeVisible({ timeout: 10_000 });

  // 4. 清理测试渠道（两步确认：确认框出现后点删除）
  await card.getByRole("button", { name: "删除渠道" }).click();
  await page.waitForTimeout(400);
  const confirm = page.getByRole("button", { name: /^确认|删除$/ }).last();
  if (await confirm.isVisible({ timeout: 1500 }).catch(() => false)) {
    await confirm.click();
  }
  await expect(page.getByText(chName)).toBeHidden({ timeout: 10_000 });
});
