<script setup lang="ts">
/**
 * Check Point（使用者 2026-10-08：比照 Palo Alto／FortiGate 支援 Check Point，R81.20）。
 * 第一階段：透過管理伺服器的 Management API 唯讀拉閘道清單、網路物件、存取規則與 NAT。
 * 跟 PA／FG 不同的地方：設定在管理伺服器（SMS／Multi-Domain），一台管很多閘道 → 整合單位是管理伺服器。
 * 還沒在實機驗收（VM 準備中）→ 標 Beta。版面照 Wazuh 頁：頁籤＋操作欄編輯／測試／立即同步／刪除。
 */
import { computed, h, onMounted, ref, watch } from "vue";
import { fmtDateTime } from "@/utils/datetime";
import { useI18n } from "vue-i18n";
import { trackTask } from "@/composables/useTaskTracker";
import ScopeOverlapWarning from "@/components/ScopeOverlapWarning.vue";
import FocusRowBanner from "@/components/FocusRowBanner.vue";
import CheckPointGaiaGateways from "@/components/CheckPointGaiaGateways.vue";
import { useFocusRow } from "@/composables/useFocusRow";
import { useRoute } from "vue-router";
import {
  NCard, NDataTable, NSpace, NButton, NTag, NIcon, NTooltip, NAlert, NTabs, NTabPane, NModal, NForm,
  NFormItem, NInput, NInputNumber, NSwitch, NSelect, NCheckbox, NPopconfirm, NRadioGroup, NRadio, NDynamicTags,
  useMessage, type DataTableColumns, type PaginationProps,
} from "naive-ui";
import { listSubnets } from "@/api/subnets";
import {
  listCheckPoint, createCheckPoint, updateCheckPoint, deleteCheckPoint, testCheckPoint, syncCheckPoint,
  listCheckPointRules, listCheckPointObjects, listCheckPointGateways,
  type CheckPointServer, type CheckPointWrite, type CheckPointRule, type CheckPointObject, type CheckPointGateway,
} from "@/api/checkpoint";
import {
  FirewallIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SyncIcon, TestIcon, SaveIcon, CancelIcon, ListIcon,
  DevicesIcon, SearchIcon,
} from "@/icons";
import { autoSort } from "@/composables/useTableSort";
import ColumnPicker from "@/components/ColumnPicker.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { apiErrMsg } from "@/api/client";

const { t } = useI18n();
const msg = useMessage();
const route = useRoute();

type Tab = "servers" | "rules" | "objects" | "gateways";
// IP 詳細資料點規則／物件進來：?tab=rules|objects&fw=<管理伺服器>&focus=<規則 id｜物件名稱>
const qTab = route.query.tab;
const tab = ref<Tab>(qTab === "rules" || qTab === "objects" || qTab === "gateways" ? qTab : "servers");

const COLS = ["name", "api_url", "enabled", "sync_flags", "summary", "last_sync_at", "last_error", "actions"];
const prefs = useColumnPrefs("checkpoint", COLS, COLS);
const picker = computed(() => [
  { key: "name", label: t("cols.name") },
  { key: "api_url", label: t("checkpoint.api_url") },
  { key: "enabled", label: t("cols.status") },
  { key: "sync_flags", label: t("cols.sync_items") },
  { key: "summary", label: t("checkpoint.last_seen") },
  { key: "last_sync_at", label: t("cols.last_sync") },
  { key: "last_error", label: t("cols.last_error") },
  { key: "actions", label: t("cols.actions") },
]);

const rows = ref<CheckPointServer[]>([]);
const loading = ref(false);
const show = ref(false);
const editing = ref<CheckPointServer | null>(null);

function blankForm() {
  return {
    name: "", api_url: "", verify_tls: true, auth_mode: "api_key" as "api_key" | "password", username: "",
    secret: "", domains: [] as string[], packages: [] as string[], enabled: true, sync_objects: true,
    sync_policies: true, sync_nat: true, sync_interval_seconds: 900, description: "",
    scope_subnet_ids: [] as string[],
  };
}
const form = ref(blankForm());

const subnetOptions = ref<{ label: string; value: string }[]>([]);
async function loadSubnetOptions() {
  try {
    const r = await listSubnets({ page: 1, pageSize: 500 });
    subnetOptions.value = r.items.map((s) => ({
      label: s.description ? `${s.cidr} — ${s.description}` : s.cidr, value: s.id }));
  } catch { /* silent */ }
}

