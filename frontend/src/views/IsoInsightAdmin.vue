<script setup lang="ts">
/**
 * ISOinsight 整合：登入 ISOinsight 的 HTTP(S) 介面，定期讀 DHCP 租約。
 * 版面照 Wazuh 整合頁：頁籤（來源／租約／同步記錄）、每頁籤工具列、操作欄（編輯、測試、預覽、立即同步、刪除）。
 *
 * 建議流程（畫面上也寫著）：新增 → 測試 → 預覽（尚未套用）→ 手動同步一次 → 開排程。
 * 主機名稱等來源文字一律當純文字顯示（Vue 會轉義，不用 v-html）。
 */
import { computed, h, onMounted, reactive, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { trackTask } from "@/composables/useTaskTracker";
import {
  NAlert, NButton, NCard, NCheckbox, NCollapse, NCollapseItem, NDataTable, NForm, NFormItem, NIcon, NInput,
  NInputNumber, NModal, NPopconfirm, NRadioButton, NRadioGroup, NSelect, NSpace, NSwitch, NTabPane, NTabs, NTag,
  NTooltip, useMessage, type DataTableColumns,
} from "naive-ui";
import { fmtDateTime } from "@/utils/datetime";
import { srvText } from "@/utils/wsError";
import { apiErrMsg } from "@/api/client";
import { listSubnets } from "@/api/subnets";
import { listCustomers } from "@/api/customers";
import {
  createSource, deleteSource, listLeases, listRuns, listSources, previewSource, syncSource, testSource,
  updateSource, type IsoInsightSource, type IsoInsightWrite, type IsoLease, type IsoRun, type IsoTestResult,
  type LeaseState,
} from "@/api/isoinsight";
import {
  CancelIcon, DeleteIcon, EditIcon, EyeIcon, IsoInsightIcon, PlusIcon, RefreshIcon, SaveIcon, SyncIcon, TasksIcon,
  TestIcon, WarnIcon,
} from "@/icons";
import { autoSort } from "@/composables/useTableSort";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";

const { t } = useI18n();
const msg = useMessage();
const tab = ref<"sources" | "leases" | "runs">("sources");

// ── 共用 ──────────────────────────────────────────────────────────────────

function codeText(code: string | null | undefined, params?: Record<string, unknown>, fallback?: string | null) {
  if (!code) return fallback ?? "";
  return srvText({ code: `isoinsight_${code.toLowerCase()}`, params: params ?? {}, message: fallback ?? code });
}
function resultTag(r: string | null | undefined) {
  if (!r) return h("span", { style: "opacity:.6" }, "—");
  const type = r === "success" ? "success" : r === "partial" ? "warning" : r === "failed" ? "error" : "default";
  return h(NTag, { size: "small", type, bordered: false }, () => t(`isoinsight.result.${r}`));
}
function stateTag(s: LeaseState) {
  const type = s === "active" ? "success" : s === "expired" ? "default" : "warning";
  return h(NTag, { size: "tiny", type, bordered: false }, () => t(`isoinsight.state.${s}`));
}
function qualityTags(q: string[] | null | undefined) {
  if (!q?.length) return "—";
  return h(NSpace, { size: 2, wrap: true }, () => q.map((tag) =>
    h(NTag, { size: "tiny", bordered: false, type: QUIET_TAGS.has(tag) ? "default" : "warning" },
      () => t(`isoinsight.q.${tag}`))));
}
const QUIET_TAGS = new Set(["name_empty", "duplicate", "ipv6_unverified"]);
function timeCell(iso: string | null, raw: string | null) {
  if (iso) return fmtDateTime(iso);
  if (raw) return h(NTooltip, null, {
    trigger: () => h("span", { class: "iso-raw-time" }, raw),
    default: () => t("isoinsight.raw_time_hint"),
  });
  return "—";
}
function iconAction(icon: any, label: string, onClick: () => void, opts: { type?: any; disabled?: boolean; testid?: string } = {}) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, {
      size: "small", quaternary: true, type: opts.type, disabled: opts.disabled, "aria-label": label,
      "data-testid": opts.testid, onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); },
    }, { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}

// ── 選項：客戶與子網路（逐頁抓完，不會只看到第一頁）────────────────────────

interface SubnetOpt { label: string; value: string; customer_id: string | null }
const allSubnets = ref<SubnetOpt[]>([]);
const customerOptions = ref<{ label: string; value: string }[]>([]);
async function loadOptions() {
  try {
    const out: SubnetOpt[] = [];
    for (let page = 1; page <= 200; page++) {
      const r = await listSubnets({ page, pageSize: 500 });
      out.push(...r.items.map((s) => ({
        label: s.description ? `${s.cidr}（${s.description}）` : s.cidr, value: s.id, customer_id: s.customer_id ?? null })));
      if (r.items.length < 500 || out.length >= r.total) break;
    }
    allSubnets.value = out;
    const c = await listCustomers({ page: 1, pageSize: 500 });
    customerOptions.value = c.items.map((x: any) => ({ label: x.name, value: x.id }));
  } catch { /* 選項載不到時表單照樣可以開，送出時後端會檢查 */ }
}
const subnetLabel = (id: string) => allSubnets.value.find((s) => s.value === id)?.label ?? id.slice(0, 8);

// ── 來源 ──────────────────────────────────────────────────────────────────

const rows = ref<IsoInsightSource[]>([]);
const loading = ref(false);
async function refresh() {
  loading.value = true;
  try { rows.value = (await listSources()).items; }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}

const COLS = ["name", "base_url", "run_on", "login", "enabled", "schedule", "last_success", "last_result", "counts",
  "last_error", "actions"];
