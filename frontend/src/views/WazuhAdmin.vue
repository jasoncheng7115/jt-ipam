<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from "vue";
import { fmtDateTime } from "@/utils/datetime";
import { wazuhStatusLabel } from "@/utils/integrationStatus";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import ScopeOverlapWarning from "@/components/ScopeOverlapWarning.vue";
import {
  NCard, NDataTable, NSpace, NIcon, NButton, NModal, NForm, NFormItem,
  NInput, NSwitch, NSelect, NTabs, NTabPane, NTag, NPopconfirm, NAlert, NTooltip,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { listSubnets } from "@/api/subnets";
import {
  WazuhIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SyncIcon, TestIcon, MissingIcon, DevicesIcon, EyeIcon, CancelIcon, SaveIcon,
} from "@/icons";
import {
  listWazuh, createWazuh, updateWazuh, deleteWazuh, testWazuh, syncWazuh,
  listWazuhAgents, listMissingAgentsPage,
  type WazuhInstance, type WazuhAgent, type MissingAgent,
} from "@/api/integrations";
import { autoSort } from "@/composables/useTableSort";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ScopeFilterBar from "@/components/ScopeFilterBar.vue";
import { useRemoteMissing } from "@/composables/useRemoteMissing";
import { livenessColumn } from "@/utils/livenessColumn";
import { withExportValue } from "@/utils/tableExport";
import ExportButton from "@/components/ExportButton.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { deviceKindColumn } from "@/utils/deviceKindCell";
const { t, te } = useI18n();

const wzInst = useColumnPrefs("wazuh_inst",
  ["name", "api_url", "api_user", "last_sync_at", "last_error", "actions"],
  ["name", "api_url", "api_user", "last_sync_at", "last_error", "actions"]);
const wzInstPicker = computed(() => [
  { key: "name", label: t("cols.name") }, { key: "api_url", label: "API URL" },
  { key: "api_user", label: "User" }, { key: "last_sync_at", label: t("cols.last_sync") },
  { key: "last_error", label: t("cols.last_error") }, { key: "actions", label: t("cols.actions") },
]);
const wzAg = useColumnPrefs("wazuh_agents",
  ["agent_id", "name", "ip", "status", "os_platform", "agent_version", "last_keep_alive"],
  ["agent_id", "name", "ip", "status", "os_platform", "agent_version", "last_keep_alive"]);
const wzAgPicker = computed(() => [
  { key: "agent_id", label: "Agent ID" }, { key: "name", label: t("cols.name") },
  { key: "ip", label: "IP" }, { key: "status", label: t("cols.status") },
  { key: "os_platform", label: "OS" }, { key: "agent_version", label: t("cols.version") },
  { key: "last_keep_alive", label: t("cols.last_alive") },
]);
// 設備類型預設顯示：一眼看出缺的是伺服器還是交換器／印表機（那些本來就裝不了 Wazuh agent）
const wzMiss = useColumnPrefs("wazuh_missing",
  ["status", "ip", "hostname", "device_kind", "subnet", "section", "customer", "actions"],
  ["status", "ip", "hostname", "device_kind", "subnet", "section", "customer", "actions"]);
const wzMissPicker = computed(() => [
  { key: "status", label: t("cols.status") },
  { key: "ip", label: "IP" }, { key: "hostname", label: t("cols.hostname") },
  { key: "device_kind", label: t("cols.device_kind") },
  { key: "subnet", label: t("cols.subnet") }, { key: "section", label: t("cols.section") },
  { key: "customer", label: t("cols.unit") }, { key: "actions", label: t("cols.actions") },
]);

const msg = useMessage();
const router = useRouter();
const tab = ref<"instances" | "agents" | "missing">("instances");
const insts = ref<WazuhInstance[]>([]);
const agents = ref<WazuhAgent[]>([]);
import { useTableQuickFilter } from "@/composables/useTableQuickFilter";
const { query: agentFilterQ, filtered: agentsFiltered } = useTableQuickFilter(agents);
import { useTablePagination } from "@/composables/useTablePagination";
import { apiErrMsg } from "@/api/client";
const pg = useTablePagination();
// 缺口清單由後端分頁、篩選、排序（大站台 5 萬筆時整份抓回來要幾十 MB）
const miss = useRemoteMissing(listMissingAgentsPage);
const loading = ref(false);

const showInst = ref(false);
const editing = ref<WazuhInstance | null>(null);
const newInst = ref({
  name: "", api_url: "",
  api_user: "", api_password: "",
  verify_tls: true,
  scope_subnet_ids: [] as string[],
});

const subnetOptions = ref<{ label: string; value: string }[]>([]);
async function loadSubnetOptions() {
  try {
    const r = await listSubnets({ page: 1, pageSize: 500 });
    subnetOptions.value = r.items.map((s) => ({
      label: s.description ? `${s.cidr} — ${s.description}` : s.cidr, value: s.id }));
  } catch { /* silent */ }
}

// 「未裝 Agent 的 IP」只列啟用中整合的範圍聯集；任一個沒設範圍就是全域（同後端 expected_subnets）
const missingScoped = computed(() => {
  const on = insts.value.filter((r) => r.enabled);
  return on.length > 0 && on.every((r) => (r.scope_subnet_ids ?? []).length > 0);
});

function openCreate() {
  editing.value = null;
  newInst.value = { name: "", api_url: "",
    api_user: "", api_password: "", verify_tls: true, scope_subnet_ids: [],
 };
  showInst.value = true;
}
function openEdit(r: WazuhInstance) {
  editing.value = r;
  newInst.value = {
    name: r.name, api_url: r.api_url, api_user: r.api_user,
    api_password: "", verify_tls: r.verify_tls,
    scope_subnet_ids: r.scope_subnet_ids ?? [],
  };
  showInst.value = true;
}

async function fetchAllAgents() {
  // 逐頁抓完整 agent 清單（後端 limit 上限 500）→ 超過 500 台也不會被截斷
  const all: typeof agents.value = [];
  for (let offset = 0; offset < 200_000; offset += 500) {
    const r = await listWazuhAgents(undefined, undefined, 500, offset);
    all.push(...r.items);
    if (r.items.length < 500 || all.length >= r.total) break;
  }
  return all;
}

// 代理清單與缺口清單點進那個頁籤才抓（2026-09-30 大量資料測試：3 萬個代理＋5 萬筆缺口，
// 以前一打開頁面就全抓，光載入就 17 秒、主執行緒卡住將近 3 秒，連只想看整合設定的人也要等）
const loaded = ref({ agents: false, missing: false });
// 代理總數只取一筆就拿得到：頁籤上的台數照樣一打開就看得到，完整清單點進去才抓
const agentsTotal = ref<number | null>(null);
async function loadTab(which: "agents" | "missing") {
  if (which === "agents") agents.value = await fetchAllAgents();
  else await miss.load();
  loaded.value[which] = true;
}
async function refresh() {
  loading.value = true;
  try {
    const [i, a] = await Promise.all([listWazuh(50, 0), listWazuhAgents(undefined, undefined, 1, 0)]);
    insts.value = i.items;
    agentsTotal.value = a.total;
    for (const which of ["agents", "missing"] as const) {
      if (tab.value === which || loaded.value[which]) await loadTab(which);
    }
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}
watch(tab, async (v) => {
  if ((v === "agents" || v === "missing") && !loaded.value[v]) {
    loading.value = true;
    try { await loadTab(v); } catch (e) { msg.error(apiErrMsg(e)); } finally { loading.value = false; }
  }
});
async function submit() {
  try {
    if (editing.value) {
      const payload: any = {
        name: newInst.value.name, api_url: newInst.value.api_url,
        api_user: newInst.value.api_user, verify_tls: newInst.value.verify_tls,
        scope_subnet_ids: newInst.value.scope_subnet_ids,
      };
      if (newInst.value.api_password) payload.api_password = newInst.value.api_password;
      await updateWazuh(editing.value.id, payload);
    } else {
      await createWazuh({
        name: newInst.value.name, api_url: newInst.value.api_url,
        api_user: newInst.value.api_user, api_password: newInst.value.api_password,
        verify_tls: newInst.value.verify_tls,
        scope_subnet_ids: newInst.value.scope_subnet_ids,
      });
    }
    showInst.value = false;
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
function fmtSyncSummary(r: any): string {
  if (!r || typeof r !== "object") return t("common.ok");
  const parts: string[] = [];
  if (typeof r.inserted === "number") parts.push(t("common.added_n", { n: r.inserted }));
  if (typeof r.updated === "number") parts.push(t("common.updated_n", { n: r.updated }));
  if (typeof r.agents === "number") parts.push(`agent ${r.agents}`);
  return parts.length ? parts.join("、") : t("common.ok");
}
async function test(id: string) {
  try { await testWazuh(id); msg.success(t("common.ok")); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function sync(id: string) {
  try { const r = await syncWazuh(id); msg.success(fmtSyncSummary(r)); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function del(id: string) {
  try { await deleteWazuh(id); await refresh(); } catch { msg.error(t("errors.server")); }
}

function iconAction(icon: any, label: string, onClick: () => void, type?: any) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}
const allInstCols = computed<DataTableColumns<WazuhInstance>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 160, ellipsis: { tooltip: true } },
  { title: "API URL", key: "api_url", minWidth: 200, ellipsis: { tooltip: true } },
  { title: t("wazuh_admin.col_user"), key: "api_user", width: 140, ellipsis: { tooltip: true } },
  {
    title: t("wazuh_admin.col_last_sync"), key: "last_sync_at", width: 170,
    render: (r) => fmtDateTime(r.last_sync_at),
  },
  { title: t("wazuh_admin.col_last_error"), key: "last_error", minWidth: 160, ellipsis: { tooltip: true }, render: (r) => r.last_error ?? "—" },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 176,
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openEdit(r)),
      iconAction(TestIcon, t("common.test"), () => test(r.id)),
      iconAction(SyncIcon, t("common.pull"), () => sync(r.id), "primary"),
      h(NPopconfirm, { onPositiveClick: () => del(r.id) },
        { trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
          default: () => t("common.confirm_delete") }),
    ]),
  },
]));
const allAgentCols = computed<DataTableColumns<WazuhAgent>>(() => autoSort([
  { title: t("cols.agent_id"), key: "agent_id", width: 100 },
  {
    title: t("common.name"), key: "name", minWidth: 160, ellipsis: { tooltip: true },
    render: (r) => r.ip
      ? h(NButton, { text: true, type: "primary", onClick: () => gotoAgentAddress(r) }, () => r.name ?? "—")
      : (r.name ?? "—"),
  },
  {
    title: "IP", key: "ip", width: 150,
    render: (r) => r.ip
      ? h(NButton, { text: true, type: "primary", onClick: () => gotoAgentAddress(r) }, () => r.ip)
      : "—",
  },
  {
    title: t("common.status"), key: "status", width: 120,
    render: (r) => h(NTag, {
      size: "small",
      type: r.status === "active" ? "success" : r.status === "disconnected" ? "error" : "default",
    }, () => wazuhStatusLabel(t, r.status)),
  },
  { title: t("wazuh_admin.col_os"), key: "os_platform", width: 140, ellipsis: { tooltip: true }, render: (r) => r.os_name || r.os_platform || "—" },
  { title: t("wazuh_admin.col_version"), key: "agent_version", width: 120, render: (r) => r.agent_version ?? "—" },
  {
    title: t("wazuh_admin.col_last_alive"), key: "last_keep_alive", width: 170,
    render: (r) => fmtDateTime(r.last_keep_alive),
  },
]));
function gotoAddress(r: MissingAgent) {
  if (!r.ip) return;
  void router.push({ name: "addresses", query: { q: r.ip } });
}
function gotoAgentAddress(r: WazuhAgent) {
  if (!r.ip) return;
  void router.push({ name: "addresses", query: { q: r.ip } });
}
const allMissCols = computed<DataTableColumns<MissingAgent>>(() => autoSort([
  livenessColumn(t("common.status"), t),
  {
    title: "IP", key: "ip", width: 150,
    render: (r) => r.ip
      ? h(NButton, { text: true, type: "primary", onClick: () => gotoAddress(r) }, () => r.ip)
      : "—",
  },
  { title: t("cols.hostname"), key: "hostname", minWidth: 180, ellipsis: { tooltip: true }, render: (r) => r.hostname ?? "—" },
  deviceKindColumn(t, te) as any,
  withExportValue({ title: t("cols.subnet"), key: "subnet", width: 170, render: (r: any) => r.subnet_cidr ?? "—" },
    (r) => r.subnet_cidr),
  withExportValue({ title: t("cols.section"), key: "section", width: 150, ellipsis: { tooltip: true }, render: (r: any) => r.section_name ?? "—" },
    (r) => r.section_name),
  withExportValue({ title: t("cols.unit"), key: "customer", width: 150, ellipsis: { tooltip: true }, render: (r: any) => r.customer_name ?? "—" },
    (r) => r.customer_name),
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 72, titleAlign: "center", align: "center",
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false, justify: "center" }, () => [
      h(NTooltip, null, {
        trigger: () => h(NButton, {
          size: "small", quaternary: true, disabled: !r.ip,
          onClick: (e: MouseEvent) => { e.stopPropagation(); gotoAddress(r); },
        }, { icon: () => h(NIcon, null, () => h(EyeIcon)) }),
        default: () => t("wazuh_admin.view_ip"),
      }),
    ]),
  },
]));

