<script setup lang="ts">
// 不渲染任何畫面，只負責把「登入逾時」的處理(提示 + 導向登入)註冊給攔截器使用。
// 必須放在 NMessageProvider 內，才能取得 message API。
import { useMessage } from "naive-ui";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import { useAuthStore } from "@/stores/auth";
import { setSessionExpiredHandler } from "@/utils/session";

const message = useMessage();
const { t } = useI18n();
const router = useRouter();
const auth = useAuthStore();

setSessionExpiredHandler(() => {
  message.warning(t("errors.session_expired"), { duration: 4500 });
  auth.clearTokens();
  const cur = router.currentRoute.value;
  if (cur.name !== "login") {
    // 用 SPA 導向(非整頁重載)，提示 toast 才不會被刷掉
    void router.push({ name: "login", query: { next: cur.fullPath, expired: "1" } });
  }
});
</script>

<template>
  <span style="display:none" aria-hidden="true"></span>
</template>
