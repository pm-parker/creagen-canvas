import { test } from "@playwright/test";

test("R3 终验：空画布引导 + Kolors置顶 + 生图真实闭环", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("用户名").fill(process.env.CANVAS_E2E_USER ?? "demo");
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForTimeout(2000);

  // 1. 新建画布 → 空态引导
  await page.goto("/projects");
  await page.waitForTimeout(800);
  await page.getByRole("button", { name: "新建画布" }).click();
  await page.waitForTimeout(2500);
  console.log("URL:", page.url());
  console.log("GUIDE_VISIBLE:", await page.getByText("从添加第一个节点开始").isVisible().catch(() => false));
  console.log("TITLE_BADGE:", await page.getByText(/审计|未命名/).first().isVisible().catch(() => false));

  // 2. 生图页 Kolors 置顶
  await page.goto("/generate/image");
  await page.waitForTimeout(1500);
  const modelSel = page.locator("select").nth(1);
  console.log("FIRST_MODEL:", await modelSel.locator("option").first().textContent());

  // 3. 真实生成一张（Kolors 免费）
  await page.locator("#quick-prompt").fill("一只戴头盔的橘猫骑士，油画风格");
  await page.getByRole("button", { name: /生成/ }).click();
  await page.waitForTimeout(25000);
  const hasResult = await page.locator("img[src*='/outputs/']").first().isVisible().catch(() => false);
  console.log("REAL_IMG_DONE:", hasResult);
  await page.screenshot({ path: "e2e-screenshots/r3-final.png", fullPage: true });
});
