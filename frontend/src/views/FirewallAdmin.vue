<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { fmtDateTime } from "@/utils/datetime";
import { autoSort } from "@/composables/useTableSort";
import { useI18n } from "vue-i18n";
import ScopeOverlapWarning from "@/components/ScopeOverlapWarning.vue";
import {
  NCard, NDataTable, NSpace, NIcon, NButton, NModal, NForm, NFormItem,
  NInput, NInputNumber, NSwitch, NCheckbox, NTabs, NTabPane, NSelect, NPopconfirm, NTag, NAlert,
  NTooltip, NEmpty,
  useMessage, type DataTableColumns,
} from "naive-ui";
import {
  FirewallIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SyncIcon, TestIcon, SaveIcon, CancelIcon,
  WarnIcon, VrfsIcon, ListIcon,
} from "@/icons";
import {
  listFirewalls, createFirewall, updateFirewall, deleteFirewall, testFirewall, syncFirewall,
  listAliasMappings, createAliasMapping, deleteAliasMapping, syncOneMapping,
  listFirewallRules, type OPNsenseRule,
  listFirewallAliases, type OPNsenseSyncedAlias,
  type OPNsenseFirewall, type OPNsenseAliasMapping,
} from "@/api/integrations";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { useCustomers } from "@/composables/useCustomers";
import { listLocations } from "@/api/basic";
import { useRoute } from "vue-router";
const { t } = useI18n();
const { options: customerOptions, ensureLoaded: ensureCustomersLoaded } = useCustomers();
const route = useRoute();
// 管理區：OPNsense 連線 + 別名對應；功能/進階區：防火牆規則 + 別名
const adminMode = computed(() => route.name === "firewall_admin");

const fwPrefs = useColumnPrefs("opnsense_fws",
  ["name", "api_url", "enabled", "verify_tls", "sync_dhcp", "sync_arp", "sync_openvpn", "sync_rules", "sync_nat", "last_sync_at", "actions"],
  ["name", "api_url", "enabled", "verify_tls", "sync_dhcp", "sync_arp", "sync_openvpn", "sync_rules", "sync_nat", "last_sync_at", "actions"]);
const fwPicker = [
  { key: "name", label: t("cols.name") }, { key: "api_url", label: "API URL" },
  { key: "enabled", label: t("cols.status") }, { key: "verify_tls", label: "Verify TLS" },
  { key: "sync_dhcp", label: "DHCP" }, { key: "sync_arp", label: "ARP" },
  { key: "sync_openvpn", label: "OpenVPN" }, { key: "sync_rules", label: "Rules" },
  { key: "sync_nat", label: "NAT" }, { key: "last_sync_at", label: t("cols.last_sync") },
  { key: "actions", label: t("cols.actions") },
];
const mapPrefs = useColumnPrefs("opnsense_maps",
  ["firewall_id", "object_type", "object_id", "alias_name", "mode", "last_synced_at", "actions"],
  ["firewall_id", "object_type", "object_id", "alias_name", "mode", "last_synced_at", "actions"]);
const mapPicker = [
  { key: "firewall_id", label: "Firewall" }, { key: "object_type", label: t("cols.object_type") },
  { key: "object_id", label: t("cols.object_id") }, { key: "alias_name", label: t("cols.alias_name") },
  { key: "mode", label: t("cols.mode") }, { key: "last_synced_at", label: t("cols.last_sync") },
  { key: "actions", label: t("cols.actions") },
];
const rulePrefs = useColumnPrefs("opnsense_rules",
  ["enabled", "sequence", "action", "interface", "direction", "protocol", "source_net", "destination_net", "description"],
  ["enabled", "sequence", "action", "interface", "direction", "protocol", "source_net", "destination_net", "description"]);
const rulePicker = [
  { key: "enabled", label: t("cols.enabled") }, { key: "sequence", label: t("cols.order") },
  { key: "action", label: t("cols.action") }, { key: "interface", label: t("cols.iface") },
  { key: "direction", label: t("cols.direction") }, { key: "protocol", label: t("cols.proto") },
  { key: "source_net", label: t("cols.source") }, { key: "destination_net", label: t("cols.destination") },
  { key: "description", label: t("cols.description") },
];
const aliasPrefs = useColumnPrefs("opnsense_aliases",
  ["name", "alias_type", "enabled", "member_count", "content", "description"],
  ["name", "alias_type", "enabled", "member_count", "content", "description"]);
const aliasPicker = [
  { key: "name", label: t("common.name") }, { key: "alias_type", label: t("cols.type") },
  { key: "enabled", label: t("cols.enabled") }, { key: "member_count", label: t("firewall_admin.members") },
  { key: "content", label: t("firewall_admin.content") }, { key: "description", label: t("common.description") },
];

const msg = useMessage();
const tab = ref<"firewalls" | "mappings" | "rules" | "aliases">("firewalls");
const fws = ref<OPNsenseFirewall[]>([]);

