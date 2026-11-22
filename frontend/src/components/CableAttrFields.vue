<template>
  <!-- 纜線的屬性欄位：新增與編輯共用同一份（以前新增只能填類型與線標，其餘要建好再編輯一次） -->
  <n-space :wrap-item="false" :size="12" style="width:100%">
    <n-form-item :label="t('cols.type')" style="flex:1">
      <n-select v-model:value="model.type" :options="typeOpts" clearable :consistent-menu-width="false" />
    </n-form-item>
    <n-form-item :label="t('physical.cable_label')" style="flex:1">
      <n-input v-model:value="model.label" placeholder="(optional)" data-testid="cable-label" />
    </n-form-item>
  </n-space>
  <n-space :wrap-item="false" :size="12" style="width:100%">
    <n-form-item :label="t('physical.cable_color')" style="flex:1">
      <n-input v-model:value="model.color" placeholder="(optional)" data-testid="cable-color" />
    </n-form-item>
    <n-form-item :label="t('physical.cable_length_m')" style="flex:1">
      <n-input-number v-model:value="model.length_m" :min="0" :max="10000" clearable style="width:100%" data-testid="cable-length" />
    </n-form-item>
  </n-space>
  <n-form-item :label="t('common.status')">
    <n-select v-model:value="model.status" :options="statusOpts" />
  </n-form-item>
  <n-form-item :label="t('sections.description')">
    <n-input v-model:value="model.description" type="textarea" :autosize="{ minRows: 2, maxRows: 4 }" />
  </n-form-item>
</template>

<script lang="ts">
export interface CableAttrs {
  type: string | null; label: string; color: string; length_m: number | null; description: string; status: string;
}
export function blankCableAttrs(): CableAttrs {
  return { type: "cat6", label: "", color: "", length_m: null, description: "", status: "connected" };
}
</script>

<script setup lang="ts">
import { NFormItem, NInput, NInputNumber, NSelect, NSpace } from "naive-ui";
import { useI18n } from "vue-i18n";

const model = defineModel<CableAttrs>({ required: true });
const { t } = useI18n();
const typeOpts = ["cat5e", "cat6", "cat6a", "dac", "fiber-mm", "fiber-sm"].map((v) => ({ label: v, value: v }));
const statusOpts = ["connected", "planned", "decommissioned"].map((v) => ({ label: t(`physical.cable_status_${v}`), value: v }));
</script>
