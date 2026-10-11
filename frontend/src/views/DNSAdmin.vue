<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { trackTask } from "@/composables/useTaskTracker";
import ScopeOverlapWarning from "@/components/ScopeOverlapWarning.vue";
import {
  NCard, NDataTable, NSpace, NButton, NTag, NIcon, NTooltip, NAlert,
  NModal, NForm, NFormItem, NInput, NInputNumber, NSelect, NSwitch, NPopconfirm, NRadioGroup, NRadio,
  useMessage, type DataTableColumns,
} from "naive-ui";
import {
  listDNSServers, createDNSServer, updateDNSServer, deleteDNSServer, testDNSServer, syncDNSServer,
  listDNSCompareGroups, createDNSCompareGroup, updateDNSCompareGroup, deleteDNSCompareGroup, checkDNSCompareGroup,
  listDNSCompareGroupDiffs,
  type DNSServer, type DNSServerType, type DNSCompareGroup, type DNSCompareGroupDiff, type DNSCompareStatus,
} from "@/api/integrations";
import { useRoute } from "vue-router";
import { fmtDateTime } from "@/utils/datetime";
import { listSubnets } from "@/api/subnets";
import {
  DnsIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SyncIcon, TestIcon, SaveIcon, CancelIcon,
  GroupsIcon, ListIcon, CheckIcon, EyeOffIcon,
} from "@/icons";
import { autoSort } from "@/composables/useTableSort";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { withExportValue } from "@/utils/tableExport";
const { t } = useI18n();

const { visibleKeys: dnsVis, setVisible: dnsSet, reset: dnsReset,
  order: dnsOrder, setOrder: dnsSetOrder, orderColumns: dnsOrderCols } = useColumnPrefs(
  "dns_admin",
  ["name", "type", "endpoint", "compare_group", "enabled", "actions"],
  ["name", "type", "endpoint", "compare_group", "enabled", "actions"],
);
const dnsPicker = computed(() => [
  { key: "name", label: t("cols.name") },
  { key: "type", label: t("cols.type") },
  { key: "endpoint", label: "Endpoint" },
  { key: "compare_group", label: t("dns_admin.compare_group") },
  { key: "enabled", label: t("cols.status") },
  { key: "actions", label: t("cols.actions") },
]);

const msg = useMessage();
const rows = ref<DNSServer[]>([]);
import { useTableQuickFilter } from "@/composables/useTableQuickFilter";
import { apiErrMsg } from "@/api/client";
const { query: filterQ, filtered: filteredRows } = useTableQuickFilter(rows);
const loading = ref(false);
const show = ref(false);

interface Form {
  name: string;
  type: DNSServerType;
  api_url: string;
  server_address: string;
  enabled: boolean;
  sync_interval_seconds: number;
  api_key: string;
  api_secret: string;
  tsig_key: string;
  zones: string[];
  password: string;
  username: string;
  verify_tls: boolean;
  /** Windows DNS：WinRM 走 HTTPS 5986 或 HTTP 5985（Windows Server 預設只開 5985） */
  winrm_https: boolean;
  winrm_port: number | null;
  scope_subnet_ids: string[];
  compare_group_id: string | null;
}

function emptyForm(): Form {
  return {
    name: "", type: "powerdns",
    api_url: "", server_address: "",
    enabled: true, sync_interval_seconds: 300,
    api_key: "", api_secret: "", tsig_key: "", password: "", zones: [] as string[],
    // WinRM 預設 HTTP 5985：Windows Server 防火牆預設封鎖 5986（使用者 2026-10-09）
    username: "", verify_tls: true, winrm_https: false, winrm_port: null,
    scope_subnet_ids: [], compare_group_id: null,
  };
}
const form = ref<Form>(emptyForm());

const subnetOptions = ref<{ label: string; value: string }[]>([]);
async function loadSubnetOptions() {
  try {
    const r = await listSubnets({ page: 1, pageSize: 500 });
    subnetOptions.value = r.items.map((s) => ({
      label: s.description ? `${s.cidr} — ${s.description}` : s.cidr, value: s.id }));
  } catch { /* silent */ }
}

const typeOpts = [
  { label: t("dns_admin.type_powerdns"),         value: "powerdns" },
  { label: t("dns_admin.type_bind9"),            value: "bind9" },
  { label: t("dns_admin.type_unbound_opnsense"), value: "unbound_opnsense" },
  { label: t("dns_admin.type_windows_dns"),      value: "windows_dns" },
  { label: t("dns_admin.type_univention_ucs"),   value: "univention_ucs" },
  { label: t("dns_admin.type_technitium"),       value: "technitium" },
];

