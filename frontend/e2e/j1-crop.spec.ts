import { test } from "@playwright/test";

test("J1 裁切：拖拽选区 → 裁切新节点", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message.slice(0, 150)));
  await page.goto("/login");
  await page.getByLabel("用户名").fill(process.env.CANVAS_E2E_USER ?? "demo");
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForTimeout(2000);

  // 用带图节点的画布 18
  await page.goto("/canvas/18");
  await page.waitForTimeout(2500);
  const node = page.locator(".react-flow__node").first();
  await node.hover();
  await page.waitForTimeout(400);
  const cropBtn = page.getByLabel("裁切图片");
  console.log("裁切按钮:", await cropBtn.isVisible().catch(() => false));
  await cropBtn.click();
  await page.waitForTimeout(1200);
  console.log("对话框:", await page.getByText("裁切图片", { exact: true }).isVisible().catch(() => false) || await page.getByRole("heading", { name: "裁切图片" }).isVisible().catch(() => false));

  // 拖拽选区（画布中央区域）
  const stage = page.locator("[data-testid=crop-stage]");
  await stage.scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  const box = await stage.boundingBox();
  if (!box) throw new Error("stage 不可见");
  const x0 = box.x + box.width * 0.2, y0 = box.y + box.height * 0.2;
  const x1 = box.x + box.width * 0.7, y1 = box.y + box.height * 0.7;
  const opts = (x: number, y: number) => ({ clientX: x, clientY: y, button: 0, bubbles: true });
  await stage.dispatchEvent("mousedown", opts(x0, y0));
  for (let i = 1; i <= 5; i++) {
    await stage.dispatchEvent("mousemove", opts(x0 + ((x1 - x0) * i) / 5, y0 + ((y1 - y0) * i) / 5));
  }
  await stage.dispatchEvent("mouseup", opts(x1, y1));
  await page.waitForTimeout(400);
  console.log("选区提示:", await page.getByText(/选区 \d+×\d+/).isVisible().catch(() => false));
  const before = await page.locator(".react-flow__node").count();

  await page.getByRole("button", { name: /裁切为新节点/ }).click();
  await page.waitForTimeout(2500);
  const after = await page.locator(".react-flow__node").count();
  console.log("节点数:", before, "→", after, "| 新增:", after === before + 1 ? "OK" : "FAIL");
  console.log("连线存在:", await page.locator(".react-flow__edge").count());
  console.log("ERRORS:", errors.length);
  await page.screenshot({ path: "e2e-screenshots/j1-crop.png" });
});