async function refresh() {
  loading.value = true;
  try {
    rows.value = (await listCheckPoint()).items;
    if (!viewServer.value && rows.value.length) {
      const fw = route.query.fw;
      viewServer.value = typeof fw === "string" && rows.value.some((r) => r.id === fw) ? fw : rows.value[0].id;
    }
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}

/** 從管理伺服器的設定視窗跳到它的閘道頁籤（DHCP／ARP 在那裡設定） */
function goGateways() {
  if (editing.value) viewServer.value = editing.value.id;
  show.value = false;
  tab.value = "gateways";
}

function openCreate() {
  editing.value = null;
  form.value = blankForm();
  show.value = true;
}

function openEdit(r: CheckPointServer) {
  editing.value = r;
  form.value = {
    name: r.name, api_url: r.api_url, verify_tls: r.verify_tls, auth_mode: r.auth_mode,
    username: r.username ?? "", secret: "", domains: [...(r.domains ?? [])], packages: [...(r.packages ?? [])],
    enabled: r.enabled, sync_objects: r.sync_objects, sync_policies: r.sync_policies, sync_nat: r.sync_nat,
    sync_interval_seconds: r.sync_interval_seconds, description: r.description ?? "",
    scope_subnet_ids: r.scope_subnet_ids ?? [],
  };
  show.value = true;
}

async function submit() {
  const f = form.value;
  if (!editing.value && !f.secret.trim()) { msg.warning(t("checkpoint.secret_required")); return; }
  if (f.auth_mode === "password" && !f.username.trim()) { msg.warning(t("checkpoint.username_required")); return; }
  const payload: CheckPointWrite = {
    name: f.name.trim(), api_url: f.api_url.trim(), verify_tls: f.verify_tls, auth_mode: f.auth_mode,
    username: f.auth_mode === "password" ? f.username.trim() : null,
    domains: f.domains, packages: f.packages, enabled: f.enabled, sync_objects: f.sync_objects,
    sync_policies: f.sync_policies, sync_nat: f.sync_nat, sync_interval_seconds: f.sync_interval_seconds,
    description: f.description || undefined, scope_subnet_ids: f.scope_subnet_ids,
  };
  if (f.secret.trim()) payload.secret = f.secret.trim();
  try {
    if (editing.value) await updateCheckPoint(editing.value.id, payload);
    else await createCheckPoint(payload);
    show.value = false;
    msg.success(t("common.ok"));
    await refresh();
  } catch (e) { msg.error(apiErrMsg(e)); }
}

async function test(id: string) {
  try {
    const r = await testCheckPoint(id);
    const lines = r.domains.map((d) => t("checkpoint.test_line", {
      domain: d.domain || t("checkpoint.default_domain"), version: d.version || "?",
      gateways: d.gateways ?? "?", hosts: d.hosts ?? "?", networks: d.networks ?? "?", packages: d.packages ?? "?",
    }));
    msg.success(() => h("div", { style: "white-space: pre-line" }, lines.join("\n")), { duration: 8000 });
    const errs = r.domains.flatMap((d) => Object.entries(d.errors ?? {}).map(([k, v]) => `${d.domain || "—"} ${k}: ${v}`));
    if (errs.length) msg.warning(t("checkpoint.test_partial", { detail: errs.join("；") }), { duration: 12_000, closable: true });
  } catch (e) {
    msg.error(apiErrMsg(e), { duration: 10_000, closable: true });
  }
}

async function sync(id: string) {
  try {
    const r = await syncCheckPoint(id);
    trackTask(r.task_id, { onDone: () => void refresh() });
  } catch (e) { msg.error(apiErrMsg(e)); }
}

async function del(id: string) {
  try { await deleteCheckPoint(id); if (viewServer.value === id) viewServer.value = null; await refresh(); }
  catch (e) { msg.error(apiErrMsg(e)); }
}

function iconAction(icon: any, label: string, onClick: () => void, type?: any, testid?: string) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type, "aria-label": label, "data-testid": testid,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}

function summaryText(r: CheckPointServer): string {
  const s = r.last_summary;
  if (!s) return "—";
  const parts: string[] = [];
  if (s.api_version) parts.push(`API ${s.api_version}`);
  if (s.gateways != null) parts.push(t("checkpoint.n_gateways", { n: s.gateways }));
  if (s.rules != null) parts.push(t("checkpoint.n_rules", { n: s.rules }));
  if (s.objects != null) parts.push(t("checkpoint.n_objects", { n: s.objects }));
  if (s.nat != null) parts.push(t("checkpoint.n_nat", { n: s.nat }));
  if (s.truncated?.length) parts.push(t("checkpoint.truncated"));
  return parts.join(" · ") || "—";
}

