/**
 * LibreNMS「未監控的位址」（使用者 11-25：未納管比對的監控系統「librenms 也是」）。
 * 與 Zabbix / Checkmk 同一個做法：操作欄按鈕開出清單；有設限定子網路就只問那些網段（帶 subnet_ids）。
 * 攔截 API，不建真的整合。
 */
import { test, expect } from "@playwright/test";

const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");

const SUB = "00000000-0000-4000-8000-000000000051";
const INST = {
  id: "00000000-0000-4000-8000-0000000000ab", name: "lnms-gap-e2e", api_url: "https://librenms.example",
  enabled: true, verify_tls: true, sync_devices: true, sync_arp: true, sync_fdb: true, sync_vlans: true,
  sync_links: true, use_for_status: true, auto_add_devices: true, auto_create_ips: true,
  auto_create_from_arp: false, arp_create_require_fdb: true, arp_create_skip_dhcp: true,
  sync_interval_seconds: 300, scope_subnet_ids: [SUB], vlan_domain_id: null, vlan_create_missing: true,
  last_sync_at: null, last_error: null, created_at: "2026-11-25T00:00:00Z", updated_at: "2026-11-25T00:00:00Z",
};

test("操作欄的「未監控的位址」列出沒被這台 LibreNMS 監控的 IP，且只問限定網段", async ({ page }) => {
  let gapUrl = "";
  await page.route("**/api/v1/librenms/instances**", async (route) => {
    const url = route.request().url();
    if (url.includes("/coverage-gap")) {
      gapUrl = url;
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({
        items: [{ ip_address_id: "00000000-0000-4000-8000-0000000000c1", ip: "198.51.100.13", hostname: "not-monitored" }],
        count: 1, scope: "subnets" }) });
    }
    return route.fulfill({ contentType: "application/json", body: JSON.stringify({ items: [INST], total: 1, page: 1, page_size: 50 }) });
  });
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill("admin");
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: /登入/ }).click();
  await page.waitForURL((u: URL) => !u.pathname.includes("/login"));
  await page.goto("/librenms");
  await expect(page.getByText("lnms-gap-e2e").first()).toBeVisible({ timeout: 20_000 });

  await page.getByRole("button", { name: "未監控的位址" }).first().click();
  const modal = page.getByTestId("librenms-gap");
  await expect(modal).toContainText("沒有被這台 LibreNMS 監控");
  await expect(modal).toContainText("198.51.100.13");
  await expect(modal).toContainText("not-monitored");
  expect(gapUrl).toContain(`subnet_ids=${SUB}`);
  await page.screenshot({ path: "test-results/librenms-coverage-gap.png" });
});
