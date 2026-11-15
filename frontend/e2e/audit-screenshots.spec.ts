import { expect, test } from "@playwright/test";

const TAG = Date.now();
const USER = `shot${TAG}`;

test("登录并截图全部页面", async ({ page }) => {
  await page.goto("/register");
  await page.getByLabel("用户名").fill(USER);
  await page.getByLabel("邮箱").fill(`${USER}@shot.io`);
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByLabel("邀请码").fill(process.env.INVITE ?? "letmein");
  await page.getByRole("button", { name: "注册" }).click();
  await expect(page.getByText(/工作台/)).toBeVisible();

  const pages: Array<[string, string]> = [
    ["/", "01-home"],
    ["/generate/image", "02-image"],
    ["/generate/video", "03-video"],
    ["/prompts", "04-prompts"],
    ["/tasks", "05-tasks"],
    ["/projects", "06-projects"],
    ["/settings/channels", "07-channels"],
  ];
  for (const [path, name] of pages) {
    await page.goto(path);
    await page.waitForTimeout(600);
    await page.screenshot({ path: `e2e-screenshots/${name}.png`, fullPage: false });
  }

  // 画布页：建项目进画布，加节点后截图
  await page.goto("/projects");
  await page.getByRole("button", { name: /新建画布/ }).click();
  await page.waitForTimeout(800);
  await page.getByText("未命名画布").first().click();
  await expect(page.getByTestId("canvas-editor")).toBeVisible();
  await page.getByTestId("add-node-text").click();
  await page.getByTestId("add-node-image").click();
  await page.waitForTimeout(600);
  await page.screenshot({ path: "e2e-screenshots/08-canvas.png" });

  // 提示词面板
  await page.getByTestId("canvas-node-text").click();
  await page.waitForTimeout(400);
  await page.screenshot({ path: "e2e-screenshots/09-panel.png" });
});
