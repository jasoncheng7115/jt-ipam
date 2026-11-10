import { test, expect, type Page } from "@playwright/test";
import { inst, mockOpenStack, mockOpenStackVms, newMock } from "./helpers/openstackMocks";

/**
 * CubeCOS／OpenStack 整合（唯讀）：整合頁、測試結果、立即同步、子網路對應、CubeCOS 節點、
 * 虛擬機清單的專案欄、IP 詳細資料的「OpenStack 網卡」。
 * 所有 /api/v1/openstack-instances 端點都用 page.route 模擬：不需要真的 OpenStack。
 */
const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");
// 第一次畫出頁面要載入整包前端，共用機器忙的時候會超過預設的 5 秒
test.setTimeout(120_000);
const FIRST = { timeout: 20_000 };

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(ADMIN_USER);
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

test("清單、選單、新增（應用程式憑證）、編輯（密碼留空、清除 CA、清空說明）", async ({ page }) => {
  const m = newMock();
  await mockOpenStack(page, m);
  await login(page);
  await page.goto("/openstack");
  // 2026-11-10 CubeCOS 開發團隊已實際搭配 jt-ipam 測試：不再標 Beta，說明改成已實測
  await expect(page.locator(".n-alert", { hasText: "CubeCOS 開發團隊已實際搭配 jt-ipam 測試" })).toBeVisible();
  await expect(page.locator(".n-card-header").first()).not.toContainText("Beta");

  const table = page.getByTestId("os-table");
  await expect(table.locator("tr", { hasText: "lab-cube" })).toContainText("Bigstack CubeCOS", FIRST);
  await expect(table.locator("tr", { hasText: "lab-cube" })).toContainText("12 / 30 / 3");
  await expect(table.locator("tr", { hasText: "public-os" })).toContainText("appcred-1");
  // 每個連線一個頁籤
  await expect(page.getByTestId("os-tab-lab-cube")).toBeVisible();
  await expect(page.getByTestId("os-tab-public-os")).toBeVisible();
  // 選單：外部系統整合底下有 CubeCOS / OpenStack
  await expect(page.locator(".n-menu").getByText("CubeCOS / OpenStack", { exact: true })).toBeAttached();

  // 新增：應用程式憑證
  await page.getByTestId("os-create").click();
  await page.getByTestId("os-name").locator("input").fill("e2e-os");
  await page.getByTestId("os-auth-url").locator("input").fill("https://keystone.lab:5000");
  await page.getByTestId("os-auth-method").getByText("應用程式憑證").click();
  await expect(page.getByTestId("os-username")).toHaveCount(0);
  await page.getByTestId("os-app-cred-id").locator("input").fill("cred-abc");
  await page.getByTestId("os-secret").locator("input").fill("very-secret");
  // 產品預設是排在左邊的 CubeCOS（10-23）；CubeCOS 網址只在選 CubeCOS 時出現，placeholder 由 Keystone 主機推得
  await expect(page.getByTestId("os-cubecos-url").locator("input")).toHaveAttribute("placeholder", "https://keystone.lab");
  await page.getByTestId("os-product").getByText("OpenStack", { exact: true }).click();
  await expect(page.getByTestId("os-cubecos-url")).toHaveCount(0);
  await page.getByTestId("os-save").click();
  await expect.poll(() => m.posted.length).toBe(1);
  expect(m.posted[0]).toMatchObject({
    name: "e2e-os", product: "openstack", auth_method: "app_credential", app_credential_id: "cred-abc",
    secret: "very-secret", username: null, project_name: null, cubecos_url: null, description: "",
  });
  expect(m.posted[0]).not.toHaveProperty("ca_cert");
  await expect(page.getByTestId("os-tab-e2e-os")).toBeVisible();

  // 編輯：密碼欄留空＝不變更；CA 已設定可清除；說明清空送空字串
  await table.locator("tr", { hasText: "lab-cube" }).getByTestId("os-edit").click();
  await expect(page.getByTestId("os-secret").locator("input")).toHaveAttribute("placeholder", "留空＝不變更");
  await expect(page.getByTestId("os-ca-set")).toBeVisible();
  await page.getByTestId("os-ca-clear").click();
  await expect(page.getByTestId("os-ca-will-clear")).toBeVisible();
  await page.getByTestId("os-description").locator("textarea").fill("");
  await page.getByTestId("os-save").click();
  await expect.poll(() => m.patched.length).toBe(1);
  expect(m.patched[0]).not.toHaveProperty("secret");
  expect(m.patched[0]).toMatchObject({ ca_cert: "", description: "", username: "ipam", interface: "internal" });
});

