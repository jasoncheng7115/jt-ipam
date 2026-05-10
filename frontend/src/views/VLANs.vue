<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NButton, NModal, NForm, NFormItem,
  NInput, NInputNumber, NSelect, NTabs, NTabPane, NPopconfirm,
  useMessage, type DataTableColumns,
} from "naive-ui";
import {
  listVLANDomains, listVLANs, createVLANDomain, createVLAN,
  updateVLAN, deleteVLAN, updateVLANDomain, deleteVLANDomain,
  type VLAN, type VLANDomain,
} from "@/api/basic";

const { t } = useI18n();
const msg = useMessage();
const tab = ref<"vlans" | "domains">("vlans");

const domains = ref<VLANDomain[]>([]);
const vlans = ref<VLAN[]>([]);
const loading = ref(false);

const showVLAN = ref(false);
const editingVLAN = ref<VLAN | null>(null);
const vlanForm = ref({ domain_id: "", number: 100, name: "", description: "" });

const showDom = ref(false);
const editingDom = ref<VLANDomain | null>(null);
const domForm = ref({ name: "", description: "" });

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

function openVlanCreate() {
  editingVLAN.value = null;
  vlanForm.value = { domain_id: domains.value[0]?.id ?? "", number: 100, name: "", description: "" };
  showVLAN.value = true;
}
function openVlanEdit(r: VLAN) {
  editingVLAN.value = r;
  vlanForm.value = { domain_id: r.domain_id, number: r.number, name: r.name, description: r.description ?? "" };
  showVLAN.value = true;
}
async function submitVlan() {
  try {
    if (editingVLAN.value) {
      await updateVLAN(editingVLAN.value.id, {
        name: vlanForm.value.name,
        description: vlanForm.value.description || undefined,
      });
    } else {
      await createVLAN({
        domain_id: vlanForm.value.domain_id,
        number: vlanForm.value.number,
        name: vlanForm.value.name,
        description: vlanForm.value.description || undefined,
      });
    }
    showVLAN.value = false;
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function delVlan(r: VLAN) {
  try { await deleteVLAN(r.id); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

function openDomCreate() {
  editingDom.value = null;
  domForm.value = { name: "", description: "" };
  showDom.value = true;
}
function openDomEdit(r: VLANDomain) {
  editingDom.value = r;
  domForm.value = { name: r.name, description: r.description ?? "" };
  showDom.value = true;
}
async function submitDom() {
  try {
    if (editingDom.value) {
      await updateVLANDomain(editingDom.value.id, {
        name: domForm.value.name,
        description: domForm.value.description || undefined,
      });
    } else {
      await createVLANDomain(domForm.value.name, domForm.value.description || undefined);
    }
    showDom.value = false;
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function delDom(r: VLANDomain) {
  try { await deleteVLANDomain(r.id); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

const vlanCols = computed<DataTableColumns<VLAN>>(() => [
  { title: "VID", key: "number", width: 70 },
  { title: t("common.name"), key: "name" },
  {
    title: "Domain", key: "domain_id",
    render: (r) => domains.value.find((d) => d.id === r.domain_id)?.name ?? "—",
  },
  { title: t("sections.description"), key: "description", render: (r) => r.description ?? "" },
  {
    title: t("common.actions"), key: "actions", width: 160,
    render: (r) => h(NSpace, { size: "small" }, () => [
      h(NButton, { size: "small", onClick: () => openVlanEdit(r) }, () => t("common.edit")),
      h(NPopconfirm, { onPositiveClick: () => delVlan(r) }, {
        trigger: () => h(NButton, { size: "small", type: "error" }, () => t("common.delete")),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]);
const domCols = computed<DataTableColumns<VLANDomain>>(() => [
  { title: t("common.name"), key: "name" },
  { title: t("sections.description"), key: "description", render: (r) => r.description ?? "" },
  {
    title: t("common.actions"), key: "actions", width: 160,
    render: (r) => h(NSpace, { size: "small" }, () => [
      h(NButton, { size: "small", onClick: () => openDomEdit(r) }, () => t("common.edit")),
      h(NPopconfirm, { onPositiveClick: () => delDom(r) }, {
        trigger: () => h(NButton, { size: "small", type: "error" }, () => t("common.delete")),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]);

onMounted(() => { void refresh(); });
</script>

<template>
  <n-card :title="t('nav.vlans')">
    <n-tabs v-model:value="tab" type="line">
      <n-tab-pane name="vlans" :tab="t('nav.vlans')">
        <n-space style="margin-bottom: 12px">
          <n-button @click="refresh" :loading="loading">{{ t("common.refresh") }}</n-button>
          <n-button type="primary" @click="openVlanCreate">{{ t("common.create") }}</n-button>
        </n-space>
        <n-data-table :columns="vlanCols" :data="vlans" :loading="loading" :bordered="false" />
      </n-tab-pane>
      <n-tab-pane name="domains" tab="VLAN Domain">
        <n-space style="margin-bottom: 12px">
          <n-button type="primary" @click="openDomCreate">{{ t("common.create") }}</n-button>
        </n-space>
        <n-data-table :columns="domCols" :data="domains" :loading="loading" :bordered="false" />
      </n-tab-pane>
    </n-tabs>

    <n-modal v-model:show="showVLAN" preset="card"
             :title="editingVLAN ? t('common.edit') : t('common.create')"
             style="width: 460px">
      <n-form>
        <n-form-item label="Domain">
          <n-select v-model:value="vlanForm.domain_id" :options="domainOptions"
                    :disabled="!!editingVLAN" />
        </n-form-item>
        <n-form-item label="VID">
          <n-input-number v-model:value="vlanForm.number" :min="1" :max="4094"
                          :disabled="!!editingVLAN" />
        </n-form-item>
        <n-form-item :label="t('common.name')">
          <n-input v-model:value="vlanForm.name" />
        </n-form-item>
        <n-form-item :label="t('sections.description')">
          <n-input v-model:value="vlanForm.description" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="showVLAN = false">{{ t("common.cancel") }}</n-button>
        <n-button type="primary" @click="submitVlan">{{ t("common.save") }}</n-button>
      </n-space>
    </n-modal>

    <n-modal v-model:show="showDom" preset="card"
             :title="editingDom ? t('common.edit') : t('common.create')"
             style="width: 460px">
      <n-form>
        <n-form-item :label="t('common.name')">
          <n-input v-model:value="domForm.name" />
        </n-form-item>
        <n-form-item :label="t('sections.description')">
          <n-input v-model:value="domForm.description" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="showDom = false">{{ t("common.cancel") }}</n-button>
        <n-button type="primary" @click="submitDom">{{ t("common.save") }}</n-button>
      </n-space>
    </n-modal>
  </n-card>
</template>
