/**
 * AI 判讀、AI 巡檢可以接不同的 LLM Server（使用者 2026-11-05）；沒設就繼承基本設定（對話＋嵌入）那台。
 * 打開「這個用途使用不同的 LLM Server」→ 填位址 → 模型清單改問那一台；關掉＝回到繼承。
 */
import { test, expect, type Page } from "@playwright/test";

const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
test.skip(!ADMIN_PASS, "需要 E2E_ADMIN_PASS");

const list = (...names: string[]) => ({
  models: names.map((name) => ({ name, size: null, modified_at: null, family: null, parameter_size: null })),
});

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder(/帳號|Username/).fill(ADMIN_USER);
  await page.getByPlaceholder(/密碼|Password/).fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

async function patchApi(page: Page, body: Record<string, unknown>) {
  return page.evaluate(async (b) => {
    const r = await fetch("/api/v1/system/llm", {
      method: "PATCH",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${sessionStorage.getItem("access_token")}` },
      body: JSON.stringify(b),
    });
    return r.json();
  }, body);
}

test("判讀接自己的 LLM Server：模型清單問那一台，關掉回到繼承", async ({ page }) => {
  await login(page);
  // 基本設定那台與判讀那台回不同的模型清單
  await page.route("**/api/v1/system/llm/models**", (r) => r.fulfill({
    json: new URL(r.request().url()).searchParams.get("role") === "interpret"
      ? list("judge-only:70b") : list("gemma4:26b", "nomic-embed-text:latest"),
  }));
  await patchApi(page, { ai_interpret_url: "", ai_interpret_model: "" });
  await page.goto("/llm");
  const card = page.getByTestId("interpret-card");
  await expect(card.getByTestId("interpret-server")).toContainText("沿用基本設定的 LLM Server");

  await card.getByTestId("interpret-server-switch").click();
  const saved = page.waitForResponse((r) => r.url().endsWith("/api/v1/system/llm") && r.request().method() === "PATCH");
  await card.getByTestId("interpret-server-url").locator("input").fill("http://judge.invalid:11434");
  const resp = await saved;
  expect(resp.request().postDataJSON()).toEqual({ ai_interpret_url: "http://judge.invalid:11434" });
  expect((await resp.json()).ai_interpret_url).toBe("http://judge.invalid:11434");
  await expect(card.getByTestId("interpret-model-needed")).toBeVisible();

  // 模型清單是判讀那台的
  await card.getByTestId("interpret-model").click();
  await expect(page.locator(".n-base-select-option", { hasText: "judge-only:70b" })).toHaveCount(1);
  await expect(page.locator(".n-base-select-option", { hasText: "gemma4:26b" })).toHaveCount(0);
  await page.locator(".n-base-select-option", { hasText: "judge-only:70b" }).click();
  await expect(card.getByTestId("interpret-model-needed")).toHaveCount(0);

  await page.waitForTimeout(1000);
  await page.reload();
  await expect(page.getByTestId("interpret-server-url").locator("input")).toHaveValue("http://judge.invalid:11434");
  await expect(page.getByTestId("interpret-card")).toContainText("judge-only:70b");

  // 關掉＝回到繼承（位址清空）
  const off = page.waitForResponse((r) => r.url().endsWith("/api/v1/system/llm") && r.request().method() === "PATCH");
  await page.getByTestId("interpret-server-switch").click();
  expect((await (await off).json()).ai_interpret_url).toBe("");
  await expect(page.getByTestId("interpret-server")).toContainText("沿用基本設定的 LLM Server");

  await patchApi(page, { ai_interpret_url: "", ai_interpret_model: "" });
});
