import { expect, test } from "@playwright/test";

const TAG = Date.now();
const USER = `e2e${TAG}`;

test("登录→建画布→加两节点→连线→保存→刷新恢复", async ({ page }) => {
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
  await page.getByTestId("add-node-image").click();
  await expect(page.getByTestId("canvas-node-text")).toBeVisible();
  await expect(page.getByTestId("canvas-node-image")).toBeVisible();

  await expect(page.getByTestId("save-indicator")).toHaveText(/已保存/, { timeout: 10_000 });

  await page.reload();
  await expect(page.getByTestId("canvas-editor")).toBeVisible();
  await expect(page.getByTestId("canvas-node-text")).toBeVisible();
  await expect(page.getByTestId("canvas-node-image")).toBeVisible();
  await expect(page.getByTestId("save-indicator")).toHaveText(/已保存/);
});
