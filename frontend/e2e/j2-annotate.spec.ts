import { test } from "@playwright/test";

test("J2 标注画板：画笔+文字 → 导出标注图新节点", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message.slice(0, 150)));
  await page.goto("/login");
  await page.getByLabel("用户名").fill(process.env.CANVAS_E2E_USER ?? "demo");
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForTimeout(2000);

  await page.goto("/canvas/18");
  await page.waitForTimeout(2500);
  await page.locator(".react-flow__node").first().hover();
  await page.getByLabel("标注图片").first().click();
  await page.waitForTimeout(1200);
  console.log("标注工具栏:", await page.getByLabel("画笔").isVisible().catch(() => false));

  // 画一笔（dispatch 事件序列在 annotate canvas 上）
  const canvas = page.locator("[data-testid=annotate-canvas]");
  await canvas.scrollIntoViewIfNeeded();
  await page.waitForTimeout(400);
  const box = await canvas.boundingBox();
  if (!box) throw new Error("canvas 不可见");
  const seq = (x: number, y: number) => ({ clientX: box.x + box.width * x, clientY: box.y + box.height * y, button: 0, bubbles: true });
  await canvas.dispatchEvent("mousedown", seq(0.2, 0.3));
  for (const [x, y] of [[0.3, 0.4], [0.4, 0.35], [0.5, 0.5]]) {
    await canvas.dispatchEvent("mousemove", seq(x, y));
  }
  await canvas.dispatchEvent("mouseup", seq(0.5, 0.5));
  await page.waitForTimeout(500);
  console.log("导出按钮可用:", await page.getByRole("button", { name: /导出标注图/ }).isEnabled().catch(() => false));

  const before = await page.locator(".react-flow__node").count();
  await page.getByRole("button", { name: /导出标注图/ }).click();
  await page.waitForTimeout(2500);
  const after = await page.locator(".react-flow__node").count();
  console.log("节点数:", before, "→", after, after === before + 1 ? "OK" : "FAIL");
  console.log("ERRORS:", JSON.stringify(errors));
  await page.screenshot({ path: "e2e-screenshots/j2-annotate.png" });
});
