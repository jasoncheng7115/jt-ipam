/**
 * 雙接主機（10-23 使用者：「有些裝置同時接在兩台交換器上，要怎麼畫？」）：
 * 不同網卡各接一台交換器的主機放進「A + B 雙接群組」，排在兩台交換器中間、兩台各拉一條線；
 * 同一張網卡在兩台都學到的仍只歸一台（另一條是待確認）。看得到的部分用匯出的 SVG 驗。
 */
import { readFile } from "node:fs/promises";
import { test, expect, type Page } from "@playwright/test";

const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");
test.use({ viewport: { width: 1600, height: 1000 } });

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(ADMIN_USER);
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

const n = (id: string, label: string, type: string) => ({ data: { id, label, type } });
const l2 = (id: string, host: string, sw: string, port: string, mac: string, direct = true) =>
  ({ data: { id, source: host, target: sw, kind: "l2", via: "fdb", evidence: "learned", label: port, port, direct,
             port_mac_count: direct ? 1 : 6, mac } });

test("不同網卡各接一台的主機進雙接群組，同一張卡兩邊學到的不算", async ({ page }) => {
  await page.route(/\/api\/v1\/topology(\?.*)?$/, (route) => route.fulfill({ json: {
    nodes: [n("swa", "switch-a", "switch"), n("swb", "switch-b", "switch"),
            n("pve", "pve-1", "server"), n("one", "srv-one", "server"), n("same", "srv-same", "server")],
    edges: [
      { data: { id: "up", source: "swa", target: "swb", kind: "l2_uplink", via: "fdb", evidence: "learned", port: "Eth1/1", peer_port: "eth1/25" } },
      l2("p1", "pve", "swa", "Eth1/4", "aa:00:00:00:00:01"),
      l2("p2", "pve", "swb", "eth1/10", "aa:00:00:00:00:02", false),
      l2("o1", "one", "swa", "Eth1/5", "aa:00:00:00:00:03"),
      l2("s1", "same", "swa", "Eth1/6", "aa:00:00:00:00:04"),
      l2("s2", "same", "swb", "eth1/11", "aa:00:00:00:00:04", false),
    ],
  } }));
  await login(page);
  await page.goto("/topology");
  await page.locator(".n-select").nth(1).click();
  await page.locator(".n-base-select-option", { hasText: "以交換器為中心" }).click();
  await expect(page.getByTestId("topology-legend-layered")).toContainText("雙接");

  const dl = page.waitForEvent("download");
  await page.getByRole("button", { name: /匯出/ }).first().click();
  await page.locator(".n-dropdown-option", { hasText: "SVG" }).click();
  const svg = await readFile((await (await dl).path())!, "utf8");
  expect(svg).toContain("switch-a + switch-b 雙接群組");
  expect(svg).toContain("switch-a 接入群組");
  // 雙接群組裡只有 pve-1：同一張卡兩邊都學到的 srv-same 照舊歸一台
  expect((svg.match(/雙接群組/g) ?? []).length).toBe(1);
});

test("網卡綁定（同一個 MAC 插在兩台，後端標 bond）也進雙接群組", async ({ page }) => {
  const bond = (e: ReturnType<typeof l2>) => ({ data: { ...e.data, bond: true } });
  await page.route(/\/api\/v1\/topology(\?.*)?$/, (route) => route.fulfill({ json: {
    nodes: [n("swa", "switch-a", "switch"), n("swc", "switch-c", "switch"),
            n("file", "nas-b", "server"), n("one", "srv-one", "server")],
    edges: [
      { data: { id: "up", source: "swa", target: "swc", kind: "l2_uplink", via: "fdb", evidence: "learned", port: "Eth1/2", peer_port: "Gi1/0/48" } },
      bond(l2("f1", "file", "swa", "Eth1/7", "aa:00:00:00:00:05")),
      bond(l2("f2", "file", "swc", "Gi1/0/7", "aa:00:00:00:00:05")),
      l2("o1", "one", "swa", "Eth1/5", "aa:00:00:00:00:03"),
    ],
  } }));
  await login(page);
  await page.goto("/topology");
  await page.locator(".n-select").nth(1).click();
  await page.locator(".n-base-select-option", { hasText: "以交換器為中心" }).click();
  await expect(page.getByTestId("topology-legend-layered")).toContainText("雙接");

  const dl = page.waitForEvent("download");
  await page.getByRole("button", { name: /匯出/ }).first().click();
  await page.locator(".n-dropdown-option", { hasText: "SVG" }).click();
  const svg = await readFile((await (await dl).path())!, "utf8");
  expect(svg).toContain("switch-a + switch-c 雙接群組");
  expect(svg).not.toContain("switch-c 接入群組");
});
