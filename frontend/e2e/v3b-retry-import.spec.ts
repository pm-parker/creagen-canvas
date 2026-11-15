import { test } from "@playwright/test";

test("V3b 任务重试 + 画布导入 JSON", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("用户名").fill(process.env.CANVAS_E2E_USER ?? "demo");
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForTimeout(2000);

  // 1. 任务重试
  await page.goto("/tasks");
  await page.waitForTimeout(1500);
  await page.getByRole("button", { name: "失败" }).click();
  await page.waitForTimeout(1200);
  await page.getByRole("button", { name: "重试" }).first().click();
  await page.waitForTimeout(2500);
  const body = await page.locator("body").innerText();
  console.log("V3b-1 重试后新任务排队:", body.includes("排队中") ? "OK" : "CHECK");
  console.log("V3b-1b 新任务出现在列表:", body.includes("把这块改成夜空") ? "OK" : "-");

  // 2. 画布导入 JSON（dispatch File 到 input）
  await page.goto("/projects");
  await page.waitForTimeout(1200);
  const href = await page.locator("a[href^='/canvas/']").first().getAttribute("href");
  await page.goto(href ?? "/canvas/18");
  await page.waitForTimeout(2500);
  const data = JSON.stringify({
    app: "creagen-canvas",
    version: 1,
    title: `导入验证${Date.now() % 1000}`,
    viewport: { x: 0, y: 0, zoom: 1 },
    nodes: [
      { id: "n1", type: "text", position: { x: 0, y: 0 }, title: "导入节点", prompt: "导入的提示词" },
    ],
    connections: [],
  });
  const fileInput = page.locator('input[type="file"]');
  await fileInput.setInputFiles({ name: "import.json", mimeType: "application/json", buffer: Buffer.from(data) });
  await page.waitForTimeout(3000);
  console.log("V3b-2 导入后跳转:", page.url());
  console.log("V3b-2b 导入节点:", await page.getByText("导入节点").isVisible().catch(() => false));
});