const instCols = computed<DataTableColumns<WazuhInstance>>(() =>
  wzInst.orderColumns(allInstCols.value.filter((c: any) => wzInst.visibleKeys.value.includes(c.key))));
const agentCols = computed<DataTableColumns<WazuhAgent>>(() =>
  wzAg.orderColumns(allAgentCols.value.filter((c: any) => wzAg.visibleKeys.value.includes(c.key))));
const missCols = computed<DataTableColumns<MissingAgent>>(() =>
  miss.remoteSort(wzMiss.orderColumns(allMissCols.value.filter((c: any) => wzMiss.visibleKeys.value.includes(c.key)))));

onMounted(() => { void refresh(); void loadSubnetOptions(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><WazuhIcon /></n-icon>
        <span>{{ t("wazuh_admin.title") }}</span>
      </n-space>
    </template>
    <n-tabs v-model:value="tab" type="line">
      <n-tab-pane name="instances">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><WazuhIcon /></n-icon>{{ t('wazuh_admin.title') }}</span>
        </template>
        <n-space style="margin-bottom: 12px">
          <n-button @click="refresh" :loading="loading">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>
            {{ t("common.refresh") }}
          </n-button>
          <n-button type="primary" @click="openCreate">
            <template #icon><n-icon><PlusIcon /></n-icon></template>
            {{ t("wazuh_admin.create_instance") }}
          </n-button>
          <ColumnPicker :all="wzInstPicker" :visible="wzInst.visibleKeys.value"
                        @update:visible="wzInst.setVisible" @reset="wzInst.reset"
                        :order="wzInst.order.value" @update:order="wzInst.setOrder" />
          <ExportButton :columns="instCols" :rows="insts" filename="wazuh-instances" :title="t('wazuh_admin.title')" />
        </n-space>
        <n-data-table :columns="instCols" :data="insts" :loading="loading" :bordered="false" :scroll-x="1006" />
      </n-tab-pane>
      <n-tab-pane name="agents">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><DevicesIcon /></n-icon>{{ `${t('wazuh_admin.agents_count')} (${loaded.agents ? agents.length : (agentsTotal ?? 0)})` }}</span>
        </template>
        <n-space style="margin-bottom: 8px" align="center">
          <n-input v-model:value="agentFilterQ" :placeholder="t('common.filter')" clearable style="width: 160px" />
          <ColumnPicker :all="wzAgPicker" :visible="wzAg.visibleKeys.value"
                        @update:visible="wzAg.setVisible" @reset="wzAg.reset"
                        :order="wzAg.order.value" @update:order="wzAg.setOrder" />
          <ExportButton :columns="agentCols" :rows="agents" filename="wazuh-agents" :title="t('wazuh_admin.agents_count')" />
        </n-space>
        <n-data-table :columns="agentCols" :data="agentsFiltered" :loading="loading" :bordered="false" :scroll-x="960" :pagination="pg" />
      </n-tab-pane>
      <n-tab-pane name="missing">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><MissingIcon /></n-icon>{{ loaded.missing ? `${t('wazuh_admin.missing_agents')} (${miss.totalAll.value})` : t('wazuh_admin.missing_agents') }}</span>
        </template>
        <n-alert v-if="miss.totalAll.value" type="warning" style="margin-bottom: 12px">
          <template #icon><n-icon><MissingIcon /></n-icon></template>
          {{ miss.active.value ? `${miss.total.value} / ${miss.totalAll.value}` : miss.totalAll.value }} {{ t("wazuh_admin.missing_agents") }}
          <span v-if="missingScoped" style="opacity: .75">{{ t("wazuh_admin.missing_scoped") }}</span>
        </n-alert>
        <n-space style="margin-bottom: 8px" align="center">
          <n-input v-model:value="miss.q.value" :placeholder="t('common.filter')" clearable style="width: 160px"
                   data-testid="missing-filter" />
          <ScopeFilterBar v-model:section="miss.section.value" v-model:subnet="miss.subnet.value"
                          v-model:customer="miss.customer.value" :section-opts="miss.facets.value.sections"
                          :subnet-opts="miss.facets.value.subnets" :customer-opts="miss.facets.value.customers"
                          v-model:status="miss.status.value" :status-opts="miss.facets.value.statuses" />
          <ColumnPicker :all="wzMissPicker" :visible="wzMiss.visibleKeys.value"
                        @update:visible="wzMiss.setVisible" @reset="wzMiss.reset"
                        :order="wzMiss.order.value" @update:order="wzMiss.setOrder" />
          <ExportButton :columns="missCols" :rows="miss.rows.value" :fetch-all="miss.fetchAll"
                        filename="wazuh-missing-agents" :title="t('wazuh_admin.missing_agents')" />
        </n-space>
        <n-data-table :columns="missCols" :data="miss.rows.value" :loading="loading || miss.loading.value"
                      :bordered="false" :scroll-x="960" remote :pagination="miss.pagination"
                      @update:sorter="miss.onSorter" />
      </n-tab-pane>
    </n-tabs>

    <n-modal v-model:show="showInst" preset="card"
             :title="editing ? t('common.edit') : t('wazuh_admin.create_instance')"
             style="width: 460px">
      <n-form>
        <n-form-item :label="t('common.name')"><n-input v-model:value="newInst.name" /></n-form-item>
        <n-form-item label="API URL">
          <n-input v-model:value="newInst.api_url"
                   placeholder="https://wazuh.example.com:55000" />
        </n-form-item>
        <n-form-item label="API user">
          <n-input v-model:value="newInst.api_user" :placeholder="t('wazuh_admin.api_user_ph')" />
        </n-form-item>
        <n-form-item :label="`API password${editing ? ' (' + t('users.password_blank_unchanged') + ')' : ''}`">
          <n-input v-model:value="newInst.api_password" type="password" show-password-on="click" />
        </n-form-item>
        <n-form-item label="Verify TLS"><n-switch v-model:value="newInst.verify_tls" /></n-form-item>
        <n-form-item :label="t('wazuh_admin.scope_subnets')">
          <div style="width: 100%">
            <n-select v-model:value="newInst.scope_subnet_ids" :options="subnetOptions"
                      multiple filterable clearable :placeholder="t('wazuh_admin.scope_all')" />
            <ScopeOverlapWarning :scope-empty="!newInst.scope_subnet_ids?.length" />
          </div>
        </n-form-item>
        <div style="margin: -8px 0 4px">
          <span style="font-size: 11px; opacity: .7">{{ t("wazuh_admin.scope_hint") }}</span>
        </div>
      </n-form>
      <n-space justify="end">
        <n-button @click="showInst = false"><template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}</n-button>
        <n-button type="primary" @click="submit"><template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}</n-button>
      </n-space>
    </n-modal>
  </n-card>
</template>
