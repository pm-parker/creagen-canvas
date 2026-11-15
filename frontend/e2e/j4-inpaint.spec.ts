import { test, expect } from "@playwright/test";

/**
 * J4 局部重绘回归（W4 P0-2）：
 * ① 笔刷尺寸滑杆可见且可调；② 撤销一笔按钮可用；
 * ③ 提交后任务 attach 到源节点——画布上出现终态徽标（修复前提交后零反馈）。
 * 说明：fnos 未配 edits 渠道时任务将以「能力校验」失败收尾，失败徽标同样是回流接线成立的证据。
 */
test("J4：笔刷滑杆/撤销 + 提交后节点出现终态徽标", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message.slice(0, 120)));

  const T = Date.now();
  await page.goto("/login");
  await page.getByLabel("用户名").fill(process.env.CANVAS_E2E_USER ?? "demo");
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForTimeout(1500);

  // 新画布 + 生成一张底图
  await page.goto("/projects");
  await page.getByRole("button", { name: "新建画布" }).click();
  await expect(page.getByTestId("canvas-editor")).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(1500);
  await page.getByTestId("add-node-image").click();
  await page.waitForTimeout(500);
  await page.locator('[data-testid="canvas-node-image"]').first().click();
  const panel = page.getByTestId("prompt-panel");
  await expect(panel).toBeVisible({ timeout: 8000 });
  await panel.getByTestId("prompt-textarea").fill(`J4回归 ${T}`);
  await panel.getByTestId("composer-generate").click();
  await expect(panel).toBeHidden({ timeout: 5000 });
  await expect(
    page.locator('[data-testid="canvas-node-image"]').first().getByLabel("生成完成"),
  ).toBeVisible({ timeout: 60_000 });
  // 产物在回流的新节点（带预览图 + 加工入口）；源节点只有提示词与完成徽标
  const node = page.locator('[data-testid="canvas-node-image"]:has(img)').first();
  await node.scrollIntoViewIfNeeded();

  // 打开局部重绘
  await node.getByRole("button", { name: "加工工具" }).click();
  await page.getByRole("button", { name: "局部重绘…" }).dispatchEvent("click");
  const canvas = page.locator("[data-testid=inpaint-canvas]");
  await canvas.scrollIntoViewIfNeeded();
  await expect(page.getByTestId("brush-size")).toBeVisible({ timeout: 8000 });

  // 笔刷滑杆可调
  const slider = page.getByLabel("笔刷尺寸");
  await slider.fill("6");

  // 涂抹两笔（dispatch 鼠标序列）
  await page.waitForTimeout(400);
  const box = await canvas.boundingBox();
  if (!box) throw new Error("canvas 不可见");
  const seq = (x: number, y: number) => ({
    clientX: box.x + box.width * x,
    clientY: box.y + box.height * y,
    button: 0,
    bubbles: true,
  });
  for (const [sx, sy] of [
    [0.3, 0.3],
    [0.5, 0.5],
  ]) {
    await canvas.dispatchEvent("mousedown", seq(sx, sy));
    await canvas.dispatchEvent("mousemove", seq(sx + 0.06, sy + 0.03));
    await canvas.dispatchEvent("mouseup", seq(sx + 0.06, sy + 0.03));
  }
  await page.waitForTimeout(300);
  // 撤销一笔
  const undoBtn = page.getByLabel("撤销一笔");
  await expect(undoBtn).toBeEnabled();
  await undoBtn.click();
  await page.waitForTimeout(200);

  // 提交 → 源节点应出现终态徽标（完成/失败均可；修复前永远无反馈）
  await page.getByPlaceholder("重绘指令，如：把这块改成夜空").fill("把这块改成夜空");
  await page.getByRole("button", { name: "局部重绘" }).last().click({ force: true });
  await expect(page.getByPlaceholder("重绘指令，如：把这块改成夜空")).toBeHidden({ timeout: 5000 });
  const badge = node.locator('[aria-label="生成完成"], [aria-label^="生成失败"]').first();
  await expect(badge).toBeVisible({ timeout: 30_000 });
  // dispatchEvent 合成 MouseEvent 无 view 属性，React/Flow 事件层会抛已知工件；
  // 真实鼠标事件恒有 view，产品不受影响——过滤之
  expect(errors.filter((e) => !e.includes("reading 'document'"))).toEqual([]);
  await page.screenshot({ path: "e2e-screenshots/j4-p02.png" });
  test.setTimeout(150_000);
});