const allCols = computed<DataTableColumns<CheckPointServer>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 150, ellipsis: { tooltip: true } },
  { title: t("checkpoint.api_url"), key: "api_url", minWidth: 220, ellipsis: { tooltip: true } },
  {
    title: t("common.status"), key: "enabled", width: 100,
    render: (r) => h(NTag, { type: r.enabled ? "success" : "default", size: "small" },
      () => r.enabled ? t("common.enabled") : t("common.disabled")),
  },
  {
    title: t("common.sync"), key: "sync_flags", width: 200,
    render: (r) => h(NSpace, { size: 4 }, () => [
      r.sync_objects ? h(NTag, { size: "tiny", type: "info", bordered: false }, () => t("checkpoint.objects")) : null,
      r.sync_policies ? h(NTag, { size: "tiny", type: "info", bordered: false }, () => t("checkpoint.policies")) : null,
      r.sync_nat ? h(NTag, { size: "tiny", type: "info", bordered: false }, () => "NAT") : null,
    ].filter(Boolean)),
  },
  { title: t("checkpoint.last_seen"), key: "summary", minWidth: 240, ellipsis: { tooltip: true },
    render: (r) => summaryText(r) },
  { title: t("cols.last_sync"), key: "last_sync_at", width: 170, render: (r) => fmtDateTime(r.last_sync_at) },
  {
    title: t("cols.last_error"), key: "last_error", minWidth: 160,
    ellipsis: { tooltip: true }, render: (r) => r.last_error ?? "—",
  },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 176, fixed: "right",
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openEdit(r), undefined, "cp-edit"),
      iconAction(TestIcon, t("common.test"), () => test(r.id), undefined, "cp-test"),
      iconAction(SyncIcon, t("common.pull"), () => sync(r.id), "primary", "cp-sync"),
      h(NPopconfirm, { onPositiveClick: () => del(r.id) }, {
        trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]));
const cols = computed<DataTableColumns<CheckPointServer>>(() =>
  prefs.orderColumns(allCols.value.filter((c: any) => prefs.visibleKeys.value.includes(c.key))),
);

// ── 規則／物件／閘道頁籤：上一次同步存下來的鏡像（伺服器端分頁＋搜尋，大型政策也撐得住）──
const viewServer = ref<string | null>(null);
const serverOptions = computed(() => rows.value.map((r) => ({ label: r.name, value: r.id })));
const q = ref("");
const listLoading = ref(false);
const ruleRows = ref<CheckPointRule[]>([]);
const objRows = ref<CheckPointObject[]>([]);
const gwRows = ref<CheckPointGateway[]>([]);
const pag = ref({ page: 1, pageSize: 100, itemCount: 0 });
const pagination = computed<PaginationProps>(() => ({
  page: pag.value.page, pageSize: pag.value.pageSize, itemCount: pag.value.itemCount,
  showSizePicker: true, pageSizes: [50, 100, 200, 500],
  prefix: ({ itemCount }) => t("common.total_n", { n: itemCount ?? 0 }),
}));

