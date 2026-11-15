import { test, expect } from "@playwright/test";

test("渠道页真实渲染检查", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(`[console] ${m.text().slice(0, 200)}`);
  });

  const T = Date.now();
  await page.goto("/register");
  await page.getByLabel("用户名").fill(`chk${T}`);
  await page.getByLabel("邮箱").fill(`chk${T}@x.io`);
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByLabel("邀请码").fill("gan-2026");
  await page.getByRole("button", { name: "注册" }).click();
  await expect(page.getByText(/工作台/)).toBeVisible({ timeout: 10_000 });

  await page.goto("/settings/channels");
  await page.waitForTimeout(2000);
  await page.screenshot({ path: "e2e-screenshots/audit-channels.png", fullPage: true });
  console.log("PAGE_ERRORS:", JSON.stringify(errors));
  const apiResp = await page.request.get("http://localhost:8080/api/providers/channels");
  console.log("DIRECT_API:", (await apiResp.text()).slice(0, 120));
});