const prefs = useColumnPrefs("isoinsight_sources", COLS, COLS);
const picker = computed(() => [
  { key: "name", label: t("cols.name") }, { key: "base_url", label: t("isoinsight.base_url") },
  { key: "run_on", label: t("isoinsight.run_on") }, { key: "login", label: t("isoinsight.login_method") },
  { key: "enabled", label: t("cols.status") }, { key: "schedule", label: t("isoinsight.schedule") },
  { key: "last_success", label: t("isoinsight.last_success") }, { key: "last_result", label: t("isoinsight.last_result") },
  { key: "counts", label: t("isoinsight.counts") }, { key: "last_error", label: t("cols.last_error") },
  { key: "actions", label: t("cols.actions") },
]);

function scheduleCell(r: IsoInsightSource) {
  if (!r.schedule_enabled) return h(NTag, { size: "small", bordered: false }, () => t("common.disabled"));
  const tags = [h(NTag, { size: "small", type: "info", bordered: false },
    () => t("isoinsight.every_n_min", { n: Math.round(r.sync_interval_seconds / 60) }))];
  if (!r.preview_ok_at) tags.push(h(NTag, { size: "small", type: "warning", bordered: false }, () => t("isoinsight.needs_preview")));
  if (r.auth_hold) tags.push(h(NTooltip, null, {
    trigger: () => h(NTag, { size: "small", type: "error", bordered: false }, () => t("isoinsight.auth_hold")),
    default: () => t("isoinsight.auth_hold_hint"),
  }));
  return h(NSpace, { size: 4 }, () => tags);
}
function countsCell(r: IsoInsightSource) {
  const s = r.last_summary;
  if (!s) return "—";
  const imported = (s.created ?? 0) + (s.updated ?? 0) + (s.unchanged ?? 0);
  const skipped = (s.invalid ?? 0) + (s.unmatched ?? 0) + (s.conflicts ?? 0) + (s.unknown_time ?? 0);
  return t("isoinsight.counts_cell", { fetched: s.fetched ?? 0, imported, skipped });
}

const allCols = computed<DataTableColumns<IsoInsightSource>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 140, ellipsis: { tooltip: true } },
  { title: t("isoinsight.base_url"), key: "base_url", minWidth: 190, ellipsis: { tooltip: true },
    render: (r) => h(NSpace, { size: 4, wrap: false, align: "center" }, () => [
      r.base_url,
      ...(r.verify_tls ? [] : [h(NTooltip, null, {
        trigger: () => h(NTag, { size: "tiny", type: "warning", bordered: false }, () => t("isoinsight.tls_off")),
        default: () => t("isoinsight.tls_off_hint"),
      })]),
    ]) },
  { title: t("isoinsight.run_on"), key: "run_on", width: 135, render: () => t("isoinsight.run_on_server") },
  { title: t("isoinsight.login_method"), key: "login", width: 150,
    render: (r) => h(NSpace, { size: 4 }, () => [
      h(NTag, { size: "tiny", bordered: false, type: r.login_method === "GET" ? "warning" : "info" },
        () => r.login_method === "GET" ? "GET" : `POST ${r.post_format === "json" ? "JSON" : "form"}`),
      h(NTag, { size: "tiny", bordered: false }, () => t(`isoinsight.auth_${r.auth_mode}`)),
    ]) },
  { title: t("common.status"), key: "enabled", width: 100,
    render: (r) => h(NTag, { type: r.enabled ? "success" : "default", size: "small" },
      () => r.enabled ? t("common.enabled") : t("common.disabled")) },
  { title: t("isoinsight.schedule"), key: "schedule", width: 190, render: scheduleCell },
  { title: t("isoinsight.last_success"), key: "last_success", width: 165,
    render: (r) => fmtDateTime(r.last_full_success_at) },
  { title: t("isoinsight.last_result"), key: "last_result", width: 110,
    render: (r) => r.running ? h(NTag, { size: "small", type: "info", bordered: false }, () => t("isoinsight.running"))
      : resultTag(r.last_result) },
  { title: t("isoinsight.counts"), key: "counts", width: 190, render: countsCell },
  { title: t("cols.last_error"), key: "last_error", minWidth: 180, ellipsis: { tooltip: true },
    render: (r) => r.last_error_code ? codeText(r.last_error_code, {}, r.last_error) : "—" },
  { title: t("common.actions"), key: "actions", className: "col-actions", width: 236, fixed: "right",
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openEdit(r), { testid: "iso-edit" }),
      iconAction(TestIcon, t("common.test"), () => openProbe(r, "test"), { testid: "iso-test" }),
      iconAction(EyeIcon, t("isoinsight.preview"), () => openProbe(r, "preview"), { testid: "iso-preview" }),
      iconAction(SyncIcon, t("isoinsight.sync_now"), () => sync(r), { type: "primary", disabled: !r.enabled, testid: "iso-sync" }),
      iconAction(TasksIcon, t("isoinsight.runs"), () => showRuns(r)),
      h(NPopconfirm, { onPositiveClick: () => del(r) }, {
        trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, { type: "error" }),
        default: () => t("isoinsight.confirm_delete"),
      }),
    ]) },
]));
const cols = computed(() => prefs.orderColumns(allCols.value.filter((c: any) => prefs.visibleKeys.value.includes(c.key))));

async function sync(r: IsoInsightSource) {
  try {
    const job = await syncSource(r.id);
    trackTask(job.task_id, { onDone: () => void refresh() });
  } catch (e) { msg.error(apiErrMsg(e), { duration: 8000, closable: true }); }
}
async function del(r: IsoInsightSource) {
  try { await deleteSource(r.id); await refresh(); }
  catch (e) { msg.error(apiErrMsg(e)); }
}

