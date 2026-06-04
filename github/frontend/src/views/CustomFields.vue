<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NIcon, NButton, NModal, NForm, NFormItem,
  NInput, NSelect, NSwitch, NInputNumber, NPopconfirm, NTag, NTooltip,
  useMessage, type DataTableColumns,
} from "naive-ui";
import {
  CustomFieldsIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SaveIcon, CancelIcon,
} from "@/icons";
import {
  listCustomFields, createCustomField, updateCustomField, deleteCustomField,
  type CustomField,
} from "@/api/phase3";
import { autoSort } from "@/composables/useTableSort";
import ColumnPicker from "@/components/ColumnPicker.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
const { t } = useI18n();

const { visibleKeys: cfVis, setVisible: cfSet, reset: cfReset } = useColumnPrefs(
  "custom_fields",
  ["object_type", "name", "field_type", "label_zh_tw", "required", "display_order", "actions"],
  ["object_type", "name", "field_type", "label_zh_tw", "required", "display_order", "actions"],
);
const cfPicker = [
  { key: "object_type", label: "Object" },
  { key: "name", label: "Name" },
  { key: "field_type", label: "Type" },
  { key: "label_zh_tw", label: "Label (zh-TW)" },
  { key: "required", label: "Required" },
  { key: "display_order", label: "Order" },
  { key: "actions", label: t("cols.actions") },
];

const msg = useMessage();
const rows = ref<CustomField[]>([]);
const loading = ref(false);
const show = ref(false);
const editing = ref<CustomField | null>(null);
const form = ref<{
  object_type: string; name: string;
  label_zh_tw: string; label_en_us: string;
  field_type: string; required: boolean; display_order: number;
  validation_regex: string;
  options_text: string;  // select / multi_select 用；每行一個選項
}>({
  object_type: "ip", name: "",
  label_zh_tw: "", label_en_us: "",
  field_type: "text", required: false, display_order: 0,
  validation_regex: "",
  options_text: "",
});

const objTypeOpts = [
  { label: t("custom_fields.obj_subnet"), value: "subnet" },
  { label: t("custom_fields.obj_ip"), value: "ip" },
  { label: t("custom_fields.obj_device"), value: "device" },
];
const ftypeOpts = [
  { label: `text — ${t("custom_fields.ft_text")}`, value: "text" },
  { label: `int — ${t("custom_fields.ft_int")}`, value: "int" },
  { label: `float — ${t("custom_fields.ft_float")}`, value: "float" },
  { label: `bool — ${t("custom_fields.ft_bool")}`, value: "bool" },
  { label: `date — ${t("custom_fields.ft_date")}`, value: "date" },
  { label: `select — ${t("custom_fields.ft_select")}`, value: "select" },
  { label: `multi_select — ${t("custom_fields.ft_multi_select")}`, value: "multi_select" },
  { label: `regex — ${t("custom_fields.ft_regex")}`, value: "regex" },
];

const showOptions = computed(() => ["select", "multi_select"].includes(form.value.field_type));
const showRegex = computed(() => form.value.field_type === "regex");

// 把 textarea 換行分隔的 options 轉成 backend 要的 dict 格式
function parseOptions(text: string): Record<string, string[]> | null {
  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
  if (!lines.length) return null;
  return { choices: lines };
}

function stringifyOptions(opts: Record<string, unknown> | null): string {
  if (!opts) return "";
  const choices = opts.choices;
  if (Array.isArray(choices)) return choices.map(String).join("\n");
  return JSON.stringify(opts, null, 2);
}

async function refresh() {
  loading.value = true;
  try { rows.value = (await listCustomFields()).items; }
  catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}
