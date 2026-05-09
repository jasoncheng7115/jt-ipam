<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NButton, NModal, NForm, NFormItem,
  NInput, NSelect, useMessage, type DataTableColumns,
} from "naive-ui";
import { listDevices, createDevice, type Device } from "@/api/basic";

const { t } = useI18n();
const msg = useMessage();
const rows = ref<Device[]>([]);
const loading = ref(false);
const showCreate = ref(false);
const newRow = ref({
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
async function submit() {
  try {
    await createDevice({
      name: newRow.value.name,
      type: newRow.value.type,
      vendor: newRow.value.vendor || undefined,
      model: newRow.value.model || undefined,
      serial: newRow.value.serial || undefined,
      description: newRow.value.description || undefined,
    });
    showCreate.value = false;
    newRow.value = { name: "", type: "server", vendor: "", model: "", serial: "", description: "" };
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
const cols = computed<DataTableColumns<Device>>(() => [
  { title: t("common.name"), key: "name" },
  { title: "type", key: "type" },
  { title: "vendor", key: "vendor", render: (r) => r.vendor ?? "—" },
  { title: "model", key: "model", render: (r) => r.model ?? "—" },
  { title: "serial", key: "serial", render: (r) => r.serial ?? "—" },
]);
onMounted(() => { void refresh(); });
</script>

<template>
  <n-card :title="t('nav.devices')">
    <n-space style="margin-bottom: 12px">
      <n-button @click="refresh" :loading="loading">{{ t("common.refresh") }}</n-button>
      <n-button type="primary" @click="showCreate = true">{{ t("common.create") }}</n-button>
    </n-space>
    <n-data-table :columns="cols" :data="rows" :loading="loading" :bordered="false" />
    <n-modal v-model:show="showCreate" preset="card" title="Device" style="width: 460px">
      <n-form>
        <n-form-item :label="t('common.name')"><n-input v-model:value="newRow.name" /></n-form-item>
        <n-form-item label="type">
          <n-select v-model:value="newRow.type" :options="typeOptions" />
        </n-form-item>
        <n-form-item label="vendor"><n-input v-model:value="newRow.vendor" /></n-form-item>
        <n-form-item label="model"><n-input v-model:value="newRow.model" /></n-form-item>
        <n-form-item label="serial"><n-input v-model:value="newRow.serial" /></n-form-item>
        <n-form-item :label="t('sections.description')">
          <n-input v-model:value="newRow.description" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="showCreate = false">{{ t("common.cancel") }}</n-button>
        <n-button type="primary" @click="submit">{{ t("common.save") }}</n-button>
      </n-space>
    </n-modal>
  </n-card>
</template>
