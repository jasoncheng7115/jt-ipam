import { test, expect, type APIRequestContext, type Page } from "@playwright/test";

/**
 * 裝置群組（堆疊／HA／叢集，客戶 2026-11-05：兩台 N2048P 堆疊標不出來、管理 IP 也不能各分）。
 * 自己建資料、自己刪（不動 seed）：機房一間、機櫃 A／B、三台交換器（A 兩台、B 一台）＋ A 背面一台伺服器、
 * 一個 /28 子網路與一個要共用的管理 IP。
 *   1. 裝置頁「群組」頁籤：用畫面新增群組（三台、代表裝置、共用 IP）
 *   2. 機櫃圖：A 有同色括線「名稱 2/3」，點開列出成員與「另有 1 台在機櫃 B」，點了切到 B，B 標「1/3」
 *   3. 多櫃並排（獨立卡片）點另一面提示的「切換到背面」要真的切過去（客戶回報：單櫃可以、多櫃沒反應）
 *   4. 裝置詳細資料的群組卡片、裝置清單的群組欄、IP 清單的「群組共用」
 */
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(ADMIN_USER);
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

async function api(request: APIRequestContext) {
  const r = await request.post("/api/v1/auth/login", { data: { username: ADMIN_USER, password: ADMIN_PASS, realm: "local" } });
  expect(r.ok(), "admin API 登入要成功").toBeTruthy();
  const H = { Authorization: `Bearer ${(await r.json()).access_token}` };
  const call = async (method: "get" | "post" | "patch" | "delete", url: string, data?: unknown) => {
    const res = await request[method](url, { headers: H, data });
    expect(res.status(), `${method.toUpperCase()} ${url}：${await res.text()}`).toBeLessThan(300);
    return res.status() === 204 ? null : res.json();
  };
  return call;
}

