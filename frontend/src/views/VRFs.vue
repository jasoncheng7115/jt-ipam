<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NButton, NModal, NForm, NFormItem,
  NInput, NSwitch, useMessage, type DataTableColumns,
} from "naive-ui";
import { listVRFs, createVRF, type VRF } from "@/api/basic";

const { t } = useI18n();
const msg = useMessage();
const rows = ref<VRF[]>([]);
const loading = ref(false);
const showCreate = ref(false);
const newRow = ref({ name: "", rd: "", description: "", allow_overlap: true });

async function refresh() {
  loading.value = true;
  try { rows.value = (await listVRFs()).items; }
  catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}
async function submit() {
  try {
    await createVRF({
      name: newRow.value.name,
      rd: newRow.value.rd || undefined,
      description: newRow.value.description || undefined,
      allow_overlap: newRow.value.allow_overlap,
    });
    showCreate.value = false;
    newRow.value = { name: "", rd: "", description: "", allow_overlap: true };
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

const cols = computed<DataTableColumns<VRF>>(() => [
  { title: t("common.name"), key: "name" },
  { title: "RD", key: "rd", render: (r) => r.rd ?? "—" },
  { title: t("sections.description"), key: "description", render: (r) => r.description ?? "" },
  { title: "allow overlap", key: "allow_overlap",
    render: (r) => r.allow_overlap ? "✓" : "—" },
]);
onMounted(() => { void refresh(); });
</script>

<template>
  <n-card :title="t('nav.vrfs')">
    <n-space style="margin-bottom: 12px">
      <n-button @click="refresh" :loading="loading">{{ t("common.refresh") }}</n-button>
      <n-button type="primary" @click="showCreate = true">{{ t("common.create") }}</n-button>
    </n-space>
    <n-data-table :columns="cols" :data="rows" :loading="loading" :bordered="false" />
    <n-modal v-model:show="showCreate" preset="card" title="VRF" style="width: 460px">
      <n-form>
        <n-form-item :label="t('common.name')"><n-input v-model:value="newRow.name" /></n-form-item>
        <n-form-item label="RD"><n-input v-model:value="newRow.rd" placeholder="65000:1" /></n-form-item>
        <n-form-item :label="t('sections.description')">
          <n-input v-model:value="newRow.description" type="textarea" :rows="2" />
        </n-form-item>
        <n-form-item label="Allow overlap">
          <n-switch v-model:value="newRow.allow_overlap" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="showCreate = false">{{ t("common.cancel") }}</n-button>
        <n-button type="primary" @click="submit">{{ t("common.save") }}</n-button>
      </n-space>
    </n-modal>
  </n-card>
</template>
