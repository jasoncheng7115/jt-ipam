<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NButton, NModal, NForm, NFormItem,
  NInput, NSwitch, NTabs, NTabPane, NTag, NPopconfirm, NAlert,
  useMessage, type DataTableColumns,
} from "naive-ui";
import {
  listWazuh, createWazuh, deleteWazuh, testWazuh, syncWazuh,
  listWazuhAgents, listMissingAgents,
  type WazuhInstance, type WazuhAgent, type MissingAgent,
} from "@/api/integrations";

const { t } = useI18n();
const msg = useMessage();
const tab = ref<"instances" | "agents" | "missing">("instances");
const insts = ref<WazuhInstance[]>([]);
const agents = ref<WazuhAgent[]>([]);
const missing = ref<MissingAgent[]>([]);
const loading = ref(false);

const showCreate = ref(false);
const newInst = ref({
  name: "", api_url: "https://wazuh:55000",
  api_user: "wazuh-api-user", api_password: "",
  verify_tls: true,
});

async function refresh() {
  loading.value = true;
  try {
    const [i, a, m] = await Promise.all([
      listWazuh(50, 0), listWazuhAgents(undefined, undefined, 200, 0),
      listMissingAgents(),
    ]);
    insts.value = i.items;
    agents.value = a.items;
    missing.value = m;
  } catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}
async function submit() {
  try {
    await createWazuh({
      name: newInst.value.name, api_url: newInst.value.api_url,
      api_user: newInst.value.api_user, api_password: newInst.value.api_password,
      verify_tls: newInst.value.verify_tls,
    });
    showCreate.value = false;
    newInst.value = { name: "", api_url: "https://wazuh:55000", api_user: "wazuh-api-user", api_password: "", verify_tls: true };
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function test(id: string) {
  try { const r = await testWazuh(id); msg.success(JSON.stringify(r).slice(0, 80)); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function sync(id: string) {
  try { const r = await syncWazuh(id); msg.success(JSON.stringify(r).slice(0, 100)); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function del(id: string) {
  try { await deleteWazuh(id); await refresh(); } catch { msg.error(t("errors.server")); }
}

const instCols = computed<DataTableColumns<WazuhInstance>>(() => [
  { title: t("common.name"), key: "name" },
  { title: "API URL", key: "api_url" },
  { title: "user", key: "api_user" },
  {
    title: "last sync", key: "last_sync_at",
    render: (r) => r.last_sync_at ? new Date(r.last_sync_at).toLocaleString() : "—",
  },
  { title: "last error", key: "last_error", render: (r) => r.last_error ?? "—" },
  {
    title: t("common.actions"), key: "actions",
    render: (r) => h(NSpace, { size: "small" }, () => [
      h(NButton, { size: "small", onClick: () => test(r.id) }, () => t("common.test")),
      h(NButton, { size: "small", type: "primary", onClick: () => sync(r.id) }, () => t("common.sync")),
      h(NPopconfirm, { onPositiveClick: () => del(r.id) },
        { trigger: () => h(NButton, { size: "small", type: "error" }, () => t("common.delete")),
          default: () => t("common.confirm_delete") }),
    ]),
  },
]);
const agentCols = computed<DataTableColumns<WazuhAgent>>(() => [
  { title: "Agent ID", key: "agent_id", width: 100 },
  { title: t("common.name"), key: "name", render: (r) => r.name ?? "—" },
  { title: "IP", key: "ip", render: (r) => r.ip ?? "—" },
  {
    title: t("common.status"), key: "status",
    render: (r) => h(NTag, {
      size: "small",
      type: r.status === "active" ? "success" : r.status === "disconnected" ? "error" : "default",
    }, () => r.status ?? "—"),
  },
  { title: "OS", key: "os_platform", render: (r) => r.os_platform ?? "—" },
  { title: "version", key: "agent_version", render: (r) => r.agent_version ?? "—" },
  {
    title: "last alive", key: "last_keep_alive",
    render: (r) => r.last_keep_alive ? new Date(r.last_keep_alive).toLocaleString() : "—",
  },
]);
const missCols = computed<DataTableColumns<MissingAgent>>(() => [
  { title: "IP", key: "ip", render: (r) => r.ip ?? "—" },
  { title: "hostname", key: "hostname", render: (r) => r.hostname ?? "—" },
  { title: "IP UUID", key: "ip_address_id" },
]);

onMounted(() => { void refresh(); });
</script>

<template>
  <n-card :title="t('wazuh_admin.title')">
    <n-tabs v-model:value="tab" type="line">
      <n-tab-pane name="instances" :tab="t('wazuh_admin.title')">
        <n-space style="margin-bottom: 12px">
          <n-button @click="refresh" :loading="loading">{{ t("common.refresh") }}</n-button>
          <n-button type="primary" @click="showCreate = true">{{ t("wazuh_admin.create_instance") }}</n-button>
        </n-space>
        <n-data-table :columns="instCols" :data="insts" :loading="loading" :bordered="false" />
      </n-tab-pane>
      <n-tab-pane name="agents" :tab="`${t('wazuh_admin.agents_count')} (${agents.length})`">
        <n-data-table :columns="agentCols" :data="agents" :loading="loading" :bordered="false" />
      </n-tab-pane>
      <n-tab-pane name="missing"
                  :tab="`${t('wazuh_admin.missing_agents')} (${missing.length})`">
        <n-alert v-if="missing.length" type="warning" style="margin-bottom: 12px">
          {{ missing.length }} {{ t("wazuh_admin.missing_agents") }}
        </n-alert>
        <n-data-table :columns="missCols" :data="missing" :loading="loading" :bordered="false" />
      </n-tab-pane>
    </n-tabs>

    <n-modal v-model:show="showCreate" preset="card" :title="t('wazuh_admin.create_instance')"
             style="width: 460px">
      <n-form>
        <n-form-item :label="t('common.name')"><n-input v-model:value="newInst.name" /></n-form-item>
        <n-form-item label="API URL"><n-input v-model:value="newInst.api_url" /></n-form-item>
        <n-form-item label="API user"><n-input v-model:value="newInst.api_user" /></n-form-item>
        <n-form-item label="API password">
          <n-input v-model:value="newInst.api_password" type="password" show-password-on="click" />
        </n-form-item>
        <n-form-item label="Verify TLS"><n-switch v-model:value="newInst.verify_tls" /></n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="showCreate = false">{{ t("common.cancel") }}</n-button>
        <n-button type="primary" @click="submit">{{ t("common.save") }}</n-button>
      </n-space>
    </n-modal>
  </n-card>
</template>