// ── 新增／編輯（同一個表單）────────────────────────────────────────────────

const show = ref(false);
const editing = ref<IsoInsightSource | null>(null);
const changePassword = ref(false);
const tzConfirmed = ref(false);
function blank() {
  return {
    name: "", base_url: "", product_version: "", description: "", login_method: "POST" as "GET" | "POST",
    post_format: "form" as "form" | "json", username: "", password: "", username_param: "username",
    password_param: "password", login_path: "/api/logon", lease_path: "/isosvc?act=DhcpLease",
    auth_mode: "cookie" as "cookie" | "token", token_path: "", token_header: "Authorization", token_prefix: "Bearer",
    verify_tls: true, source_timezone: "Asia/Taipei", customer_id: null as string | null,
    scope_subnet_ids: [] as string[], create_ips: true, enabled: true, schedule_enabled: true,
    sync_interval_seconds: 300, connect_timeout_seconds: 5, request_timeout_seconds: 30, job_timeout_seconds: 120,
    max_response_mib: 20, max_rows: 100000,
  };
}
const form = ref(blank());
const subnetOptions = computed(() => allSubnets.value
  .filter((s) => (s.customer_id ?? null) === (form.value.customer_id ?? null))
  .map(({ label, value }) => ({ label, value })));
watch(() => form.value.customer_id, () => {
  // 換了租戶：不屬於它的子網路不能留著（後端也會擋）
  const ok = new Set(subnetOptions.value.map((o) => o.value));
  form.value.scope_subnet_ids = form.value.scope_subnet_ids.filter((id) => ok.has(id));
});

function openCreate() {
  editing.value = null;
  form.value = blank();
  changePassword.value = true;
  tzConfirmed.value = false;
  show.value = true;
}
function openEdit(r: IsoInsightSource) {
  editing.value = r;
  form.value = {
    ...blank(), name: r.name, base_url: r.base_url, product_version: r.product_version ?? "",
    description: r.description ?? "", login_method: r.login_method, post_format: r.post_format, username: r.username,
    username_param: r.username_param, password_param: r.password_param, login_path: r.login_path,
    lease_path: r.lease_path, auth_mode: r.auth_mode, token_path: r.token_path ?? "", token_header: r.token_header,
    token_prefix: r.token_prefix, verify_tls: r.verify_tls, source_timezone: r.source_timezone,
    customer_id: r.customer_id, scope_subnet_ids: [...r.scope_subnet_ids], create_ips: r.create_ips,
    enabled: r.enabled, schedule_enabled: r.schedule_enabled, sync_interval_seconds: r.sync_interval_seconds,
    connect_timeout_seconds: r.connect_timeout_seconds, request_timeout_seconds: r.request_timeout_seconds,
    job_timeout_seconds: r.job_timeout_seconds, max_response_mib: r.max_response_mib, max_rows: r.max_rows,
  };
  changePassword.value = false;
  tzConfirmed.value = false;
  show.value = true;
}
const tzChanged = computed(() => !editing.value || editing.value.source_timezone !== form.value.source_timezone);
const canSchedule = computed(() => !!editing.value?.preview_ok_at);
const saving = ref(false);
async function submit() {
  const f = form.value;
  const payload: IsoInsightWrite = {
    name: f.name.trim(), base_url: f.base_url.trim(), product_version: f.product_version || null,
    description: f.description || null, login_method: f.login_method, post_format: f.post_format,
    username: f.username, username_param: f.username_param.trim(), password_param: f.password_param.trim(),
    login_path: f.login_path.trim(), lease_path: f.lease_path.trim(), auth_mode: f.auth_mode,
    token_path: f.auth_mode === "token" ? f.token_path.trim() : (f.token_path.trim() || null),
    token_header: f.token_header.trim(), token_prefix: f.token_prefix.trim(), verify_tls: f.verify_tls,
    source_timezone: f.source_timezone.trim(), customer_id: f.customer_id, scope_subnet_ids: f.scope_subnet_ids,
    create_ips: f.create_ips, enabled: f.enabled, sync_interval_seconds: f.sync_interval_seconds,
    connect_timeout_seconds: f.connect_timeout_seconds, request_timeout_seconds: f.request_timeout_seconds,
    job_timeout_seconds: f.job_timeout_seconds, max_response_mib: f.max_response_mib, max_rows: f.max_rows,
  };
  if (tzChanged.value) payload.timezone_confirmed = tzConfirmed.value;
  if (changePassword.value && f.password) payload.password = f.password;
  // 排程預設開啟（使用者 2026-10-08）；還沒成功預覽前排程不會同步，新增時也照表單送
  payload.schedule_enabled = f.schedule_enabled;
  saving.value = true;
  try {
    if (editing.value) await updateSource(editing.value.id, payload);
    else await createSource(payload);
    show.value = false;
    msg.success(t("common.ok"));
    await refresh();
  } catch (e) { msg.error(apiErrMsg(e), { duration: 8000, closable: true }); }
  finally { saving.value = false; }
}
const methodOptions = computed(() => [{ label: "POST", value: "POST" }, { label: "GET", value: "GET" }]);

// ── 測試連線／預覽 ─────────────────────────────────────────────────────────