// Rules tab
const rulesFw = ref<string | null>(null);
const rules = ref<OPNsenseRule[]>([]);
import { useTableQuickFilter } from "@/composables/useTableQuickFilter";
const { query: ruleFilterQ, filtered: rulesFiltered } = useTableQuickFilter(rules);
const rulesLoading = ref(false);
// 規則的來源/目的若是別名名稱，點擊可跳到「別名」分頁看成員內容
const ruleAliasNames = ref<Set<string>>(new Set());
// 動作 / 介面 / 方向 下拉篩選
const fAction = ref<string | null>(null);
const fIface = ref<string | null>(null);
const fDir = ref<string | null>(null);
function distinctOpts(getter: (r: OPNsenseRule) => string | null | undefined) {
  const vals = new Set<string>();
  for (const r of rules.value) { const v = getter(r); if (v) vals.add(v); }
  return [...vals].sort((a, b) => a.localeCompare(b)).map((v) => ({ label: v, value: v }));
}
const actionOpts = computed(() => distinctOpts((r) => r.action));
const ifaceOpts = computed(() => distinctOpts((r) => r.interface));
const dirOpts = computed(() => distinctOpts((r) => r.direction));
const rulesView = computed(() => rulesFiltered.value.filter((r) =>
  (!fAction.value || r.action === fAction.value) &&
  (!fIface.value || r.interface === fIface.value) &&
  (!fDir.value || r.direction === fDir.value)));
async function loadRules() {
  if (!rulesFw.value) { rules.value = []; return; }
  rulesLoading.value = true;
  fAction.value = null; fIface.value = null; fDir.value = null;
  try {
    const res = await listFirewallRules(rulesFw.value, 1);
    rules.value = res.items;
    // 順便撈該防火牆的別名名稱集合，供來源/目的判斷是否可點
    try {
      const al = await listFirewallAliases(rulesFw.value);
      ruleAliasNames.value = new Set(al.map((a) => a.name));
    } catch { ruleAliasNames.value = new Set(); }
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.network"));
  } finally {
    rulesLoading.value = false;
  }
}
function gotoAlias(name: string) {
  tab.value = "aliases";
  if (rulesFw.value) aliasesFw.value = rulesFw.value;
  aliasFilterQ.value = name;
  void loadAliases();
}
// 來源/目的 cell：是別名就 render 成可點 tag（比照 NAT 頁），否則純文字
function netCell(net: string | null, port: string | number | null) {
  const portSuffix = port ? ":" + port : "";
  if (net && ruleAliasNames.value.has(net)) {
    return h(NTag, {
      size: "small", type: "info", bordered: false,
      style: "cursor: pointer; max-width: 100%; vertical-align: middle",
      title: `@${net} — ${t("nat.alias_goto")}`,
      onClick: () => gotoAlias(net),
    }, { default: () => h("span", {
      style: "display:inline-block; max-width:120px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; vertical-align:bottom",
    }, `@${net}${portSuffix}`) });
  }
  return `${net ?? "*"}${portSuffix}`;
}
// Aliases tab（從 OPNsense 拉回的 alias 定義）
const aliasesFw = ref<string | null>(null);
const aliases = ref<OPNsenseSyncedAlias[]>([]);
const { query: aliasFilterQ, filtered: aliasesFiltered } = useTableQuickFilter(aliases);
const aliasesLoading = ref(false);
async function loadAliases() {
  if (!aliasesFw.value) { aliases.value = []; return; }
  aliasesLoading.value = true;
  try {
    aliases.value = await listFirewallAliases(aliasesFw.value);
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.network"));
  } finally {
    aliasesLoading.value = false;
  }
}
const allAliasCols = computed<DataTableColumns<OPNsenseSyncedAlias>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 160, ellipsis: { tooltip: true } },
  { title: t("cols.type"), key: "alias_type", width: 110, render: (a) => a.alias_type ?? "—" },
  { title: t("cols.enabled"), key: "enabled", width: 70,
    render: (a) => h(NTag, { type: a.enabled ? "success" : "default", size: "small" }, () => a.enabled ? "✓" : "—") },
  { title: t("firewall_admin.members"), key: "member_count", width: 90,
    sorter: (a, b) => a.member_count - b.member_count,
    render: (a) => String(a.member_count) },
  { title: t("firewall_admin.content"), key: "content", minWidth: 240, ellipsis: { tooltip: true },
    render: (a) => (a.content || []).join(", ") || "—" },
  { title: t("common.description"), key: "description", minWidth: 160, ellipsis: { tooltip: true },
    render: (a) => a.description ?? "—" },
]));
function iconAction(icon: any, label: string, onClick: () => void, type?: any) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}
const allRuleCols = computed<DataTableColumns<OPNsenseRule>>(() => autoSort([
  {
    title: t("cols.enabled"), key: "enabled", width: 60,
    render: (r) => h(NTag, { type: r.enabled ? "success" : "default", size: "small" },
      () => r.enabled ? "✓" : "—"),
  },
  { title: t("cols.order"), key: "sequence", width: 70, render: (r) => r.sequence ?? "—",
    sorter: (a, b) => (a.sequence ?? 0) - (b.sequence ?? 0) },
  {
    title: t("cols.action"), key: "action", width: 80,
    render: (r) => {
      const c = r.action === "pass" ? "success" : r.action === "block" ? "error" : "warning";
      return h(NTag, { type: c, size: "small" }, () => r.action ?? "—");
    },
  },
  { title: t("cols.iface"), key: "interface", width: 90, render: (r) => r.interface ?? "—" },
  { title: t("cols.direction"), key: "direction", width: 60, render: (r) => r.direction ?? "—" },
  { title: t("cols.proto"), key: "protocol", width: 70, render: (r) => r.protocol ?? "—" },
  { title: t("cols.source"), key: "source_net", minWidth: 140, ellipsis: { tooltip: true },
    render: (r) => netCell(r.source_net, r.source_port) },
  { title: t("cols.destination"), key: "destination_net", minWidth: 140, ellipsis: { tooltip: true },
    render: (r) => netCell(r.destination_net, r.destination_port) },
  { title: t("cols.description"), key: "description", minWidth: 200, ellipsis: { tooltip: true }, render: (r) => r.description ?? "—" },
]));
const mappings = ref<OPNsenseAliasMapping[]>([]);
const loading = ref(false);

