import { test } from "@playwright/test";

test("J3 多角度生成（真实出图 1 个角度）", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message.slice(0, 120)));
  await page.goto("/login");
  await page.getByLabel("用户名").fill(process.env.CANVAS_E2E_USER ?? "demo");
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForTimeout(2000);

  // 用有 prompt 的源节点画布（18 的节点 prompt=数据一致性验证…）
  await page.goto("/canvas/18");
  await page.waitForTimeout(2500);
  await page.locator(".react-flow__node").first().hover();
  const angleBtn = page.getByLabel("多角度生成").first();
  console.log("多角度按钮:", await angleBtn.isVisible().catch(() => false));
  await angleBtn.click();
  await page.waitForTimeout(800);
  console.log("对话框:", await page.getByRole("heading", { name: "多角度生成" }).isVisible().catch(() => false));

  // 只留一个角度（快速验证闭环）
  await page.getByRole("button", { name: "俯视" }).click();
  await page.getByRole("button", { name: "特写" }).click();
  await page.waitForTimeout(300);
  const before = await page.locator(".react-flow__node").count();
  await page.getByRole("button", { name: /生成 1 个角度/ }).click();
  await page.waitForTimeout(3000);
  console.log("进度行:", await page.getByText(/排队中|生成中/).first().isVisible().catch(() => false));

  // 等真实生成（Kolors ~10s）
  let done = false;
  for (let i = 0; i < 20; i++) {
    if (await page.getByText("✓ 完成").first().isVisible().catch(() => false)) { done = true; break; }
    await page.waitForTimeout(3000);
  }
  console.log("真实生成:", done ? "OK" : "FAIL");
  await page.waitForTimeout(1000);
  const after = await page.locator(".react-flow__node").count();
  console.log("节点数:", before, "→", after, after > before ? "OK" : "FAIL");
  console.log("ERRORS:", errors.length);
  await page.screenshot({ path: "e2e-screenshots/j3-angle.png" });
});
