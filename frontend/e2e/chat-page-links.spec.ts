/**
 * AI 對話回答「功能在哪」時，回答下方有「前往」按鈕，點了在站內切到那一頁（使用者 2026-11-04「直接給點選連結」）。
 * 連結是後端從畫面地圖工具產生的（不是模型寫的網址）；這裡攔截串流，只驗證畫面與導覽，不碰 LLM。
 */
import { test, expect } from "@playwright/test";

const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");

test("回答下方的「前往」按鈕切到對應頁面與頁籤", async ({ page }) => {
  await page.route("**/api/v1/auth/me", async (route) => {
    const resp = await route.fetch();
    const body = await resp.json();
    body.ai_enabled = true;
    await route.fulfill({ response: resp, body: JSON.stringify(body) });
  });
  const done = {
    type: "done", answer: "掃描代理在「管理 › 掃描代理」，跳板主機在同一頁的頁籤。", model: "m", elapsed_ms: 5,
    trace_messages: [],
    links: [{ label: "掃描代理", href: "/scan-agents" },
            { label: "掃描代理 › SSH 跳板主機", href: "/scan-agents?tab=jump" },
            { label: "外部", href: "//evil.example.com/x" }],
  };
  await page.route("**/api/v1/ai/chat/stream", (route) => route.fulfill({
    status: 200, headers: { "content-type": "text/event-stream" },
    body: `data: ${JSON.stringify(done)}\n\n`,
  }));
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|username/i).fill("admin");
  await page.getByPlaceholder(/密碼|password/i).fill(ADMIN_PASS);
  await page.keyboard.press("Enter");
  await page.waitForURL(/dashboard|\/$/, { timeout: 15000 });

  await page.locator(".chat-fab").click();
  await page.locator(".chat-shell textarea").fill("掃描代理在哪");
  await page.keyboard.press("Enter");
  const links = page.getByTestId("chat-links");
  await expect(links).toContainText("前往");
  await expect(links.getByRole("button", { name: "掃描代理", exact: true })).toBeVisible();

  await links.getByRole("button", { name: "掃描代理 › SSH 跳板主機" }).click();
  await expect(page).toHaveURL(/\/scan-agents\?tab=jump$/);
  await expect(page.locator(".n-tabs-tab--active")).toContainText("跳板主機");
  // 外部網址的按鈕點了不會離開這個站
  await links.getByRole("button", { name: "外部" }).click();
  await expect(page).toHaveURL(/\/scan-agents\?tab=jump$/);
});