// 不同 type 該顯示哪些憑證欄位
const showApiKey   = computed(() => ["powerdns", "unbound_opnsense", "technitium"].includes(form.value.type));
const showApiSecret = computed(() => form.value.type === "unbound_opnsense");
const showTsig     = computed(() => form.value.type === "bind9");
const showPassword = computed(() => ["windows_dns", "univention_ucs"].includes(form.value.type));
const showApiUrl   = computed(() => ["powerdns", "unbound_opnsense", "univention_ucs", "technitium"].includes(form.value.type));
const showServerAddr = computed(() => ["bind9", "windows_dns"].includes(form.value.type));
const showUsername = computed(() => ["windows_dns", "univention_ucs"].includes(form.value.type));
// Windows DNS：WinRM HTTPS 常是自簽憑證（客戶 2026-10-08 回報 CERTIFICATE_VERIFY_FAILED）；走 HTTP 時沒有憑證可驗
const showVerifyTls = computed(() => ["univention_ucs", "technitium"].includes(form.value.type)
  || (form.value.type === "windows_dns" && form.value.winrm_https));

async function refresh() {
  loading.value = true;
  try { rows.value = (await listDNSServers()).items ?? []; }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}
const editingId = ref<string | null>(null);
function openCreate() {
  editingId.value = null;
  form.value = emptyForm();
  show.value = true;
}
function openEdit(r: DNSServer) {
  editingId.value = r.id;
  const f = emptyForm();
  f.name = r.name;
  f.type = r.type as DNSServerType;
  f.enabled = r.enabled;
  f.sync_interval_seconds = r.sync_interval_seconds ?? 300;
  // api_url / server_address 依類型回填（祕密欄留空＝不變）
  f.api_url = r.api_url ?? "";
  f.server_address = r.server_address ?? "";
  f.scope_subnet_ids = r.scope_subnet_ids ?? [];
  f.compare_group_id = r.compare_group_id ?? null;
  // extra_config（JSON）回填 username / verify_tls，否則重開會跑回預設值
  if (r.extra_config) {
    try {
      const extra = JSON.parse(r.extra_config) as {
        username?: string; verify_tls?: boolean; zones?: string[]; use_ssl?: boolean; winrm_port?: number;
      };
      if (Array.isArray(extra.zones)) form.value.zones = [...extra.zones];
      if (typeof extra.username === "string") f.username = extra.username;
      if (typeof extra.verify_tls === "boolean") f.verify_tls = extra.verify_tls;
      if (typeof extra.use_ssl === "boolean") f.winrm_https = extra.use_ssl;
      if (typeof extra.winrm_port === "number") f.winrm_port = extra.winrm_port;
    } catch { /* ignore malformed */ }
  }
  form.value = f;
  show.value = true;
}
async function submit() {
  if (!form.value.name.trim()) { msg.error(t("dns_admin.error_name_required")); return; }
  // UCS 走 Basic auth：帳號必填（空帳號 → UCS 回 400「basic auth malformed」，整個同步抓 0 筆）
  if (showUsername.value && !form.value.username.trim()) {
    msg.error(t("dns_admin.error_username_required")); return;
  }
  const payload: any = {
    name: form.value.name,
    type: form.value.type,
    enabled: form.value.enabled,
    sync_interval_seconds: form.value.sync_interval_seconds,
    scope_subnet_ids: form.value.scope_subnet_ids,
    compare_group_id: form.value.compare_group_id || null,
  };
  if (showApiUrl.value && form.value.api_url) payload.api_url = form.value.api_url;
  if (showServerAddr.value && form.value.server_address) payload.server_address = form.value.server_address;
  if (showApiKey.value && form.value.api_key) payload.api_key = form.value.api_key;
  if (showApiSecret.value && form.value.api_secret) payload.api_secret = form.value.api_secret;
  if (showTsig.value && form.value.tsig_key) payload.tsig_key = form.value.tsig_key;
  if (showPassword.value && form.value.password) payload.password = form.value.password;
  // 非機密設定走 extra_config：username / verify_tls（windows_dns、univention_ucs）
  // 與 BIND9 的 zone 清單。**不能只在 showUsername/showVerifyTls 為真時才組**，
  // BIND9 兩者都是 false，zone 清單會被安靜地丟掉、設定看起來存了卻沒生效。
  const extra: Record<string, unknown> = {};
  if (showUsername.value && form.value.username) extra.username = form.value.username;
  if (showVerifyTls.value) extra.verify_tls = form.value.verify_tls;
  if (form.value.type === "windows_dns") {
    extra.use_ssl = form.value.winrm_https;
    if (form.value.winrm_port) extra.winrm_port = form.value.winrm_port;
  }
  if (form.value.type === "bind9") {
    extra.zones = form.value.zones.map((z) => z.trim().replace(/\.$/, "")).filter(Boolean);
  }
  if (Object.keys(extra).length) payload.extra_config = JSON.stringify(extra);
  try {
    if (editingId.value) await updateDNSServer(editingId.value, payload);
    else await createDNSServer(payload);
    show.value = false;
    msg.success(t("common.ok"));
    await refresh();
    await loadGroups();
  } catch (e) { msg.error(apiErrMsg(e)); }
}
async function test(id: string) {
  try {
    const r = await testDNSServer(id);
    const s = (r?.server ?? {}) as Record<string, unknown>;
    // Technitium：講出版本、帳號、讀得到幾個 zone；逐個 zone 的權限沒給到的列出來（新建的 zone 預設只給管理員）
    if (rows.value.find((x) => x.id === id)?.type === "technitium") {
      msg.success(t("dns_admin.technitium_test_ok", { version: s.version ?? "?", user: s.user ?? "?", n: s.zones ?? 0 }));
      const bad = (s.unreadable_zones as string[] | undefined) ?? [];
      if (bad.length) {
        msg.warning(t("dns_admin.technitium_unreadable", { n: bad.length, zones: bad.slice(0, 5).join(", ") }),
                    { duration: 12000, closable: true });
      }
      if (s.can_modify) msg.info(t("dns_admin.technitium_can_modify"), { duration: 8000 });
      return;
    }
    msg.success(t("librenms_admin.test_ok"));
  } catch (e) { msg.error(apiErrMsg(e)); }
}
async function del(id: string) {
  try { await deleteDNSServer(id); msg.success(t("common.ok")); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function sync(id: string) {
  try {
    const r = await syncDNSServer(id);
    // 跑完重新整理伺服器與比對群組（比對在拉取完成時進行）
    trackTask(r.task_id, { onDone: () => { void refresh(); void loadGroups(); } });
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

function iconAction(icon: any, label: string, onClick: () => void, type?: any) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type, "aria-label": label,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}
const allCols = computed<DataTableColumns<DNSServer>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 160, ellipsis: { tooltip: true } },
  {
    title: t("dns_admin.type"), key: "type", width: 110,
    render: (r) => h(NTag, { size: "small", type: "info" }, () => r.type),
  },
  { title: t("dns_admin.endpoint"), key: "endpoint", minWidth: 200, ellipsis: { tooltip: true },
    render: (r) => r.api_url ?? r.server_address ?? "—" },
  { title: t("dns_admin.compare_group"), key: "compare_group", width: 150,
    render: (r) => {
      const g = groups.value.find((x) => x.id === r.compare_group_id);
      return g ? h(NTag, { size: "small", type: statusType(g.last_status), bordered: false }, () => g.name) : "—";
    } },
  {
    title: t("common.status"), key: "enabled", width: 110,
    render: (r) => h(NTag, { type: r.enabled ? "success" : "default", size: "small" },
      () => r.enabled ? t("common.enabled") : t("common.disabled")),
  },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 158,
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openEdit(r)),
      iconAction(TestIcon, t("common.test"), () => test(r.id)),
      iconAction(SyncIcon, t("common.pull"), () => sync(r.id), "primary"),
      h(NPopconfirm, { onPositiveClick: () => del(r.id) }, {
        trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]));

