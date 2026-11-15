import { test } from "@playwright/test";

test("N1 新用户全生命周期", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message.slice(0, 120)));
  const T = Date.now();

  // 注册
  await page.goto("/register");
  await page.screenshot({ path: "e2e-screenshots/n1-register.png" });
  await page.getByLabel("用户名").fill(`n1${T}`);
  await page.getByLabel("邮箱").fill(`n1${T}@x.io`);
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByLabel("邀请码").fill("gan-2026");
  await page.getByRole("button", { name: "注册" }).click();
  await page.waitForTimeout(2000);
  console.log("STEP1 落地页:", page.url());

  // 空首页
  await page.screenshot({ path: "e2e-screenshots/n1-home-empty.png" });

  // 生图页：高级选项展开态
  await page.goto("/generate/image");
  await page.waitForTimeout(1200);
  await page.getByRole("button", { name: /高级选项/ }).click();
  await page.waitForTimeout(500);
  await page.screenshot({ path: "e2e-screenshots/n1-image-advanced.png" });
  // 提示词库"去生成"参数
  await page.goto("/prompts");
  await page.waitForTimeout(1200);
  const genBtn = page.getByLabel("去生成").first();
  await genBtn.click();
  await page.waitForTimeout(1200);
  const promptFilled = await page.locator("#quick-prompt").inputValue();
  console.log("STEP2 提示词带参数跳转:", promptFilled.length > 10 ? "OK" : `FAIL(${promptFilled.slice(0, 30)})`, "URL:", page.url());

  // 真实生成（Kolors 免费，等待出图）
  await page.getByRole("button", { name: /生成 1 张/ }).click();
  await page.waitForTimeout(3000);
  await page.screenshot({ path: "e2e-screenshots/n1-generating.png" });
  let done = false;
  for (let i = 0; i < 20; i++) {
    if (await page.locator("img[src*='/outputs/']").first().isVisible().catch(() => false)) { done = true; break; }
    await page.waitForTimeout(3000);
  }
  console.log("STEP3 新用户首图生成:", done ? "OK" : "FAIL");
  await page.screenshot({ path: "e2e-screenshots/n1-first-image.png" });

  // 资产库应有 1 项；任务中心应有记录
  await page.goto("/assets");
  await page.waitForTimeout(1200);
  console.log("STEP4 资产库:", (await page.content()).includes("共 1 项") ? "OK" : "CHECK");
  // 退出登录
  await page.locator("button[aria-label*='退出'], aside button").last().click();
  await page.waitForTimeout(1000);
  console.log("STEP5 退出后:", page.url(), "ERRORS:", JSON.stringify(errors.slice(0, 5)));
});
