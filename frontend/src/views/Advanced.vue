<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NTabs, NTabPane, NDataTable, NSpace, NIcon, NButton,
  NModal, NForm, NFormItem, NInput, NInputNumber, NSelect,
  useMessage, type DataTableColumns,
} from "naive-ui";
import {
  AdvancedIcon, PlusIcon, DeleteIcon, SaveIcon, CancelIcon,
} from "@/icons";
import { apiClient } from "@/api/client";
import { Advanced } from "@/api/phase3";

const { t } = useI18n();
const msg = useMessage();
const tab = ref<"tenancy" | "asn" | "circuits" | "contacts" | "wireless">("tenancy");

// 共用：通用 lazy 取資料 + create form
const tenants = ref<any[]>([]);
const tenantGroups = ref<any[]>([]);
const asns = ref<any[]>([]);
const providers = ref<any[]>([]);
const circuitTypes = ref<any[]>([]);
const circuits = ref<any[]>([]);
const contactGroups = ref<any[]>([]);
const contactRoles = ref<any[]>([]);
const contacts = ref<any[]>([]);
const ssids = ref<any[]>([]);
const links = ref<any[]>([]);

const loading = ref(false);

async function loadAll() {
  loading.value = true;
  try {
    [tenants.value, tenantGroups.value, asns.value, providers.value,
     circuitTypes.value, circuits.value, contactGroups.value, contactRoles.value,
     contacts.value, ssids.value, links.value]
      = await Promise.all([
        Advanced.tenants(), Advanced.tenantGroups(), Advanced.asns(),
        Advanced.providers(), Advanced.circuitTypes(), Advanced.circuits(),
        Advanced.contactGroups(), Advanced.contactRoles(), Advanced.contacts(),
        Advanced.ssids(), Advanced.links(),
      ]);
  } catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}

// ── Generic create modal ──
const createForm = ref<{ resource: string; payload: Record<string, any> }>({
  resource: "", payload: {},
});
const showCreate = ref(false);

