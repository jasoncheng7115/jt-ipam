<script setup lang="ts">
/**
 * 表格欄位顯示偏好挑選器。
 *
 * 用法：
 *   <ColumnPicker
 *     :all="[{key: 'ip', label: 'IP'}, ...]"
 *     v-model:visible="visibleKeys"
 *     @reset="reset"
 *   />
 */
import { useI18n } from "vue-i18n";
import { NButton, NPopover, NCheckbox, NSpace, NIcon } from "naive-ui";
import { Settings as SettingsIcon } from "@iconoir/vue";

const { t } = useI18n();

const props = defineProps<{
  all: { key: string; label: string }[];
  visible: string[];
  size?: "tiny" | "small" | "medium" | "large";   // 對齊鄰近按鈕高度（明細頁多為 small）
}>();
const emit = defineEmits<{
  (e: "update:visible", v: string[]): void;
  (e: "reset"): void;
}>();

function toggle(key: string, checked: boolean) {
  const next = checked
    ? [...new Set([...props.visible, key])]
    : props.visible.filter((k) => k !== key);
  emit("update:visible", next);
}
</script>

<template>
  <n-popover trigger="click" placement="bottom-end">
    <template #trigger>
      <n-button :size="props.size" :title="t('column_picker.columns')">
        <template #icon><n-icon><SettingsIcon /></n-icon></template>
        {{ t("column_picker.columns") }}
      </n-button>
    </template>
    <div style="min-width: 180px; max-height: 360px; overflow-y: auto;">
      <n-space vertical :size="6">
        <div
          v-for="c in props.all" :key="c.key"
          style="display: flex; align-items: center; gap: 6px;"
        >
          <n-checkbox
            :checked="props.visible.includes(c.key)"
            @update:checked="(v: boolean) => toggle(c.key, v)"
          >
            {{ c.label || c.key }}
          </n-checkbox>
        </div>
        <div style="border-top: 1px solid rgba(127,127,127,0.2); margin-top: 6px; padding-top: 6px;">
          <n-button size="tiny" quaternary @click="emit('reset')">{{ t("column_picker.reset") }}</n-button>
        </div>
      </n-space>
    </div>
  </n-popover>
</template>