test("裝置群組：新增、機櫃圖括線與跨櫃連結、多櫃切換到背面、詳細資料與清單", async ({ page, request }) => {
  test.setTimeout(150_000);
  const call = await api(request);
  const sfx = Date.now().toString(36);
  const made: { devices: string[]; racks: string[]; loc?: string; subnet?: string; ip?: string; group?: string } =
    { devices: [], racks: [] };
  try {
    const loc = await call("post", "/api/v1/locations", { name: `e2e-dg-room-${sfx}` });
    made.loc = loc.id;
    const ra = await call("post", "/api/v1/racks", { name: `e2e-dg-A-${sfx}`, u_height: 12, location_id: loc.id });
    const rb = await call("post", "/api/v1/racks", { name: `e2e-dg-B-${sfx}`, u_height: 12, location_id: loc.id });
    made.racks.push(ra.id, rb.id);
    const dev = async (name: string, rack: string, u: number, face = "front", type = "switch") => {
      const d = await call("post", "/api/v1/devices", { name, type, location_id: loc.id, rack_id: rack,
                                                       u_position: u, u_size: 1, rack_face: face });
      made.devices.push(d.id);
      return d;
    };
    const sw1 = await dev(`e2e-dg-sw1-${sfx}`, ra.id, 10);
    const sw2 = await dev(`e2e-dg-sw2-${sfx}`, ra.id, 8);
    const sw3 = await dev(`e2e-dg-sw3-${sfx}`, rb.id, 6);
    await dev(`e2e-dg-rear-${sfx}`, ra.id, 4, "rear", "server");
    const section = (await call("get", "/api/v1/sections")).items?.[0] ?? (await call("get", "/api/v1/sections"))[0];
    const octet = 16 * (Date.now() % 15);
    const sn = await call("post", "/api/v1/subnets", { section_id: section.id, cidr: `198.18.250.${octet}/28`,
                                                      description: `e2e-dg-${sfx}` });
    made.subnet = sn.id;
    const ip = await call("post", "/api/v1/addresses", { subnet_id: sn.id, ip: `198.18.250.${octet + 6}` });
    made.ip = ip.id;
    const mgmt = `198.18.250.${octet + 6}`;

    // 1. 裝置頁 → 群組頁籤 → 新增
    await login(page);
    await page.goto("/devices");
    await page.getByTestId("devices-tab-groups").click();
    await expect(page).toHaveURL(/tab=groups/);
    // 新增旁邊的「說明」：這個功能是什麼、什麼時候用
    await page.getByTestId("dg-help").click();
    const help = page.getByTestId("dg-help-modal");
    await expect(help).toContainText("什麼時候用");
    await expect(help).toContainText("成員必須是「裝置」");
    await page.waitForTimeout(400);
    await help.screenshot({ path: "test-results/device-groups-help.png" });
    await help.getByRole("button", { name: "關閉" }).click();
    await page.getByTestId("dg-create").click();
    const groupName = `e2e-stack-${sfx}`;
    await page.getByTestId("dg-name").locator("input").fill(groupName);
    const rows = page.getByTestId("dg-member-row");
    const pick = async (i: number, name: string) => {
      await rows.nth(i).locator(".n-base-selection").first().click();
      await page.keyboard.type(name);
      await page.locator(".n-base-select-option:visible", { hasText: name }).first().click();
    };
    await pick(0, sw1.name);
    await pick(1, sw2.name);
    await page.getByTestId("dg-add-member").click();
    await pick(2, sw3.name);
    await page.getByTestId("dg-shared-ips").locator(".n-base-selection").click();
    await page.keyboard.type(mgmt);
    await page.locator(".n-base-select-option:visible", { hasText: mgmt }).first().click();
    await page.keyboard.press("Escape");
    await page.getByTestId("dg-save").click();
    const grow = page.locator("tr", { hasText: groupName });
    await expect(grow).toBeVisible({ timeout: 10_000 });
    made.group = (await call("get", "/api/v1/device-groups")).items.find((g: any) => g.name === groupName).id;
    await expect(grow).toContainText(sw1.name);
    await expect(grow).toContainText(sw3.name);
    await expect(grow).toContainText(mgmt);
    await expect(grow).toContainText("代表裝置");
    const ipLink = grow.locator("a", { hasText: mgmt });
    expect(await ipLink.evaluate((e) => getComputedStyle(e).textDecorationLine)).toBe("none");
    // 換成 HA 主備：第一台是「主要」、其他是「備援」（以前兩台都變成備援）
    await grow.getByTestId("dg-edit").click();
    await page.getByTestId("dg-kind").click();
    await page.locator(".n-base-select-option:visible", { hasText: "HA（主備）" }).click();
    await expect(page.getByTestId("dg-member-row").nth(0)).toContainText("主要");
    await expect(page.getByTestId("dg-member-row").nth(1)).toContainText("備援");
    // 找不到裝置時提示要先新增為裝置（不自動建立）；用 IP 也搜得到裝置
    await page.getByTestId("dg-add-member").click();
    await page.getByTestId("dg-member-row").nth(3).locator(".n-base-selection").first().click();
    await page.keyboard.type("no-such-device-zz");
    await expect(page.locator(".n-base-select-menu:visible", { hasText: "先到裝置頁新增為裝置" })).toBeVisible();
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "取消" }).click();
    await page.screenshot({ path: "test-results/device-groups-list.png" });

    // 2. 機櫃圖：A 兩台框在一起，B 另有一台
    await page.goto(`/racks?rack=${ra.id}`);
    const chip = page.getByTestId("rack-group-chip").first();
    await expect(chip).toBeVisible({ timeout: 15_000 });
    await expect(chip).toContainText(groupName);
    await expect(chip).toContainText("2/3");
    await chip.click();
    const pop = page.getByTestId("rack-group-pop");
    await expect(pop).toContainText(sw1.name);
    await expect(pop).toContainText(sw2.name);
    await expect(pop).toContainText("主控");
    await page.screenshot({ path: "test-results/device-groups-rack.png" });
    const away = pop.getByTestId("rack-group-elsewhere");
    await expect(away).toContainText(`另有 1 台在機櫃 ${rb.name}`);
    await away.click();
    await expect(page).toHaveURL(new RegExp(`rack=${rb.id}`));
    await expect(page.getByTestId("rack-group-chip").first()).toContainText("1/3", { timeout: 15_000 });

    // 3. 多櫃並排（獨立卡片）：「背 ×1」→ 切換到背面要真的切過去
    await page.goto("/racks");
    await page.locator(".n-base-selection", { hasText: /機房|選擇/ }).first().click();
    await page.locator(".n-base-select-option", { hasText: loc.name }).click();
    await page.getByText("獨立卡片", { exact: true }).last().click();
    await expect(page.locator(".rack-row .rack-frame")).toHaveCount(2, { timeout: 15_000 });
    await page.getByTestId("rack-face-hint").first().click();
    await page.getByRole("button", { name: "切換到背面" }).click();
    await expect(page.locator(".rack-row .u-occupied", { hasText: `e2e-dg-rear-${sfx}` })).toBeVisible();
    await expect(page.locator(".rack-row .u-occupied", { hasText: sw1.name })).toHaveCount(0);

    // 4. 裝置詳細資料、裝置清單、IP 清單
    await page.goto(`/devices/${sw2.id}`);
    const card = page.getByTestId("device-group-card");
    await expect(card).toBeVisible({ timeout: 15_000 });
    await expect(card).toContainText(groupName);
    await expect(card).toContainText(sw1.name);
    await expect(card).toContainText("本機");
    await expect(card).toContainText(mgmt);
    await card.screenshot({ path: "test-results/device-groups-card.png" });
    await page.goto(`/devices?q=${mgmt}`);
    await expect(page.locator("tr", { hasText: sw1.name }).first()).toBeVisible({ timeout: 15_000 });
    await page.goto(`/devices?q=e2e-dg-sw2-${sfx}`);
    await expect(page.getByTestId("device-group-cell").first()).toContainText(groupName, { timeout: 15_000 });
    // IP 詳細資料：標「由群組共用」，裝置欄鎖住（要換裝置得到群組改），群組欄顯示這個群組
    await page.goto(`/addresses/${ip.id}`);
    await expect(page.getByTestId("ip-group-shared-tag")).toContainText(groupName, { timeout: 15_000 });
    await page.getByRole("button", { name: "編輯", exact: true }).click();
    await expect(page.getByTestId("ip-group-locked")).toBeVisible();
    await expect(page.getByTestId("ip-device-select").locator(".n-base-selection")).toHaveClass(/n-base-selection--disabled/);
    await expect(page.getByTestId("ip-group-select")).toContainText(groupName);
    await page.getByTestId("ip-group-select").scrollIntoViewIfNeeded();
    await page.screenshot({ path: "test-results/device-groups-ip-edit.png" });
    // 拓樸圖：同組成員之間一條群組連線（圖例有這一項）
    await page.goto("/topology");
    await expect(page.locator(".topo-legend").first()).toContainText("裝置群組", { timeout: 20_000 });
    await page.screenshot({ path: "test-results/device-groups-topology.png" });
  } finally {
    const del = async (url: string) => { await call("delete", url).catch(() => {}); };
    if (made.group) await del(`/api/v1/device-groups/${made.group}`);
    if (made.ip && made.subnet) {
      await del(`/api/v1/addresses/${made.ip}`);
    }
    if (made.subnet) await del(`/api/v1/subnets/${made.subnet}`);
    for (const d of made.devices) await del(`/api/v1/devices/${d}`);
    for (const r of made.racks) await del(`/api/v1/racks/${r}`);
    if (made.loc) await del(`/api/v1/locations/${made.loc}`);
  }
});
