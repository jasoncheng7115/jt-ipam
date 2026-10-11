<script setup lang="ts">
/**
 * OCS Inventory NG 整合 —— 端點資產盤點來源（唯讀拉取，不會更動 OCS）。
 *
 * 這一頁有兩件事跟其他整合不同，都直接呈現在畫面上：
 * 1. **帳密選用** —— OCS 的 REST 預設無驗證。連線診斷會主動測「沒帶憑證連不連得上」，
 *    連得上就跳一條紅色警告：這套 OCS 對任何能到達它的人都是開放的。
 * 2. **軟體區段預設關** —— 每台會從 ~2 KB 膨脹到 ~80 KB；先看規模再決定要不要開。
 */
import { computed, h, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { trackTask } from "@/composables/useTaskTracker";
import {
  NCard, NDataTable, NSpace, NButton, NTag, NIcon, NAlert, NModal, NForm,
  NFormItem, NInput, NInputNumber, NSwitch, NSelect, NPopconfirm, NTooltip, NTabs, NTabPane,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { useRouter } from "vue-router";
import {
  listOcs, createOcs, updateOcs, deleteOcs, testOcs, syncOcs,
  listOcsAgents, listOcsMissingAgentsPage,
  type OcsServer, type OcsDiagnosis, type OcsAgent, type OcsMissingAgent,
} from "@/api/ocs";
import {
  PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SyncIcon, TestIcon,
  SaveIcon, CancelIcon, DevicesIcon, MissingIcon, ListIcon, EyeIcon,
} from "@/icons";
import { autoSort } from "@/composables/useTableSort";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ScopeFilterBar from "@/components/ScopeFilterBar.vue";
import { useRemoteMissing } from "@/composables/useRemoteMissing";
import { livenessColumn } from "@/utils/livenessColumn";
import { withExportValue } from "@/utils/tableExport";
import ExportButton from "@/components/ExportButton.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { useTableQuickFilter } from "@/composables/useTableQuickFilter";
import { useTablePagination } from "@/composables/useTablePagination";
import { fmtDateTime } from "@/utils/datetime";
import { shortOcsAgent } from "@/utils/ocsAgent";
import { deviceKindColumn } from "@/utils/deviceKindCell";
import { apiErrMsg } from "@/api/client";
import { listSubnets } from "@/api/subnets";
import ScopeOverlapWarning from "@/components/ScopeOverlapWarning.vue";

const { t, te } = useI18n();
const msg = useMessage();

const rows = ref<OcsServer[]>([]);
const loading = ref(false);
const show = ref(false);
const editing = ref<OcsServer | null>(null);
const busy = ref<string>("");

const diag = ref<OcsDiagnosis | null>(null);
const diagOpen = ref(false);

function blankForm() {
  return {
    name: "", base_url: "", api_username: "", api_password: "",
    enabled: true, verify_tls: true,
    sync_networks: true, sync_bios: true, sync_software: false,
    sync_interval_seconds: 3600, stale_after_days: 30,
    scope_subnet_ids: [] as string[],
    clear_credentials: false,
  };
}
const form = ref(blankForm());

// 限定子網路範圍：選單比照 Wazuh 整合頁
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
  const on = rows.value.filter((r) => r.enabled);
  return on.length > 0 && on.every((r) => (r.scope_subnet_ids ?? []).length > 0);
});

// 頁籤比照 Wazuh 整合頁：整合主機／代理數／未裝 Agent 的 IP
const router = useRouter();
const tab = ref<"instances" | "agents" | "missing">("instances");
const agents = ref<OcsAgent[]>([]);
// 缺口清單由後端分頁、篩選、排序（大站台 5 萬筆時整份抓回來要幾十 MB）
const miss = useRemoteMissing(listOcsMissingAgentsPage);
const { query: agentFilterQ, filtered: agentsFiltered } = useTableQuickFilter(agents);
const pg = useTablePagination();

// 缺口清單點進那個頁籤才抓（2026-09-30 大量資料測試：大站台一打開頁面就全抓，要等十幾秒、
// 回應四十幾 MB）。代理清單一台電腦一筆、量小，照舊一起載入（頁籤上的台數要看得到）
const loaded = ref({ missing: false });
async function loadMissing() {
  await miss.load();
  loaded.value.missing = true;
}
async function load() {
  loading.value = true;
  try {
    const [i, a] = await Promise.all([listOcs(), listOcsAgents()]);
    rows.value = i.items;
    agents.value = a.items;
    if (tab.value === "missing" || loaded.value.missing) await loadMissing();
  } catch (e) {
    msg.error(apiErrMsg(e));
  } finally {
    loading.value = false;
  }
}
watch(tab, async (v) => {
  if (v === "missing" && !loaded.value.missing) {
    loading.value = true;
    try { await loadMissing(); } catch (e) { msg.error(apiErrMsg(e)); } finally { loading.value = false; }
  }
});
onMounted(() => { void load(); void loadSubnetOptions(); });

