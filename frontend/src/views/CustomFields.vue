<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NButton, NModal, NForm, NFormItem,
  NInput, NSelect, NSwitch, NInputNumber, NPopconfirm, NTag,
  useMessage, type DataTableColumns,
} from "naive-ui";
import {
  listCustomFields, createCustomField, updateCustomField, deleteCustomField,
  type CustomField,
} from "@/api/phase3";

const { t } = useI18n();
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
}>({
  object_type: "ip", name: "",
  label_zh_tw: "", label_en_us: "",
  field_type: "text", required: false, display_order: 0,
  validation_regex: "",
});

const objTypeOpts = ["subnet", "ip", "device"].map((v) => ({ label: v, value: v }));
const ftypeOpts = ["text", "int", "float", "bool", "date", "select", "multi_select", "regex"]
  .map((v) => ({ label: v, value: v }));

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
  };
  show.value = true;
}
async function submit() {
  try {
    if (editing.value) {
      await updateCustomField(editing.value.id, {
        label_zh_tw: form.value.label_zh_tw || null,
        label_en_us: form.value.label_en_us || null,
        required: form.value.required,
        display_order: form.value.display_order,
        validation_regex: form.value.validation_regex || null,
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

const cols = computed<DataTableColumns<CustomField>>(() => [
  { title: "object", key: "object_type",
    render: (r) => h(NTag, { size: "small", type: "info" }, () => r.object_type) },
  { title: "name", key: "name", render: (r) => h("code", null, r.name) },
  { title: "type", key: "field_type" },
  { title: "label (zh-TW)", key: "label_zh_tw", render: (r) => r.label_zh_tw ?? "—" },
  { title: "required", key: "required", render: (r) => r.required ? "✓" : "—" },
  { title: "order", key: "display_order", width: 70 },
  {
    title: t("common.actions"), key: "actions", width: 160,
    render: (r) => h(NSpace, { size: "small" }, () => [
      h(NButton, { size: "small", onClick: () => openEdit(r) }, () => t("common.edit")),
      h(NPopconfirm, { onPositiveClick: () => del(r) }, {
        trigger: () => h(NButton, { size: "small", type: "error" }, () => t("common.delete")),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]);
onMounted(() => { void refresh(); });
</script>

<template>
  <n-card :title="t('nav.custom_fields')">
    <n-space style="margin-bottom: 12px">
      <n-button @click="refresh" :loading="loading">{{ t("common.refresh") }}</n-button>
      <n-button type="primary" @click="openCreate">{{ t("common.create") }}</n-button>
    </n-space>
    <n-data-table :columns="cols" :data="rows" :loading="loading" :bordered="false" />

    <n-modal v-model:show="show" preset="card"
             :title="editing ? t('common.edit') : t('common.create')" style="width: 520px">
      <n-form>
        <n-form-item label="object_type">
          <n-select v-model:value="form.object_type" :options="objTypeOpts"
                    :disabled="!!editing" />
        </n-form-item>
        <n-form-item label="name (machine key)">
          <n-input v-model:value="form.name" placeholder="snake_case" :disabled="!!editing" />
        </n-form-item>
        <n-form-item label="label (zh-TW)">
          <n-input v-model:value="form.label_zh_tw" />
        </n-form-item>
        <n-form-item label="label (en-US)">
          <n-input v-model:value="form.label_en_us" />
        </n-form-item>
        <n-form-item label="field_type">
          <n-select v-model:value="form.field_type" :options="ftypeOpts"
                    :disabled="!!editing" />
        </n-form-item>
        <n-form-item label="required">
          <n-switch v-model:value="form.required" />
        </n-form-item>
        <n-form-item label="display order">
          <n-input-number v-model:value="form.display_order" :min="0" :max="10000" />
        </n-form-item>
        <n-form-item label="validation regex">
          <n-input v-model:value="form.validation_regex" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="show = false">{{ t("common.cancel") }}</n-button>
        <n-button type="primary" @click="submit">{{ t("common.save") }}</n-button>
      </n-space>
    </n-modal>
  </n-card>
</template>