const cols = computed<DataTableColumns<DNSServer>>(() =>
  dnsOrderCols(allCols.value.filter((c: any) => dnsVis.value.includes(c.key))),
);

// ── 比對群組 ──────────────────────────────────────────────────────────
const groups = ref<DNSCompareGroup[]>([]);
const groupOptions = computed(() => groups.value.map((g) => ({ label: g.name, value: g.id })));
// 成員選單標出軟體類型：不同軟體可以混搭，但 Unbound 只能和 Unbound 同組（後端會擋，這裡先讓人看得出來）
const serverOptions = computed(() => rows.value.map((r) => ({
  label: `${r.name}（${t("dns_admin.type_" + r.type)}）`, value: r.id })));
async function loadGroups() {
  try { groups.value = await listDNSCompareGroups(); } catch (e) { msg.error(apiErrMsg(e)); }
}
function statusType(s: DNSCompareStatus | null | undefined): "success" | "error" | "warning" | "default" {
  return s === "ok" ? "success" : s === "mismatch" ? "error"
    : s === "pending" || s === "incompatible" ? "warning" : "default";
}
interface GroupForm {
  name: string; description: string; notify_enabled: boolean; grace_minutes: number; server_ids: string[]; excluded_zones: string[]
}
const groupShow = ref(false);
const groupEditing = ref<string | null>(null);
const groupForm = ref<GroupForm>({ name: "", description: "", notify_enabled: true, grace_minutes: 30, server_ids: [], excluded_zones: [] });
// 「不比對的 zone」選單：成員拉回來的 zone（編輯時有）＋已經排除的；也可以直接輸入
const zoneOptions = computed(() => {
  const g = groups.value.find((x) => x.id === groupEditing.value);
  return [...new Set([...(g?.zones ?? []), ...groupForm.value.excluded_zones])].map((z) => ({ label: z, value: z }));
});
function openGroupCreate() {
  groupEditing.value = null;
  groupForm.value = { name: "", description: "", notify_enabled: true, grace_minutes: 30, server_ids: [], excluded_zones: [] };
  groupShow.value = true;
}
function openGroupEdit(g: DNSCompareGroup) {
  groupEditing.value = g.id;
  groupForm.value = { name: g.name, description: g.description ?? "", notify_enabled: g.notify_enabled,
                      grace_minutes: g.grace_minutes, server_ids: g.members.map((m) => m.id),
                      excluded_zones: [...(g.excluded_zones ?? [])] };
  groupShow.value = true;
}
async function submitGroup() {
  if (!groupForm.value.name.trim()) { msg.error(t("dns_admin.cg_name_required")); return; }
  const payload = { ...groupForm.value, name: groupForm.value.name.trim(), description: groupForm.value.description || null };
  try {
    if (groupEditing.value) await updateDNSCompareGroup(groupEditing.value, payload);
    else await createDNSCompareGroup(payload);
    groupShow.value = false;
    msg.success(t("common.ok"));
    await Promise.all([loadGroups(), refresh()]);
  } catch (e) { msg.error(apiErrMsg(e)); }
}
async function removeGroup(id: string) {
  try { await deleteDNSCompareGroup(id); msg.success(t("common.ok")); await Promise.all([loadGroups(), refresh()]); }
  catch (e) { msg.error(apiErrMsg(e)); }
}
const checking = ref<string | null>(null);
async function checkGroup(g: DNSCompareGroup) {
  checking.value = g.id;
  try {
    const r = await checkDNSCompareGroup(g.id);
    msg.info(t("dns_admin.cg_checked", { status: t(`dns_admin.cg_status_${r.status}`) }));
    await loadGroups();
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { checking.value = null; }
}
const diffShow = ref(false);
const diffGroup = ref<DNSCompareGroup | null>(null);
const diffs = ref<DNSCompareGroupDiff[]>([]);
const diffLoading = ref(false);
async function openDiffs(g: DNSCompareGroup) {
  diffGroup.value = g;
  diffShow.value = true;
  diffLoading.value = true;
  try { diffs.value = await listDNSCompareGroupDiffs(g.id); }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { diffLoading.value = false; }
}
// 差異清單「整個 zone 只在部分伺服器上」：一鍵加進不比對的 zone（後端存檔時馬上重新比對）
async function excludeZone(zone: string) {
  const g = diffGroup.value;
  if (!g) return;
  try {
    await updateDNSCompareGroup(g.id, { name: g.name, excluded_zones: [...new Set([...(g.excluded_zones ?? []), zone])] });
    msg.success(t("dns_admin.cg_zone_excluded", { zone }));
    await loadGroups();
    const fresh = groups.value.find((x) => x.id === g.id);
    if (fresh) await openDiffs(fresh);
  } catch (e) { msg.error(apiErrMsg(e)); }
}
const serverTags = (list: { name: string }[], type: "default" | "error") =>
  h(NSpace, { size: 4 }, () => list.map((s) => h(NTag, { size: "small", type, bordered: false }, () => s.name)));
const groupStatusLabel = (g: DNSCompareGroup) =>
  g.last_status ? t(`dns_admin.cg_status_${g.last_status}`) : t("dns_admin.cg_status_never");

// 群組表格：成員、狀態等攤平成文字欄位，排序、篩選、匯出才有東西可比
interface GroupRow extends DNSCompareGroup {
  member_names: string; status_label: string; notify_label: string; excluded_label: string
}
const groupRows = computed<GroupRow[]>(() => groups.value.map((g) => ({
  ...g, member_names: g.members.map((m) => m.name).join(", "),
  status_label: groupStatusLabel(g), notify_label: g.notify_enabled ? t("common.yes") : t("common.no"),
  excluded_label: (g.excluded_zones ?? []).join(", "),
})));
const GROUP_KEYS = ["name", "member_names", "status_label", "diff_count", "last_checked_at", "grace_minutes",
  "excluded_label", "notify_label", "description", "actions"];
const { visibleKeys: gVis, setVisible: gSet, reset: gReset, order: gOrder, setOrder: gSetOrder,
  orderColumns: gOrderCols } = useColumnPrefs("dns_compare_groups", GROUP_KEYS,
  GROUP_KEYS.filter((k) => k !== "description"));
const { query: groupQ, filtered: groupFiltered } = useTableQuickFilter(groupRows,
  () => gVis.value.filter((k) => k !== "actions"));
const allGroupCols = computed<DataTableColumns<GroupRow>>(() => autoSort<GroupRow>([
  { title: t("common.name"), key: "name", minWidth: 140, ellipsis: { tooltip: true } },
  { title: t("dns_admin.cg_members"), key: "member_names", minWidth: 200,
    render: (g) => g.members.length ? serverTags(g.members, "default") : "—" },
  { title: t("common.status"), key: "status_label", width: 140,
    render: (g) => h(NTooltip, null, {
      trigger: () => h(NTag, { size: "small", type: statusType(g.last_status) }, () => g.status_label),
      default: () => g.last_message || t(`dns_admin.cg_status_hint_${g.last_status ?? "never"}`),
    }) },
  { title: t("dns_admin.cg_diff_count"), key: "diff_count", width: 90 },
  withExportValue({ title: t("dns_admin.cg_last_checked"), key: "last_checked_at", width: 170,
    render: (g: GroupRow) => g.last_checked_at ? fmtDateTime(g.last_checked_at) : "—" },
    (g: GroupRow) => g.last_checked_at ? fmtDateTime(g.last_checked_at) : ""),
  { title: t("dns_admin.cg_grace"), key: "grace_minutes", width: 110,
    render: (g) => `${g.grace_minutes} ${t("dns_admin.cg_minutes")}` },
  { title: t("dns_admin.cg_excluded"), key: "excluded_label", minWidth: 150, ellipsis: { tooltip: true },
    render: (g) => g.excluded_label || "—" },
  { title: t("dns_admin.cg_notify"), key: "notify_label", width: 90 },
  { title: t("dns_admin.cg_description"), key: "description", minWidth: 160, ellipsis: { tooltip: true } },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 158,
    render: (g) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(ListIcon, t("dns_admin.cg_diffs"), () => openDiffs(g), "info"),
      iconAction(CheckIcon, t("dns_admin.cg_check"), () => checkGroup(g), "primary"),
      iconAction(EditIcon, t("common.edit"), () => openGroupEdit(g)),
      h(NPopconfirm, { onPositiveClick: () => removeGroup(g.id) }, {
        trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
        default: () => t("dns_admin.cg_delete_confirm"),
      }),
    ]),
  },
]));
const groupCols = computed(() => gOrderCols(allGroupCols.value.filter((c: any) => gVis.value.includes(c.key))));
const groupPicker = computed(() => allGroupCols.value.map((c: any) => ({ key: c.key, label: String(c.title) })));