function openCreate() {
  editing.value = null;
  form.value = blankForm();
  show.value = true;
}
function openEdit(row: OcsServer) {
  editing.value = row;
  form.value = {
    ...blankForm(),
    name: row.name, base_url: row.base_url ?? "",
    api_username: row.api_username ?? "", api_password: "",
    enabled: row.enabled, verify_tls: row.verify_tls,
    sync_networks: row.sync_networks, sync_bios: row.sync_bios,
    sync_software: row.sync_software,
    sync_interval_seconds: row.sync_interval_seconds,
    stale_after_days: row.stale_after_days,
    scope_subnet_ids: row.scope_subnet_ids ?? [],
  };
  show.value = true;
}

async function save() {
  const f = form.value;
  if (!f.name.trim() || !f.base_url.trim()) {
    msg.warning(t("ocs.name_url_required"));
    return;
  }
  try {
    const payload: Record<string, unknown> = {
      name: f.name.trim(), base_url: f.base_url.trim(),
      enabled: f.enabled, verify_tls: f.verify_tls,
      api_username: f.api_username.trim() || null,
      sync_networks: f.sync_networks, sync_bios: f.sync_bios,
      sync_software: f.sync_software,
      sync_interval_seconds: f.sync_interval_seconds,
      stale_after_days: f.stale_after_days,
      scope_subnet_ids: f.scope_subnet_ids,
    };
    if (f.api_password) payload.api_password = f.api_password;
    if (editing.value) {
      await updateOcs(editing.value.id, payload);
    } else {
      await createOcs(payload as never);
    }
    show.value = false;
    msg.success(t("common.saved"));
    await load();
  } catch (e) {
    msg.error(apiErrMsg(e));
  }
}

async function remove(row: OcsServer) {
  try {
    await deleteOcs(row.id);
    msg.success(t("common.deleted"));
    await load();
  } catch (e) {
    msg.error(apiErrMsg(e));
  }
}

async function test(row: OcsServer) {
  busy.value = row.id;
  try {
    diag.value = await testOcs(row.id);
    diagOpen.value = true;
  } catch (e) {
    msg.error(apiErrMsg(e));
  } finally {
    busy.value = "";
  }
}

async function sync(row: OcsServer) {
  busy.value = row.id;
  try {
    const r = await syncOcs(row.id);
    trackTask(r.task_id, { onDone: () => void load() });
  } catch (e) {
    msg.error(apiErrMsg(e));
  } finally {
    busy.value = "";
  }
}

const columns = computed<DataTableColumns<OcsServer>>(() => [
  { title: t("cols.name"), key: "name" },
  { title: "URL", key: "base_url" },
  {
    title: t("cols.status"), key: "enabled",
    render: (r) => h(NTag, { type: r.enabled ? "success" : "default", size: "small" },
      { default: () => (r.enabled ? t("common.enabled") : t("common.disabled")) }),
  },
  {
    title: t("ocs.version"), key: "detected_version",
    render: (r) => r.detected_version ?? "—",
  },
  {
    title: t("cols.last_sync"), key: "last_sync_at",
    render: (r) => (r.last_sync_at ? fmtDateTime(r.last_sync_at) : "—"),
  },
  {
    title: t("cols.last_error"), key: "last_error",
    render: (r) => (r.last_error
      ? h(NTag, { type: "error", size: "small" }, { default: () => r.last_error })
      : "—"),
  },
  {
    title: t("cols.actions"), key: "actions", className: "col-actions", width: 176,
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openEdit(r)),
      iconAction(TestIcon, t("ocs.test"), () => test(r)),
      iconAction(SyncIcon, t("ocs.sync_now"), () => sync(r), "primary"),
      h(NPopconfirm, { onPositiveClick: () => remove(r) }, {
        trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
        default: () => t("ocs.confirm_delete", { name: r.name }),
      }),
    ]),
  },
]);

// 操作欄用 icon 按鈕（tooltip 帶文字），與其他整合頁一致
function iconAction(icon: any, label: string, onClick: () => void, type?: any) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}

const ocsAg = useColumnPrefs("ocs_agents",
  ["ocs_id", "name", "ips", "os", "agent_version", "tag", "last_inventory"],
  ["ocs_id", "name", "ips", "os", "agent_version", "tag", "last_inventory"]);
