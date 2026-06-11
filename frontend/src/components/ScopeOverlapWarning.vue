<script setup lang="ts">
/**
 * 整合設定頁用：當「全庫有重疊網段」且「此整合未設限定子網路範圍」時，
 * 顯示警告，提醒同步可能把存活/DHCP/MAC 標到錯誤單位的同 IP。
 * 重疊旗標以模組層 promise 快取，多個整合頁/多次掛載只查一次。
 */
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { NAlert } from "naive-ui";
import { getSubnetOverlapExists } from "@/api/integrations";

const props = defineProps<{ scopeEmpty: boolean }>();
const { t } = useI18n();
const hasOverlap = ref(false);

let cached: Promise<boolean> | null = null;
function fetchOverlap(): Promise<boolean> {
  if (!cached) cached = getSubnetOverlapExists().catch(() => false);
  return cached;
}

onMounted(async () => {
  hasOverlap.value = await fetchOverlap();
});

const show = computed(() => props.scopeEmpty && hasOverlap.value);
</script>

<template>
  <n-alert
    v-if="show"
    type="warning"
    :bordered="false"
    :show-icon="true"
    style="margin-top: 6px"
  >
    {{ t("scope_overlap.warning") }}
  </n-alert>
</template>