// 差異清單：名稱、型別、值拆開，才能各自排序；整個 zone 的差異在「名稱」欄寫明
interface DiffRow extends DNSCompareGroupDiff {
  what: string; present_names: string; missing_names: string; status_label: string
}
const diffRows = computed<DiffRow[]>(() => diffs.value.map((d) => ({
  ...d, what: d.kind === "zone" ? t("dns_admin.cg_kind_zone") : d.name,
  present_names: d.present_on.map((x) => x.name).join(", "), missing_names: d.missing_on.map((x) => x.name).join(", "),
  status_label: d.confirmed ? t("dns_admin.cg_confirmed") : t("dns_admin.cg_pending"),
})));
const DIFF_KEYS = ["zone", "what", "type", "value", "present_names", "missing_names", "first_seen_at", "last_seen_at",
  "status_label", "actions"];
const { visibleKeys: dVis, setVisible: dSet, reset: dReset, order: dOrder, setOrder: dSetOrder,
  orderColumns: dOrderCols } = useColumnPrefs("dns_compare_diffs", DIFF_KEYS,
  DIFF_KEYS.filter((k) => k !== "last_seen_at"));
const { query: diffQ, filtered: diffFiltered } = useTableQuickFilter(diffRows, () => dVis.value.filter((k) => k !== "actions"));
const allDiffCols = computed<DataTableColumns<DiffRow>>(() => autoSort<DiffRow>([
  { title: "Zone", key: "zone", minWidth: 150, ellipsis: { tooltip: true } },
  { title: t("common.name"), key: "what", minWidth: 200, ellipsis: { tooltip: true } },
  { title: t("cols.type"), key: "type", width: 80 },
  { title: t("dns_admin.cg_value"), key: "value", minWidth: 150, ellipsis: { tooltip: true } },
  { title: t("dns_admin.cg_present_on"), key: "present_names", minWidth: 140, render: (d) => serverTags(d.present_on, "default") },
  { title: t("dns_admin.cg_missing_on"), key: "missing_names", minWidth: 140, render: (d) => serverTags(d.missing_on, "error") },
  withExportValue({ title: t("dns_admin.cg_first_seen"), key: "first_seen_at", width: 170,
    render: (d: DiffRow) => fmtDateTime(d.first_seen_at) }, (d: DiffRow) => fmtDateTime(d.first_seen_at)),
  withExportValue({ title: t("dns_admin.cg_last_seen"), key: "last_seen_at", width: 170,
    render: (d: DiffRow) => fmtDateTime(d.last_seen_at) }, (d: DiffRow) => fmtDateTime(d.last_seen_at)),
  { title: t("common.status"), key: "status_label", width: 110,
    render: (d) => h(NTag, { size: "small", type: d.confirmed ? "error" : "warning", bordered: false }, () => d.status_label) },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 70,
    render: (d) => d.kind !== "zone" ? null : h(NPopconfirm, { onPositiveClick: () => excludeZone(d.zone) }, {
      trigger: () => iconAction(EyeOffIcon, t("dns_admin.cg_exclude_zone"), () => {}, "warning"),
      default: () => t("dns_admin.cg_exclude_zone_confirm", { zone: d.zone }),
    }),
  },
]));
const diffCols = computed(() => dOrderCols(allDiffCols.value.filter((c: any) => dVis.value.includes(c.key))));
const diffPicker = computed(() => allDiffCols.value.map((c: any) => ({ key: c.key, label: String(c.title) })));