const probe = reactive({
  show: false, kind: "test" as "test" | "preview", source: null as IsoInsightSource | null, loading: false,
  anonymous: false, result: null as IsoTestResult | null,
});
function openProbe(r: IsoInsightSource, kind: "test" | "preview") {
  Object.assign(probe, { show: true, kind, source: r, loading: false, anonymous: false, result: null });
}
async function runProbe() {
  if (!probe.source) return;
  probe.loading = true;
  probe.result = null;
  try {
    probe.result = probe.kind === "test" ? await testSource(probe.source.id, probe.anonymous)
      : await previewSource(probe.source.id);
  } catch (e) { msg.error(apiErrMsg(e), { duration: 10_000, closable: true }); }
  finally { probe.loading = false; }
  void refresh();     // 列表上的「待重新預覽」「最近測試」跟著更新（不擋住按鈕）
}
const stageCols = computed<DataTableColumns<any>>(() => [
  { title: t("isoinsight.stage_col"), key: "stage", width: 120, render: (s) => t(`isoinsight.stage.${s.stage}`) },
  { title: t("isoinsight.method_col"), key: "method", width: 150,
    render: (s) => [s.method, s.post_format ? `(${s.post_format})` : ""].filter(Boolean).join(" ") || "—" },
  { title: t("isoinsight.path_col"), key: "path", minWidth: 180, ellipsis: { tooltip: true }, render: (s) => s.path ?? "—" },
  { title: "HTTP", key: "status", width: 70, render: (s) => s.status ?? "—" },
  { title: t("isoinsight.elapsed_col"), key: "elapsed_ms", width: 90, render: (s) => s.elapsed_ms != null ? `${s.elapsed_ms} ms` : "—" },
  { title: t("isoinsight.detail_col"), key: "detail", minWidth: 200, render: (s) => {
    if (s.error) return h("span", { class: "iso-err" }, codeText(s.error));
    if (s.stage === "login" && s.cookies) return s.cookies.length
      ? t("isoinsight.cookies_seen", { names: s.cookies.join(", ") }) : (s.token_found ? t("isoinsight.token_seen") : "—");
    if (s.stage === "validate") return t("isoinsight.rows_seen", { rows: s.rows ?? 0, valid: s.valid ?? 0 });
    if (s.stage === "fetch") return s.content_type ?? "—";
    return "—";
  } },
]);
const previewCols = computed<DataTableColumns<any>>(() => [
  { title: "IP", key: "ip", width: 140 },
  { title: "MAC", key: "mac", width: 150, render: (r) => r.mac ?? "—" },
  { title: t("isoinsight.name_col"), key: "name", minWidth: 120, ellipsis: { tooltip: true }, render: (r) => r.name ?? "—" },
  { title: t("isoinsight.start_col"), key: "start", width: 160, render: (r) => timeCell(r.start, r.start_raw) },
  { title: t("isoinsight.end_col"), key: "end", width: 160, render: (r) => timeCell(r.end, r.end_raw) },
  { title: t("isoinsight.state_col"), key: "state", width: 100, render: (r) => stateTag(r.state) },
  { title: t("isoinsight.outcome_col"), key: "bucket", width: 150,
    render: (r) => h(NSpace, { size: 2, vertical: true }, () => [
      t(`isoinsight.bucket.${r.bucket}`),
      ...(r.reason ? [h("span", { class: "iso-sub" }, t(`isoinsight.reason.${r.reason}`))] : []),
    ]) },
  { title: t("cols.subnet"), key: "subnet_cidr", width: 150, render: (r) => r.subnet_cidr ?? "—" },
  { title: t("isoinsight.quality_col"), key: "quality", minWidth: 160, render: (r) => qualityTags(r.quality) },
]);
const BUCKET_KEYS = ["created", "unchanged", "observed_only", "expired", "unknown_time", "unmatched", "conflicts"];
function previewBucketLabel(k: string) {
  return t(`isoinsight.bucket.${k === "created" ? "would_create" : k === "unchanged" ? "would_apply" : k}`);
}

// ── 同步記錄 ──────────────────────────────────────────────────────────────

const runsSource = ref<string | null>(null);
const runs = ref<IsoRun[]>([]);
const runsLoading = ref(false);
const runsPg = reactive({ page: 1, pageSize: 50, itemCount: 0, showSizePicker: true, pageSizes: [20, 50, 100, 200],
  prefix: ({ itemCount }: { itemCount?: number }) => t("common.total_rows", { n: itemCount ?? 0 }) });
