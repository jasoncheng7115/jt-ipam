/**
 * Kubernetes 整合頁（真的叢集）：用 token 與貼 kubeconfig 兩種方式新增、測試連線、同步、各頁籤、唯讀帳號設定、刪除；
 * 第二階段：網域名稱頁籤、Calico / Whereabouts 集區、可路由的 Pod 網段與開關、舊版唯讀帳號的缺權限提示。
 *
 * 要 scripts/k8s-test-target/run.sh 起的 k3s（MetalLB、Traefik）與 kind（Cilium、Gateway API、Calico、Whereabouts、
 * OpenShift Route 的 CRD）：`E2E_K8S_DIR` 指向它寫出的目錄，
 * 後端要把兩個 API 的 IP 放進 OUTBOUND_ALLOW_CIDRS。沒設就略過。
 */
import { test, expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
const DIR = process.env.E2E_K8S_DIR || "";
test.skip(!ADMIN_PASS || !DIR, "需要 E2E_ADMIN_PASS 與 E2E_K8S_DIR");
test.setTimeout(240_000);
const read = (f: string) => readFileSync(`${DIR}/${f}`, "utf-8").trim();

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(ADMIN_USER);
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

/** 在頁面裡打 API（沿用登入後的 token 與同源代理） */
async function api(page: Page, method: string, path: string, body?: unknown) {
  return page.evaluate(async ({ method, path, body }) => {
    const r = await fetch(path, { method, body: body ? JSON.stringify(body) : undefined, headers: {
      "Content-Type": "application/json", Authorization: `Bearer ${sessionStorage.getItem("access_token")}` } });
    return { status: r.status, json: r.status === 204 ? null : await r.json().catch(() => null) };
  }, { method, path, body });
}

/** seed_e2e 的 203.0.113.0/24 裡要有這筆 IP（有就沿用；做完不刪，刪掉的位址會進冷卻期） */
async function ensureIp(page: Page, ip: string): Promise<string> {
  const found = await api(page, "GET", `/api/v1/addresses?q=${ip}&page_size=50`);
  const hit = found.json.items.find((x: any) => String(x.ip).split("/")[0] === ip);
  if (hit) return hit.id;
  const subs = await api(page, "GET", "/api/v1/subnets?page_size=500");
  const sn = subs.json.items.find((s: any) => s.cidr === "203.0.113.0/24");
  await api(page, "POST", `/api/v1/addresses/cooldowns/${sn.id}/${ip}/clear`, { reason: "e2e" });
  const r = await api(page, "POST", "/api/v1/addresses", { subnet_id: sn.id, ip });
  expect(r.status, JSON.stringify(r.json)).toBe(201);
  return r.json.id;
}

async function del(page: Page, name: string) {
  await page.getByTestId("k8s-tabs").locator(".n-tabs-tab").first().click();   // 第一個頁籤是叢集清單
  await page.getByTestId("k8s-table").locator("tr", { hasText: name }).getByTestId("k8s-delete").click();
  await page.mouse.move(5, 5);                                   // 刪除鈕的提示框會蓋住確認鈕
  await page.locator(".n-popconfirm").getByRole("button", { name: /確定|確認|OK/ }).click();
  await expect(page.getByTestId("k8s-table").locator("tr", { hasText: name })).toHaveCount(0);
}

test("k3s：token 連線、測試、同步、節點／Service／集區頁籤", async ({ page }) => {
  const name = `e2e-k3s-${Date.now().toString(36)}`;
  await login(page);
  await page.goto("/k8s");
  await expect(page.locator(".n-card-header")).toContainText("整合 Kubernetes", { timeout: 20_000 });
  await expect(page.locator(".n-menu").getByText("Kubernetes", { exact: true })).toBeAttached();

  // 唯讀帳號設定：下載的就是只有 get／list 的那一份
  const [dl] = await Promise.all([page.waitForEvent("download"), page.getByTestId("k8s-manifest").click()]);
  expect(dl.suggestedFilename()).toBe("jt-ipam-readonly.yaml");
  expect(readFileSync(await dl.path(), "utf-8")).toContain("verbs: [get, list]");

  await page.getByTestId("k8s-create").click();
  await page.getByTestId("k8s-name").locator("input").fill(name);
  await page.getByTestId("k8s-api-url").locator("input").fill(read("k3s.url"));
  await page.getByTestId("k8s-token").locator("input").fill(read("k3s.token"));
  await page.getByTestId("k8s-ca").locator("textarea").fill(read("k3s.ca"));
  await page.getByTestId("k8s-save").click();
  const row = page.getByTestId("k8s-table").locator("tr", { hasText: name });
  await expect(row).toBeVisible();

  await row.getByTestId("k8s-test").click();
  const health = page.getByTestId("k8s-health");
  await expect(health).toContainText(/v1\.\d+/, { timeout: 30_000 });
  await expect(page.getByTestId("k8s-check-nodes")).toContainText("讀得到");
  await expect(page.getByTestId("k8s-check-metallb")).toContainText("共 1 筆");
  await expect(page.getByTestId("k8s-check-cilium")).toContainText("沒有安裝");
  await expect(page.getByTestId("k8s-check-ingresses")).toContainText("讀得到");
  await expect(page.getByTestId("k8s-check-routes")).toContainText("沒有安裝");
  await page.screenshot({ path: "test-results/k8s-test.png" });
  await page.keyboard.press("Escape");

  await row.getByTestId("k8s-sync").click();
  await expect(page.getByTestId("k8s-table").locator("tr", { hasText: name }).getByTestId("k8s-sync"))
    .toBeEnabled({ timeout: 90_000 });
  await page.waitForTimeout(1000);
  await page.getByTestId(`k8s-tab-${name}`).click();
  await expect(page.getByTestId("k8s-nodes")).toContainText("Ready", { timeout: 20_000 });
  await page.screenshot({ path: "test-results/k8s-nodes.png" });
  await page.locator(".n-tabs-tab", { hasText: /^Service 位址/ }).click();
  const svcs = page.getByTestId("k8s-services");
  await expect(svcs).toContainText("192.0.2.50");
  await expect(svcs).toContainText("externalIPs");
  await expect(svcs.locator("tr", { hasText: "LoadBalancer" }).filter({ hasText: "web" })).toContainText("192.0.2.2");
  // Traefik（k3s 內建的 Ingress 控制器）從 MetalLB 拿到位址，寫回 Ingress
  await page.locator(".n-tabs-tab", { hasText: /^網域名稱/ }).click();
  const hosts = page.getByTestId("k8s-hostnames");
  await expect(hosts.locator("tr", { hasText: "shop.example.com" })).toContainText("192.0.2.2", { timeout: 20_000 });
  await expect(hosts.locator("tr", { hasText: "shop.example.com" })).toContainText("TLS");
  await expect(hosts.locator("tr", { hasText: "api.example.com" })).toContainText("沒有記錄");
  await page.screenshot({ path: "test-results/k8s-hostnames.png" });
  await page.locator(".n-tabs-tab", { hasText: /^位址集區/ }).click();
  await expect(page.getByTestId("k8s-pools")).toContainText("192.0.2.200 - 192.0.2.220");
  await expect(page.getByTestId("k8s-pools")).toContainText("MetalLB");
  await page.screenshot({ path: "test-results/k8s-pools.png" });
  await page.locator(".n-tabs-tab", { hasText: /^叢集網段/ }).click();
  await expect(page.getByTestId("k8s-cidrs")).toContainText("Pod 網段");
  await page.locator(".n-tabs-tab", { hasText: /^衝突/ }).click();
  await expect(page.locator(".n-tab-pane").last()).toContainText(/跟 IPAM/);

  await del(page, name);
});

test("kind：貼 kubeconfig 新增、Cilium 集區", async ({ page }) => {
  const name = `e2e-kind-${Date.now().toString(36)}`;
  const kc = [
    "apiVersion: v1", "kind: Config", "current-context: jt",
    "clusters:", "- name: kind", "  cluster:", `    server: ${read("kind.url")}`,
    `    certificate-authority-data: ${Buffer.from(read("kind.ca")).toString("base64")}`,
    "users:", "- name: ro", "  user:", `    token: ${read("kind.token")}`,
    "contexts:", "- name: jt", "  context: {cluster: kind, user: ro}",
  ].join("\n");
  await login(page);
  await page.goto("/k8s");
  await page.getByTestId("k8s-create").click({ timeout: 20_000 });
  await page.getByTestId("k8s-name").locator("input").fill(name);
  await page.getByTestId("k8s-mode").getByText("貼上 kubeconfig").click();
  await page.getByTestId("k8s-kubeconfig").locator("textarea").fill(kc);
  await page.getByTestId("k8s-save").click();
  const row = page.getByTestId("k8s-table").locator("tr", { hasText: name });
  await expect(row).toContainText(read("kind.url"));
  const gwIp = await ensureIp(page, "203.0.113.201");                     // Gateway 的位址：IP 詳細頁要看得到名稱
  await row.getByTestId("k8s-test").click();
  await expect(page.getByTestId("k8s-check-cilium")).toContainText("共 1 筆", { timeout: 30_000 });
  await expect(page.getByTestId("k8s-check-gateways")).toContainText("共 1 筆");
  await expect(page.getByTestId("k8s-check-calico")).toContainText("共 3 筆");
  await expect(page.getByTestId("k8s-check-whereabouts")).toContainText("共 1 筆");
  await expect(page.getByTestId("k8s-check-routes")).toContainText("共 1 筆");
  await expect(page.getByTestId("k8s-check-ovn_egress")).toContainText("共 1 筆");
  await expect(page.getByTestId("k8s-check-nads")).toContainText("共 2 筆");
  await page.screenshot({ path: "test-results/k8s-test-kind.png" });
  await page.keyboard.press("Escape");
  await row.getByTestId("k8s-sync").click();
  await expect(page.getByTestId("k8s-table").locator("tr", { hasText: name }).getByTestId("k8s-sync"))
    .toBeEnabled({ timeout: 90_000 });
  await page.waitForTimeout(1000);
  await page.getByTestId(`k8s-tab-${name}`).click();
  await page.locator(".n-tabs-tab", { hasText: /^位址集區/ }).click();
  await expect(page.getByTestId("k8s-pools")).toContainText("Cilium", { timeout: 20_000 });
  await expect(page.getByTestId("k8s-pools")).toContainText("203.0.113.100 - 203.0.113.110");
  await expect(page.getByTestId("k8s-pools").locator("tr", { hasText: "Whereabouts" })).toContainText("Pod 次要網卡");
  await expect(page.getByTestId("k8s-pools").locator("tr", { hasText: "Calico" })).toContainText("203.0.113.224 - 203.0.113.239");
  await expect(page.getByTestId("k8s-pools").locator("tr", { hasText: "Antrea" })).toContainText("LoadBalancer / Egress");
  await expect(page.getByTestId("k8s-pools").locator("tr", { hasText: "Multus" })).toContainText("203.0.113.66 - 203.0.113.78");
  await page.locator(".n-tabs-tab", { hasText: /^Service 位址/ }).click();
  const svcs = page.getByTestId("k8s-services");
  await expect(svcs.locator("tr", { hasText: "db-0" })).toContainText("Pod 次要網卡");
  await expect(svcs.locator("tr", { hasText: "legacy-db" })).toContainText("203.0.113.90");          // Multus static
  await expect(svcs.locator("tr", { hasText: "203.0.113.150" })).toContainText("Egress IP");
  await expect(svcs.locator("tr", { hasText: "203.0.113.150" })).toContainText("jtipam-control-plane");
  await expect(svcs.locator("tr", { hasText: "API 伺服器" })).toContainText(new URL(read("kind.url")).hostname);
  await page.waitForTimeout(500);
  await page.screenshot({ path: "test-results/k8s-services-kind.png" });
  await page.locator(".n-tabs-tab", { hasText: /^網域名稱/ }).click();
  const hosts = page.getByTestId("k8s-hostnames");
  await expect(hosts.locator("tr", { hasText: "console.apps.example.com" })).toContainText("203.0.113.202");
  await expect(hosts.locator("tr", { hasText: "console.apps.example.com" })).toContainText("OpenShift Route");
  await expect(hosts.locator("tr", { hasText: "blog.apps.example.com" })).toContainText("HTTPRoute");
  await expect(hosts.locator("tr", { hasText: "*.apps.example.com" })).toContainText("萬用字元");
  await page.screenshot({ path: "test-results/k8s-hostnames-kind.png" });
  await page.locator(".n-tabs-tab", { hasText: /^叢集網段/ }).click();
  const cidrs = page.getByTestId("k8s-cidrs");
  await expect(cidrs.locator("tr", { hasText: "10.80.0.0/16" })).toContainText("是");
  await expect(cidrs.locator("tr", { hasText: "10.244.0.0/16" })).toContainText("VXLAN");
  await page.waitForTimeout(500);                                       // 頁籤切換有動畫
  await page.screenshot({ path: "test-results/k8s-cidrs-kind.png" });

  // 叢集標成「Pod 網段可直接路由」→ 節點的 podCIDR 顯示「是（叢集設定）」
  await page.getByTestId("k8s-tabs").locator(".n-tabs-tab").first().click();
  const clear = page.getByTestId("task-tracker-clear");                // 背景作業面板會蓋住最後一列的操作鈕
  if (await clear.isVisible()) await clear.click();
  await page.getByTestId("k8s-table").locator("tr", { hasText: name }).getByTestId("k8s-edit").click();
  await page.getByTestId("k8s-pod-routable").click();
  await page.getByTestId("k8s-save").click();
  await expect(page.getByTestId("k8s-save")).toHaveCount(0);
  await page.getByTestId(`k8s-tab-${name}`).click();
  await page.locator(".n-tabs-tab", { hasText: /^叢集網段/ }).click();
  await expect(page.getByTestId("k8s-cidrs").locator("tr", { hasText: "10.244.0." }).first()).toBeVisible();
  await expect(page.getByTestId("k8s-cidrs")).toContainText("是（叢集設定）");

  // IP 詳細頁的 Kubernetes 卡片：指向這個位址的名稱
  await page.goto(`/addresses/${gwIp}`);
  const card = page.getByTestId("k8s-ip-card");
  await expect(card.getByTestId("k8s-ip-hostnames")).toContainText("blog.apps.example.com", { timeout: 20_000 });
  await expect(card.getByTestId("k8s-ip-hostnames")).toContainText("*.apps.example.com");
  await card.scrollIntoViewIfNeeded();
  await page.screenshot({ path: "test-results/k8s-ip-card.png" });
  await page.goto("/k8s");
  await del(page, name);

  // 異常偵測多了「Kubernetes 位址衝突」這一類
  await page.goto("/anomaly?tab=k8s_conflicts");
  await expect(page.locator(".n-tabs-tab--active")).toContainText("Kubernetes 位址衝突", { timeout: 20_000 });
});

test("第一版唯讀帳號：同步照跑、列出缺少的權限", async ({ page }) => {
  const name = `e2e-k3s1-${Date.now().toString(36)}`;
  await login(page);
  await page.goto("/k8s");
  await page.getByTestId("k8s-create").click({ timeout: 20_000 });
  await page.getByTestId("k8s-name").locator("input").fill(name);
  await page.getByTestId("k8s-api-url").locator("input").fill(read("k3s.url"));
  await page.getByTestId("k8s-token").locator("input").fill(read("k3s-phase1.token"));
  await page.getByTestId("k8s-ca").locator("textarea").fill(read("k3s.ca"));
  await page.getByTestId("k8s-save").click();
  const row = page.getByTestId("k8s-table").locator("tr", { hasText: name });
  await row.getByTestId("k8s-sync").click();
  await expect(page.getByTestId("k8s-table").locator("tr", { hasText: name }).getByTestId("k8s-sync"))
    .toBeEnabled({ timeout: 90_000 });
  await page.waitForTimeout(1000);
  await page.getByTestId(`k8s-tab-${name}`).click();
  await expect(page.getByTestId("k8s-missing-perms")).toContainText("ingresses.networking.k8s.io", { timeout: 20_000 });
  await expect(page.getByTestId("k8s-missing-perms")).not.toContainText("route.openshift.io");   // k3s 沒裝的不算
  await expect(page.getByTestId("k8s-nodes")).toContainText("Ready");
  await page.screenshot({ path: "test-results/k8s-missing-perms.png" });
  await del(page, name);
});

test("兩個叢集用了同一個位址：兩邊的衝突頁與異常偵測都看得到", async ({ page }) => {
  // 兩個測試叢集都有 jt-demo/legacy，externalIPs 是 192.0.2.50
  const a = `e2e-xa-${Date.now().toString(36)}`;
  const b = `e2e-xb-${Date.now().toString(36)}`;
  await login(page);
  await page.goto("/k8s");
  for (const [name, t] of [[a, "k3s"], [b, "kind"]] as const) {
    await page.getByTestId("k8s-create").click({ timeout: 20_000 });
    await page.getByTestId("k8s-name").locator("input").fill(name);
    await page.getByTestId("k8s-api-url").locator("input").fill(read(`${t}.url`));
    await page.getByTestId("k8s-token").locator("input").fill(read(`${t}.token`));
    await page.getByTestId("k8s-ca").locator("textarea").fill(read(`${t}.ca`));
    await page.getByTestId("k8s-save").click();
    const row = page.getByTestId("k8s-table").locator("tr", { hasText: name });
    const clear = page.getByTestId("task-tracker-clear");              // 背景作業面板會蓋住後面幾列的按鈕
    if (await clear.isVisible()) await clear.click();
    await row.getByTestId("k8s-sync").click();
    await expect(page.getByTestId("k8s-table").locator("tr", { hasText: name }).getByTestId("k8s-sync"))
      .toBeEnabled({ timeout: 90_000 });
  }
  await page.waitForTimeout(1000);
  for (const [me, other] of [[a, b], [b, a]]) {
    await page.getByTestId(`k8s-tab-${me}`).click();
    await page.locator(".n-tabs-tab", { hasText: /^衝突/ }).last().click();
    const row = page.getByTestId("k8s-conflicts").locator("tr", { hasText: "兩個叢集用了同一個位址" });
    await expect(row).toContainText("192.0.2.50", { timeout: 20_000 });
    await expect(row).toContainText(other);
  }
  await page.waitForTimeout(500);
  await page.screenshot({ path: "test-results/k8s-cross-cluster.png" });
  await page.goto("/k8s");
  await del(page, a);
  await del(page, b);
});
