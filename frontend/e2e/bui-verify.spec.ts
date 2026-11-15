import { test } from "@playwright/test";

test("Beautiful UI 落地验证：Pulse 卡 + 任务 icon", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("用户名").fill(process.env.CANVAS_E2E_USER ?? "demo");
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForTimeout(2000);

  // 1. 提交一个真实生成 → 观察 Pulse 卡（shimmer+阶段文案+计时）
  await page.goto("/generate/image");
  await page.waitForTimeout(1200);
  await page.locator("#quick-prompt").fill("极简主义海报，一只纸鹤，蓝色渐变背景");
  await page.getByRole("button", { name: /生成 1 张/ }).click();
  await page.waitForTimeout(1500);
  const pulse = await page.getByText(/排队中|已提交渠道|模型生成中|即将完成/).first().isVisible().catch(() => false);
  const timer = await page.getByText(/\d+\.\ds/).first().isVisible().catch(() => false);
  console.log("PULSE_PHASE:", pulse, "| TIMER:", timer);
  await page.screenshot({ path: "e2e-screenshots/bui-pulse.png" });

  // 等生成完成
  let done = false;
  for (let i = 0; i < 20; i++) {
    if (await page.locator("img[src*='/outputs/'], img[src*='http']").first().isVisible().catch(() => false)) { done = true; break; }
    await page.waitForTimeout(3000);
  }
  console.log("生成完成:", done);

  // 2. 任务中心 icon + 徽章
  await page.goto("/tasks");
  await page.waitForTimeout(1500);
  const svgIcons = await page.locator("svg.h-4.w-4").count();
  console.log("任务行 SVG icon 数:", svgIcons);
  console.log("完成徽章:", await page.getByText(/\/\d+/).first().isVisible().catch(() => false));
  await page.screenshot({ path: "e2e-screenshots/bui-tasks.png" });
});
