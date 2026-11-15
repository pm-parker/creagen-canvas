import { test, expect } from "@playwright/test";

test("fnos 环境：注册→建画布→加节点", async ({ page }) => {
  test.setTimeout(60_000);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(`[pageerror] ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(`[console] ${m.text().slice(0, 200)}`);
  });

  const T = Date.now();
  await page.goto("/register");
  await page.getByLabel("用户名").fill(`fnos${T}`);
  await page.getByLabel("邮箱").fill(`fnos${T}@x.io`);
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByLabel("邀请码").fill("gan-2026");
  await page.getByRole("button", { name: "注册" }).click();
  await expect(page.getByText(/工作台/)).toBeVisible({ timeout: 10_000 });

  await page.goto("/projects");
  await page.getByRole("button", { name: /新建画布/ }).click();
  await page.waitForTimeout(1000);
  await page.getByText("未命名画布").first().click();
  await expect(page.getByTestId("canvas-editor")).toBeVisible({ timeout: 10_000 });

  await page.getByTestId("add-node-text").click();
  await page.waitForTimeout(800);
  const nodeCount = await page.getByTestId("canvas-node-text").count();
  console.log("NODE_COUNT:", nodeCount);
  const domNodes = await page.locator(".react-flow__node").count();
  console.log("RF_DOM_NODES:", domNodes);
  const storeDebug = await page.evaluate(() => {
    const w = window as unknown as { __ccDebug?: unknown };
    return JSON.stringify(w.__ccDebug ?? "no-debug");
  });
  console.log("STORE_DEBUG:", storeDebug);
  console.log("ERRORS:", JSON.stringify(errors, null, 1).slice(0, 1500));
  expect(nodeCount).toBeGreaterThan(0);
});