async function loadRuns() {
  if (!runsSource.value) { runs.value = []; runsPg.itemCount = 0; return; }
  runsLoading.value = true;
  try {
    const r = await listRuns(runsSource.value, runsPg.page, runsPg.pageSize);
    runs.value = r.items;
    runsPg.itemCount = r.total;
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { runsLoading.value = false; }
}
function showRuns(r: IsoInsightSource) {
  runsSource.value = r.id;
  runsPg.page = 1;
  tab.value = "runs";
  void loadRuns();
}
const sourceOptions = computed(() => rows.value.map((r) => ({ label: r.name, value: r.id })));
const runCols = computed<DataTableColumns<IsoRun>>(() => [
  { title: t("isoinsight.started_col"), key: "started_at", width: 165, render: (r) => fmtDateTime(r.started_at) },
  { title: t("isoinsight.kind_col"), key: "kind", width: 90, render: (r) => t(`isoinsight.kind.${r.kind}`) },
  { title: t("isoinsight.trigger_col"), key: "trigger", width: 90, render: (r) => t(`isoinsight.trigger.${r.trigger}`) },
  { title: t("isoinsight.result_col"), key: "result", width: 100, render: (r) => resultTag(r.result) },
  { title: t("isoinsight.elapsed_col"), key: "duration_ms", width: 90,
    render: (r) => r.duration_ms != null ? `${(r.duration_ms / 1000).toFixed(1)} s` : "—" },
  { title: t("isoinsight.run_counts_col"), key: "counts", minWidth: 280,
    render: (r) => t("isoinsight.run_counts", { fetched: r.fetched, valid: r.valid, invalid: r.invalid,
      created: r.created, updated: r.updated, unchanged: r.unchanged, expired: r.expired, unknown: r.unknown_time,
      unmatched: r.unmatched, conflicts: r.conflicts }) },
  { title: t("cols.last_error"), key: "error_code", minWidth: 200, ellipsis: { tooltip: true },
    render: (r) => r.error_code ? `${codeText(r.error_code, {}, r.error_detail)}${r.http_status ? `（HTTP ${r.http_status}）` : ""}` : "—" },
]);

// ── 租約（伺服器分頁、篩選）────────────────────────────────────────────────

const leases = ref<IsoLease[]>([]);
const leasesLoading = ref(false);
const lf = reactive({ source_id: null as string | null, q: "", state: null as LeaseState | null,
  match_status: null as string | null });
const leasePg = reactive({ page: 1, pageSize: 50, itemCount: 0, showSizePicker: true, pageSizes: [20, 50, 100, 200, 500],
  prefix: ({ itemCount }: { itemCount?: number }) => t("common.total_rows", { n: itemCount ?? 0 }) });
async function loadLeases() {
  leasesLoading.value = true;
  try {
    const r = await listLeases({ ...lf, page: leasePg.page, page_size: leasePg.pageSize });
    leases.value = r.items;
    leasePg.itemCount = r.total;
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { leasesLoading.value = false; }
}
let lfTimer: ReturnType<typeof setTimeout> | undefined;
watch(() => [lf.source_id, lf.q, lf.state, lf.match_status], () => {
  clearTimeout(lfTimer);
  lfTimer = setTimeout(() => { leasePg.page = 1; void loadLeases(); }, 300);
});
const stateOptions = computed(() => (["active", "expired", "not_started", "invalid_period", "unknown"] as const)
  .map((s) => ({ label: t(`isoinsight.state.${s}`), value: s })));
const matchOptions = computed(() => (["matched", "ambiguous", "no_subnet"] as const)
  .map((s) => ({ label: t(`isoinsight.match.${s}`), value: s })));
const leaseCols = computed<DataTableColumns<IsoLease>>(() => [
  { title: "IP", key: "ip", width: 140 },
  { title: "MAC", key: "mac", width: 150, render: (r) => r.mac ?? "—" },
  { title: t("isoinsight.name_col"), key: "name", minWidth: 130, ellipsis: { tooltip: true }, render: (r) => r.name ?? "—" },
  { title: t("isoinsight.source_col"), key: "source_name", width: 130, ellipsis: { tooltip: true } },
  { title: t("cols.subnet"), key: "subnet_cidr", width: 150,
    render: (r) => r.subnet_cidr ?? h(NTag, { size: "tiny", type: "warning", bordered: false }, () => t(`isoinsight.match.${r.match_status}`)) },
  { title: t("isoinsight.start_col"), key: "start_at", width: 160, render: (r) => timeCell(r.start_at, r.start_raw) },
  { title: t("isoinsight.end_col"), key: "end_at", width: 160, render: (r) => timeCell(r.end_at, r.end_raw) },
  { title: t("isoinsight.state_col"), key: "state", width: 100, render: (r) => stateTag(r.state) },
  { title: t("isoinsight.observed_col"), key: "lease_observed_at", width: 160, render: (r) => fmtDateTime(r.lease_observed_at) },
  { title: t("isoinsight.quality_col"), key: "quality", minWidth: 160, render: (r) => qualityTags(r.quality) },
]);
const loaded = ref({ leases: false });
watch(tab, (v) => {
  if (v === "leases" && !loaded.value.leases) { loaded.value.leases = true; void loadLeases(); }
  if (v === "runs" && !runsSource.value && rows.value.length) { runsSource.value = rows.value[0].id; void loadRuns(); }
});

onMounted(() => { void refresh(); void loadOptions(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><IsoInsightIcon /></n-icon>
        <span>{{ t("isoinsight.title") }}</span>
        <n-tag size="small" type="warning" :bordered="false">Beta</n-tag>
      </n-space>
    </template>

    <n-tabs v-model:value="tab" type="line">
      <n-tab-pane name="sources">
        <template #tab>
          <span class="iso-tab"><n-icon :size="16"><IsoInsightIcon /></n-icon>{{ t("isoinsight.tab_sources") }}</span>
        </template>
        <n-alert type="info" :bordered="false" style="margin-bottom: 12px">
          <div>{{ t("isoinsight.intro") }}</div>
          <ol class="iso-steps">
            <li>{{ t("isoinsight.step_create") }}</li>
            <li>{{ t("isoinsight.step_test") }}</li>
            <li>{{ t("isoinsight.step_preview") }}</li>
            <li>{{ t("isoinsight.step_scope") }}</li>
            <li>{{ t("isoinsight.step_sync") }}</li>
          </ol>
          <div class="iso-note">{{ t("isoinsight.unverified_note") }}</div>
        </n-alert>
        <n-space style="margin-bottom: 12px">
          <n-button @click="refresh" :loading="loading">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>
            {{ t("common.refresh") }}
          </n-button>
          <n-button type="primary" data-testid="iso-create" @click="openCreate">
            <template #icon><n-icon><PlusIcon /></n-icon></template>
            {{ t("common.create") }}
          </n-button>
          <ColumnPicker :all="picker" :visible="prefs.visibleKeys.value" @update:visible="prefs.setVisible"
                        @reset="prefs.reset" :order="prefs.order.value" @update:order="prefs.setOrder" />
          <ExportButton :columns="cols" :rows="rows" filename="isoinsight-sources" :title="t('isoinsight.title')" />
        </n-space>
        <n-data-table :columns="cols" :data="rows" :loading="loading" :bordered="false" :scroll-x="1700"
                      data-testid="iso-sources" />
      </n-tab-pane>

      <n-tab-pane name="leases">
        <template #tab>
          <span class="iso-tab"><n-icon :size="16"><EyeIcon /></n-icon>{{ t("isoinsight.tab_leases") }}</span>
        </template>
        <n-alert type="default" :bordered="false" style="margin-bottom: 12px">{{ t("isoinsight.lease_note") }}</n-alert>
        <n-space style="margin-bottom: 8px" align="center">
          <n-select v-model:value="lf.source_id" :options="sourceOptions" clearable :placeholder="t('isoinsight.all_sources')"
                    style="width: 180px" />
          <n-input v-model:value="lf.q" :placeholder="t('isoinsight.lease_search')" clearable style="width: 220px" />
          <n-select v-model:value="lf.state" :options="stateOptions" clearable :placeholder="t('isoinsight.any_state')"
                    style="width: 150px" />
          <n-select v-model:value="lf.match_status" :options="matchOptions" clearable
                    :placeholder="t('isoinsight.any_match')" style="width: 170px" />
          <n-button @click="loadLeases" :loading="leasesLoading">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>
            {{ t("common.refresh") }}
          </n-button>
        </n-space>
        <n-data-table :columns="leaseCols" :data="leases" :loading="leasesLoading" :bordered="false" remote
                      :pagination="leasePg" :scroll-x="1500" data-testid="iso-leases"
                      @update:page="(p: number) => { leasePg.page = p; loadLeases(); }"
                      @update:page-size="(s: number) => { leasePg.pageSize = s; leasePg.page = 1; loadLeases(); }" />
      </n-tab-pane>

      <n-tab-pane name="runs">
        <template #tab>
          <span class="iso-tab"><n-icon :size="16"><TasksIcon /></n-icon>{{ t("isoinsight.tab_runs") }}</span>
        </template>
        <n-space style="margin-bottom: 8px" align="center">
          <n-select v-model:value="runsSource" :options="sourceOptions" style="width: 220px"
                    :placeholder="t('isoinsight.pick_source')" @update:value="() => { runsPg.page = 1; loadRuns(); }" />
          <n-button @click="loadRuns" :loading="runsLoading">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>
            {{ t("common.refresh") }}
          </n-button>
        </n-space>
        <n-data-table :columns="runCols" :data="runs" :loading="runsLoading" :bordered="false" remote
                      :pagination="runsPg" :scroll-x="1250" data-testid="iso-runs"
                      @update:page="(p: number) => { runsPg.page = p; loadRuns(); }"
                      @update:page-size="(s: number) => { runsPg.pageSize = s; runsPg.page = 1; loadRuns(); }" />
      </n-tab-pane>
    </n-tabs>

    <!-- 新增／編輯：同一個表單 -->
    <n-modal v-model:show="show" preset="card" :title="editing ? `${t('common.edit')}：${editing.name}` : `${t('common.create')}：${t('isoinsight.title')}`"
             style="width: min(680px, 100%)">
      <n-form label-placement="top">
        <n-form-item :label="t('common.name')"><n-input v-model:value="form.name" data-testid="iso-name" /></n-form-item>
        <n-form-item :label="t('isoinsight.base_url')">
          <div class="iso-full">
            <n-input v-model:value="form.base_url" placeholder="https://192.0.2.10" data-testid="iso-url" />
            <div class="form-hint">{{ t("isoinsight.base_url_hint") }}</div>
          </div>
        </n-form-item>
        <n-form-item :label="t('isoinsight.product_version')">
          <n-input v-model:value="form.product_version" :placeholder="t('isoinsight.product_version_ph')" />
        </n-form-item>

        <n-form-item :label="t('isoinsight.login_method')">
          <div class="iso-full">
            <n-space align="center">
              <n-radio-group v-model:value="form.login_method" data-testid="iso-method">
                <n-radio-button v-for="o in methodOptions" :key="o.value" :value="o.value" :label="o.label" />
              </n-radio-group>
              <n-radio-group v-if="form.login_method === 'POST'" v-model:value="form.post_format">
                <n-radio-button value="form" label="form" />
                <n-radio-button value="json" label="JSON" />
              </n-radio-group>
            </n-space>
            <div class="form-hint">{{ t("isoinsight.method_hint") }}</div>
            <n-alert v-if="form.login_method === 'GET'" type="warning" :bordered="false" class="iso-inline-alert">
              {{ t("isoinsight.get_warning") }}
            </n-alert>
          </div>
        </n-form-item>
        <n-form-item :label="t('isoinsight.username')">
          <n-input v-model:value="form.username" autocomplete="off" data-testid="iso-user" />
        </n-form-item>
        <n-form-item :label="t('isoinsight.password')">
          <div class="iso-full">
            <n-space v-if="editing" align="center" style="margin-bottom: 6px">
              <n-checkbox v-model:checked="changePassword" data-testid="iso-change-pw">{{ t("isoinsight.change_password") }}</n-checkbox>
            </n-space>
            <n-input v-model:value="form.password" type="password" show-password-on="click" autocomplete="new-password"
                     :disabled="!!editing && !changePassword"
                     :placeholder="editing && !changePassword ? t('isoinsight.password_kept') : ''" data-testid="iso-pw" />
          </div>
        </n-form-item>

        <n-form-item :label="t('isoinsight.scope_customer')">
          <div class="iso-full">
            <n-select v-model:value="form.customer_id" :options="customerOptions" clearable filterable
                      :placeholder="t('isoinsight.no_customer')" />
            <div class="form-hint">{{ t("isoinsight.scope_customer_hint") }}</div>
          </div>
        </n-form-item>
        <n-form-item :label="t('isoinsight.scope_subnets')">
          <div class="iso-full">
            <n-select v-model:value="form.scope_subnet_ids" :options="subnetOptions" multiple filterable
                      :placeholder="t('isoinsight.scope_subnets_ph')" data-testid="iso-subnets"
                      :render-tag="({ option, handleClose }: any) => h(NTag, { size: 'small', closable: true, onClose: handleClose }, () => subnetLabel(option.value))" />
            <div class="form-hint">{{ t("isoinsight.scope_subnets_hint") }}</div>
          </div>
        </n-form-item>
        <n-form-item :label="t('isoinsight.create_ips')">
          <div class="iso-full">
            <n-switch v-model:value="form.create_ips" />
            <div class="form-hint">{{ t("isoinsight.create_ips_hint") }}</div>
          </div>
        </n-form-item>
        <n-form-item :label="t('isoinsight.timezone')">
          <div class="iso-full">
            <n-input v-model:value="form.source_timezone" placeholder="Asia/Taipei" data-testid="iso-tz" />
            <n-checkbox v-if="tzChanged" v-model:checked="tzConfirmed" style="margin-top: 6px" data-testid="iso-tz-ok">
              {{ t("isoinsight.timezone_confirm") }}
            </n-checkbox>
            <div class="form-hint">{{ t("isoinsight.timezone_hint") }}</div>
          </div>
        </n-form-item>
        <n-form-item :label="t('isoinsight.verify_tls')">
          <div class="iso-full">
            <n-switch v-model:value="form.verify_tls" />
            <n-alert v-if="!form.verify_tls" type="warning" :bordered="false" class="iso-inline-alert">
              {{ t("isoinsight.tls_off_warning") }}
            </n-alert>
          </div>
        </n-form-item>
        <n-form-item :label="t('common.enable')"><n-switch v-model:value="form.enabled" /></n-form-item>
        <n-form-item :label="t('isoinsight.schedule')">
          <div class="iso-full">
            <n-space align="center">
              <n-switch v-model:value="form.schedule_enabled" data-testid="iso-schedule" />
              <span>{{ t("isoinsight.interval") }}</span>
              <n-input-number v-model:value="form.sync_interval_seconds" :min="60" :max="86400" :step="60" style="width: 130px" />
              <span>{{ t("isoinsight.seconds") }}</span>
            </n-space>
            <div class="form-hint">{{ editing ? (canSchedule ? t("isoinsight.schedule_hint") : t("isoinsight.schedule_needs_preview")) : t("isoinsight.schedule_after_create") }}</div>
          </div>
        </n-form-item>

        <n-collapse>
          <n-collapse-item :title="t('isoinsight.advanced')" name="adv">
            <!-- 登入相關（路徑＋帳密參數名）放同一列，租約路徑獨立一列 -->
            <div class="iso-grid3">
              <n-form-item :label="t('isoinsight.login_path')"><n-input v-model:value="form.login_path" /></n-form-item>
              <n-form-item :label="t('isoinsight.username_param')"><n-input v-model:value="form.username_param" /></n-form-item>
              <n-form-item :label="t('isoinsight.password_param')"><n-input v-model:value="form.password_param" /></n-form-item>
            </div>
            <n-form-item :label="t('isoinsight.lease_path')"><n-input v-model:value="form.lease_path" /></n-form-item>
            <div class="form-hint" style="margin: -8px 0 10px">{{ t("isoinsight.paths_hint") }}</div>
            <n-form-item :label="t('isoinsight.auth_mode')">
              <div class="iso-full">
                <n-radio-group v-model:value="form.auth_mode">
                  <n-radio-button value="cookie" :label="t('isoinsight.auth_cookie')" />
                  <n-radio-button value="token" :label="t('isoinsight.auth_token')" />
                </n-radio-group>
                <div class="form-hint">{{ t("isoinsight.auth_mode_hint") }}</div>
              </div>
            </n-form-item>
            <div v-if="form.auth_mode === 'token'" class="iso-grid">
              <n-form-item :label="t('isoinsight.token_path')"><n-input v-model:value="form.token_path" placeholder="data.token" /></n-form-item>
              <n-form-item :label="t('isoinsight.token_header')"><n-input v-model:value="form.token_header" /></n-form-item>
              <n-form-item :label="t('isoinsight.token_prefix')"><n-input v-model:value="form.token_prefix" placeholder="Bearer" /></n-form-item>
            </div>
            <div v-if="form.auth_mode === 'token'" class="form-hint" style="margin: -8px 0 10px">{{ t("isoinsight.token_hint") }}</div>
            <div class="iso-grid">
              <n-form-item :label="t('isoinsight.connect_timeout')"><n-input-number v-model:value="form.connect_timeout_seconds" :min="1" :max="60" /></n-form-item>
              <n-form-item :label="t('isoinsight.request_timeout')"><n-input-number v-model:value="form.request_timeout_seconds" :min="1" :max="300" /></n-form-item>
              <n-form-item :label="t('isoinsight.job_timeout')"><n-input-number v-model:value="form.job_timeout_seconds" :min="10" :max="900" /></n-form-item>
              <n-form-item :label="t('isoinsight.max_response')"><n-input-number v-model:value="form.max_response_mib" :min="1" :max="200" /></n-form-item>
              <n-form-item :label="t('isoinsight.max_rows')"><n-input-number v-model:value="form.max_rows" :min="1" :max="1000000" :step="1000" /></n-form-item>
            </div>
          </n-collapse-item>
        </n-collapse>
        <n-form-item :label="t('common.description')" style="margin-top: 12px">
          <n-input v-model:value="form.description" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="show = false">
          <template #icon><n-icon><CancelIcon /></n-icon></template>
          {{ t("common.cancel") }}
        </n-button>
        <n-button type="primary" :loading="saving" :disabled="tzChanged && !tzConfirmed" data-testid="iso-save" @click="submit">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </n-space>
    </n-modal>

    <!-- 測試連線／預覽：尚未套用 -->
    <n-modal v-model:show="probe.show" preset="card" style="width: min(1100px, 100%)"
             :title="`${probe.kind === 'test' ? t('isoinsight.test_title') : t('isoinsight.preview_title')}：${probe.source?.name ?? ''}`">
      <n-alert type="warning" :bordered="false" style="margin-bottom: 12px" data-testid="iso-not-applied">
        <template #icon><n-icon><WarnIcon /></n-icon></template>
        {{ probe.kind === "test" ? t("isoinsight.test_not_applied") : t("isoinsight.preview_not_applied") }}
      </n-alert>
      <n-space align="center" style="margin-bottom: 12px">
        <n-button type="primary" :loading="probe.loading" data-testid="iso-probe-run" @click="runProbe">
          <template #icon><n-icon><component :is="probe.kind === 'test' ? TestIcon : EyeIcon" /></n-icon></template>
          {{ probe.kind === "test" ? t("isoinsight.run_test") : t("isoinsight.run_preview") }}
        </n-button>
        <n-checkbox v-if="probe.kind === 'test'" v-model:checked="probe.anonymous">{{ t("isoinsight.anonymous_check") }}</n-checkbox>
      </n-space>
      <template v-if="probe.result">
        <n-alert :type="probe.result.ok ? 'success' : 'error'" :bordered="false" style="margin-bottom: 12px" data-testid="iso-probe-result">
          <template v-if="probe.result.ok">{{ t("isoinsight.probe_ok", probe.result.summary ?? { fetched: 0, valid: 0, invalid: 0 }) }}</template>
          <template v-else>{{ t("isoinsight.probe_failed", { stage: t(`isoinsight.stage.${probe.result.stage ?? 'login'}`) }) }}
            {{ codeText(probe.result.error_code, probe.result.params, probe.result.error) }}
            <div v-if="probe.result.error" class="iso-sub iso-detail">{{ probe.result.error }}</div></template>
        </n-alert>
        <n-alert v-if="probe.result.anonymous" :type="probe.result.anonymous.readable ? 'warning' : 'info'" :bordered="false"
                 style="margin-bottom: 12px">
          {{ probe.result.anonymous.readable ? t("isoinsight.anonymous_readable") : t("isoinsight.anonymous_blocked", { status: probe.result.anonymous.status ?? "—" }) }}
        </n-alert>
        <n-data-table :columns="stageCols" :data="probe.result.stages" :bordered="false" size="small" :scroll-x="900"
                      style="margin-bottom: 12px" />
        <div v-if="probe.result.summary?.warnings?.length" class="iso-sub" style="margin-bottom: 8px">
          {{ t("isoinsight.compat_warnings") }}：{{ probe.result.summary.warnings.map((w) => t(`isoinsight.warn.${w}`)).join("、") }}
        </div>
        <template v-if="probe.result.counts">
          <n-space style="margin-bottom: 8px" :size="6">
            <n-tag v-for="k in BUCKET_KEYS" :key="k" size="small" :bordered="false"
                   :type="['unmatched', 'conflicts', 'unknown_time'].includes(k) && (probe.result.counts as any)[k] ? 'warning' : 'default'">
              {{ previewBucketLabel(k) }} {{ (probe.result.counts as any)[k] ?? 0 }}
            </n-tag>
          </n-space>
          <div v-if="(probe.result.rows_total ?? 0) > (probe.result.rows?.length ?? 0)" class="iso-sub" style="margin-bottom: 6px">
            {{ t("isoinsight.preview_truncated", { shown: probe.result.rows?.length ?? 0, total: probe.result.rows_total ?? 0 }) }}
          </div>
          <n-data-table :columns="previewCols" :data="probe.result.rows ?? []" :bordered="false" size="small"
                        :scroll-x="1300" :max-height="420" virtual-scroll data-testid="iso-preview-rows" />
        </template>
      </template>
    </n-modal>
  </n-card>
</template>

<style scoped>
.form-hint { font-size: 11.5px; opacity: .7; margin-top: 4px; line-height: 1.5; }
.iso-full { width: 100%; }
.iso-tab { display: inline-flex; align-items: center; gap: 6px; }
.iso-steps { margin: 6px 0 4px 18px; padding: 0; line-height: 1.7; }
.iso-note { font-size: 12px; opacity: .8; }
.iso-inline-alert { margin-top: 6px; }
.iso-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); column-gap: 12px; }
.iso-grid3 { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); column-gap: 12px; }
@media (max-width: 600px) { .iso-grid3 { grid-template-columns: 1fr; } }
</style>

<style>
/* render 函式畫的儲存格吃不到 scoped 樣式 */
.iso-raw-time { font-family: ui-monospace, monospace; opacity: .75; text-decoration: underline dotted; }
.iso-sub { font-size: 11.5px; opacity: .7; }
.iso-err { color: var(--n-error-color, #d03050); }
</style>