const showFw = ref(false);
const editingFw = ref<OPNsenseFirewall | null>(null);
interface IfaceMapRow { iface: string; subnet_id: string | null }
interface FwForm {
  name: string; api_url: string; api_key: string; api_secret: string;
  verify_tls: boolean;
  sync_dhcp: boolean; sync_arp: boolean; sync_openvpn: boolean;
  sync_rules: boolean; sync_nat: boolean; sync_aliases: boolean;
  sync_interval_seconds: number;
  description: string;
  scope_location_id: string | null;
  scope_customer_id: string | null;
  scope_subnet_ids: string[];
  iface_map_rows: IfaceMapRow[];
}
function blankFwForm(): FwForm {
  return {
    name: "", api_url: "", api_key: "", api_secret: "", verify_tls: true,
    sync_dhcp: false, sync_arp: false, sync_openvpn: false,
    sync_rules: false, sync_nat: false, sync_aliases: true,
    sync_interval_seconds: 300, description: "",
    scope_location_id: null, scope_customer_id: null,
    scope_subnet_ids: [], iface_map_rows: [],
  };
}
const newFw = ref<FwForm>(blankFwForm());

function openFwCreate() {
  editingFw.value = null;
  newFw.value = blankFwForm();
  showFw.value = true;
  void loadScopeOpts();
}
function openFwEdit(r: OPNsenseFirewall) {
  editingFw.value = r;
  newFw.value = {
    name: r.name, api_url: r.api_url, api_key: "", api_secret: "",
    verify_tls: r.verify_tls,
    sync_dhcp: r.sync_dhcp, sync_arp: r.sync_arp, sync_openvpn: r.sync_openvpn,
    sync_rules: r.sync_rules, sync_nat: r.sync_nat, sync_aliases: (r as any).sync_aliases ?? true,
    sync_interval_seconds: r.sync_interval_seconds ?? 300,
    description: r.description ?? "",
    scope_location_id: r.scope_location_id ?? null,
    scope_customer_id: r.scope_customer_id ?? null,
    scope_subnet_ids: r.scope_subnet_ids ? [...r.scope_subnet_ids] : [],
    iface_map_rows: r.iface_subnet_map
      ? Object.entries(r.iface_subnet_map).map(([iface, subnet_id]) => ({ iface, subnet_id }))
      : [],
  };
  showFw.value = true;
  void loadScopeOpts();
}
function addIfaceRow() {
  newFw.value.iface_map_rows.push({ iface: "", subnet_id: null });
}
function removeIfaceRow(idx: number) {
  newFw.value.iface_map_rows.splice(idx, 1);
}
const showMapCreate = ref(false);
const newMap = ref({
  firewall_id: "", alias_name: "", alias_type: "host",
  selector_type: "section" as "section" | "subnet" | "tag" | "custom_field",
  selector_section_id: "" as string,
  selector_subnet_id: "" as string,
  selector_tag: "",
  selector_field: "",
  selector_value: "",
  direction: "push" as "push" | "pull" | "both",
});

import { listSections } from "@/api/sections";
import { listSubnets } from "@/api/subnets";
import { useTablePagination } from "@/composables/useTablePagination";
const pg = useTablePagination();
const sectionOpts = ref<{ label: string; value: string }[]>([]);
const subnetOpts = ref<{ label: string; value: string }[]>([]);

async function loadAliasSelectorOpts() {
  try {
    const [secs, subs] = await Promise.all([listSections(1, 200), listSubnets({ page: 1, pageSize: 500 })]);
    sectionOpts.value = secs.items.map((s) => ({ label: s.name, value: s.id }));
    subnetOpts.value = subs.items.map((s: any) => ({
      label: `${s.cidr}${s.description ? ' — ' + s.description : ''}`, value: s.id,
    }));
  } catch {}
}

// 關聯範圍（NAT 對應）用的選項
const locationOpts = ref<{ label: string; value: string }[]>([]);
async function loadScopeOpts() {
  try {
    void ensureCustomersLoaded();
    const tasks: Promise<unknown>[] = [];
    if (!locationOpts.value.length) {
      tasks.push(
        listLocations().then((locs) => {
          locationOpts.value = locs.items.map((l) => ({ label: l.name, value: l.id }));
        }),
      );
    }
    if (!subnetOpts.value.length) tasks.push(loadAliasSelectorOpts());
    await Promise.all(tasks);
  } catch {}
}

