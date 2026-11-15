import { test } from "@playwright/test";

test("N2 异常路径审查", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message.slice(0, 100)));
  const T = Date.now();
  await page.goto("/register");
  await page.getByLabel("用户名").fill(`n2${T}`);
  await page.getByLabel("邮箱").fill(`n2${T}@x.io`);
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByLabel("邀请码").fill("gan-2026");
  await page.getByRole("button", { name: "注册" }).click();
  await page.waitForTimeout(2000);

  // 1. 生图重复点击（防重提交）
  await page.goto("/generate/image");
  await page.waitForTimeout(1000);
  await page.locator("#quick-prompt").fill("重复点击测试");
  const btn = page.getByRole("button", { name: /生成 1 张/ });
  await btn.click();
  await btn.click({ force: true }).catch(() => {});
  await btn.click({ force: true }).catch(() => {});
  await page.waitForTimeout(1500);
  console.log("N2-1 重复点击后按钮态:", await btn.isDisabled(), await btn.textContent());

  // 2. 超长提示词（5000 字）
  await page.goto("/generate/image");
  await page.waitForTimeout(800);
  await page.locator("#quick-prompt").fill("长".repeat(5000));
  const disabledLong = await page.getByRole("button", { name: /生成 1 张/ }).isDisabled();
  console.log("N2-2 超长提示词可提交:", !disabledLong, "(提交预期: 后端校验拒绝或成功)");
  await page.getByRole("button", { name: /生成 1 张/ }).click();
  await page.waitForTimeout(2500);
  console.log("N2-2b 提交后反馈:", (await page.content()).match(/错误|失败|error|生成中/)?.[0] ?? "无反馈?");

  // 3. XSS 向量
  await page.goto("/generate/image");
  await page.waitForTimeout(800);
  await page.locator("#quick-prompt").fill('<script>alert(1)</script><img src=x onerror=alert(2)>');
  await page.getByRole("button", { name: /生成 1 张/ }).click();
  await page.waitForTimeout(2500);
  console.log("N2-3 XSS alert 触发:", errors.length === 0 ? "未触发 OK" : "触发!");

  // 4. emoji/特殊字符
  await page.goto("/generate/image");
  await page.waitForTimeout(800);
  await page.locator("#quick-prompt").fill("🦺✅ 中文𝄞混合🎉 999");
  await page.getByRole("button", { name: /生成 1 张/ }).click();
  await page.waitForTimeout(2000);
  console.log("N2-4 emoji 提交:", errors.length === 0 ? "OK" : JSON.stringify(errors.slice(0, 2)));

  // 5. 画布导入非法 JSON
  await page.goto("/projects");
  await page.waitForTimeout(800);
  await page.getByRole("button", { name: "新建画布" }).click();
  await page.waitForTimeout(2000);
  console.log("N2-5 画布 URL:", page.url());

  // 6. 深链不存在画布
  await page.goto("/canvas/99999");
  await page.waitForTimeout(1500);
  console.log("N2-6 不存在画布:", (await page.content()).match(/找不到|404|不存在|空|加载失败/)?.[0] ?? "无提示(白屏?)");
  await page.screenshot({ path: "e2e-screenshots/n2-canvas-404.png" });

  // 7. 未登录访问受保护页
  await page.goto("/settings/channels");
  await page.waitForTimeout(500);
  await page.evaluate(() => localStorage.removeItem("cc-auth"));
  await page.goto("/tasks");
  await page.waitForTimeout(1500);
  console.log("N2-7 未登录访问任务页:", page.url().includes("/login") ? "跳登录 OK" : page.url());
  console.log("PAGE_ERRORS:", JSON.stringify(errors.slice(0, 6)));
});
