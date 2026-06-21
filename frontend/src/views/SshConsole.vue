<script setup lang="ts">
/** 另開視窗的全頁 SSH 終端機。載入 IP 後渲染全高 SshTerminal。 */
import { onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { useI18n } from "vue-i18n";
import { NSpin, NResult } from "naive-ui";
import { getAddress } from "@/api/addresses";
import SshTerminal from "@/components/SshTerminal.vue";
import type { IPAddress } from "@/types";

const route = useRoute();
const { t } = useI18n();
const addr = ref<IPAddress | null>(null);
const loading = ref(true);
const failed = ref(false);

onMounted(async () => {
  try {
    addr.value = await getAddress(String(route.params.id));
    if (addr.value?.ip) document.title = `SSH · ${addr.value.ip}`;
  } catch {
    failed.value = true;
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <div class="ssh-page">
    <n-spin v-if="loading" :show="true" style="margin:80px auto;display:block" />
    <n-result v-else-if="failed || !addr" status="403" :title="t('ssh.err_generic')" />
    <SshTerminal v-else :address-id="addr.id" :ip="addr.ip"
                 :hostname="addr.hostname" :device-name="addr.device_name" full-height />
  </div>
</template>

<style scoped>
/* 此頁是獨立全頁 route（不在 MainLayout 的 n-layout 內），需自己跟著主題上底色，
   否則深色模式下淺色文字會落在白底上。html[data-theme] 由全站主題設定。 */
/* 與主頁內容區同底色（App.vue 主題 bodyColor：light #eef1f8 / dark #070b14）。
   position:fixed + inset:0 + overflow:hidden：精準填滿視窗、不受 body margin 影響，
   終端機只剩 xterm 自己的捲動，不會出現第二層（頁面）捲軸。 */
.ssh-page { position: fixed; inset: 0; display: flex; flex-direction: column;
  padding: 16px; box-sizing: border-box; overflow: hidden; background: #eef1f8; color: #1f2937; }
html[data-theme="dark"] .ssh-page { background: #070b14; color: #e8f0fb; }
</style>
