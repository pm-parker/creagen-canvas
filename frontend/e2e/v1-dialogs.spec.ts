import { test } from "@playwright/test";

// 盲区 1-3：提示词库新建 / 批量导入 / 画布卡片重命名+删除
test("V1 提示词新建+批量导入+画布改名删除", async ({ page }) => {
  const T = Date.now();
  await page.goto("/login");
  await page.getByLabel("用户名").fill(process.env.CANVAS_E2E_USER ?? "demo");
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForTimeout(2000);

  // 1. 提示词库新建对话框
  await page.goto("/prompts");
  await page.waitForTimeout(1200);
  await page.getByRole("button", { name: "新建提示词" }).click();
  await page.waitForTimeout(600);
  console.log("V1-1 新建弹窗:", await page.getByText(/标题|title/i).first().isVisible().catch(() => false));
  await page.locator("#p-title").fill(`验证提示词${T}`);
  await page.locator("#p-content").fill("验证用提示词内容，一只白色猫");
  await page.getByRole("button", { name: /保存|创建|确定/ }).last().click();
  await page.waitForTimeout(1500);
  console.log("V1-1b 新建后卡片:", await page.getByText(`验证提示词${T}`).isVisible().catch(() => false));

  // 2. 批量导入对话框（粘贴 JSON）
  await page.getByRole("button", { name: "批量导入" }).click();
  await page.waitForTimeout(600);
  console.log("V1-2 批量导入弹窗:", await page.getByText(/批量导入|JSON/).first().isVisible().catch(() => false));
  const importTa = page.locator("textarea:visible").last();
  await importTa.fill(`[{"title":"导入A${T}","content":"导入测试内容A","category":"验证"},{"title":"导入B${T}","content":"导入测试内容B","category":"验证"}]`);
  await page.getByRole("button", { name: /导入$|^导入/ }).last().click();
  await page.waitForTimeout(2000);
  console.log("V1-2b 导入后卡片A:", await page.getByText(`导入A${T}`).isVisible().catch(() => false));
  console.log("V1-2c 导入后卡片B:", await page.getByText(`导入B${T}`).isVisible().catch(() => false));
  await page.screenshot({ path: "e2e-screenshots/v1-prompts.png" });

  // 3. 画布卡片重命名 + 删除（新建一个专用）
  await page.goto("/projects");
  await page.waitForTimeout(1000);
  await page.getByRole("button", { name: "新建画布" }).click();
  await page.waitForTimeout(2500);
  await page.goto("/projects");
  await page.waitForTimeout(1500);
  const renameBtn = page.getByLabel("重命名").first();
  await renameBtn.click();
  await page.waitForTimeout(400);
  const renameInput = page.locator("input:visible").first();
  await renameInput.fill(`改名画布${T}`);
  await renameInput.press("Enter");
  await page.waitForTimeout(1200);
  console.log("V1-3 重命名:", await page.getByText(`改名画布${T}`).isVisible().catch(() => false));
  // 删除（两步确认）
  await page.getByLabel("删除").first().click();
  await page.waitForTimeout(400);
  await page.getByRole("button", { name: "确认删除" }).first().click();
  await page.waitForTimeout(1200);
  console.log("V1-4 删除后不存在:", !(await page.getByText(`改名画布${T}`).isVisible().catch(() => false)));
});
