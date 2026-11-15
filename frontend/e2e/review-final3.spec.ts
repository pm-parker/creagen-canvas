import { test } from "@playwright/test";

test("三项新功能终验", async ({ page }) => {
  const T = Date.now();
  await page.goto("/register");
  await page.getByLabel("用户名").fill(`fv${T}`);
  await page.getByLabel("邮箱").fill(`fv${T}@x.io`);
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByLabel("邀请码").fill("gan-2026");
  await page.getByRole("button", { name: "注册" }).click();
  await page.waitForTimeout(2000);

  // 1. 成本预估：生图页选 4 张
  await page.goto("/generate/image");
  await page.waitForTimeout(1500);
  await page.getByRole("button", { name: "4", exact: true }).click();
  await page.waitForTimeout(800);
  console.log("F1 成本预估(4张):", await page.getByText(/预计/).textContent().catch(() => "MISSING"));
  // 视频页 10s
  await page.goto("/generate/video");
  await page.waitForTimeout(1200);
  await page.getByRole("button", { name: "10s" }).click();
  await page.waitForTimeout(800);
  console.log("F1b 成本预估(10s):", await page.getByText(/预计/).textContent().catch(() => "MISSING"));

  // 2. 侧栏 hover 展开
  await page.goto("/");
  await page.waitForTimeout(1000);
  await page.locator("aside").hover();
  await page.waitForTimeout(400);
  console.log("F2 侧栏展开文字:", await page.getByText("画布库").isVisible().catch(() => false));

  // 3. 资产加入画布：admin 有资产 → 建一个画布 → 加入
  await page.goto("/assets");
  await page.waitForTimeout(1500);
  const addBtn = page.getByLabel("加入画布").first();
  console.log("F3 加入按钮可见:", await addBtn.isVisible().catch(() => false));
  if (await addBtn.isVisible().catch(() => false)) {
    await addBtn.hover();
    await addBtn.click();
    await page.waitForTimeout(800);
    const dialogVisible = await page.getByText("加入画布").nth(1).isVisible().catch(() => false);
    console.log("F3b 选择弹层:", dialogVisible);
    const target = page.locator("div.w-80 button").first();
    await target.click();
    await page.waitForTimeout(1500);
    console.log("F3c 成功提示:", await page.getByText(/已加入/).isVisible().catch(() => false));
  }
  await page.screenshot({ path: "e2e-screenshots/fv-final.png", fullPage: true });
});
