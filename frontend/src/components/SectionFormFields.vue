<template>
  <!-- 區段的編輯欄位：列表頁的新增 / 編輯與詳細頁的編輯共用同一份（同一個物件，兩個入口內容要一樣） -->
  <n-form-item :label="t('common.name')" required>
    <n-input v-model:value="model.name" data-testid="section-form-name" />
  </n-form-item>
  <n-form-item :label="t('common.description')">
    <n-input v-model:value="model.description" type="textarea" :autosize="{ minRows: 2, maxRows: 6 }" />
  </n-form-item>
  <n-form-item :label="t('sections.parent')">
    <n-select v-model:value="model.parent_id" :options="parentOptions" :placeholder="t('sections.no_parent')"
              clearable filterable data-testid="section-form-parent" />
  </n-form-item>
  <n-form-item :label="t('cols.unit')">
    <n-select v-model:value="model.customer_id" :options="customerOptions" :placeholder="t('common.not_specified')"
              clearable filterable />
  </n-form-item>
  <n-form-item :label="t('sections.order')">
    <n-input-number v-model:value="model.display_order" :min="0" :max="10000" style="width: 160px" />
  </n-form-item>
  <n-form-item :label="t('sections.strict_mode')">
    <n-space vertical :size="4">
      <n-switch v-model:value="model.strict_mode" data-testid="section-form-strict" />
      <span class="sf-hint">{{ t("sections.strict_mode_hint") }}</span>
    </n-space>
  </n-form-item>
</template>

<script lang="ts">
export interface SectionFormModel {
  name: string; description: string; parent_id: string | null; customer_id: string | null;
  display_order: number | null; strict_mode: boolean;
}
</script>

<script setup lang="ts">
import { computed } from "vue";
import { NFormItem, NInput, NInputNumber, NSelect, NSpace, NSwitch } from "naive-ui";
import { useI18n } from "vue-i18n";

const props = defineProps<{
  /** 全部區段（算上層選單用） */
  sections: { id: string; name: string; parent_id?: string | null }[];
  /** 編輯中的區段（新增時為 null）：上層選單排除它自己與它底下的區段，不然會形成循環 */
  editingId: string | null;
  customerOptions: { label: string; value: string }[];
}>();
const model = defineModel<SectionFormModel>({ required: true });
const { t } = useI18n();

const parentOptions = computed(() => {
  const banned = new Set<string>();
  if (props.editingId) {
    banned.add(props.editingId);
    let grew = true;
    while (grew) {
      grew = false;
      for (const s of props.sections) {
        if (s.parent_id && banned.has(s.parent_id) && !banned.has(s.id)) { banned.add(s.id); grew = true; }
      }
    }
  }
  return props.sections.filter((s) => !banned.has(s.id)).map((s) => ({ label: s.name, value: s.id }));
});
</script>

<style scoped>
.sf-hint { font-size: 12px; opacity: .7; }
</style>