async function loadList() {
  const id = viewServer.value;
  if (!id || tab.value === "servers") return;
  listLoading.value = true;
  try {
    if (tab.value === "gateways") {
      gwRows.value = await listCheckPointGateways(id);
      return;
    }
    const params = { q: q.value.trim() || undefined, page: pag.value.page, page_size: pag.value.pageSize };
    if (tab.value === "rules") {
      // 伺服器端分頁：要找的那一筆不一定在第一頁 → 直接請後端只回那一筆
      const r = await listCheckPointRules(id, { ...params, rule_id: ruleFocus.focus.value ?? undefined });
      ruleRows.value = r.items; pag.value.itemCount = r.total;
    } else {
      const r = await listCheckPointObjects(id, { ...params, name: objFocus.focus.value ?? undefined });
      objRows.value = r.items; pag.value.itemCount = r.total;
    }
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { listLoading.value = false; }
}
const ruleFocus = useFocusRow(ruleRows, (r, key) => r.id === key, "rules");
const objFocus = useFocusRow(objRows, (o, key) => o.name === key, "objects");
watch([tab, viewServer], () => { pag.value.page = 1; q.value = ""; void loadList(); });
// 「顯示全部」→ 重新載入完整清單
watch([ruleFocus.focus, objFocus.focus], () => { pag.value.page = 1; void loadList(); });
let qTimer: ReturnType<typeof setTimeout> | undefined;
watch(q, () => {
  clearTimeout(qTimer);
  qTimer = setTimeout(() => { pag.value.page = 1; void loadList(); }, 300);
});
function onPage(p: number) { pag.value.page = p; void loadList(); }
function onPageSize(s: number) { pag.value.pageSize = s; pag.value.page = 1; void loadList(); }

/** 「除了這些以外」要講出來：否定欄位讀成正向是最危險的誤讀 */
function sideText(v: string | null, negate: boolean) {
  const text = v || "—";
  if (!negate) return text;
  return h(NSpace, { size: 4, wrapItem: false, align: "center" }, () => [
    h(NTag, { size: "tiny", type: "warning", bordered: false }, () => t("checkpoint.negated")), text]);
}
function actionTag(a: string | null) {
  if (!a) return "—";
  const low = a.toLowerCase();
  const type = low === "accept" ? "success" : (low === "drop" || low === "reject") ? "error" : "default";
  return h(NTag, { size: "small", type, bordered: false }, () => a);
}
const ruleCols = computed<DataTableColumns<CheckPointRule>>(() => [
  { title: t("checkpoint.domain"), key: "domain", width: 110, ellipsis: { tooltip: true },
    render: (r) => r.domain || "—" },
  { title: t("checkpoint.package"), key: "package", width: 120, ellipsis: { tooltip: true } },
  { title: t("checkpoint.layer"), key: "layer", width: 160, ellipsis: { tooltip: true } },
  { title: "#", key: "rule_number", width: 60, align: "right" },
  { title: t("cols.name"), key: "name", minWidth: 150, ellipsis: { tooltip: true },
    render: (r) => h("span", { style: r.enabled ? "" : "opacity:.5" }, r.name || "—") },
  { title: t("checkpoint.section"), key: "section", width: 130, ellipsis: { tooltip: true },
    render: (r) => r.section || "—" },
  { title: t("checkpoint.source"), key: "source", minWidth: 170, ellipsis: { tooltip: true },
    render: (r) => sideText(r.source, r.source_negate) },
  { title: t("checkpoint.destination"), key: "destination", minWidth: 170, ellipsis: { tooltip: true },
    render: (r) => sideText(r.destination, r.destination_negate) },
  { title: t("checkpoint.service"), key: "service", minWidth: 130, ellipsis: { tooltip: true },
    render: (r) => r.service || "—" },
  { title: t("checkpoint.action"), key: "action", width: 110, render: (r) => actionTag(r.action) },
  {
    title: t("cols.status"), key: "enabled", width: 90,
    render: (r) => h(NTag, { size: "small", type: r.enabled ? "success" : "default", bordered: false },
      () => r.enabled ? t("common.enabled") : t("common.disabled")),
  },
  { title: t("checkpoint.install_on"), key: "install_on", width: 150, ellipsis: { tooltip: true },
    render: (r) => r.install_on || "—" },
  { title: t("checkpoint.hits"), key: "hits", width: 90, align: "right", render: (r) => r.hits ?? "—" },
  { title: t("checkpoint.last_hit"), key: "last_hit_at", width: 170, render: (r) => fmtDateTime(r.last_hit_at) },
  { title: t("common.description"), key: "comments", minWidth: 160, ellipsis: { tooltip: true },
    render: (r) => r.comments || "—" },
]);
const objCols = computed<DataTableColumns<CheckPointObject>>(() => [
  { title: t("checkpoint.domain"), key: "domain", width: 110, ellipsis: { tooltip: true },
    render: (r) => r.domain || "—" },
  { title: t("cols.name"), key: "name", minWidth: 170, ellipsis: { tooltip: true } },
  { title: t("cols.type"), key: "type", width: 170,
    render: (r) => h(NTag, { size: "small", bordered: false }, () => r.type) },
  { title: t("checkpoint.value"), key: "value", minWidth: 200, ellipsis: { tooltip: true },
    render: (r) => r.value || "—" },
  { title: t("checkpoint.members"), key: "members", minWidth: 220, ellipsis: { tooltip: true },
    render: (r) => r.members?.length ? r.members.join(", ") : "—" },
  { title: t("common.description"), key: "comments", minWidth: 160, ellipsis: { tooltip: true },
    render: (r) => r.comments || "—" },
]);
// 閘道頁籤（含第二階段的 Gaia API 連線）在 CheckPointGaiaGateways 元件裡
const gaiaRef = ref<InstanceType<typeof CheckPointGaiaGateways> | null>(null);

onMounted(() => { void refresh(); void loadSubnetOptions(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><FirewallIcon /></n-icon>
        <span>{{ t("checkpoint.title") }}</span>
        <n-tag size="small" type="warning" :bordered="false">Beta</n-tag>
      </n-space>
    </template>

    <n-tabs v-model:value="tab" type="line">
      <n-tab-pane name="servers">
        <template #tab>
          <span class="tab-label"><n-icon :size="16"><FirewallIcon /></n-icon>{{ t("checkpoint.tab_servers") }}</span>
        </template>
        <n-alert type="info" :bordered="false" style="margin-bottom: 12px">{{ t("checkpoint.intro") }}</n-alert>
        <n-space style="margin-bottom: 12px">
          <n-button @click="refresh" :loading="loading">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>
            {{ t("common.refresh") }}
          </n-button>
          <n-button type="primary" data-testid="cp-create" @click="openCreate">
            <template #icon><n-icon><PlusIcon /></n-icon></template>
            {{ t("common.create") }}
          </n-button>
          <ColumnPicker :all="picker" :visible="prefs.visibleKeys.value" @update:visible="prefs.setVisible"
                        @reset="prefs.reset" :order="prefs.order.value" @update:order="prefs.setOrder" />
        </n-space>
        <n-data-table :columns="cols" :data="rows" :loading="loading" :bordered="false" :scroll-x="1340"
                      data-testid="cp-list" />
      </n-tab-pane>

      <n-tab-pane v-for="p in (['rules', 'objects', 'gateways'] as const)" :key="p" :name="p">
        <template #tab>
          <span class="tab-label">
            <n-icon :size="16"><ListIcon v-if="p === 'rules'" /><SearchIcon v-else-if="p === 'objects'" /><DevicesIcon v-else /></n-icon>
            {{ t(`checkpoint.tab_${p}`) }}
          </span>
        </template>
        <n-space style="margin-bottom: 12px" align="center">
          <n-select v-if="serverOptions.length > 1" v-model:value="viewServer" :options="serverOptions"
                    style="width: 240px" />
          <n-input v-if="p !== 'gateways'" v-model:value="q" clearable :placeholder="t('checkpoint.search')"
                   style="width: 280px" :data-testid="`cp-search-${p}`" />
          <n-button @click="loadList" :loading="listLoading" :disabled="!viewServer">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>
            {{ t("common.refresh") }}
          </n-button>
          <n-button v-if="p === 'gateways'" type="primary" :disabled="!viewServer" data-testid="cpg-manual"
                    @click="gaiaRef?.openManual()">
            <template #icon><n-icon><PlusIcon /></n-icon></template>
            {{ t("checkpoint.gaia_add_manual") }}
          </n-button>
        </n-space>
        <div class="form-hint" style="margin-bottom: 8px">{{ t(`checkpoint.${p}_hint`) }}</div>
        <FocusRowBanner v-if="p === 'rules'" :ctl="ruleFocus" :loading="listLoading" />
        <FocusRowBanner v-else-if="p === 'objects'" :ctl="objFocus" :loading="listLoading" />
        <n-data-table v-if="p === 'rules'" remote :columns="ruleCols" :data="ruleRows" :loading="listLoading"
                      :bordered="false" :scroll-x="2100" :pagination="pagination" data-testid="cp-rules"
                      @update:page="onPage" @update:page-size="onPageSize" />
        <n-data-table v-else-if="p === 'objects'" remote :columns="objCols" :data="objRows" :loading="listLoading"
                      :bordered="false" :scroll-x="1100" :pagination="pagination" data-testid="cp-objects"
                      @update:page="onPage" @update:page-size="onPageSize" />
        <CheckPointGaiaGateways v-else ref="gaiaRef" :server-id="viewServer" :gateways="gwRows"
                                :loading="listLoading" :subnet-options="subnetOptions" />
      </n-tab-pane>
    </n-tabs>

    <n-modal v-model:show="show" preset="card"
             :title="editing ? t('common.edit') : `${t('common.create')} — ${t('checkpoint.title')}`"
             style="width: min(640px, 100%)">
      <n-alert type="default" :bordered="false" :show-icon="true" style="margin-bottom: 12px">
        {{ t("checkpoint.setup_hint") }}
      </n-alert>
      <n-form>
        <n-form-item :label="t('common.name')"><n-input v-model:value="form.name" data-testid="cp-name" /></n-form-item>
        <n-form-item :label="t('checkpoint.api_url')">
          <div style="width: 100%">
            <n-input v-model:value="form.api_url" placeholder="https://mgmt.example.test" data-testid="cp-url" />
            <div class="form-hint">{{ t("checkpoint.api_url_hint") }}</div>
          </div>
        </n-form-item>
        <n-form-item :label="t('checkpoint.auth_mode')">
          <n-radio-group v-model:value="form.auth_mode">
            <n-radio value="api_key">{{ t("checkpoint.auth_api_key") }}</n-radio>
            <n-radio value="password">{{ t("checkpoint.auth_password") }}</n-radio>
          </n-radio-group>
        </n-form-item>
        <n-form-item v-if="form.auth_mode === 'password'" :label="t('checkpoint.username')">
          <n-input v-model:value="form.username" autocomplete="off" data-testid="cp-username" />
        </n-form-item>
        <n-form-item :label="editing?.has_secret ? t('checkpoint.secret_keep')
          : (form.auth_mode === 'api_key' ? t('checkpoint.api_key') : t('checkpoint.password'))">
          <n-input v-model:value="form.secret" type="password" show-password-on="click" autocomplete="new-password"
                   data-testid="cp-secret" />
        </n-form-item>
        <n-form-item :label="t('firewall_admin.verify_tls')">
          <n-switch v-model:value="form.verify_tls" />
        </n-form-item>
        <n-form-item :label="t('checkpoint.domains')">
          <div style="width: 100%">
            <n-dynamic-tags v-model:value="form.domains" />
            <div class="form-hint">{{ t("checkpoint.domains_hint") }}</div>
          </div>
        </n-form-item>
        <n-form-item :label="t('checkpoint.packages')">
          <div style="width: 100%">
            <n-dynamic-tags v-model:value="form.packages" />
            <div class="form-hint">{{ t("checkpoint.packages_hint") }}</div>
          </div>
        </n-form-item>
        <n-form-item :label="t('checkpoint.pull_what')">
          <n-space :size="20">
            <n-checkbox v-model:checked="form.sync_objects">{{ t("checkpoint.objects") }}</n-checkbox>
            <n-checkbox v-model:checked="form.sync_policies">{{ t("checkpoint.policies") }}</n-checkbox>
            <n-checkbox v-model:checked="form.sync_nat">NAT</n-checkbox>
          </n-space>
        </n-form-item>
        <!-- 使用者 2026-10-09 在這裡找 DHCP／ARP 找不到：那是閘道的 Gaia 連線，在另一個頁籤 -->
        <n-alert type="info" :bordered="false" :show-icon="true" style="margin-bottom: 18px" data-testid="cp-gaia-where">
          {{ t("checkpoint.gaia_where_hint") }}
          <template v-if="editing">
            <div style="margin-top: 8px">
              <n-button size="small" type="primary" secondary data-testid="cp-go-gateways" @click="goGateways">
                <template #icon><n-icon><DevicesIcon /></n-icon></template>{{ t("checkpoint.go_gateways") }}
              </n-button>
            </div>
          </template>
          <div v-else style="margin-top: 6px">{{ t("checkpoint.gaia_where_after_save") }}</div>
        </n-alert>
        <n-form-item :label="t('adguard_admin.sync_interval')">
          <n-input-number v-model:value="form.sync_interval_seconds" :min="300" :max="86400" />
        </n-form-item>
        <n-form-item :label="t('common.enable')">
          <n-switch v-model:value="form.enabled" />
        </n-form-item>
        <n-form-item :label="t('adguard_admin.scope_subnets')">
          <div style="width: 100%">
            <n-select v-model:value="form.scope_subnet_ids" :options="subnetOptions"
                      multiple filterable clearable :placeholder="t('adguard_admin.scope_all')" />
            <ScopeOverlapWarning :scope-empty="!form.scope_subnet_ids?.length" />
          </div>
        </n-form-item>
        <n-form-item :label="t('common.description')">
          <n-input v-model:value="form.description" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="show = false">
          <template #icon><n-icon><CancelIcon /></n-icon></template>
          {{ t("common.cancel") }}
        </n-button>
        <n-button type="primary" data-testid="cp-save" @click="submit">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </n-space>
    </n-modal>
  </n-card>
</template>

<style scoped>
.form-hint { font-size: 11.5px; opacity: .7; margin-top: 4px; line-height: 1.5; }
.tab-label { display: inline-flex; align-items: center; gap: 6px; }
</style>
