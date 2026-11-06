import { test, expect, type Page } from "@playwright/test";

/** 掃描代理頁：子網路數字可以點，彈出這台代理負責哪些子網路；點 CIDR 到子網路頁（使用者 2026-10-22）。 */
const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(ADMIN_USER);
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

test("子網路數字可以點，列出負責的子網路", async ({ page }) => {
  await login(page);
  await page.goto("/scan-agents");
  const count = page.getByTestId("agent-subnet-count").first();
  await expect(count).toBeVisible({ timeout: 15_000 });
  const n = Number((await count.innerText()).trim());
  await count.click();
  const dlg = page.getByTestId("agent-subnet-list");
  await expect(dlg).toBeVisible();
  const rows = dlg.locator("tbody tr");
  await expect(rows).toHaveCount(n);
  await expect(rows.first()).toContainText(/\d+\.\d+\.\d+\.\d+\/\d+/);
  await rows.first().getByRole("button").first().click();
  await expect(page).toHaveURL(/\/subnets\/[0-9a-f-]{36}/);
});

test("掃描代理可以改名；說明刪光儲存後真的清空", async ({ page }) => {
  await login(page);
  await page.goto("/scan-agents");
  const row = page.locator(".n-data-table-tr").filter({ has: page.getByTestId("agent-subnet-count") }).first();
  await expect(row).toBeVisible({ timeout: 15_000 });
  await row.getByRole("button", { name: /編輯/ }).click();
  const name = page.getByTestId("agent-name").locator("input");
  await expect(name).toBeEnabled();
  const orig = await name.inputValue();
  await name.fill(`${orig}-renamed`);
  const desc = page.locator(".n-modal textarea").first();
  await desc.fill("暫時的說明");
  await page.locator(".n-modal").getByRole("button", { name: /儲存/ }).click();
  await expect(page.locator(".n-data-table-tr", { hasText: `${orig}-renamed` })).toBeVisible();

  // 再編輯：清空說明、名稱改回原本
  await page.locator(".n-data-table-tr", { hasText: `${orig}-renamed` }).getByRole("button", { name: /編輯/ }).click();
  await page.getByTestId("agent-name").locator("input").fill(orig);
  await page.locator(".n-modal textarea").first().fill("");
  await page.locator(".n-modal").getByRole("button", { name: /儲存/ }).click();
  await expect(page.locator(".n-data-table-tr", { hasText: orig }).first()).toBeVisible();
  await page.locator(".n-data-table-tr").filter({ hasText: orig }).first().getByRole("button", { name: /編輯/ }).click();
  await expect(page.locator(".n-modal textarea").first()).toHaveValue("");
});

test("勾選別台代理的子網路：選單標出目前由誰掃描，儲存前要確認（客戶 2026-11-05）", async ({ page, request }) => {
  const r = await request.post("/api/v1/auth/login", { data: { username: ADMIN_USER, password: ADMIN_PASS, realm: "local" } });
  const H = { Authorization: `Bearer ${(await r.json()).access_token}` };
  const tag = Date.now().toString(36);
  const ids: { a?: string; b?: string; sn?: string } = {};
  try {
    ids.a = (await (await request.post("/api/v1/scan-agents", { headers: H, data: { name: `e2e-mv-a-${tag}` } })).json()).id;
    ids.b = (await (await request.post("/api/v1/scan-agents", { headers: H, data: { name: `e2e-mv-b-${tag}` } })).json()).id;
    const sec = (await (await request.get("/api/v1/sections", { headers: H })).json());
    const sectionId = (sec.items ?? sec)[0].id;
    const octet = 16 * (Date.now() % 15);
    const cidr = `198.18.251.${octet}/28`;
    const sn = await request.post("/api/v1/subnets", { headers: H, data: { section_id: sectionId, cidr, description: `e2e-mv-${tag}` } });
    expect(sn.status(), await sn.text()).toBe(201);
    ids.sn = (await sn.json()).id;
    const put = await request.put(`/api/v1/scan-agents/${ids.b}/subnets`, { headers: H, data: { subnet_ids: [ids.sn] } });
    expect(put.ok(), await put.text()).toBeTruthy();

    await login(page);
    await page.goto("/scan-agents");
    await page.locator(".n-data-table-tr", { hasText: `e2e-mv-a-${tag}` }).getByRole("button", { name: /編輯/ }).click();
    const modal = page.locator(".n-modal");
    const pick = modal.locator(".n-form-item", { hasText: "掃描的子網路" }).locator(".n-base-selection");
    await pick.click();
    await page.keyboard.type(cidr.split("/")[0]);
    const opt = page.locator(".n-base-select-option:visible", { hasText: cidr });
    await expect(opt).toContainText(`目前由「e2e-mv-b-${tag}」掃描`);
    await opt.click();
    await page.keyboard.press("Escape");
    await modal.getByRole("button", { name: /儲存/ }).click();
    const confirm = page.getByTestId("agent-move-confirm");
    await expect(confirm).toContainText(cidr);
    await expect(confirm).toContainText(`e2e-mv-b-${tag}`);
    await page.screenshot({ path: "test-results/agent-move-confirm.png" });
    await page.locator(".n-dialog").getByRole("button", { name: "取消" }).click();      // 取消：什麼都不動
    const still = await (await request.get(`/api/v1/scan-agents/${ids.b}/subnets`, { headers: H })).json();
    expect(still.subnet_ids).toContain(ids.sn);
    await modal.getByRole("button", { name: /儲存/ }).click();
    await page.locator(".n-dialog").getByRole("button", { name: "確定搬過來" }).click();
    await expect(page.getByTestId("agent-name")).toBeHidden();
    const moved = await (await request.get(`/api/v1/scan-agents/${ids.a}/subnets`, { headers: H })).json();
    expect(moved.subnet_ids).toContain(ids.sn);
  } finally {
    if (ids.sn) await request.delete(`/api/v1/subnets/${ids.sn}`, { headers: H });
    for (const a of [ids.a, ids.b]) if (a) await request.delete(`/api/v1/scan-agents/${a}`, { headers: H });
  }
});
