import { test } from "@playwright/test";

test("画布创建流程逐步审计", async ({ page }) => {
  const T = Date.now();
  await page.goto("/register");
  await page.getByLabel("用户名").fill(`cv${T}`);
  await page.getByLabel("邮箱").fill(`cv${T}@x.io`);
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByLabel("邀请码").fill("gan-2026");
  await page.getByRole("button", { name: "注册" }).click();
  await page.waitForTimeout(2000);

  await page.goto("/projects");
  await page.waitForTimeout(1000);
  console.log("STEP1 projects buttons:", JSON.stringify(await page.getByRole("button").allTextContents()));
  await page.getByRole("button", { name: "新建画布" }).click();
  await page.waitForTimeout(1000);
  console.log("STEP2 after click, dialog visible:", await page.getByRole("dialog").isVisible().catch(() => false));
  console.log("STEP2 all buttons:", JSON.stringify(await page.getByRole("button").allTextContents()));
  const inputs = await page.locator("input:visible").all();
  console.log("STEP2 inputs:", inputs.length);
  if (inputs.length > 0) await inputs[0].fill(`审计${T}`);
  const btns = await page.getByRole("button").all();
  for (const b of btns) {
    const t = (await b.textContent())?.trim();
    if (t === "创建" || t === "确定" || t === "确认") {
      console.log("STEP3 clicking:", t);
      await b.click();
      break;
    }
  }
  await page.waitForTimeout(2500);
  console.log("STEP4 url now:", page.url());
  console.log("STEP4 canvas toolbar:", JSON.stringify((await page.getByRole("button").allTextContents()).slice(0, 25)));
  await page.screenshot({ path: "e2e-screenshots/audit-canvas.png", fullPage: false });
});
