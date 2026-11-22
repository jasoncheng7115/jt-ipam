import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";
import { fileURLToPath, URL } from "node:url";

// 獨立 vitest config — 不靠 vite.config（避免 vite server 設定干擾測試）
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    // 預設 5 秒：開發機負載 20 以上時，RustDesk / rdweb 那幾支（壓縮、128 KiB 分塊上傳、重連倒數）
    // 會超時假紅，單獨跑都過（2026-11-21 全套 7 個失敗全是 "Test timed out in 5000ms"）
    testTimeout: 20_000,
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    coverage: {
      provider: "v8",
      reporter: ["text", "html", "lcov"],
      exclude: [
        "**/node_modules/**",
        "**/dist/**",
        "**/*.config.*",
        "src/main.ts",
      ],
    },
    setupFiles: ["src/__tests__/setup.ts"],
  },
});