test("測試結果逐項顯示；立即同步走背景作業、跑完前按鈕反灰", async ({ page }) => {
  const m = newMock();
  await mockOpenStack(page, m);
  await login(page);
  await page.goto("/openstack");
  const row = page.getByTestId("os-table").locator("tr", { hasText: "lab-cube" });

  await expect(row).toBeVisible(FIRST);
  await row.getByTestId("os-test").click();
  const modal = page.locator(".n-modal").filter({ has: page.getByTestId("os-capabilities") });
  await expect(modal.getByTestId("os-test-summary")).toContainText("連線失敗");
  await expect(modal.getByTestId("os-test-error")).toContainText("服務目錄裡找不到 network 服務");
  await expect(modal.getByTestId("os-svc-identity")).toContainText("https://10.9.8.7:5000/v3");
  await expect(modal.getByTestId("os-svc-cubecos")).toContainText("連不上 CubeCOS 管理介面：connection refused");
  await expect(modal.getByTestId("os-cap-all_projects")).toContainText("只讀得到這組憑證所屬的專案");
  await expect(modal.getByTestId("os-cap-nova_microversion")).toContainText("2.79");
  await expect(modal).not.toContainText("{");                       // 不是原始 JSON
  await modal.getByRole("button", { name: "關閉" }).click();

  await row.getByTestId("os-sync").click();
  const syncBtn = page.getByTestId("os-table").locator("tr", { hasText: "lab-cube" }).getByTestId("os-sync");
  await expect(syncBtn).toBeDisabled();
  await expect(syncBtn.locator(".n-base-loading")).toBeVisible();
  await expect(syncBtn).toBeEnabled({ timeout: 15_000 });
  expect(m.taskPolls).toBeGreaterThan(2);
});

test("子網路對應：待對應標出來、選了就存；CubeCOS 節點不顯示空的 provider IP 欄", async ({ page }) => {
  const m = newMock();
  await mockOpenStack(page, m);
  await login(page);
  await page.goto("/openstack");
  await page.getByTestId("os-tab-lab-cube").click(FIRST);

  const sub = page.getByTestId("os-subnets-table");
  await expect(page.getByTestId("os-pending-count")).toContainText("1 個子網路待對應", FIRST);
  const pending = sub.locator("tr", { hasText: "192.168.77.0/24" });
  await expect(pending.getByTestId("os-map-state-pending")).toContainText("待對應");
  await expect(sub.locator("tr", { hasText: "mgmt-sub" }).getByTestId("os-map-state-auto")).toBeVisible();
  // 待對應排最前面
  await expect(sub.locator("tbody tr").first()).toContainText("192.168.77.0/24");

  await pending.getByTestId("os-map-select").click();
  await page.locator(".n-base-select-option", { hasText: "10.20.0.0/24" }).first().click();
  await expect.poll(() => m.mapped.length).toBe(1);
  expect(m.mapped[0]).toEqual({ row: "s-pend", mapped_subnet_id: "840acfe0-daf6-4d0e-8d0f-69a99caa235f" });
  await expect(sub.locator("tr", { hasText: "192.168.77.0/24" }).getByTestId("os-map-state-manual")).toBeVisible();

  await page.locator(".n-tabs-tab", { hasText: "CubeCOS 節點" }).click();
  const nodes = page.getByTestId("os-nodes-table");
  await expect(nodes).toContainText("cube-01");
  await expect(nodes).toContainText("10.99.0.11");
  await expect(nodes.locator("th", { hasText: "Provider IP" })).toHaveCount(0);

  // OpenStack（非 CubeCOS）連線沒有節點頁籤
  await page.getByTestId("os-tab-public-os").click();
  await expect(page.locator(".n-tabs-tab", { hasText: "CubeCOS 節點" })).toHaveCount(0);
});

test("虛擬機清單：OpenStack 頁有專案與主機；IP 詳細資料顯示 OpenStack 網卡", async ({ page, request }) => {
  const m = newMock();
  await mockOpenStack(page, m);
  await mockOpenStackVms(page);
  await login(page);
  await page.goto("/virt-openstack");
  await page.locator(".n-tabs-tab", { hasText: /^VM \(1\)/ }).click(FIRST);
  const vmRow = page.locator(".n-data-table-tr", { hasText: "web-a" });
  await expect(vmRow).toContainText("demo");
  await expect(vmRow).toContainText("compute-03");

  // IP 詳細資料：真的位址＋模擬的 Neutron port
  const tok = await (await request.post("/api/v1/auth/login", { data: { username: ADMIN_USER, password: ADMIN_PASS, realm: "local" } })).json();
  const list = await (await request.get("/api/v1/addresses?page=1&page_size=1",
    { headers: { Authorization: `Bearer ${tok.access_token}` } })).json();
  test.skip(!list.items?.length, "測試資料庫沒有任何 IP");
  const ipId = list.items[0].id;
  let portsBody: any[] = [{
    id: "p1", instance_id: inst().id, instance_name: "lab-cube", port_id: "7d1c6a7e-2b4f-4c61-9f8e-111111111111",
    mac: "fa:16:3e:00:00:01", status: "ACTIVE", network_name: "tenant-net", project_name: "demo", vm_name: "web-a",
    binding_host: "compute-03", floating_ips: [{ floating_ip_address: "203.0.113.20" }],
  }];
  await page.unroute(/\/api\/v1\/openstack-instances(\/.*)?(\?.*)?$/);
  await page.route(/\/api\/v1\/openstack-instances\/ports\/by-ip\/[^/?]+/, (route) => route.fulfill({ json: portsBody }));
  await page.goto(`/addresses/${ipId}`);
  const card = page.getByTestId("os-ports-card");
  await expect(card).toContainText("OpenStack 網卡", FIRST);
  await expect(card).toContainText("fa:16:3e:00:00:01");
  await expect(card).toContainText("203.0.113.20");
  await expect(card).toContainText("compute-03");

  // 沒有資料：整張不出現
  portsBody = [];
  await page.reload();
  await expect(page.locator("input, span, div").filter({ hasText: list.items[0].ip }).first()).toBeAttached({ timeout: 15_000 });
  await page.waitForLoadState("networkidle");
  await expect(page.getByTestId("os-ports-card")).toHaveCount(0);
});