const route = useRoute();
onMounted(async () => {
  void loadSubnetOptions();
  await Promise.all([refresh(), loadGroups()]);
  // 通知的連結帶 ?compare_group=<id>：直接打開那一組的差異
  const want = typeof route.query.compare_group === "string" ? route.query.compare_group : "";
  const g = want ? groups.value.find((x) => x.id === want) : undefined;
  if (g) void openDiffs(g);
});
</script>

<template>
  <div class="dns-admin">
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><DnsIcon /></n-icon>
        <span>{{ t("dns_admin.title") }}</span>
      </n-space>
    </template>

    <n-space style="margin-bottom: 12px" align="center">
      <n-input v-model:value="filterQ" :placeholder="t('common.filter')" clearable style="width: 160px" />
      <n-button @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
      <n-button type="primary" data-testid="dns-create" @click="openCreate">
        <template #icon><n-icon><PlusIcon /></n-icon></template>
        {{ t("dns_admin.create") }}
      </n-button>
      <ColumnPicker :all="dnsPicker" :visible="dnsVis"
                    @update:visible="dnsSet" @reset="dnsReset"
                    :order="dnsOrder" @update:order="dnsSetOrder" />
      <ExportButton :columns="cols" :rows="rows" filename="dns-servers" :title="t('dns_admin.title')" />
    </n-space>

    <n-data-table :columns="cols" :data="filteredRows" :loading="loading" :bordered="false" :scroll-x="766">
      <template #empty>
        <n-space justify="center">{{ t("common.no_data") }}</n-space>
      </template>
    </n-data-table>

    <n-modal v-model:show="show" preset="card" style="width: 560px">
      <template #header>
        <n-space align="center">
          <n-icon :size="20"><component :is="editingId ? EditIcon : PlusIcon" /></n-icon>
          <span>{{ editingId ? t("dns_admin.edit") : t("dns_admin.create") }}</span>
        </n-space>
      </template>
      <n-form>
        <n-form-item :label="t('common.name')">
          <n-input v-model:value="form.name" placeholder="dns-edge" />
        </n-form-item>
        <n-form-item :label="t('dns_admin.type')">
          <n-select v-model:value="form.type" :options="typeOpts" data-testid="dns-type" />
        </n-form-item>

        <!-- 各類型設定說明 -->
        <n-alert v-if="form.type === 'univention_ucs'" type="info" :bordered="false"
                 :show-icon="true" style="margin-bottom: 12px">
          {{ t("dns_admin.help_ucs") }}
        </n-alert>
        <n-alert v-else type="default" :bordered="false" :show-icon="true" style="margin-bottom: 12px">
          {{ t("dns_admin.help_" + form.type) }}
        </n-alert>

        <n-form-item v-if="showApiUrl" label="API URL">
          <n-input v-model:value="form.api_url"
                   :placeholder="form.type === 'powerdns'
                     ? 'https://powerdns.example.com:8081'
                     : form.type === 'univention_ucs'
                       ? 'https://ucs.example.com'
                       : form.type === 'technitium'
                         ? 'https://dns.example.com:53443'
                         : 'https://opnsense.example.com'" />
        </n-form-item>
        <n-form-item v-if="showUsername" :label="t('dns_admin.username')">
          <n-input v-model:value="form.username"
                   :placeholder="form.type === 'univention_ucs' ? 'Administrator' : 'DOMAIN\\svc-dns'" />
        </n-form-item>
        <n-form-item v-if="showServerAddr" :label="t('dns_admin.server_address')">
          <n-input v-model:value="form.server_address"
                   :placeholder="form.type === 'bind9' ? 'ns1.example.com' : 'dc01.example.com'" />
        </n-form-item>
        <n-form-item v-if="form.type === 'windows_dns'" :label="t('dns_admin.winrm_transport')">
          <n-space vertical :size="4" style="width: 100%">
            <n-space align="center" :wrap-item="false">
              <n-radio-group v-model:value="form.winrm_https" data-testid="dns-winrm-transport">
                <n-radio :value="false">{{ t("dns_admin.winrm_http_label") }}</n-radio>
                <n-radio :value="true">HTTPS（5986）</n-radio>
              </n-radio-group>
              <n-input-number v-model:value="form.winrm_port" :min="1" :max="65535" clearable style="width: 140px"
                              :placeholder="form.winrm_https ? '5986' : '5985'" />
            </n-space>
            <span style="font-size:11px;opacity:.7">{{ t("dns_admin.winrm_transport_hint") }}</span>
          </n-space>
        </n-form-item>

        <n-form-item v-if="showApiKey"
                     :label="form.type === 'powerdns' ? 'X-API-Key' : form.type === 'technitium' ? t('dns_admin.technitium_token') : 'OPNsense API key'">
          <n-input v-model:value="form.api_key" type="password" show-password-on="click" />
        </n-form-item>
        <n-form-item v-if="showApiSecret" label="OPNsense API secret">
          <n-input v-model:value="form.api_secret" type="password" show-password-on="click" />
        </n-form-item>
        <n-form-item v-if="showTsig" label="TSIG key (BIND9)">
          <n-input v-model:value="form.tsig_key" type="password" show-password-on="click"
                   placeholder="hmac-sha256:keyname:base64key" />
        </n-form-item>
        <!-- BIND 沒有「列出所有 zone」的協定，一定要在這裡指定要同步哪些；
             沒填的話同步會安靜地跑完、一筆記錄都沒有 -->
        <n-form-item v-if="showTsig" :label="t('dns_admin.zones')">
          <n-space vertical style="width:100%" :size="4">
            <n-select v-model:value="form.zones" multiple filterable tag
                      :options="[]" :placeholder="t('dns_admin.zones_ph')" />
            <span style="font-size:11px;opacity:.7">{{ t("dns_admin.zones_hint") }}</span>
          </n-space>
        </n-form-item>
        <n-form-item v-if="showPassword"
                     :label="form.type === 'univention_ucs' ? t('dns_admin.password') : t('dns_admin.winrm_password')">
          <n-input v-model:value="form.password" type="password" show-password-on="click" />
        </n-form-item>
        <n-form-item v-if="showVerifyTls" :label="t('dns_admin.verify_tls')">
          <n-space vertical :size="2">
            <n-switch v-model:value="form.verify_tls" data-testid="dns-verify-tls" />
            <span v-if="form.type === 'windows_dns'" style="font-size:11px;opacity:.7">{{ t("dns_admin.verify_tls_winrm_hint") }}</span>
          </n-space>
        </n-form-item>

        <n-form-item :label="t('dns_admin.compare_group')">
          <n-space vertical :size="2" style="width: 100%">
            <n-select v-model:value="form.compare_group_id" :options="groupOptions" clearable
                      :placeholder="t('dns_admin.compare_group_none')" data-testid="dns-compare-group" />
            <span style="font-size:11px;opacity:.7">{{ t("dns_admin.compare_group_hint") }}</span>
          </n-space>
        </n-form-item>
        <n-form-item :label="t('common.enabled')">
          <n-switch v-model:value="form.enabled" />
        </n-form-item>
        <n-form-item :label="t('librenms_admin.sync_interval')">
          <n-input-number v-model:value="form.sync_interval_seconds" :min="60" :max="86400" />
        </n-form-item>
        <n-form-item :label="t('dns_admin.scope_subnets')">
          <div style="width: 100%">
            <n-select v-model:value="form.scope_subnet_ids" :options="subnetOptions"
                      multiple filterable clearable :placeholder="t('dns_admin.scope_all')" />
            <ScopeOverlapWarning :scope-empty="!form.scope_subnet_ids?.length" />
          </div>
        </n-form-item>
        <div style="margin: -8px 0 4px">
          <span style="font-size: 11px; opacity: .7">{{ t("dns_admin.scope_hint") }}</span>
        </div>
      </n-form>
      <n-space justify="end">
        <n-button @click="show = false">
          <template #icon><n-icon><CancelIcon /></n-icon></template>
          {{ t("common.cancel") }}
        </n-button>
        <n-button type="primary" @click="submit">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </n-space>
    </n-modal>
  </n-card>

  <!-- 比對群組：內容應該一致的 DNS 伺服器（jt-ipam 不同步，只比對拉回來的資料；相同紀錄合併顯示、不一致時告警） -->
  <n-card style="margin-top: 16px" data-testid="dns-compare-groups">
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="20"><GroupsIcon /></n-icon>
        <span>{{ t("dns_admin.cg_title") }}</span>
        <n-tag v-if="groups.length" size="small" round :bordered="false">{{ groups.length }}</n-tag>
      </n-space>
    </template>
    <n-space vertical :size="10">
      <span style="font-size: 13px; opacity: .8">{{ t("dns_admin.cg_intro") }}</span>
      <n-space align="center">
        <n-input v-model:value="groupQ" :placeholder="t('common.filter')" clearable style="width: 160px"
                 data-testid="dns-cg-filter" />
        <n-button @click="loadGroups">
          <template #icon><n-icon><RefreshIcon /></n-icon></template>
          {{ t("common.refresh") }}
        </n-button>
        <n-button type="primary" data-testid="dns-cg-create" @click="openGroupCreate">
          <template #icon><n-icon><PlusIcon /></n-icon></template>
          {{ t("dns_admin.cg_create") }}
        </n-button>
        <ColumnPicker :all="groupPicker" :visible="gVis" @update:visible="gSet" @reset="gReset"
                      :order="gOrder" @update:order="gSetOrder" />
        <ExportButton :columns="groupCols" :rows="groupFiltered" filename="dns-compare-groups" :title="t('dns_admin.cg_title')" />
      </n-space>
      <n-data-table :columns="groupCols" :data="groupFiltered" :bordered="false" :scroll-x="1100" size="small">
        <template #empty>
          <n-space justify="center">{{ t("dns_admin.cg_empty") }}</n-space>
        </template>
      </n-data-table>
    </n-space>
  </n-card>

  <n-modal v-model:show="groupShow" preset="card" style="width: 520px" data-testid="dns-cg-modal">
    <template #header>
      <n-space align="center">
        <n-icon :size="20"><component :is="groupEditing ? EditIcon : PlusIcon" /></n-icon>
        <span>{{ groupEditing ? t("dns_admin.cg_edit") : t("dns_admin.cg_create") }}</span>
      </n-space>
    </template>
    <n-form>
      <n-form-item :label="t('common.name')">
        <n-input v-model:value="groupForm.name" placeholder="corp-dns" data-testid="dns-cg-name" />
      </n-form-item>
      <n-form-item :label="t('dns_admin.cg_members')">
        <n-space vertical :size="2" style="width: 100%">
          <n-select v-model:value="groupForm.server_ids" :options="serverOptions" multiple filterable
                    :placeholder="t('dns_admin.cg_members_ph')" data-testid="dns-cg-members" />
          <span style="font-size:11px;opacity:.7">{{ t("dns_admin.cg_members_hint") }}</span>
        </n-space>
      </n-form-item>
      <n-form-item :label="t('dns_admin.cg_grace')">
        <n-space vertical :size="2" style="width: 100%">
          <n-input-number v-model:value="groupForm.grace_minutes" :min="0" :max="1440" style="width: 160px">
            <template #suffix>{{ t("dns_admin.cg_minutes") }}</template>
          </n-input-number>
          <span style="font-size:11px;opacity:.7">{{ t("dns_admin.cg_grace_hint") }}</span>
        </n-space>
      </n-form-item>
      <n-form-item :label="t('dns_admin.cg_excluded')">
        <n-space vertical :size="2" style="width: 100%">
          <n-select v-model:value="groupForm.excluded_zones" :options="zoneOptions" multiple filterable tag clearable
                    :placeholder="t('dns_admin.cg_excluded_ph')" data-testid="dns-cg-excluded" />
          <span style="font-size:11px;opacity:.7">{{ t("dns_admin.cg_excluded_hint") }}</span>
        </n-space>
      </n-form-item>
      <n-form-item :label="t('dns_admin.cg_notify')">
        <n-space vertical :size="2">
          <n-switch v-model:value="groupForm.notify_enabled" />
          <span style="font-size:11px;opacity:.7">{{ t("dns_admin.cg_notify_hint") }}</span>
        </n-space>
      </n-form-item>
      <n-form-item :label="t('dns_admin.cg_description')">
        <n-input v-model:value="groupForm.description" type="textarea" :autosize="{ minRows: 1, maxRows: 4 }" />
      </n-form-item>
    </n-form>
    <n-space justify="end">
      <n-button @click="groupShow = false">
        <template #icon><n-icon><CancelIcon /></n-icon></template>
        {{ t("common.cancel") }}
      </n-button>
      <n-button type="primary" data-testid="dns-cg-save" @click="submitGroup">
        <template #icon><n-icon><SaveIcon /></n-icon></template>
        {{ t("common.save") }}
      </n-button>
    </n-space>
  </n-modal>

  <n-modal v-model:show="diffShow" preset="card" style="width: min(1100px, 96vw)" data-testid="dns-cg-diffs">
    <template #header>
      <n-space align="center">
        <n-icon :size="20"><ListIcon /></n-icon>
        <span>{{ t("dns_admin.cg_diffs_title", { name: diffGroup?.name ?? "" }) }}</span>
      </n-space>
    </template>
    <n-space vertical :size="10">
      <n-alert v-if="diffGroup && diffGroup.last_status && diffGroup.last_status !== 'mismatch' && diffGroup.last_status !== 'pending'"
               :type="statusType(diffGroup.last_status) === 'success' ? 'success' : 'info'" :bordered="false">
        {{ t(`dns_admin.cg_status_hint_${diffGroup.last_status}`) }}
        <span v-if="diffGroup.last_message">（{{ diffGroup.last_message }}）</span>
      </n-alert>
      <span style="font-size: 12px; opacity: .75">{{ t("dns_admin.cg_check_hint", { n: diffGroup?.grace_minutes ?? 0 }) }}</span>
      <span v-if="diffGroup?.excluded_zones?.length" style="font-size: 12px; opacity: .75" data-testid="dns-cg-excluded-note">
        {{ t("dns_admin.cg_excluded_note", { zones: diffGroup.excluded_zones.join(", ") }) }}
      </span>
      <n-space align="center">
        <n-input v-model:value="diffQ" :placeholder="t('common.filter')" clearable style="width: 200px"
                 data-testid="dns-cg-diff-filter" />
        <ColumnPicker :all="diffPicker" :visible="dVis" @update:visible="dSet" @reset="dReset"
                      :order="dOrder" @update:order="dSetOrder" />
        <ExportButton :columns="diffCols" :rows="diffFiltered" :filename="`dns-compare-diffs-${diffGroup?.name ?? ''}`"
                      :title="t('dns_admin.cg_diffs_title', { name: diffGroup?.name ?? '' })" />
        <span style="font-size: 12px; opacity: .7">{{ t("common.total_n", { n: diffFiltered.length }) }}</span>
      </n-space>
      <n-data-table :columns="diffCols" :data="diffFiltered" :loading="diffLoading" :bordered="false" size="small"
                    :scroll-x="1240" :max-height="520">
        <template #empty>
          <n-space justify="center">{{ t("dns_admin.cg_no_diffs") }}</n-space>
        </template>
      </n-data-table>
      <n-space justify="end">
        <n-button v-if="diffGroup" type="primary" :loading="checking === diffGroup.id"
                  @click="diffGroup && checkGroup(diffGroup).then(() => diffGroup && openDiffs(groups.find((x) => x.id === diffGroup!.id) ?? diffGroup))">
          <template #icon><n-icon><CheckIcon /></n-icon></template>
          {{ t("dns_admin.cg_check") }}
        </n-button>
        <n-button @click="diffShow = false">
          <template #icon><n-icon><CancelIcon /></n-icon></template>
          {{ t("common.close") }}
        </n-button>
      </n-space>
    </n-space>
  </n-modal>
  </div>
</template>
