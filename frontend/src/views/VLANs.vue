<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NButton, NModal, NForm, NFormItem,
  NInput, NInputNumber, NSelect, NTabs, NTabPane,
  useMessage, type DataTableColumns,
} from "naive-ui";
import {
  listVLANDomains, listVLANs, createVLANDomain, createVLAN,
  type VLAN, type VLANDomain,
} from "@/api/basic";

const { t } = useI18n();
const msg = useMessage();
const tab = ref<"vlans" | "domains">("vlans");

const domains = ref<VLANDomain[]>([]);
const vlans = ref<VLAN[]>([]);
const loading = ref(false);

const showVLANCreate = ref(false);
const showDomCreate = ref(false);
const newVlan = ref({ domain_id: "", number: 100, name: "", description: "" });
const newDom = ref({ name: "", description: "" });

const domainOptions = computed(() =>
  domains.value.map((d) => ({ label: d.name, value: d.id })));

async function refresh() {
  loading.value = true;
  try {
    const [d, v] = await Promise.all([listVLANDomains(), listVLANs()]);
    domains.value = d.items;
    vlans.value = v.items;
  } catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}

async function submitVlan() {
  try {
    await createVLAN(newVlan.value);
    showVLANCreate.value = false;
    newVlan.value = { domain_id: "", number: 100, name: "", description: "" };
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function submitDom() {
  try {
    await createVLANDomain(newDom.value.name, newDom.value.description || undefined);
    showDomCreate.value = false;
    newDom.value = { name: "", description: "" };
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

const vlanCols = computed<DataTableColumns<VLAN>>(() => [
  { title: "VID", key: "number" },
  { title: t("common.name"), key: "name" },
  {
    title: "Domain", key: "domain_id",
    render: (r) => domains.value.find((d) => d.id === r.domain_id)?.name ?? "—",
  },
  { title: t("sections.description"), key: "description", render: (r) => r.description ?? "" },
]);
const domCols = computed<DataTableColumns<VLANDomain>>(() => [
  { title: t("common.name"), key: "name" },
  { title: t("sections.description"), key: "description", render: (r) => r.description ?? "" },
]);

onMounted(() => { void refresh(); });
</script>

<template>
  <n-card :title="t('nav.vlans')">
    <n-tabs v-model:value="tab" type="line">
      <n-tab-pane name="vlans" :tab="t('nav.vlans')">
        <n-space style="margin-bottom: 12px">
          <n-button @click="refresh" :loading="loading">{{ t("common.refresh") }}</n-button>
          <n-button type="primary" @click="showVLANCreate = true">{{ t("common.create") }}</n-button>
        </n-space>
        <n-data-table :columns="vlanCols" :data="vlans" :loading="loading" :bordered="false" />
      </n-tab-pane>
      <n-tab-pane name="domains" tab="VLAN Domain">
        <n-space style="margin-bottom: 12px">
          <n-button type="primary" @click="showDomCreate = true">{{ t("common.create") }}</n-button>
        </n-space>
        <n-data-table :columns="domCols" :data="domains" :loading="loading" :bordered="false" />
      </n-tab-pane>
    </n-tabs>

    <n-modal v-model:show="showVLANCreate" preset="card" title="VLAN" style="width: 460px">
      <n-form>
        <n-form-item label="Domain">
          <n-select v-model:value="newVlan.domain_id" :options="domainOptions" />
        </n-form-item>
        <n-form-item label="VID">
          <n-input-number v-model:value="newVlan.number" :min="1" :max="4094" />
        </n-form-item>
        <n-form-item :label="t('common.name')">
          <n-input v-model:value="newVlan.name" />
        </n-form-item>
        <n-form-item :label="t('sections.description')">
          <n-input v-model:value="newVlan.description" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="showVLANCreate = false">{{ t("common.cancel") }}</n-button>
        <n-button type="primary" @click="submitVlan">{{ t("common.save") }}</n-button>
      </n-space>
    </n-modal>

    <n-modal v-model:show="showDomCreate" preset="card" title="VLAN Domain" style="width: 460px">
      <n-form>
        <n-form-item :label="t('common.name')">
          <n-input v-model:value="newDom.name" />
        </n-form-item>
        <n-form-item :label="t('sections.description')">
          <n-input v-model:value="newDom.description" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="showDomCreate = false">{{ t("common.cancel") }}</n-button>
        <n-button type="primary" @click="submitDom">{{ t("common.save") }}</n-button>
      </n-space>
    </n-modal>
  </n-card>
</template>
