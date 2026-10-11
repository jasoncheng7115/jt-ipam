import { test, expect, type Page } from "@playwright/test";

/**
 * 背景作業即時回報（客戶 2026-10-10：「設定完要先去看工作跑完了沒，再去看 log 才知道狀態」）。
 *   1. 真的失敗：連不上的 PowerDNS 按「拉取」→ 右下角面板顯示失敗與錯誤原文，不用去作業頁
 *   2. 進度與結果：攔截 API 模擬「執行中 → 成功」，換頁後面板還在，跑完顯示摘要
 */
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(process.env.E2E_ADMIN_USER || "admin");
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

async function token(page: Page): Promise<string> {
  return (await page.evaluate(() => sessionStorage.getItem("access_token"))) || "";
}

test("拉取失敗：面板直接顯示失敗與錯誤原文", async ({ page }) => {
  await login(page);
  const name = `e2e-refused-${Date.now().toString(36)}`;
  const headers = { Authorization: `Bearer ${await token(page)}` };
  // 127.0.0.1:9 沒有人在聽：連線立刻被拒（e2e 後端的 OUTBOUND_ALLOW_CIDRS 放行 127.0.0.1）
  const r = await page.request.post("/api/v1/dns/servers", { headers,
    data: { name, type: "powerdns", api_url: "http://127.0.0.1:9", api_key: "x", enabled: true } });
  expect(r.ok(), await r.text()).toBeTruthy();
  const sid = (await r.json()).id;
  try {
    await page.goto("/dns");
    const row = page.locator("tr", { hasText: name }).first();
    await expect(row).toBeVisible({ timeout: 15_000 });
    await row.getByRole("button", { name: "拉取" }).click();
    const panel = page.getByTestId("task-tracker");
    await expect(panel).toBeVisible();
    const item = panel.getByTestId("task-tracker-item").filter({ hasText: name });
    await expect(item).toHaveAttribute("data-state", "failed", { timeout: 30_000 });
    await expect(item.getByTestId("task-tracker-status")).toContainText("失敗");
    // 錯誤原文就在面板上（以前要去作業頁、再去翻日誌）
    await expect(item.getByTestId("task-tracker-result")).toContainText(/refused|拒絕|Connect|transport/i);
    await expect(item.getByRole("button", { name: "複製錯誤訊息" })).toBeVisible();
    await expect(item.getByRole("button", { name: "到作業頁" })).toBeVisible();
    // 失敗的不會自己消失；按關閉才收起
    await item.getByRole("button", { name: "關閉" }).click();
    await expect(item).toHaveCount(0);
  } finally {
    await page.request.delete(`/api/v1/dns/servers/${sid}`, { headers });
  }
});

test("執行中顯示進度，換頁後面板還在，跑完顯示結果摘要", async ({ page }) => {
  await login(page);
  const fakeId = "00000000-0000-4000-8000-00000000e2e1";
  let phase: "running" | "succeeded" = "running";
  await page.route("**/api/v1/dns/servers/*/sync", (route) =>
    route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({ task_id: fakeId }) }));
  await page.route(`**/api/v1/tasks/${fakeId}`, (route) => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify({
      id: fakeId, kind: "dns.sync", status: phase, trigger: "manual", target_type: "dns_server", target_id: null,
      target_label: "e2e-ns1", actor_user_id: null, progress: phase === "running" ? 40 : 100,
      summary: phase === "succeeded" ? { pulled_zones: 3, pulled_records: 42 } : null, error: null,
      queued_at: new Date().toISOString(), started_at: new Date().toISOString(),
      finished_at: phase === "succeeded" ? new Date().toISOString() : null,
    }) }));
  await page.goto("/dns");
  const row = page.locator("tr", { hasText: "e2e-ns1" }).first();
  await expect(row).toBeVisible({ timeout: 15_000 });
  await row.getByRole("button", { name: "拉取" }).click();
  const item = page.getByTestId("task-tracker-item").filter({ hasText: "e2e-ns1" });
  await expect(item).toHaveAttribute("data-state", "running", { timeout: 10_000 });
  await expect(item.getByTestId("task-tracker-status")).toContainText("執行中");
  await expect(item.locator(".n-progress")).toBeVisible();

  await page.goto("/tasks");   // 換頁：面板跟著走
  await expect(item).toBeVisible();
  phase = "succeeded";
  await expect(item).toHaveAttribute("data-state", "succeeded", { timeout: 10_000 });
  await expect(item.getByTestId("task-tracker-result")).toContainText("records 42");
});
