<script setup lang="ts">
import { GoToIcon } from "@/icons";
/**
 * 「只顯示從別頁點進來的那一筆」提示列（搭配 useFocusRow）。
 * 找不到時要講清楚可能的原因，不要只給一張空表格。
 */
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { NIcon, NAlert, NButton } from "naive-ui";
import type { FocusRow } from "@/composables/useFocusRow";

const props = defineProps<{ ctl: FocusRow; loading?: boolean }>();
const { t } = useI18n();
const active = computed(() => props.ctl.focus.value != null);
const found = computed(() => props.ctl.focused.value?.length ?? 0);
// 還在載入、或清單還沒載入過 → 先不下「找不到」的結論
const show = computed(() => active.value && (found.value > 0
  || (!props.loading && props.ctl.settled.value)));
</script>

<template>
  <n-alert v-if="show" :type="found ? 'info' : 'warning'" :show-icon="true"
           style="margin-bottom: 10px" class="focus-row-banner">
    <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap">
      <span>{{ found ? t("common.focus_only") : t("common.focus_missing") }}</span>
      <n-button size="tiny" secondary @click="ctl.clear()"><template #icon><n-icon><GoToIcon /></n-icon></template>{{ t("common.show_all") }}</n-button>
    </div>
  </n-alert>
</template>