const ocsAgPicker = computed(() => [
  { key: "ocs_id", label: t("ocs.col_ocs_id") }, { key: "name", label: t("cols.name") },
  { key: "ips", label: t("ocs.col_ips") }, { key: "os", label: t("ocs.col_os") },
  { key: "agent_version", label: t("ocs.col_agent") }, { key: "tag", label: t("ocs.col_tag") },
  { key: "last_inventory", label: t("ocs.col_last_inventory") },
]);
// 設備類型預設顯示：一眼看出缺的是電腦還是印表機／交換器（那些本來就不會裝 OCS agent）
const ocsMiss = useColumnPrefs("ocs_missing",
  ["status", "ip", "hostname", "device_kind", "subnet", "section", "customer", "actions"],
  ["status", "ip", "hostname", "device_kind", "subnet", "section", "customer", "actions"]);
const ocsMissPicker = computed(() => [
  { key: "status", label: t("cols.status") },
  { key: "ip", label: "IP" }, { key: "hostname", label: t("cols.hostname") },
  { key: "device_kind", label: t("cols.device_kind") },
  { key: "subnet", label: t("cols.subnet") }, { key: "section", label: t("cols.section") },
  { key: "customer", label: t("cols.unit") }, { key: "actions", label: t("cols.actions") },
]);

function gotoIp(ip: string | null | undefined) {
  if (ip) void router.push({ name: "addresses", query: { q: ip } });
}
function ipLink(ip: string) {
  return h(NButton, { text: true, type: "primary", onClick: () => gotoIp(ip) }, () => ip);
}

const allAgentCols = computed<DataTableColumns<OcsAgent>>(() => autoSort([
  { title: t("ocs.col_ocs_id"), key: "ocs_id", width: 90, render: (r) => r.ocs_id ?? "—" },
  {
    title: t("cols.name"), key: "name", minWidth: 160, ellipsis: { tooltip: true },
    render: (r) => r.ips.length
      ? h(NButton, { text: true, type: "primary", onClick: () => gotoIp(r.ips[0]) }, () => r.name ?? "—")
      : (r.name ?? "—"),
  },
  {
    // 一台電腦的多個 IP 都列出來（以前清單只能一個 IP 一行，數量看起來多好幾倍）
    title: t("ocs.col_ips"), key: "ips", minWidth: 160,
    render: (r) => h(NSpace, { size: [8, 0] }, () => r.ips.map(ipLink)),
  },
  { title: t("ocs.col_os"), key: "os", minWidth: 160, ellipsis: { tooltip: true }, render: (r) => r.os ?? "—" },
  {
    // 原始字串很長、版本號在最後，一截斷就看不到版本 → 顯示精簡版，完整字串放 title
    title: t("ocs.col_agent"), key: "agent_version", width: 140,
    render: (r) => (r.agent_version
      ? h("span", { title: r.agent_version }, shortOcsAgent(r.agent_version))
      : "—"),
  },
  { title: t("ocs.col_tag"), key: "tag", width: 120, render: (r) => r.tag ?? "—" },
  {
    title: t("ocs.col_last_inventory"), key: "last_inventory", width: 170,
    render: (r) => fmtDateTime(r.last_inventory),
  },
]));
const allMissCols = computed<DataTableColumns<OcsMissingAgent>>(() => autoSort([
  livenessColumn(t("common.status"), t),
  { title: "IP", key: "ip", width: 150, render: (r) => (r.ip ? ipLink(r.ip) : "—") },
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
          onClick: (e: MouseEvent) => { e.stopPropagation(); gotoIp(r.ip); },
        }, { icon: () => h(NIcon, null, () => h(EyeIcon)) }),
        default: () => t("ocs.view_ip"),
      }),
    ]),
  },
]));
const agentCols = computed<DataTableColumns<OcsAgent>>(() =>
  ocsAg.orderColumns(allAgentCols.value.filter((c: any) => ocsAg.visibleKeys.value.includes(c.key))));
const missCols = computed<DataTableColumns<OcsMissingAgent>>(() =>
  miss.remoteSort(ocsMiss.orderColumns(allMissCols.value.filter((c: any) => ocsMiss.visibleKeys.value.includes(c.key)))));

</script>

