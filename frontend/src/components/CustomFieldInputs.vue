<template>
  <!-- 管理員在「自訂欄位」定義的欄位：以前定義得出來、任何表單都填不了值（2026-11-21） -->
  <template v-if="defs.length">
    <n-divider title-placement="left" style="margin: 8px 0 12px">{{ t("custom_fields.section_title") }}</n-divider>
    <n-form-item v-for="d in defs" :key="d.id" :label="labelOf(d)" :required="d.required"
                 :data-testid="`cf-${d.name}`">
      <n-switch v-if="d.field_type === 'bool'" :value="!!model[d.name]" @update:value="(v: boolean) => set(d, v)" />
      <n-input-number v-else-if="d.field_type === 'int' || d.field_type === 'float'" :value="numberOf(d)"
                      :precision="d.field_type === 'int' ? 0 : undefined" clearable style="width: 100%"
                      @update:value="(v: number | null) => set(d, v)" />
      <jt-date-picker v-else-if="d.field_type === 'date'" type="date" value-format="yyyy-MM-dd" clearable
                     :formatted-value="(model[d.name] as string | null) ?? null" style="width: 100%"
                     @update:formatted-value="(v: string | null) => set(d, v)" />
      <n-select v-else-if="d.field_type === 'select' || d.field_type === 'multi_select'"
                :value="(model[d.name] as any) ?? null" :options="choicesOf(d)" filterable clearable
                :multiple="d.field_type === 'multi_select'" @update:value="(v: any) => set(d, v)" />
      <n-input v-else :value="(model[d.name] as string | null) ?? ''" clearable
               :placeholder="d.validation_regex ? t('custom_fields.pattern_ph', { re: d.validation_regex }) : ''"
               @update:value="(v: string) => set(d, v)" />
    </n-form-item>
  </template>
</template>

<script lang="ts">
import { apiClient } from "@/api/client";
import type { CustomField } from "@/api/phase3";

// 同一頁可能開好幾次表單：定義每種物件只讀一次（讀失敗就刪掉，下次再試）
const cache = new Map<string, Promise<CustomField[]>>();
function loadDefs(kind: string): Promise<CustomField[]> {
  if (!cache.has(kind)) {
    cache.set(kind, apiClient.get("/api/v1/custom-fields", { params: { object_type: kind, page_size: 500 } })
      .then((r) => (r.data.items as CustomField[]).slice().sort((a, b) => a.display_order - b.display_order
        || a.name.localeCompare(b.name))));
  }
  return cache.get(kind)!;
}
/** 定義改了（自訂欄位頁存檔）時清掉快取 */
export function forgetCustomFieldDefs(): void { cache.clear(); }
</script>

<script setup lang="ts">
/**
 * 自訂欄位的輸入框（子網路、IP、裝置的表單共用）。
 *
 * 送出時呼叫 `payload()`：只帶目前有定義的欄位（定義被刪掉的舊值後端會拒收 unknown key），
 * 空值不帶；定義還沒載到或載不到時回 undefined，表單就不送 custom_fields，避免把既有的值清掉。
 */
import { onMounted, ref, watch } from "vue";
import { NDivider, NFormItem, NInput, NInputNumber, NSelect, NSwitch } from "naive-ui";
import { useI18n } from "vue-i18n";
import JtDatePicker from "@/components/JtDatePicker.vue";

const props = defineProps<{
  objectType: "subnet" | "ip" | "device";
  /** 物件目前的值（新增時空的） */
  initial?: Record<string, unknown> | null;
}>();
const { t, locale } = useI18n();
const defs = ref<CustomField[]>([]);
const loaded = ref(false);
const model = ref<Record<string, unknown>>({});

onMounted(async () => {
  try {
    defs.value = await loadDefs(props.objectType);
    loaded.value = true;
  } catch { cache.delete(props.objectType); /* 讀不到：payload() 回 undefined，不動既有的值 */ }
});
watch(() => props.initial, (v) => { model.value = { ...(v ?? {}) }; }, { immediate: true });

function labelOf(d: CustomField): string {
  const zh = String(locale.value).startsWith("zh");
  return (zh ? d.label_zh_tw : d.label_en_us) || d.label_en_us || d.label_zh_tw || d.name;
}
function choicesOf(d: CustomField) {
  const c = (d.options as { choices?: unknown } | null)?.choices;
  return (Array.isArray(c) ? c : []).map((x) => ({ label: String(x), value: String(x) }));
}
function numberOf(d: CustomField): number | null {
  const v = model.value[d.name];
  return typeof v === "number" ? v : v == null || v === "" ? null : Number(v);
}
function set(d: CustomField, v: unknown) {
  model.value = { ...model.value, [d.name]: v };
}

function payload(): Record<string, unknown> | undefined {
  if (!loaded.value) return undefined;
  const out: Record<string, unknown> = {};
  for (const d of defs.value) {
    const v = model.value[d.name];
    if (v == null || v === "" || (Array.isArray(v) && !v.length)) continue;
    out[d.name] = d.field_type === "bool" ? !!v : v;
  }
  return out;
}
defineExpose({ payload });
</script>
