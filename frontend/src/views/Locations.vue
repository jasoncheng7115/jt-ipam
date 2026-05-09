<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NButton, NModal, NForm, NFormItem,
  NInput, useMessage, type DataTableColumns,
} from "naive-ui";
import { listLocations, createLocation, type Location } from "@/api/basic";

const { t } = useI18n();
const msg = useMessage();
const rows = ref<Location[]>([]);
const loading = ref(false);
const showCreate = ref(false);
const newRow = ref({ name: "", site: "", address: "", description: "" });

async function refresh() {
  loading.value = true;
  try { rows.value = (await listLocations()).items; }
  catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}
async function submit() {
  try {
    await createLocation({
      name: newRow.value.name,
      site: newRow.value.site || undefined,
      address: newRow.value.address || undefined,
      description: newRow.value.description || undefined,
    });
    showCreate.value = false;
    newRow.value = { name: "", site: "", address: "", description: "" };
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
const cols = computed<DataTableColumns<Location>>(() => [
  { title: t("common.name"), key: "name" },
  { title: "site", key: "site", render: (r) => r.site ?? "—" },
  { title: "address", key: "address", render: (r) => r.address ?? "—" },
  { title: t("sections.description"), key: "description", render: (r) => r.description ?? "" },
]);
onMounted(() => { void refresh(); });
</script>

<template>
  <n-card :title="t('nav.locations')">
    <n-space style="margin-bottom: 12px">
      <n-button @click="refresh" :loading="loading">{{ t("common.refresh") }}</n-button>
      <n-button type="primary" @click="showCreate = true">{{ t("common.create") }}</n-button>
    </n-space>
    <n-data-table :columns="cols" :data="rows" :loading="loading" :bordered="false" />
    <n-modal v-model:show="showCreate" preset="card" title="Location" style="width: 460px">
      <n-form>
        <n-form-item :label="t('common.name')"><n-input v-model:value="newRow.name" /></n-form-item>
        <n-form-item label="site"><n-input v-model:value="newRow.site" /></n-form-item>
        <n-form-item label="address"><n-input v-model:value="newRow.address" /></n-form-item>
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