<template>
  <NCard>
    <template #header>
      <NSpace align="center" :wrap-item="false">
        <NIcon :size="22"><DevicesIcon /></NIcon>
        <span>{{ t("ocs.title") }}</span>
      </NSpace>
    </template>
    <!-- 頁籤與工具列比照 Wazuh 整合頁 -->
    <NTabs v-model:value="tab" type="line">
      <NTabPane name="instances">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><NIcon :size="16"><DevicesIcon /></NIcon>{{ t("ocs.title") }}</span>
        </template>
        <NSpace style="margin-bottom: 12px">
          <NButton @click="load" :loading="loading">
            <template #icon><NIcon><RefreshIcon /></NIcon></template>
            {{ t("common.refresh") }}
          </NButton>
          <NButton type="primary" @click="openCreate">
            <template #icon><NIcon><PlusIcon /></NIcon></template>
            {{ t("ocs.add") }}
          </NButton>
          <ExportButton :columns="columns" :rows="rows" filename="ocs-servers" :title="t('ocs.title')" />
        </NSpace>
        <NAlert type="info" :show-icon="true" style="margin-bottom: 12px">
          {{ t("ocs.intro") }}
        </NAlert>
        <NDataTable :columns="columns" :data="rows" :loading="loading" :bordered="false"
                    :row-key="(r: OcsServer) => r.id" :scroll-x="820" />
      </NTabPane>
      <NTabPane name="agents">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><NIcon :size="16"><ListIcon /></NIcon>{{ `${t("ocs.agents_count")} (${agents.length})` }}</span>
        </template>
        <NSpace style="margin-bottom: 8px" align="center">
          <NInput v-model:value="agentFilterQ" :placeholder="t('common.filter')" clearable style="width: 160px" />
          <ColumnPicker :all="ocsAgPicker" :visible="ocsAg.visibleKeys.value"
                        @update:visible="ocsAg.setVisible" @reset="ocsAg.reset"
                        :order="ocsAg.order.value" @update:order="ocsAg.setOrder" />
          <ExportButton :columns="agentCols" :rows="agents" filename="ocs-agents" :title="t('ocs.agents_count')" />
          <span style="font-size: 12px; opacity: .65">{{ t("ocs.agents_hint") }}</span>
        </NSpace>
        <NDataTable :columns="agentCols" :data="agentsFiltered" :loading="loading" :bordered="false"
                    :scroll-x="1100" :pagination="pg" />
      </NTabPane>
      <NTabPane name="missing">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><NIcon :size="16"><MissingIcon /></NIcon>{{ loaded.missing ? `${t("ocs.missing_agents")} (${miss.totalAll.value})` : t("ocs.missing_agents") }}</span>
        </template>
        <NAlert v-if="miss.totalAll.value" type="warning" style="margin-bottom: 12px">
          <template #icon><NIcon><MissingIcon /></NIcon></template>
          {{ miss.active.value ? `${miss.total.value} / ${miss.totalAll.value}` : miss.totalAll.value }} {{ t("ocs.missing_agents") }}
          <span v-if="missingScoped" style="opacity: .75">{{ t("ocs.missing_scoped") }}</span>
        </NAlert>
        <NSpace style="margin-bottom: 8px" align="center">
          <NInput v-model:value="miss.q.value" :placeholder="t('common.filter')" clearable style="width: 160px"
                  data-testid="missing-filter" />
          <ScopeFilterBar v-model:section="miss.section.value" v-model:subnet="miss.subnet.value"
                          v-model:customer="miss.customer.value" :section-opts="miss.facets.value.sections"
                          :subnet-opts="miss.facets.value.subnets" :customer-opts="miss.facets.value.customers"
                          v-model:status="miss.status.value" :status-opts="miss.facets.value.statuses" />
          <ColumnPicker :all="ocsMissPicker" :visible="ocsMiss.visibleKeys.value"
                        @update:visible="ocsMiss.setVisible" @reset="ocsMiss.reset"
                        :order="ocsMiss.order.value" @update:order="ocsMiss.setOrder" />
          <ExportButton :columns="missCols" :rows="miss.rows.value" :fetch-all="miss.fetchAll"
                        filename="ocs-missing-agents" :title="t('ocs.missing_agents')" />
        </NSpace>
        <NDataTable :columns="missCols" :data="miss.rows.value" :loading="loading || miss.loading.value"
                    :bordered="false" :scroll-x="960" remote :pagination="miss.pagination"
                    @update:sorter="miss.onSorter" />
      </NTabPane>
    </NTabs>
  </NCard>

  <!-- 新增／編輯 -->
  <NModal v-model:show="show" preset="card" style="max-width: 620px"
          :title="editing ? t('ocs.edit_title') : t('ocs.add')">
    <NForm label-placement="left" :label-width="160" size="small">
      <NFormItem :label="t('cols.name')">
        <NInput v-model:value="form.name" :placeholder="t('ocs.name_ph')" />
      </NFormItem>
      <NFormItem label="URL">
        <NInput v-model:value="form.base_url" placeholder="https://ocs.example.com" />
      </NFormItem>
      <NFormItem :label="t('ocs.username')">
        <NInput v-model:value="form.api_username" :placeholder="t('ocs.auth_optional')" />
      </NFormItem>
      <NFormItem :label="t('ocs.password')">
        <NInput v-model:value="form.api_password" type="password" show-password-on="click"
                :placeholder="editing?.has_password ? t('ocs.password_kept') : t('ocs.auth_optional')" />
      </NFormItem>
      <NFormItem :label="t('ocs.verify_tls')">
        <NSwitch v-model:value="form.verify_tls" />
      </NFormItem>
      <NFormItem :label="t('common.enabled')">
        <NSwitch v-model:value="form.enabled" />
      </NFormItem>
      <NFormItem :label="t('ocs.sync_networks')">
        <NSwitch v-model:value="form.sync_networks" />
      </NFormItem>
      <NFormItem :label="t('ocs.sync_bios')">
        <NSwitch v-model:value="form.sync_bios" />
      </NFormItem>
      <!-- 「同步軟體清單」先不露出：開關與欄位都在（送出時照舊帶值，既有設定不會被清掉），
           但抓軟體清單本身還沒實作，開了也不會發生事情 —— 擺在畫面上只會誤導。
           實作完把 v-if 拿掉即可。 -->
      <NFormItem v-if="false" :label="t('ocs.sync_software')">
        <NSpace vertical :size="2">
          <NSwitch v-model:value="form.sync_software" />
          <span style="font-size: 12px; opacity: .7">{{ t("ocs.sync_software_hint") }}</span>
        </NSpace>
      </NFormItem>
      <NFormItem :label="t('ocs.scope_subnets')">
        <div style="width: 100%">
          <NSelect v-model:value="form.scope_subnet_ids" :options="subnetOptions"
                   multiple filterable clearable :placeholder="t('ocs.scope_all')" />
          <ScopeOverlapWarning :scope-empty="!form.scope_subnet_ids?.length" />
          <div style="font-size: 11px; opacity: .7; margin-top: 2px">{{ t("ocs.scope_hint") }}</div>
        </div>
      </NFormItem>
      <NFormItem :label="t('ocs.interval')">
        <NInputNumber v-model:value="form.sync_interval_seconds" :min="300" :max="86400"
                      style="width: 160px" />
      </NFormItem>
      <NFormItem :label="t('ocs.stale_days')">
        <NInputNumber v-model:value="form.stale_after_days" :min="1" :max="3650"
                      style="width: 160px" />
      </NFormItem>
    </NForm>
    <template #footer>
      <NSpace justify="end">
        <NButton @click="show = false">
          <template #icon><NIcon><CancelIcon /></NIcon></template>
          {{ t("common.cancel") }}
        </NButton>
        <NButton type="primary" @click="save">
          <template #icon><NIcon><SaveIcon /></NIcon></template>
          {{ t("common.save") }}
        </NButton>
      </NSpace>
    </template>
  </NModal>

  <!-- 連線診斷 -->
  <NModal v-model:show="diagOpen" preset="card" style="max-width: 560px"
          :title="t('ocs.diag_title')">
    <div v-if="diag">
      <NAlert v-if="diag.unauthenticated_access" type="warning" :show-icon="true"
              style="margin-bottom: 12px">
        {{ t("ocs.warn_unauthenticated") }}
      </NAlert>
      <NSpace vertical :size="6">
        <div>{{ t("ocs.diag_reachable") }}：
          <NTag :type="diag.reachable ? 'success' : 'error'" size="small">
            {{ diag.reachable ? t("common.yes") : t("common.no") }}
          </NTag>
        </div>
        <div>{{ t("ocs.diag_count") }}：{{ diag.computer_count ?? "—" }}</div>
        <div>{{ t("ocs.diag_incremental") }}：
          <NTag :type="diag.incremental ? 'success' : 'default'" size="small">
            {{ diag.incremental ? t("ocs.incremental_yes") : t("ocs.incremental_no") }}
          </NTag>
        </div>
        <div>{{ t("ocs.diag_auth") }}：
          <NTag :type="diag.auth_required ? 'success' : 'warning'" size="small">
            {{ diag.auth_required ? t("ocs.auth_on") : t("ocs.auth_off") }}
          </NTag>
        </div>
      </NSpace>
    </div>
  </NModal>
</template>
