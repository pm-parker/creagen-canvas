import { test, Page } from "@playwright/test";

const errors: string[] = [];
let page: Page;

async function auditPage(name: string, path: string, extra?: () => Promise<void>) {
  await page.goto(path);
  await page.waitForTimeout(1200);
  const buttons = await page.getByRole("button").all();
  const report: string[] = [];
  for (const b of buttons) {
    const visible = await b.isVisible().catch(() => false);
    if (!visible) continue;
    const text = (await b.textContent())?.trim().slice(0, 18) || "[icon]";
    const disabled = await b.isDisabled();
    report.push(`${text}${disabled ? "(disabled)" : ""}`);
  }
  console.log(`AUDIT ${name} [${path}] buttons: ${JSON.stringify(report)}`);
  if (extra) await extra();
  await page.screenshot({ path: `e2e-screenshots/audit-${name}.png`, fullPage: false });
}

test.describe.configure({ mode: "serial" });

test("全站交互审计", async ({ browser }) => {
  const ctx = await browser.newContext();
  page = await ctx.newPage();
  page.on("pageerror", (e) => errors.push(`[pageerror] ${path0}: ${e.message.slice(0, 150)}`));
  page.on("console", (m) => {
    if (m.type() === "error" && !m.text().includes("favicon")) errors.push(`[console] ${m.text().slice(0, 150)}`);
  });
  let path0 = "/login";

  // 0. 登录页
  await auditPage("login", "/login");

  // 注册
  const T = Date.now();
  await page.goto("/register");
  await page.getByLabel("用户名").fill(`au${T}`);
  await page.getByLabel("邮箱").fill(`au${T}@x.io`);
  await page.getByLabel("密码").fill(process.env.CANVAS_E2E_PASSWORD ?? "");
  await page.getByLabel("邀请码").fill("gan-2026");
  await page.getByRole("button", { name: "注册" }).click();
  await page.waitForTimeout(2000);

  // 1-8 各页
  path0 = "/";
  await auditPage("home", "/");
  path0 = "/generate/image";
  await auditPage("image", "/generate/image");
  path0 = "/generate/video";
  await auditPage("video", "/generate/video");
  path0 = "/prompts";
  await auditPage("prompts", "/prompts");
  path0 = "/tasks";
  await auditPage("tasks", "/tasks");
  path0 = "/projects";
  await auditPage("projects", "/projects");
  path0 = "/assets";
  await auditPage("assets", "/assets");
  path0 = "/costs";
  await auditPage("costs", "/costs");
  path0 = "/settings/channels";
  await auditPage("channels", "/settings/channels");

  // 画布页：新建画布进入
  await page.goto("/projects");
  await page.waitForTimeout(800);
  const createBtn = page.getByRole("button", { name: /新建画布|新建/ }).first();
  if (await createBtn.isVisible().catch(() => false)) {
    await createBtn.click();
    await page.waitForTimeout(800);
    const nameInput = page.getByLabel(/画布名|名称/).first();
    if (await nameInput.isVisible().catch(() => false)) await nameInput.fill(`审计画布${T}`);
    const confirm = page.getByRole("button", { name: /创建|确定/ }).last();
    await confirm.click().catch(() => {});
    await page.waitForTimeout(1500);
  }
  path0 = "/canvas";
  await auditPage("canvas", page.url(), async () => {
    // 加一个 text 节点检查节点交互
    const addBtn = page.getByRole("button", { name: /文本|提示词/ }).first();
    if (await addBtn.isVisible().catch(() => false)) {
      await addBtn.click();
      await page.waitForTimeout(800);
    }
  });

  // 404 兜底
  path0 = "/nonexistent-404";
  await auditPage("notfound", "/nonexistent-404");

  console.log("ALL_ERRORS:", JSON.stringify(errors.slice(0, 15)));
});
