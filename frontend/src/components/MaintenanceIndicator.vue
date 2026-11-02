<script setup lang="ts">
/**
 * 頂端列「維護中 N」：有維護正在進行時才出現，提醒大家「現在有些告警被靜音了」。
 * 滑過列出是哪些維護與到幾點，點了到維護排程頁。每分鐘更新一次。
 */
import { onBeforeUnmount, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import { NButton, NIcon, NTooltip } from "naive-ui";
import { MaintenanceIcon } from "@/icons";
import { MAINT_CHANGED, Maintenance } from "@/api/maintenance";
import { fmtDateTime } from "@/utils/datetime";

const { t } = useI18n();
const router = useRouter();
const data = ref<{ active: number; items: { id: string; title: string; until: string | null }[] }>({ active: 0, items: [] });
let timer: ReturnType<typeof setInterval> | null = null;
async function load() {
  try { data.value = await Maintenance.summary(); } catch { /* 沒權限或網路斷：不顯示 */ }
}
onMounted(() => { void load(); timer = setInterval(load, 60_000); window.addEventListener(MAINT_CHANGED, load); });
onBeforeUnmount(() => { if (timer) clearInterval(timer); window.removeEventListener(MAINT_CHANGED, load); });
defineExpose({ load });
</script>

<template>
  <n-tooltip v-if="data.active" trigger="hover">
    <template #trigger>
      <n-button size="small" quaternary type="warning" data-testid="maint-indicator" @click="router.push({ name: 'maintenance' })">
        <template #icon><n-icon><MaintenanceIcon /></n-icon></template>
        {{ t("maint.indicator", { n: data.active }) }}
      </n-button>
    </template>
    <div v-for="w in data.items" :key="w.id">{{ w.title }}<span v-if="w.until">（{{ t("maint.until_short", { at: fmtDateTime(w.until) }) }}）</span></div>
  </n-tooltip>
</template>
