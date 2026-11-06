import { test, expect } from "@playwright/test";
import { createTempAdmin, deleteTempUser, type TempUser } from "./helpers/tempAdmin";

/**
 * 拓樸圖預設只畫常用子網路時（客戶 2026-11-05）：篩選欄以前只看到「+1」—— 標籤連說明一起放、太寬，
 * responsive 連第一個都收起來 —— 也沒說為什麼自動選。現在標籤只放 CIDR、至少顯示第一個，下面一行講明白，
 * 「顯示全部子網路」一鍵清掉。用臨時帳號設常用子網路，不動 admin 的偏好。
 */
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");

test("拓樸圖：預設只畫常用子網路時標出來，可一鍵顯示全部", async ({ page, request }) => {
  let user: TempUser | null = null;
  try {
    user = await createTempAdmin(request, "topo-pin");
    const r = await request.post("/api/v1/auth/login", { data: { username: user.username, password: user.password, realm: "local" } });
    const H = { Authorization: `Bearer ${(await r.json()).access_token}` };
    const subs = (await (await request.get("/api/v1/subnets?page_size=5", { headers: H })).json()).items as { id: string; cidr: string }[];
    test.skip(subs.length < 1, "測試資料要有子網路");
    const pin = subs[0];
    const put = await request.patch("/api/v1/me/preferences", { headers: H, data: { pinned_subnet_ids: [pin.id] } });
    expect(put.ok(), await put.text()).toBeTruthy();

    await page.goto("/login");
    await page.getByPlaceholder(/帳號|Username/).fill(user.username);
    await page.getByPlaceholder(/密碼|Password/).fill(user.password);
    await page.getByRole("button", { name: "登入", exact: true }).click();
    await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
    await page.goto("/topology");
    const filter = page.getByTestId("topology-subnet-filter");
    await expect(filter.locator(".n-tag").first()).toHaveText(pin.cidr, { timeout: 15_000 });
    const note = page.getByTestId("topology-pinned-note");
    await expect(note).toContainText("常用子網路（1 個）");
    await page.getByTestId("topology-pinned-note").screenshot({ path: "test-results/topology-pinned-note.png" }).catch(() => {});
    await note.getByRole("button", { name: "顯示全部子網路" }).click();
    await expect(note).toBeHidden();
    await expect(filter.locator(".n-tag")).toHaveCount(0);
  } finally {
    await deleteTempUser(request, user);
  }
});
