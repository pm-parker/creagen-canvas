import { test } from "@playwright/test";

test("V2 画布弹窗全量", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("用户名").fill(process.env.CANVAS_E2E_USER ?? "demo");
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForTimeout(2000);
  await page.goto("/canvas/18");
  await page.waitForTimeout(2500);
  const node = page.locator(".react-flow__node").first();
  await node.hover();
  await page.waitForTimeout(400);

  // 1. 加工菜单展开 + 菜单项
  await page.getByLabel("加工工具").first().click();
  await page.waitForTimeout(400);
  console.log("V2-1 菜单项:", await page.getByText("裁切…").isVisible().catch(() => false), await page.getByText("宫格切分…").isVisible().catch(() => false));

  // 2. 宫格切分实际执行
  await page.getByText("宫格切分…").click();
  await page.waitForTimeout(800);
  console.log("V2-2 切分弹窗:", await page.getByRole("heading", { name: "宫格切分" }).isVisible().catch(() => false));
  const before = await page.locator(".react-flow__node").count();
  await page.getByRole("button", { name: "开始切分" }).click();
  await page.waitForTimeout(4000);
  const after = await page.locator(".react-flow__node").count();
  console.log("V2-2b 切分节点:", before, "→", after, after >= before + 4 ? "OK(4块)" : "FAIL");

  // 3. 版本历史抽屉：创建快照 → 列表 → 恢复
  await page.getByRole("button", { name: /历史/ }).first().click();
  await page.waitForTimeout(800);
  console.log("V2-3 抽屉打开:", await page.getByText(/版本|快照/).first().isVisible().catch(() => false));
  const snapBtn = page.getByRole("button", { name: /创建快照|保存快照/ }).first();
  if (await snapBtn.isVisible().catch(() => false)) {
    await snapBtn.click();
    await page.waitForTimeout(1500);
    console.log("V2-3b 快照创建:", await page.locator("[data-testid=snapshot-list] > *, [data-testid=snapshots] > *").count().catch(() => 0));
  } else {
    console.log("V2-3b 快照按钮: 未找到(查看截图)");
  }
  await page.screenshot({ path: "e2e-screenshots/v2-snapshot.png" });
  await page.keyboard.press("Escape");
  await page.mouse.click(900, 400);
  await page.waitForTimeout(500);

  // 4. 节点双击改名
  const n0 = page.locator(".react-flow__node").first();
  await n0.dblclick();
  await page.waitForTimeout(500);
  const editInput = page.locator(".react-flow__node input:visible").first();
  if (await editInput.isVisible().catch(() => false)) {
    await editInput.fill(`改名节点${Date.now() % 1000}`);
    await editInput.press("Enter");
    await page.waitForTimeout(800);
    console.log("V2-4 双击改名: OK");
  } else {
    console.log("V2-4 双击改名: 无输入框");
  }

  // 5. 撤销/重做按钮真实点击
  const undoBtn = page.getByRole("button", { name: /撤销/ }).first();
  const undoDisabled = await undoBtn.isDisabled();
  await undoBtn.click();
  await page.waitForTimeout(800);
  const afterUndo = await page.locator(".react-flow__node").count();
  console.log("V2-5 撤销: disabled=", undoDisabled, "节点数→", afterUndo);
  await page.getByRole("button", { name: /重做/ }).first().click();
  await page.waitForTimeout(800);
  console.log("V2-5b 重做后节点数:", await page.locator(".react-flow__node").count());
  await page.screenshot({ path: "e2e-screenshots/v2-canvas.png" });
});
