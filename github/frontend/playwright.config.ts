import { defineConfig, devices } from "@playwright/test";

// Playwright e2e — 對「已部署的 jt-ipam」跑瀏覽器主路徑驗證
//
// 用法：
//   E2E_BASE_URL=https://ipam.local E2E_ADMIN_PASS=xxx pnpm test:e2e
//
// CI 預設不跑（先跳過，除非 base URL 與密碼都提供）。可以對 local dev / 144 跑。

// 有給 E2E_BASE_URL → 對已部署的實例跑（含登入主路徑，需 E2E_ADMIN_PASS）。
// 沒給 → 本地自起 `vite preview` 跑免登入冒煙（smoke.spec.ts），CI 可直接用。
const deployed = !!process.env.E2E_BASE_URL;
const baseURL = process.env.E2E_BASE_URL || "http://localhost:5173";

export default defineConfig({
  testDir: "./e2e",
  webServer: deployed
    ? undefined
    : {
        command: "npm run build && npm run preview",
        url: "http://localhost:5173",
        reuseExistingServer: !process.env.CI,
        timeout: 180_000,
      },
  fullyParallel: false,             // 共用同一 admin / DB，序列跑
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? "github" : "list",

  use: {
    baseURL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    ignoreHTTPSErrors: true,        // 自簽憑證
    actionTimeout: 10_000,
    navigationTimeout: 15_000,
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
