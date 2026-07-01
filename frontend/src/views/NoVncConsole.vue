<script setup lang="ts">
/** 另開視窗的全頁 PVE 主控台。載入 IP 後依其 PVE 目標（vm→noVNC / ct→xterm）渲染全高 NoVncScreen。 */
import { onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { useI18n } from "vue-i18n";
import { NSpin, NResult } from "naive-ui";
import { getAddress } from "@/api/addresses";
import NoVncScreen from "@/components/NoVncScreen.vue";
import type { IPAddress } from "@/types";

const route = useRoute();
const { t } = useI18n();
const addr = ref<IPAddress | null>(null);
const loading = ref(true);
const failed = ref(false);

onMounted(async () => {
  try {
    addr.value = await getAddress(String(route.params.id));
    if (addr.value?.ip) document.title = `PVE · ${addr.value.ip}`;
  } catch {
    failed.value = true;
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <div class="novnc-page">
    <n-spin v-if="loading" :show="true" style="margin:80px auto;display:block" />
    <n-result v-else-if="failed || !addr || !addr.pve" status="403" :title="t('novnc.err_generic')" />
    <NoVncScreen v-else :address-id="addr.id" :ip="addr.ip" :kind="addr.pve.kind"
                 :hostname="addr.hostname" :device-name="addr.device_name" full-height />
  </div>
</template>

<style scoped>
.novnc-page { position: fixed; inset: 0; display: flex; flex-direction: column;
  padding: 16px; box-sizing: border-box; overflow: hidden; background: #eef1f8; color: #1f2937; }
html[data-theme="dark"] .novnc-page { background: #070b14; color: #e8f0fb; }
</style>
