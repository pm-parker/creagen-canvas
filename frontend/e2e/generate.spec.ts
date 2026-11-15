import { expect, test } from "@playwright/test";

const TAG = Date.now();
const USER = `w3e2e${TAG}`;

test("生成闭环：prompt→生成→徽标→回流节点→保存", async ({ page }) => {
  await page.goto("/register");
  await page.getByLabel("用户名").fill(USER);
  await page.getByLabel("邮箱").fill(`${USER}@e2e.io`);
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByLabel("邀请码").fill(process.env.INVITE ?? "gan-2026");
  await page.getByRole("button", { name: "注册" }).click();
  await expect(page.getByText(/工作台/)).toBeVisible();

  await page.goto("/projects");
  await page.getByRole("button", { name: /新建画布/ }).click();
  await page.waitForTimeout(800);
  await page.getByText("未命名画布").first().click();
  await expect(page.getByTestId("canvas-editor")).toBeVisible();

  await page.getByTestId("add-node-text").click();
  await expect(page.getByTestId("canvas-node-text")).toBeVisible();
  await page.getByTestId("canvas-node-text").click();
  const box = page.getByRole("textbox", { name: "提示词编辑" });
  await box.fill("一只在月球上奔跑的猫");
  await page.keyboard.press("Escape");

  await page.getByTestId("generate-button").click();
  await expect(page.getByLabel("生成完成")).toBeVisible({ timeout: 20_000 });

  await expect(page.getByTestId("canvas-node-text")).toHaveCount(2);

  await expect(page.getByTestId("save-indicator")).toHaveText(/已保存/, { timeout: 10_000 });
  await page.reload();
  await expect(page.getByTestId("canvas-node-text")).toHaveCount(2);
});
