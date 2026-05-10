<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NButton, NModal, NForm, NFormItem,
  NInput, NSelect, NPopconfirm,
  useMessage, type DataTableColumns,
} from "naive-ui";
import {
  listDevices, createDevice, updateDevice, deleteDevice, type Device,
} from "@/api/basic";

const { t } = useI18n();
const msg = useMessage();
const rows = ref<Device[]>([]);
const loading = ref(false);
const show = ref(false);
const editing = ref<Device | null>(null);
const form = ref({
  name: "", type: "server", vendor: "", model: "", serial: "", description: "",
});

const typeOptions = ["server", "switch", "router", "firewall", "ap", "storage", "ipmi", "other"]
  .map((t_) => ({ label: t_, value: t_ }));

async function refresh() {
  loading.value = true;
  try { rows.value = (await listDevices()).items; }
  catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}
function openCreate() {
  editing.value = null;
  form.value = { name: "", type: "server", vendor: "", model: "", serial: "", description: "" };
  show.value = true;
}
function openEdit(r: Device) {
  editing.value = r;
  form.value = {
    name: r.name, type: r.type,
    vendor: r.vendor ?? "", model: r.model ?? "", serial: r.serial ?? "",
    description: r.description ?? "",
  };
  show.value = true;
}
async function submit() {
  try {
    const payload = {
      name: form.value.name,
      type: form.value.type,
      vendor: form.value.vendor || undefined,
      model: form.value.model || undefined,
      serial: form.value.serial || undefined,
      description: form.value.description || undefined,
    };
    if (editing.value) await updateDevice(editing.value.id, payload);
    else await createDevice(payload);
    show.value = false;
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function del(r: Device) {
  try { await deleteDevice(r.id); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

const cols = computed<DataTableColumns<Device>>(() => [
  { title: t("common.name"), key: "name" },
  { title: t("devices.type"), key: "type" },
  { title: t("devices.vendor"), key: "vendor", render: (r) => r.vendor ?? "—" },
  { title: t("devices.model"), key: "model", render: (r) => r.model ?? "—" },
  { title: t("devices.serial"), key: "serial", render: (r) => r.serial ?? "—" },
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
  <n-card :title="t('nav.devices')">
    <n-space style="margin-bottom: 12px">
      <n-button @click="refresh" :loading="loading">{{ t("common.refresh") }}</n-button>
      <n-button type="primary" @click="openCreate">{{ t("common.create") }}</n-button>
    </n-space>
    <n-data-table :columns="cols" :data="rows" :loading="loading" :bordered="false" />
    <n-modal v-model:show="show" preset="card"
             :title="editing ? t('common.edit') : t('common.create')" style="width: 460px">
      <n-form>
        <n-form-item :label="t('common.name')"><n-input v-model:value="form.name" /></n-form-item>
        <n-form-item :label="t('devices.type')">
          <n-select v-model:value="form.type" :options="typeOptions" />
        </n-form-item>
        <n-form-item :label="t('devices.vendor')"><n-input v-model:value="form.vendor" /></n-form-item>
        <n-form-item :label="t('devices.model')"><n-input v-model:value="form.model" /></n-form-item>
        <n-form-item :label="t('devices.serial')"><n-input v-model:value="form.serial" /></n-form-item>
        <n-form-item :label="t('sections.description')">
          <n-input v-model:value="form.description" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="show = false">{{ t("common.cancel") }}</n-button>
        <n-button type="primary" @click="submit">{{ t("common.save") }}</n-button>
      </n-space>
    </n-modal>
  </n-card>
</template>
