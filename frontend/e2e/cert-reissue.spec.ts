import { test, expect, type APIRequestContext, type Page } from "@playwright/test";

/**
 * 憑證重新簽發（2026-11-03）：CA 出事要全面換憑證時，拿到新憑證之後的幾個步驟。
 * - 已有版本時「上傳新版」的私鑰可以留空（沿用目前版本的私鑰）；換了金鑰的憑證要講清楚
 * - 可以直接上傳 PFX / P12
 * - 憑證清單有「簽發者」欄與簽發者篩選
 */
const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");

async function headers(request: APIRequestContext): Promise<Record<string, string>> {
  const r = await request.post("/api/v1/auth/login", { data: { username: ADMIN_USER, password: ADMIN_PASS, realm: "local" } });
  return { Authorization: `Bearer ${(await r.json()).access_token}` };
}

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(ADMIN_USER);
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

async function selfSigned(request: APIRequestContext, H: Record<string, string>, name: string, cn: string) {
  const c = await (await request.post("/api/v1/certificates", { headers: H, data: { name } })).json();
  const v = await (await request.post(`/api/v1/certificates/${c.id}/self-signed`,
    { headers: H, data: { common_name: cn, sans: [cn], days: 90 } })).json();
  return { id: c.id as string, vid: v.id as string };
}

async function openUpload(page: Page, name: string) {
  await page.getByPlaceholder(/篩選名稱/).fill(name);
  await page.locator("tr", { hasText: name }).getByRole("button", { name: "上傳新版" }).click();
}

test("重新簽發：私鑰留空沿用、換了金鑰要講清楚、PFX 上傳、簽發者欄與篩選", async ({ page, request }) => {
  const H = await headers(request);
  const tag = `reissue-${Date.now().toString(36)}`;
  const a = await selfSigned(request, H, `${tag}-a`, "a.example.net");
  const b = await selfSigned(request, H, `${tag}-b`, "b.example.net");
  const created = [a.id, b.id];
  try {
    const otherCert = await (await request.get(`/api/v1/certificates/${b.id}/versions/${b.vid}/file?fmt=cert`, { headers: H })).text();
    const pfx = await (await request.post(`/api/v1/certificates/${b.id}/versions/${b.vid}/file`,
      { headers: H, data: { fmt: "pfx", password: "Pfx-e2e-123" } })).body();

    await login(page);
    await page.goto("/certificates");

    // 簽發者欄：自簽的顯示「自簽」；簽發者篩選有選項
    await page.getByPlaceholder(/篩選名稱/).fill(tag);
    await expect(page.locator("tr", { hasText: `${tag}-a` }).getByTestId("cert-issuer")).toHaveText("自簽");
    await page.getByTestId("cert-issuer-filter").click();
    await expect(page.locator(".n-base-select-option", { hasText: "自簽" }).first()).toBeVisible();   // 自簽列成「自簽」，不是主機名稱
    await page.keyboard.press("Escape");

    // 已有版本：私鑰可以留空，說明在對話框上
    await openUpload(page, `${tag}-a`);
    const dlg = page.locator(".n-card", { hasText: `${tag}-a` }).last();
    await expect(dlg.getByTestId("cert-up-key-optional")).toBeVisible();
    await expect(dlg.getByText("私鑰（.key，選用）")).toBeVisible();
    // 放一張別的金鑰簽的憑證、不附私鑰 → 講清楚是換了金鑰
    await dlg.locator('input[type="file"]').first().setInputFiles(
      { name: "new.crt", mimeType: "application/x-pem-file", buffer: Buffer.from(otherCert) });
    await dlg.getByTestId("cert-up-submit").click();
    await expect(page.getByText(/簽發時換了金鑰/)).toBeVisible();

    // PFX：同一張憑證 b 的 PFX 傳到新的一張 c
    const c = await (await request.post("/api/v1/certificates", { headers: H, data: { name: `${tag}-c` } })).json();
    created.push(c.id);
    await page.keyboard.press("Escape");
    await page.reload();
    await openUpload(page, `${tag}-c`);
    const dlg2 = page.locator(".n-card", { hasText: `${tag}-c` }).last();
    await expect(dlg2.getByTestId("cert-up-key-optional")).toHaveCount(0);   // 沒有版本：私鑰必填
    await dlg2.getByTestId("cert-up-mode-pfx").click();
    await dlg2.getByTestId("cert-up-pfx").setInputFiles({ name: "b.pfx", mimeType: "application/x-pkcs12", buffer: pfx });
    await dlg2.getByTestId("cert-up-pfx-password").locator("input").fill("wrong-password");
    await dlg2.getByTestId("cert-up-submit").click();
    await expect(page.getByText(/PFX 密碼不對/)).toBeVisible();
    await dlg2.getByTestId("cert-up-pfx-password").locator("input").fill("Pfx-e2e-123");
    const up = page.waitForResponse((r) => r.url().includes(`/certificates/${c.id}/versions`) && r.request().method() === "POST");
    await dlg2.getByTestId("cert-up-submit").click();
    expect((await up).status()).toBe(201);
    await expect(page.locator("tr", { hasText: `${tag}-c` })).toContainText("b.example.net");
  } finally {
    for (const id of created) await request.delete(`/api/v1/certificates/${id}`, { headers: H });
  }
});