function openCreate(resource: string, defaults: Record<string, any> = {}) {
  createForm.value = { resource, payload: { ...defaults } };
  showCreate.value = true;
}
async function submit() {
  try {
    await apiClient.post(`/api/v1/${createForm.value.resource}`, createForm.value.payload);
    showCreate.value = false;
    await loadAll();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function delResource(resource: string, id: string) {
  if (!confirm(t("common.confirm_delete"))) return;
  try {
    await apiClient.delete(`/api/v1/${resource}/${id}`);
    await loadAll();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

const tenantCols = computed<DataTableColumns<any>>(() => [
  { title: t("common.name"), key: "name" },
  { title: "group", key: "tenant_group_id",
    render: (r) => tenantGroups.value.find((g) => g.id === r.tenant_group_id)?.name ?? "—" },
  { title: t("sections.description"), key: "description" },
  { title: t("common.actions"), key: "_", width: 100,
    render: (r) => h(NButton, { size: "small", type: "error",
      onClick: () => delResource("tenants", r.id) }, () => t("common.delete")) },
]);
const tenantGroupCols = computed<DataTableColumns<any>>(() => [
  { title: t("common.name"), key: "name" },
  { title: t("sections.description"), key: "description" },
  { title: t("common.actions"), key: "_", width: 100,
    render: (r) => h(NButton, { size: "small", type: "error",
      onClick: () => delResource("tenant-groups", r.id) }, () => t("common.delete")) },
]);
const asnCols = computed<DataTableColumns<any>>(() => [
  { title: "ASN", key: "number" },
  { title: "RIR", key: "rir" },
  { title: t("sections.description"), key: "description" },
  { title: t("common.actions"), key: "_", width: 100,
    render: (r) => h(NButton, { size: "small", type: "error",
      onClick: () => delResource("asns", r.id) }, () => t("common.delete")) },
]);
const providerCols = computed<DataTableColumns<any>>(() => [
  { title: t("common.name"), key: "name" },
  { title: t("circuits.account"), key: "account", render: (r) => r.account ?? "—" },
  { title: t("sections.description"), key: "description" },
]);
const circuitCols = computed<DataTableColumns<any>>(() => [
  { title: "CID", key: "cid" },
  { title: t("circuits.provider"), key: "provider_id",
    render: (r) => providers.value.find((p) => p.id === r.provider_id)?.name ?? "—" },
  { title: t("circuits.type"), key: "type_id",
    render: (r) => circuitTypes.value.find((p) => p.id === r.type_id)?.name ?? "—" },
  { title: t("common.status"), key: "status" },
]);
const contactCols = computed<DataTableColumns<any>>(() => [
  { title: t("common.name"), key: "name" },
  { title: "email", key: "email", render: (r) => r.email ?? "—" },
  { title: "phone", key: "phone", render: (r) => r.phone ?? "—" },
  { title: t("contacts.group"), key: "group_id",
    render: (r) => contactGroups.value.find((g) => g.id === r.group_id)?.name ?? "—" },
]);
const ssidCols = computed<DataTableColumns<any>>(() => [
  { title: "SSID", key: "name" },
  { title: t("sections.description"), key: "description" },
]);

onMounted(() => { void loadAll(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><AdvancedIcon /></n-icon>
        <span>{{ t("nav.advanced") }}</span>
      </n-space>
    </template>
    <n-tabs v-model:value="tab" type="line">
      <n-tab-pane name="tenancy" :tab="t('advanced.tenancy')">
        <h3>{{ t("advanced.tenants") }}</h3>
        <n-space style="margin: 8px 0">
          <n-button size="small" type="primary"
            @click="openCreate('tenants', { name: '', description: '' })">
            {{ t("common.create") }}
          </n-button>
        </n-space>
        <n-data-table :columns="tenantCols" :data="tenants" :loading="loading" :bordered="false" />

        <h3 style="margin-top: 24px">{{ t("advanced.tenant_groups") }}</h3>
        <n-space style="margin: 8px 0">
          <n-button size="small" type="primary"
            @click="openCreate('tenant-groups', { name: '', description: '' })">
            {{ t("common.create") }}
          </n-button>
        </n-space>
        <n-data-table :columns="tenantGroupCols" :data="tenantGroups" :loading="loading" :bordered="false" />
      </n-tab-pane>

      <n-tab-pane name="asn" tab="ASN">
        <n-space style="margin: 8px 0">
          <n-button size="small" type="primary"
            @click="openCreate('asns', { number: 65000, rir: '', description: '' })">
            {{ t("common.create") }}
          </n-button>
        </n-space>
        <n-data-table :columns="asnCols" :data="asns" :loading="loading" :bordered="false" />
      </n-tab-pane>

      <n-tab-pane name="circuits" :tab="t('advanced.circuits')">
        <h3>{{ t("circuits.providers") }}</h3>
        <n-space style="margin: 8px 0">
          <n-button size="small" type="primary"
            @click="openCreate('providers', { name: '', account: '', description: '' })">
            {{ t("common.create") }}
          </n-button>
        </n-space>
        <n-data-table :columns="providerCols" :data="providers" :loading="loading" :bordered="false" />

        <h3 style="margin-top: 24px">{{ t("advanced.circuits") }}</h3>
        <n-data-table :columns="circuitCols" :data="circuits" :loading="loading" :bordered="false" />
      </n-tab-pane>

      <n-tab-pane name="contacts" :tab="t('advanced.contacts')">
        <h3>{{ t("advanced.contact_groups") }}</h3>
        <n-space style="margin: 8px 0">
          <n-button size="small" type="primary"
            @click="openCreate('contact-groups', { name: '', description: '' })">
            {{ t("common.create") }}
          </n-button>
        </n-space>
        <n-data-table :columns="tenantGroupCols" :data="contactGroups" :loading="loading" :bordered="false" />

        <h3 style="margin-top: 24px">{{ t("advanced.contacts") }}</h3>
        <n-space style="margin: 8px 0">
          <n-button size="small" type="primary"
            @click="openCreate('contacts', { name: '', email: '', phone: '', description: '' })">
            {{ t("common.create") }}
          </n-button>
        </n-space>
        <n-data-table :columns="contactCols" :data="contacts" :loading="loading" :bordered="false" />
      </n-tab-pane>

      <n-tab-pane name="wireless" :tab="t('advanced.wireless')">
        <h3>SSID</h3>
        <n-space style="margin: 8px 0">
          <n-button size="small" type="primary"
            @click="openCreate('wireless/ssids', { name: '', description: '' })">
            {{ t("common.create") }}
          </n-button>
        </n-space>
        <n-data-table :columns="ssidCols" :data="ssids" :loading="loading" :bordered="false" />
      </n-tab-pane>
    </n-tabs>

    <n-modal v-model:show="showCreate" preset="card"
             :title="`${t('common.create')} — ${createForm.resource}`" style="width: 520px">
      <n-form>
        <template v-for="(_, k) in createForm.payload" :key="k">
          <n-form-item :label="String(k)">
            <n-input-number v-if="k === 'number'" v-model:value="createForm.payload[k]" :min="0" />
            <n-input v-else v-model:value="createForm.payload[k]" />
          </n-form-item>
        </template>
      </n-form>
      <n-space justify="end">
        <n-button @click="showCreate = false">{{ t("common.cancel") }}</n-button>
        <n-button type="primary" @click="submit">{{ t("common.save") }}</n-button>
      </n-space>
    </n-modal>
  </n-card>
</template>