function openCreate() {
  editing.value = null;
  form.value = {
    object_type: "ip", name: "", label_zh_tw: "", label_en_us: "",
    field_type: "text", required: false, display_order: 0, validation_regex: "",
    options_text: "",
  };
  show.value = true;
}
function openEdit(r: CustomField) {
  editing.value = r;
  form.value = {
    object_type: r.object_type, name: r.name,
    label_zh_tw: r.label_zh_tw ?? "", label_en_us: r.label_en_us ?? "",
    field_type: r.field_type, required: r.required, display_order: r.display_order,
    validation_regex: r.validation_regex ?? "",
    options_text: stringifyOptions(r.options as any),
  };
  show.value = true;
}
async function submit() {
  // name 格式檢查 (與後端一致)
  if (!editing.value) {
    if (!/^[a-z][a-z0-9_]{0,63}$/.test(form.value.name)) {
      msg.error(t("custom_fields.error_name_format"));
      return;
    }
  }
  if (showOptions.value && !form.value.options_text.trim()) {
    msg.error(t("custom_fields.error_options_required"));
    return;
  }
  if (showRegex.value && !form.value.validation_regex.trim()) {
    msg.error(t("custom_fields.error_regex_required"));
    return;
  }
  try {
    const opts = showOptions.value ? parseOptions(form.value.options_text) : null;
    if (editing.value) {
      await updateCustomField(editing.value.id, {
        label_zh_tw: form.value.label_zh_tw || null,
        label_en_us: form.value.label_en_us || null,
        required: form.value.required,
        display_order: form.value.display_order,
        validation_regex: form.value.validation_regex || null,
        options: opts,
      } as any);
    } else {
      await createCustomField({
        object_type: form.value.object_type as any,
        name: form.value.name,
        label_zh_tw: form.value.label_zh_tw || null,
        label_en_us: form.value.label_en_us || null,
        field_type: form.value.field_type,
        required: form.value.required,
        display_order: form.value.display_order,
        validation_regex: form.value.validation_regex || null,
        options: opts,
      } as any);
    }
    show.value = false;
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function del(r: CustomField) {
  try { await deleteCustomField(r.id); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

function iconAction(icon: any, label: string, onClick: () => void, type?: any) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}
const allCols = computed<DataTableColumns<CustomField>>(() => autoSort([
  { title: t("cols.object"), key: "object_type", width: 120,
    render: (r) => h(NTag, { size: "small", type: "info" }, () => r.object_type) },
  { title: t("cols.name"), key: "name", minWidth: 160, ellipsis: { tooltip: true }, render: (r) => h("code", null, r.name) },
  { title: t("cols.type"), key: "field_type", width: 110 },
  { title: t("cols.label_zhtw"), key: "label_zh_tw", minWidth: 160, ellipsis: { tooltip: true }, render: (r) => r.label_zh_tw ?? "—" },
  { title: t("cols.required"), key: "required", width: 100, render: (r) => r.required ? "✓" : "—" },
  { title: t("cols.order"), key: "display_order", width: 80 },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 96,
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openEdit(r)),
      h(NPopconfirm, { onPositiveClick: () => del(r) }, {
        trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]));

const cols = computed<DataTableColumns<CustomField>>(() =>
  allCols.value.filter((c: any) => cfVis.value.includes(c.key)),
);

onMounted(() => { void refresh(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><CustomFieldsIcon /></n-icon>
        <span>{{ t("nav.custom_fields") }}</span>
      </n-space>
    </template>
    <n-space style="margin-bottom: 12px">
      <n-button @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
      <n-button type="primary" @click="openCreate">
        <template #icon><n-icon><PlusIcon /></n-icon></template>
        {{ t("common.create") }}
      </n-button>
      <ColumnPicker :all="cfPicker" :visible="cfVis"
                    @update:visible="cfSet" @reset="cfReset" />
    </n-space>
    <n-data-table :columns="cols" :data="rows" :loading="loading" :bordered="false" :scroll-x="826" />

    <n-modal v-model:show="show" preset="card" style="width: 520px">
      <template #header>
        <n-space align="center">
          <n-icon :size="20"><component :is="editing ? EditIcon : PlusIcon" /></n-icon>
          <span>{{ editing ? t("common.edit") : t("common.create") }}</span>
        </n-space>
      </template>
      <n-form label-placement="top">
        <n-form-item :label="t('custom_fields.object_type')">
          <n-select v-model:value="form.object_type" :options="objTypeOpts"
                    :disabled="!!editing" />
        </n-form-item>
        <n-form-item :label="t('custom_fields.name')">
          <n-input v-model:value="form.name" placeholder="contact_person"
                   :disabled="!!editing" />
          <template #feedback>
            {{ t("custom_fields.name_hint") }}
          </template>
        </n-form-item>
        <n-space>
          <n-form-item :label="t('custom_fields.label_zh_tw')" style="min-width: 220px">
            <n-input v-model:value="form.label_zh_tw" :placeholder="t('custom_fields.label_zh_tw_ph')" />
          </n-form-item>
          <n-form-item :label="t('custom_fields.label_en_us')" style="min-width: 220px">
            <n-input v-model:value="form.label_en_us" placeholder="Contact person" />
          </n-form-item>
        </n-space>
        <n-form-item :label="t('custom_fields.field_type')">
          <n-select v-model:value="form.field_type" :options="ftypeOpts"
                    :disabled="!!editing" />
        </n-form-item>

        <n-form-item v-if="showOptions" :label="t('custom_fields.options')">
          <n-input v-model:value="form.options_text" type="textarea" :rows="5"
                   :placeholder="t('custom_fields.options_placeholder')" />
          <template #feedback>
            {{ t("custom_fields.options_hint") }}
          </template>
        </n-form-item>

        <n-form-item v-if="showRegex" :label="t('custom_fields.validation_regex')">
          <n-input v-model:value="form.validation_regex"
                   placeholder="^[A-Z]{3}-[0-9]{4}$" />
          <template #feedback>
            {{ t("custom_fields.regex_hint") }}
          </template>
        </n-form-item>

        <n-space>
          <n-form-item :label="t('custom_fields.required')">
            <n-switch v-model:value="form.required" />
          </n-form-item>
          <n-form-item :label="t('custom_fields.display_order')" style="min-width: 160px">
            <n-input-number v-model:value="form.display_order" :min="0" :max="10000" />
          </n-form-item>
        </n-space>
      </n-form>
      <n-space justify="end">
        <n-button @click="show = false">
          <template #icon><n-icon><CancelIcon /></n-icon></template>
          {{ t("common.cancel") }}
        </n-button>
        <n-button type="primary" @click="submit">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </n-space>
    </n-modal>
  </n-card>
</template>