const fwOptions = computed(() => fws.value.map((f) => ({ label: f.name, value: f.id })));
const insecureFws = computed(() => fws.value.filter((f) => !f.verify_tls));

async function refresh() {
  loading.value = true;
  try {
    const [f, m] = await Promise.all([listFirewalls(200, 0), listAliasMappings()]);
    fws.value = f.items;
    mappings.value = m.items;
    // 自動選第一台防火牆，別名 / 規則分頁不必再手動選就有資料
    if (f.items.length) {
      if (!aliasesFw.value) { aliasesFw.value = f.items[0].id; void loadAliases(); }
      if (!rulesFw.value) { rulesFw.value = f.items[0].id; void loadRules(); }
    }
  } catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}
function scopePayload() {
  const ifaceMap: Record<string, string> = {};
  for (const row of newFw.value.iface_map_rows) {
    const k = row.iface.trim();
    if (k && row.subnet_id) ifaceMap[k] = row.subnet_id;
  }
  return {
    scope_location_id: newFw.value.scope_location_id || null,
    scope_customer_id: newFw.value.scope_customer_id || null,
    scope_subnet_ids: newFw.value.scope_subnet_ids.length ? newFw.value.scope_subnet_ids : null,
    iface_subnet_map: Object.keys(ifaceMap).length ? ifaceMap : null,
  };
}
async function submitFw() {
  try {
    if (editingFw.value) {
      const payload: any = {
        name: newFw.value.name,
        api_url: newFw.value.api_url,
        verify_tls: newFw.value.verify_tls,
        sync_dhcp: newFw.value.sync_dhcp,
        sync_arp: newFw.value.sync_arp,
        sync_openvpn: newFw.value.sync_openvpn,
        sync_rules: newFw.value.sync_rules,
        sync_nat: newFw.value.sync_nat,
        sync_aliases: newFw.value.sync_aliases,
        sync_interval_seconds: newFw.value.sync_interval_seconds,
        description: newFw.value.description || undefined,
        ...scopePayload(),
      };
      // 只在使用者輸入新憑證時才送 — backend 要 key+secret 同時送
      if (newFw.value.api_key && newFw.value.api_secret) {
        payload.api_key = newFw.value.api_key;
        payload.api_secret = newFw.value.api_secret;
      }
      await updateFirewall(editingFw.value.id, payload);
    } else {
      await createFirewall({
        name: newFw.value.name, api_url: newFw.value.api_url,
        api_key: newFw.value.api_key, api_secret: newFw.value.api_secret,
        verify_tls: newFw.value.verify_tls,
        sync_dhcp: newFw.value.sync_dhcp,
        sync_arp: newFw.value.sync_arp,
        sync_openvpn: newFw.value.sync_openvpn,
        sync_rules: newFw.value.sync_rules,
        sync_nat: newFw.value.sync_nat,
        sync_aliases: newFw.value.sync_aliases,
        sync_interval_seconds: newFw.value.sync_interval_seconds,
        description: newFw.value.description || undefined,
        ...scopePayload(),
      } as any);
    }
    showFw.value = false;
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function submitMap() {
  try {
    const sel: Record<string, unknown> = { type: newMap.value.selector_type };
    if (newMap.value.selector_type === "section") {
      sel.section_id = newMap.value.selector_section_id;
    } else if (newMap.value.selector_type === "subnet") {
      sel.subnet_id = newMap.value.selector_subnet_id;
    } else if (newMap.value.selector_type === "tag") {
      sel.tag = newMap.value.selector_tag;
    } else if (newMap.value.selector_type === "custom_field") {
      sel.field = newMap.value.selector_field;
      sel.value = newMap.value.selector_value;
    }
    await createAliasMapping({
      firewall_id: newMap.value.firewall_id,
      alias_name: newMap.value.alias_name,
      alias_type: newMap.value.alias_type,
      selector: sel,
      direction: newMap.value.direction,
    });
    showMapCreate.value = false;
    newMap.value = { firewall_id: "", alias_name: "", alias_type: "host",
      selector_type: "section", selector_section_id: "", selector_subnet_id: "",
      selector_tag: "", selector_field: "", selector_value: "",
      direction: "push" };
    await refresh();
  } catch (e: any) { msg.error(e?.message ?? e?.response?.data?.detail ?? t("errors.server")); }
}
function fmtSyncSummary(r: any): string {
  // 嘗試從 sync 結果撈幾個常見欄位；找不到就 fallback 通用字
  if (!r || typeof r !== "object") return t("common.ok");
  const parts: string[] = [];
  if (typeof r.inserted === "number") parts.push(t("common.added_n", { n: r.inserted }));
  if (typeof r.updated === "number") parts.push(t("common.updated_n", { n: r.updated }));
  if (typeof r.skipped === "number") parts.push(t("common.skipped_n", { n: r.skipped }));
  if (typeof r.errored === "number" && r.errored > 0) parts.push(t("common.failed_n", { n: r.errored }));
  if (Array.isArray(r.results) && r.results.length === 0) return t("common.ok");
  return parts.length ? parts.join("、") : t("common.ok");
}
async function testFw(id: string) {
  try { await testFirewall(id); msg.success(t("common.ok")); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function syncFw(id: string) {
  const row = fws.value.find((r) => r.id === id);
  const targetName = row?.name ?? id.slice(0, 8);
  try {
    await syncFirewall(id);
    // 後端立刻回 task_id，sync 在背景跑
    msg.success(t("tasks.queued_toast", { kind: "OPNsense sync", target: targetName }));
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function delFw(id: string) {
  try { await deleteFirewall(id); await refresh(); } catch { msg.error(t("errors.server")); }
}
async function syncMap(id: string) {
  try { const r = await syncOneMapping(id); msg.success(fmtSyncSummary(r)); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function delMap(id: string) {
  try { await deleteAliasMapping(id); await refresh(); } catch { msg.error(t("errors.server")); }
}

const allFwCols = computed<DataTableColumns<OPNsenseFirewall>>(() => autoSort([
  { title: t("firewall_admin.name"), key: "name", minWidth: 160, ellipsis: { tooltip: true } },
  { title: "API URL", key: "api_url", minWidth: 200, ellipsis: { tooltip: true } },
  {
    title: "TLS", key: "verify_tls", width: 120,
    render: (r) => r.verify_tls
      ? h(NTag, { size: "small", type: "success" }, () => t("firewall_admin.tls_verified"))
      : h(NTooltip, null, {
          trigger: () => h(NSpace, { size: 4, align: "center", "wrap-item": false }, () => [
            h(NIcon, { size: 16, color: "#d03050" }, () => h(WarnIcon)),
            h(NTag, { size: "small", type: "error", bordered: false },
              () => t("firewall_admin.tls_skip")),
          ]),
          default: () => t("firewall_admin.tls_skip_warning"),
        }),
  },
  {
    title: t("cols.last_sync"), key: "last_sync_at", width: 170,
    render: (r) => fmtDateTime(r.last_sync_at),
  },
  { title: t("cols.last_error"), key: "last_error", minWidth: 160, ellipsis: { tooltip: true }, render: (r) => r.last_error ?? "—" },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 176,
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openFwEdit(r)),
      iconAction(TestIcon, t("common.test"), () => testFw(r.id)),
      iconAction(SyncIcon, t("common.pull"), () => syncFw(r.id), "primary"),
      h(NPopconfirm, { onPositiveClick: () => delFw(r.id) },
        { trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
          default: () => t("common.confirm_delete") }),
    ]),
  },
]));
const allMapCols = computed<DataTableColumns<OPNsenseAliasMapping>>(() => autoSort([
  { title: t("firewall_admin.alias_name"), key: "alias_name", minWidth: 160, ellipsis: { tooltip: true } },
  { title: t("firewall_admin.alias_type"), key: "alias_type", width: 110 },
  {
    title: t("cols.fw"), key: "firewall_id", width: 150, ellipsis: { tooltip: true },
    render: (r) => fws.value.find((f) => f.id === r.firewall_id)?.name ?? r.firewall_id.slice(0, 8),
  },
  { title: t("firewall_admin.direction"), key: "direction", width: 110 },
  { title: t("firewall_admin.last_synced_count"), key: "last_synced_count", width: 120,
    render: (r) => r.last_synced_count ?? "—" },
  {
    title: t("cols.selector"), key: "selector", minWidth: 200, ellipsis: { tooltip: true },
    render: (r) => h("code", { style: "font-size: 11px" }, JSON.stringify(r.selector).slice(0, 60)),
  },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 96,
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(SyncIcon, t("common.sync"), () => syncMap(r.id), "primary"),
      h(NPopconfirm, { onPositiveClick: () => delMap(r.id) },
        { trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
          default: () => t("common.confirm_delete") }),
    ]),
  },
]));

const fwCols = computed<DataTableColumns<OPNsenseFirewall>>(() =>
  allFwCols.value.filter((c: any) => fwPrefs.visibleKeys.value.includes(c.key)));
const mapCols = computed<DataTableColumns<OPNsenseAliasMapping>>(() =>
  allMapCols.value.filter((c: any) => mapPrefs.visibleKeys.value.includes(c.key)));
const ruleCols = computed<DataTableColumns<OPNsenseRule>>(() =>
  allRuleCols.value.filter((c: any) => rulePrefs.visibleKeys.value.includes(c.key)));
const aliasCols = computed<DataTableColumns<OPNsenseSyncedAlias>>(() =>
  allAliasCols.value.filter((c: any) => aliasPrefs.visibleKeys.value.includes(c.key)));

onMounted(() => {
  // 預設分頁：管理區從 firewalls 起、進階區從 rules 起
  tab.value = adminMode.value ? "firewalls" : "rules";
  // 支援 ?tab=aliases&q=<alias> 直接帶到對應分頁並預填篩選（NAT 規則的 alias chip 連過來）
  const qt = route.query.tab;
  if (typeof qt === "string" && ["firewalls", "mappings", "rules", "aliases"].includes(qt)) {
    tab.value = qt as typeof tab.value;
  }
  const qq = route.query.q;
  if (typeof qq === "string" && qq) {
    if (qt === "aliases") aliasFilterQ.value = qq;
    else if (qt === "rules") ruleFilterQ.value = qq;
  }
  // ?fw=<id>：NAT alias chip 帶來該規則所屬防火牆，預選之（refresh 的 !value 守衛會保留）
  const qfw = route.query.fw;
  if (typeof qfw === "string" && qfw) {
    if (qt === "aliases") aliasesFw.value = qfw;
    else if (qt === "rules") rulesFw.value = qfw;
  }
  void refresh();
});
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><FirewallIcon /></n-icon>
        <span>{{ adminMode ? t("firewall_admin.title") : t("firewall_admin.func_title") }}</span>
      </n-space>
    </template>
    <n-tabs v-model:value="tab" type="line">
      <n-tab-pane v-if="adminMode" name="firewalls">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><FirewallIcon /></n-icon>{{ t('firewall_admin.title') }}</span>
        </template>
        <n-alert v-if="insecureFws.length" type="warning" style="margin-bottom: 12px"
                 :title="t('firewall_admin.tls_alert_title')">
          <template #icon><n-icon><WarnIcon /></n-icon></template>
          {{ t('firewall_admin.tls_alert_body', { n: insecureFws.length, names: insecureFws.map(f => f.name).join('、') }) }}
        </n-alert>
        <n-space style="margin-bottom: 12px">
          <n-button @click="refresh" :loading="loading">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>
            {{ t("common.refresh") }}
          </n-button>
          <n-button type="primary" @click="openFwCreate">
            <template #icon><n-icon><PlusIcon /></n-icon></template>
            {{ t("firewall_admin.create_firewall") }}
          </n-button>
          <ColumnPicker :all="fwPicker" :visible="fwPrefs.visibleKeys.value"
                        @update:visible="fwPrefs.setVisible" @reset="fwPrefs.reset" />
          <ExportButton :columns="fwCols" :rows="fws" filename="firewalls" :title="t('firewall_admin.title')" />
        </n-space>
        <n-data-table :columns="fwCols" :data="fws" :loading="loading" :bordered="false" :scroll-x="986" />
      </n-tab-pane>
      <n-tab-pane v-if="adminMode" name="mappings">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><VrfsIcon /></n-icon>{{ t('firewall_admin.alias_mappings') }}</span>
        </template>
        <n-alert type="info" size="small" :show-icon="true" style="margin-bottom: 12px">
          {{ t("firewall_admin.mappings_hint") }}
        </n-alert>
        <n-space style="margin-bottom: 12px">
          <n-button type="primary"
                    @click="loadAliasSelectorOpts(); showMapCreate = true">
            <template #icon><n-icon><PlusIcon /></n-icon></template>
            {{ t("firewall_admin.create_mapping") }}
          </n-button>
          <ColumnPicker :all="mapPicker" :visible="mapPrefs.visibleKeys.value"
                        @update:visible="mapPrefs.setVisible" @reset="mapPrefs.reset" />
          <ExportButton :columns="mapCols" :rows="mappings" filename="firewall-alias-mappings" :title="t('firewall_admin.alias_mappings')" />
        </n-space>
        <n-data-table :columns="mapCols" :data="mappings" :loading="loading" :bordered="false" :scroll-x="946" :pagination="pg" />
      </n-tab-pane>
      <n-tab-pane v-if="!adminMode" name="rules">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><ListIcon /></n-icon>{{ t("firewall_admin.rules") }}</span>
        </template>
        <n-alert type="info" size="small" :show-icon="true" style="margin-bottom: 12px">
          {{ t("firewall_admin.rules_hint") }}
        </n-alert>
        <n-space style="margin-bottom: 12px" align="center">
          <n-select
            v-model:value="rulesFw"
            :options="fwOptions"
            :placeholder="t('firewall_admin.pick_firewall')"
            style="width: 240px"
            @update:value="loadRules"
          />
          <n-button @click="loadRules" :loading="rulesLoading">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>
            {{ t("common.refresh") }}
          </n-button>
          <span v-if="rulesFw && rules.length" style="opacity: 0.7;">
            {{ t("firewall_admin.rules_count", { n: rules.length }) }}
          </span>
          <n-input v-model:value="ruleFilterQ" :placeholder="t('common.filter')" clearable style="width: 160px" />
          <n-select v-model:value="fAction" :options="actionOpts" clearable
                    :placeholder="t('cols.action')" style="width: 120px" />
          <n-select v-model:value="fIface" :options="ifaceOpts" clearable
                    :placeholder="t('cols.iface')" style="width: 120px" />
          <n-select v-model:value="fDir" :options="dirOpts" clearable
                    :placeholder="t('cols.direction')" style="width: 110px" />
          <ColumnPicker :all="rulePicker" :visible="rulePrefs.visibleKeys.value"
                        @update:visible="rulePrefs.setVisible" @reset="rulePrefs.reset" />
          <ExportButton :columns="ruleCols" :rows="rulesView" filename="firewall-rules" :title="t('firewall_admin.rules')" />
        </n-space>
        <n-data-table
          v-if="rulesFw"
          :columns="ruleCols" :data="rulesView" :loading="rulesLoading"
          :bordered="false" size="small" :scroll-x="910"
          :pagination="pg"
        />
        <n-empty v-else :description="t('firewall_admin.pick_firewall_to_view')" />
      </n-tab-pane>

      <n-tab-pane v-if="!adminMode" name="aliases">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><ListIcon /></n-icon>{{ t("firewall_admin.aliases") }}</span>
        </template>
        <n-alert type="info" size="small" :show-icon="true" style="margin-bottom: 12px">
          {{ t("firewall_admin.aliases_hint") }}
        </n-alert>
        <n-space style="margin-bottom: 12px" align="center">
          <n-select
            v-model:value="aliasesFw"
            :options="fwOptions"
            :placeholder="t('firewall_admin.pick_firewall')"
            style="width: 240px"
            @update:value="loadAliases"
          />
          <n-button @click="loadAliases" :loading="aliasesLoading">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>
            {{ t("common.refresh") }}
          </n-button>
          <span v-if="aliasesFw && aliases.length" style="opacity: 0.7;">
            {{ t("common.total_n", { n: aliases.length }) }}
          </span>
          <n-input v-model:value="aliasFilterQ" :placeholder="t('common.filter')" clearable style="width: 160px" />
          <ColumnPicker :all="aliasPicker" :visible="aliasPrefs.visibleKeys.value"
                        @update:visible="aliasPrefs.setVisible" @reset="aliasPrefs.reset" />
          <ExportButton :columns="aliasCols" :rows="aliasesFiltered" filename="firewall-aliases" :title="t('firewall_admin.aliases')" />
        </n-space>
        <n-data-table
          v-if="aliasesFw"
          :columns="aliasCols" :data="aliasesFiltered" :loading="aliasesLoading"
          :bordered="false" size="small" :scroll-x="860"
          :pagination="pg"
        />
        <n-empty v-else :description="t('firewall_admin.pick_firewall_to_view')" />
      </n-tab-pane>
    </n-tabs>

    <n-modal v-model:show="showFw" preset="card"
             :title="editingFw ? t('common.edit') : t('firewall_admin.create_firewall')"
             style="width: 480px">
      <n-form>
        <n-alert type="info" :bordered="false" :show-icon="true" style="margin-bottom: 12px">
          {{ t("firewall_admin.version_hint") }}
        </n-alert>
        <n-form-item :label="t('firewall_admin.name')"><n-input v-model:value="newFw.name" /></n-form-item>
        <n-form-item :label="t('firewall_admin.api_url')">
          <n-input v-model:value="newFw.api_url"
                   :placeholder="t('firewall_admin.url_ph')" />
        </n-form-item>
        <n-form-item :label="`API key${editingFw ? ' (' + t('users.password_blank_unchanged') + ')' : ''}`">
          <n-input v-model:value="newFw.api_key" />
        </n-form-item>
        <n-form-item :label="`API secret${editingFw ? ' (' + t('users.password_blank_unchanged') + ')' : ''}`">
          <n-input v-model:value="newFw.api_secret" type="password" show-password-on="click" />
        </n-form-item>
        <n-form-item :label="t('firewall_admin.verify_tls')">
          <n-switch v-model:value="newFw.verify_tls" />
          <template #feedback>
            <span v-if="!newFw.verify_tls" style="color: #d03050">
              {{ t('firewall_admin.tls_skip_warning') }}
            </span>
            <span v-else style="opacity: 0.7">
              {{ t('firewall_admin.tls_verified_hint') }}
            </span>
          </template>
        </n-form-item>
        <n-form-item :label="t('firewall_admin.sync_interval')">
          <n-input-number v-model:value="newFw.sync_interval_seconds" :min="30" :max="86400" />
          <template #feedback>
            <span style="opacity: 0.7; font-size: 12px;">
              {{ t("firewall_admin.sync_interval_hint") }}
            </span>
          </template>
        </n-form-item>
        <n-form-item :label="t('firewall_admin.sync_sources')">
          <n-space :size="20" wrap>
            <n-checkbox v-model:checked="newFw.sync_dhcp">{{ t("firewall_admin.sync_dhcp_label") }}</n-checkbox>
            <n-checkbox v-model:checked="newFw.sync_arp">{{ t("firewall_admin.sync_arp_label") }}</n-checkbox>
            <n-checkbox v-model:checked="newFw.sync_openvpn">{{ t("firewall_admin.sync_openvpn_label") }}</n-checkbox>
            <n-checkbox v-model:checked="newFw.sync_rules">{{ t("firewall_admin.sync_filter_rules") }}</n-checkbox>
            <n-checkbox v-model:checked="newFw.sync_nat">{{ t("firewall_admin.sync_nat_rules") }}</n-checkbox>
            <n-checkbox v-model:checked="newFw.sync_aliases">{{ t("firewall_admin.sync_aliases_label") }}</n-checkbox>
          </n-space>
          <template #feedback>
            <span style="opacity: 0.7; font-size: 12px;">
              {{ t("firewall_admin.sync_sources_hint") }}
            </span>
          </template>
        </n-form-item>
        <n-form-item :label="t('firewall.scope_title')" style="margin-top: 14px; border-top: 1px solid var(--n-border-color, rgba(128,128,128,0.18)); padding-top: 16px;">
          <n-space vertical :size="10" style="width: 100%">
            <span style="opacity: 0.7; font-size: 12px;">{{ t("firewall.scope_hint") }}</span>
            <div>
              <div style="font-size: 12px; opacity: 0.8; margin-bottom: 2px;">{{ t("firewall.scope_location") }}</div>
              <n-select v-model:value="newFw.scope_location_id" :options="locationOpts"
                        clearable filterable :placeholder="t('firewall.scope_location')" />
            </div>
            <div>
              <div style="font-size: 12px; opacity: 0.8; margin-bottom: 2px;">{{ t("firewall.scope_customer") }}</div>
              <n-select v-model:value="newFw.scope_customer_id" :options="customerOptions"
                        clearable filterable :placeholder="t('firewall.scope_customer')" />
            </div>
            <div>
              <div style="font-size: 12px; opacity: 0.8; margin-bottom: 2px;">{{ t("firewall.scope_subnets") }}</div>
              <n-select v-model:value="newFw.scope_subnet_ids" :options="subnetOpts"
                        multiple clearable filterable :placeholder="t('firewall.scope_subnets')" />
              <ScopeOverlapWarning :scope-empty="!newFw.scope_subnet_ids?.length" />
            </div>
            <div>
              <div style="font-size: 12px; opacity: 0.8; margin-bottom: 2px;">{{ t("firewall.scope_iface_map") }}</div>
              <n-space v-for="(row, idx) in newFw.iface_map_rows" :key="idx" :size="8" align="center"
                       style="margin-bottom: 6px;">
                <n-input v-model:value="row.iface" style="width: 140px;" placeholder="LAN / opt1" />
                <n-select v-model:value="row.subnet_id" :options="subnetOpts" clearable filterable
                          style="width: 280px;" :placeholder="t('firewall.scope_subnets')" />
                <n-button quaternary size="small" @click="removeIfaceRow(idx)">
                  <template #icon><n-icon><DeleteIcon /></n-icon></template>
                </n-button>
              </n-space>
              <n-button size="small" dashed @click="addIfaceRow">
                <template #icon><n-icon><PlusIcon /></n-icon></template>
                {{ t("common.add") }}
              </n-button>
            </div>
          </n-space>
        </n-form-item>
        <n-form-item :label="t('common.description')">
          <n-input v-model:value="newFw.description" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="showFw = false">
          <template #icon><n-icon><CancelIcon /></n-icon></template>
          {{ t("common.cancel") }}
        </n-button>
        <n-button type="primary" @click="submitFw">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </n-space>
    </n-modal>

    <n-modal v-model:show="showMapCreate" preset="card" :title="t('firewall_admin.create_mapping')"
             style="width: 520px">
      <n-form>
        <n-form-item label="Firewall">
          <n-select v-model:value="newMap.firewall_id" :options="fwOptions" />
        </n-form-item>
        <n-form-item :label="t('firewall_admin.alias_name')">
          <n-input v-model:value="newMap.alias_name" placeholder="jt_section_addrs" />
        </n-form-item>
        <n-form-item :label="t('firewall_admin.alias_type')">
          <n-select v-model:value="newMap.alias_type"
                    :options="['host','network','port','url','urltable','geoip','networkgroup','mac','asn'].map(v => ({label: v, value: v}))" />
        </n-form-item>
        <n-form-item :label="t('firewall_admin.selector_type')">
          <n-select v-model:value="newMap.selector_type"
                    :options="[
                      {label: 'Section', value: 'section'},
                      {label: 'Subnet', value: 'subnet'},
                      {label: 'Tag', value: 'tag'},
                      {label: 'Custom field', value: 'custom_field'},
                    ]" />
        </n-form-item>
        <n-form-item v-if="newMap.selector_type === 'section'" label="Section">
          <n-select v-model:value="newMap.selector_section_id" :options="sectionOpts" filterable />
        </n-form-item>
        <n-form-item v-else-if="newMap.selector_type === 'subnet'" label="Subnet">
          <n-select v-model:value="newMap.selector_subnet_id" :options="subnetOpts" filterable />
        </n-form-item>
        <n-form-item v-else-if="newMap.selector_type === 'tag'" label="Tag">
          <n-input v-model:value="newMap.selector_tag" placeholder="wifi-guest" />
        </n-form-item>
        <template v-else>
          <n-form-item label="Custom field name">
            <n-input v-model:value="newMap.selector_field" placeholder="role" />
          </n-form-item>
          <n-form-item label="Value">
            <n-input v-model:value="newMap.selector_value" placeholder="monitoring" />
          </n-form-item>
        </template>
        <n-form-item :label="t('firewall_admin.direction')">
          <n-select v-model:value="newMap.direction"
                    :options="[{label:'push',value:'push'},{label:'pull',value:'pull'},{label:'both',value:'both'}]" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="showMapCreate = false">{{ t("common.cancel") }}</n-button>
        <n-button type="primary" @click="submitMap">{{ t("common.save") }}</n-button>
      </n-space>
    </n-modal>
  </n-card>
</template>
