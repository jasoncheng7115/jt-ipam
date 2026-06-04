import pluginVue from "eslint-plugin-vue";
import { defineConfigWithVueTs, vueTsConfigs } from "@vue/eslint-config-typescript";
import skipFormatting from "@vue/eslint-config-prettier/skip-formatting";

// ESLint v9 flat config（搭 eslint-plugin-vue 9 + @vue/eslint-config-typescript 14）。
export default defineConfigWithVueTs(
  {
    name: "app/files",
    files: ["**/*.{ts,mts,tsx,vue}"],
  },
  {
    name: "app/ignores",
    ignores: [
      "dist/**",
      "coverage/**",
      "playwright-report/**",
      "test-results/**",
    ],
  },
  pluginVue.configs["flat/essential"],
  vueTsConfigs.recommended,
  skipFormatting,
  {
    name: "app/rules",
    rules: {
      // 本專案在 API 邊界刻意大量使用 any（務實取捨）；不強制改型別。
      "@typescript-eslint/no-explicit-any": "off",
      // view/元件檔名採單字慣例（Addresses / Devices …），不套多字規則。
      "vue/multi-word-component-names": "off",
      // 未使用變數仍視為錯誤，但允許 _ 前綴刻意忽略。
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrors: "none" },
      ],
    },
  },
);
