<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import {
  NCard,
  NSpace,
  NIcon,
  NDescriptions,
  NDescriptionsItem,
  NProgress,
  NSpin,
  NButton,
  NCheckbox,
  NAlert,
  NDataTable,
  NTag,
  NUpload,
  NPopover,
  NPopconfirm,
  NTooltip,
  NModal,
  NForm,
  NFormItem,
  NInput,
  NSelect,
  NSwitch,
  NInputNumber,
  NSlider,
  NDropdown,
  type UploadCustomRequestOptions,
  type DataTableColumns,
  useMessage,
} from "naive-ui";
import { updateSubnet } from "@/api/subnets";
import { EditIcon, SaveIcon, CancelIcon } from "@/icons";
import { fmtDateTime } from "@/utils/datetime";
import { autoSort } from "@/composables/useTableSort";
import { useCustomers } from "@/composables/useCustomers";
import { usePinnedSubnets } from "@/composables/usePinnedSubnets";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import ColumnPicker from "@/components/ColumnPicker.vue";
import SwitchPortLabel from "@/components/SwitchPortLabel.vue";
const { t } = useI18n();

const { labelFor: customerLabelFor, options: customerOptions, ensureLoaded: ensureCustomersLoaded } = useCustomers();

// 子網路編輯
const editShow = ref(false);
const editSaving = ref(false);
const editForm = ref({
  description: "", customer_id: null as string | null,
  is_pool: false, is_full: false, scan_enabled: false,
  threshold_pct: null as number | null,
  scan_agent_id: null as string | null,
  gateway: "" as string, dns_servers: "" as string,
  location_id: null as string | null,
});
const scanAgentOpts = ref<{ label: string; value: string }[]>([]);
const locationOpts = ref<{ label: string; value: string }[]>([]);
async function loadScanAgentOpts() {
  try {
    const { listScanAgents } = await import("@/api/phase3");
    const ag = await listScanAgents();
    scanAgentOpts.value = ag.items.map((a) => ({ label: a.name, value: a.id }));
  } catch { /* silent */ }
}
async function loadLocationOpts() {
  try {
    const { listLocations } = await import("@/api/basic");
    const r = await listLocations();
    locationOpts.value = r.items.map((l) => ({ label: l.name, value: l.id }));
  } catch { /* silent */ }
}
function openSubnetEdit() {
  const s: any = subnet.value;
  if (!s) return;
  editForm.value = {
    description: s.description ?? "",
    customer_id: s.customer_id ?? null,
    is_pool: !!s.is_pool, is_full: !!s.is_full,
    scan_enabled: !!s.scan_enabled,
    threshold_pct: s.threshold_pct ?? null,
    scan_agent_id: s.scan_agent_id ?? null,
    gateway: s.gateway ?? "", dns_servers: s.dns_servers ?? "",
    location_id: s.location_id ?? null,
  };
  void loadScanAgentOpts();
  void loadLocationOpts();
  editShow.value = true;
}
async function saveSubnetEdit() {
  if (!subnet.value) return;
  editSaving.value = true;
  try {
    await updateSubnet(subnet.value.id, {
      description: editForm.value.description || null,
      customer_id: editForm.value.customer_id ?? null,
      is_pool: editForm.value.is_pool, is_full: editForm.value.is_full,
      scan_enabled: editForm.value.scan_enabled,
      threshold_pct: editForm.value.threshold_pct ?? null,
      scan_agent_id: editForm.value.scan_agent_id ?? null,
      gateway: editForm.value.gateway.trim() || null,
      dns_servers: editForm.value.dns_servers.trim() || null,
      location_id: editForm.value.location_id ?? null,
    });
    editShow.value = false;
    await load(subnet.value.id);
    msg.success(t("common.ok"));
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.server"));
  } finally { editSaving.value = false; }
}
const { isPinned, toggle: togglePinned, ensureLoaded: ensurePinsLoaded } = usePinnedSubnets();

const { visibleKeys: ipVisibleKeys, setVisible: setIpVisible, reset: resetIpVisible } = useColumnPrefs(
  "subnet_detail_ips",
  ["live", "ip", "hostname", "state", "dhcp", "mac", "mac_vendor", "owner", "switch_port", "description", "last_seen", "stale_days", "note"],
  ["live", "ip", "hostname", "state", "dhcp", "mac", "mac_vendor", "switch_port", "description", "last_seen"],
);
const ipColumnPickerItems = [
  { key: "live", label: t("cols.live") },
  { key: "ip", label: "IP" },
  { key: "hostname", label: t("cols.hostname") },
  { key: "state", label: t("cols.status") },
  { key: "dhcp", label: "DHCP" },
  { key: "mac", label: "MAC" },
  { key: "mac_vendor", label: t("cols.vendor") },
  { key: "owner", label: t("cols.owner") },
  { key: "switch_port", label: t("cols.switch_port") },
  { key: "description", label: t("cols.description") },
  { key: "last_seen", label: t("cols.last_seen") },
  { key: "stale_days", label: t("stale.col_stale") },
  { key: "note", label: t("cols.note") },
];
import { SubnetsIcon, RefreshIcon, UsageIcon, GridIcon, ListIcon, PinIcon, PlusIcon } from "@/icons";
import { ArrowLeft as ArrowLeftIcon } from "@iconoir/vue";
import { apiClient } from "@/api/client";
import { listAddresses } from "@/api/addresses";
import { listDhcpRanges } from "@/api/integrations";
import { getSubnetUsage } from "@/api/subnets";
import { getSection } from "@/api/sections";
import { listVLANs, listVRFs, type VLAN, type VRF } from "@/api/basic";
import SubnetGrid from "@/components/SubnetGrid.vue";
import IPAddressEditModal from "@/components/IPAddressEditModal.vue";
import LiveStatusDot from "@/components/LiveStatusDot.vue";
import type { IPAddress, Section, Subnet, SubnetUsage } from "@/types";

const route = useRoute();
const router = useRouter();
const msg = useMessage();

const subnet = ref<Subnet | null>(null);
const usage = ref<SubnetUsage | null>(null);
const addresses = ref<IPAddress[]>([]);
const loading = ref(false);

// ── DHCP 發放範圍：標示落在 pool 內的 IP（多段都涵蓋）──
const dhcpRanges = ref<[number, number][]>([]);
function ipv4ToInt(ip: string): number | null {
  const m = ip.trim().split(".");
  if (m.length !== 4) return null;
  let n = 0;
  for (const p of m) {
    const o = Number(p);
    if (!Number.isInteger(o) || o < 0 || o > 255) return null;
    n = n * 256 + o;
  }
  return n >>> 0;
}
async function loadDhcpRanges() {
  try {
    const rows = await listDhcpRanges();
    const out: [number, number][] = [];
    for (const r of rows) {
      const a = ipv4ToInt(r.start_ip), b = ipv4ToInt(r.end_ip);
      if (a != null && b != null) out.push([Math.min(a, b), Math.max(a, b)]);
    }
    dhcpRanges.value = out;
  } catch { dhcpRanges.value = []; }
}
function isDhcpIp(ip: string | null | undefined): boolean {
  if (!ip) return false;
  const n = ipv4ToInt(String(ip).split("/")[0]);
  if (n == null) return false;
  return dhcpRanges.value.some(([a, b]) => n >= a && n <= b);
}

const section = ref<Section | null>(null);
const vlan = ref<VLAN | null>(null);
const vrf = ref<VRF | null>(null);
const masterSubnet = ref<Subnet | null>(null);

const dryRun = ref(true);
const importBusy = ref(false);
const importResult = ref<Record<string, unknown> | null>(null);

const selected = ref<IPAddress | null>(null);
const modalShow = ref(false);
const createCtx = ref<{ subnet_id: string; ip: string } | null>(null);

function onGridOpen(a: IPAddress) {
  void router.push({ name: "address-detail", params: { id: a.id } });
}

function onGridCreate(ip: string) {
  if (!subnet.value) return;
  selected.value = null;
  createCtx.value = { subnet_id: subnet.value.id, ip };
  modalShow.value = true;
}

// 新增位址（IP 清單右上）：開新增 modal，IP 留空讓使用者填或取第一個空位
function onAddAddress() {
  if (!subnet.value) return;
  selected.value = null;
  createCtx.value = { subnet_id: subnet.value.id, ip: "" };
  modalShow.value = true;
}

// 新增下層子網路：帶著本網段的區段去子網路頁開新增（CIDR 落在本網段內會自動歸為下層）
function addChildSubnet() {
  if (!subnet.value) return;
  router.push({ name: "subnets", query: { create: "1", section: subnet.value.section_id } });
}

function onCreated(created: IPAddress) {
  addresses.value = [...addresses.value, created];
  createCtx.value = null;
}

async function load(id: string) {
  loading.value = true;
  try {
    const [s, u, a] = await Promise.all([
      apiClient.get<Subnet>(`/api/v1/subnets/${id}`).then((r) => r.data),
      getSubnetUsage(id),
      listAddresses({ subnetId: id, page: 1, pageSize: 1000 }),
    ]);
    subnet.value = s;
    usage.value = u;
    addresses.value = a.items;

    // 解析名稱：section 必載；vlan/vrf/master_subnet 視情況
    const tasks: Promise<unknown>[] = [];
    tasks.push(
      getSection(s.section_id)
        .then((sec) => { section.value = sec; })
        .catch(() => { section.value = null; }),
    );
    if (s.vlan_id) {
      tasks.push(
        listVLANs().then((res) => {
          vlan.value = res.items.find((v) => v.id === s.vlan_id) ?? null;
        }).catch(() => { vlan.value = null; }),
      );
    } else { vlan.value = null; }
    if (s.vrf_id) {
      tasks.push(
        listVRFs().then((res) => {
          vrf.value = res.items.find((v) => v.id === s.vrf_id) ?? null;
        }).catch(() => { vrf.value = null; }),
      );
    } else { vrf.value = null; }
    if (s.master_subnet_id) {
      tasks.push(
        apiClient.get<Subnet>(`/api/v1/subnets/${s.master_subnet_id}`)
          .then((r) => { masterSubnet.value = r.data; })
          .catch(() => { masterSubnet.value = null; }),
      );
    } else { masterSubnet.value = null; }
    await Promise.all(tasks);
  } catch {
    msg.error(t("errors.network"));
  } finally {
    loading.value = false;
  }
}

async function handleExport() {
  if (!subnet.value) return;
  try {
    const resp = await apiClient.get("/api/v1/addresses/export.csv", {
      params: { subnet_id: subnet.value.id },
      responseType: "blob",
    });
    const blob = new Blob([resp.data], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `addresses-${subnet.value.cidr.replace("/", "_")}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  } catch {
    msg.error(t("errors.network"));
  }
}

async function uploadCsv(opts: UploadCustomRequestOptions) {
  const { file } = opts;
  if (!subnet.value) return;
  if (!file.file) {
    opts.onError();
    return;
  }
  importBusy.value = true;
  importResult.value = null;
  try {
    const form = new FormData();
    form.append("subnet_id", subnet.value.id);
    form.append("file", file.file as Blob, file.name);
    form.append("dry_run", String(dryRun.value));
    const resp = await apiClient.post("/api/v1/addresses/import", form, {
      headers: { "Content-Type": "multipart/form-data" },
    });
    importResult.value = resp.data;
    if (!dryRun.value) {
      // 實際匯入改走背景作業 → 回 task_id，到「作業」頁看進度
      msg.success(t("csv_import.queued"));
    } else {
      msg.info(t("csv_import.dry_run_preview", { n: resp.data.preview?.length ?? 0 }));
    }
    opts.onFinish();
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("csv_import.import_failed"));
    opts.onError();
  } finally {
    importBusy.value = false;
  }
}

// ── IP list table ──
function lastSeen(r: IPAddress): string {
  const arr = [r.last_seen_scanner, r.last_seen_librenms, r.last_seen_dns].filter(Boolean) as string[];
  if (!arr.length) return "—";
  const max = arr.sort().reverse()[0];
  return max.replace("T", " ").split(".")[0];
}

// liveDot 改用共用組件 LiveStatusDot(hover 即時 tooltip)
function liveDot(r: IPAddress) {
  return h(LiveStatusDot, { address: r });
}

function stateTag(state: string) {
  const map: Record<string, "success" | "warning" | "error" | "default" | "info"> = {
    active: "success", reserved: "info", offline: "error", dhcp: "warning", used: "default",
  };
  const key = `addresses.state_${state}`;
  const label = t(key) === key ? state : t(key);
  return h(NTag, { type: map[state] ?? "default", size: "small" }, () => label);
}

// 閒置區間列：IP 欄要橫跨「ip 之後的所有可見欄位」，文字才不會被切在一欄裡。
const IP_COL_ORDER = ["live", "ip", "hostname", "state", "dhcp", "mac", "mac_vendor",
  "owner", "switch_port", "description", "last_seen", "stale_days", "note"];
const gapSpan = computed(() => {
  const vis = IP_COL_ORDER.filter((k) => ipVisibleKeys.value.includes(k));
  const i = vis.indexOf("ip");
  return i < 0 ? 1 : vis.length - i;   // ip 自己 + 後面所有可見欄
});

const allIpColumns = computed<DataTableColumns<IPAddress>>(() => autoSort([
  { type: "selection", disabled: (r: any) => !!r.__gap },
  { title: "", key: "live", width: 28, render: (r) => (r as any).__gap ? "" : liveDot(r) },
  { title: t("addresses.ip"), key: "ip", width: 140, sorter: (a, b) => ipSort(a.ip, b.ip),
    colSpan: (r: any) => r.__gap ? gapSpan.value : 1,
    render: (r) => (r as any).__gap
      ? h("div", { style: "text-align: center; color: var(--n-text-color-3, #999); font-style: italic" }, gapLabel(r))
      : r.ip },
  { title: t("addresses.hostname"), key: "hostname", minWidth: 140,
    ellipsis: { tooltip: true }, render: (r) => (r as any).__gap ? "" : (r.hostname ?? "") },
  { title: t("common.status"), key: "state", width: 100,
    render: (r) => (r as any).__gap ? "" : stateTag(r.state) },
  { title: "DHCP", key: "dhcp", width: 80,
    render: (r) => (r as any).__gap || !isDhcpIp(r.ip)
      ? ""
      : h(NTag, { size: "tiny", type: "warning", bordered: false }, { default: () => "DHCP" }) },
  { title: t("addresses.mac"), key: "mac", width: 150, render: (r) => r.mac ?? "" },
  { title: t("cols.vendor"), key: "mac_vendor", width: 140,
    ellipsis: { tooltip: true }, render: (r) => r.mac_vendor ?? "—" },
  { title: t("addresses.owner"), key: "owner", width: 120,
    ellipsis: { tooltip: true }, render: (r) => r.owner ?? "" },
  { title: t("addresses.switch_port"), key: "switch_port", width: 160,
    ellipsis: { tooltip: false },   // 裁切但不開 cell tooltip，否則會跟下方 NTooltip 疊成兩個彈框
    render: (r) => !r.switch_port ? ""
      : h(NTooltip, null, {
          trigger: () => h(SwitchPortLabel, { value: r.switch_port, dim: r.switch_port_confident === false }),
          default: () => r.switch_port_confident === false
            ? t("addresses.switch_port_uncertain")
            : r.switch_port }) },
  { title: t("common.description"), key: "description", width: 200,
    ellipsis: { tooltip: true }, render: (r) => r.description ?? "" },
  { title: t("addresses.last_seen"), key: "last_seen", width: 170, render: (r) => lastSeen(r) },
  { title: t("stale.col_stale"), key: "stale_days", width: 110,
    sorter: (a: any, b: any) => {
      if (a.__gap || b.__gap) return 0;
      const da = isProbed(a) ? (staleDays(a) ?? Number.MAX_SAFE_INTEGER) : -1;
      const db = isProbed(b) ? (staleDays(b) ?? Number.MAX_SAFE_INTEGER) : -1;
      return da - db;
    },
    render: (r) => (r as any).__gap ? "" : staleDaysLabel(r) },
  { title: t("addresses.note"), key: "note", width: 220,
    ellipsis: { tooltip: true }, render: (r) => r.note ?? "" },
]));

const ipColumns = computed<DataTableColumns<IPAddress>>(() =>
  allIpColumns.value.filter((c: any) => c.type === "selection" || ipVisibleKeys.value.includes(c.key)),
);

// IP 清單複選 + 批次刪除（閒置區間列不可選）
const checkedIps = ref<Array<string | number>>([]);
const ipBulkBusy = ref(false);
async function bulkDeleteIps() {
  const ids = checkedIps.value.map(String).filter((k) => !k.startsWith("gap:"));
  if (!ids.length) return;
  ipBulkBusy.value = true;
  try {
    const { bulkDeleteAddresses } = await import("@/api/addresses");
    const res = await bulkDeleteAddresses(ids);
    if (res.failed) msg.warning(t("common.deleted_failed", { deleted: res.deleted, failed: res.failed }));
    else msg.success(t("common.deleted_n", { n: res.deleted }));
    checkedIps.value = [];
    if (subnet.value) await load(subnet.value.id);
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
  finally { ipBulkBusy.value = false; }
}

// ── 失聯 IP 篩選 ──
// 失聯天數 = now − max(last_seen_*)；null = 從未上線。不偵測的 IP(排除 ping / 子網路未掃描) 不納入失聯判定。
const STALE_PRESETS = [7, 14, 30, 60, 90];
const staleFilterOn = ref(false);
const staleThreshold = ref(30);          // 天
const staleNeverOnly = ref(false);       // 只看「從未上線」

function staleDays(r: IPAddress): number | null {
  const arr = [r.last_seen_scanner, r.last_seen_librenms, r.last_seen_dns].filter(Boolean) as string[];
  if (!arr.length) return null;
  const max = Math.max(...arr.map((s) => new Date(s).getTime()));
  return Math.floor((Date.now() - max) / 86400000);
}
function isProbed(r: IPAddress): boolean {
  return !r.exclude_from_ping && r.subnet_scan_enabled !== false;
}
function staleDaysLabel(r: IPAddress): string {
  if (!isProbed(r)) return t("stale.not_probed");
  const d = staleDays(r);
  if (d === null) return t("stale.never");
  return t("stale.n_days", { n: d });
}
const staleMatches = computed<IPAddress[]>(() =>
  addresses.value.filter((a) => {
    if (!isProbed(a)) return false;
    const d = staleDays(a);
    if (staleNeverOnly.value) return d === null;
    return d === null || d >= staleThreshold.value;   // 從未上線視為無限失聯
  }),
);
function applyPreset(days: number) {
  staleNeverOnly.value = false;
  staleThreshold.value = days;
  staleFilterOn.value = true;
}
function applyNeverOnly() {
  staleNeverOnly.value = true;
  staleFilterOn.value = true;
}

// 批次設定狀態
const stateMenuOptions = computed(() =>
  ["reserved", "offline", "active"].map((s) => ({
    label: t(`stale.set_state_to`, { state: t(`addresses.state_${s}`) }),
    key: s,
  })),
);
async function bulkSetState(state: string) {
  const ids = checkedIps.value.map(String).filter((k) => !k.startsWith("gap:"));
  if (!ids.length) return;
  ipBulkBusy.value = true;
  try {
    const { bulkSetAddressState } = await import("@/api/addresses");
    const res = await bulkSetAddressState(ids, state);
    if (res.failed) msg.warning(t("stale.state_done_partial", { updated: res.updated, failed: res.failed }));
    else msg.success(t("stale.state_done", { n: res.updated }));
    checkedIps.value = [];
    if (subnet.value) await load(subnet.value.id);
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
  finally { ipBulkBusy.value = false; }
}
async function bulkNotifyStale() {
  const ids = checkedIps.value.map(String).filter((k) => !k.startsWith("gap:"));
  if (!ids.length || !subnet.value) return;
  ipBulkBusy.value = true;
  try {
    const { notifyStaleAddresses } = await import("@/api/addresses");
    const res = await notifyStaleAddresses(subnet.value.id, ids, staleNeverOnly.value ? 0 : staleThreshold.value);
    msg.success(t("stale.notify_done", { n: res.ip_count, admins: res.notified_admins }));
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
  finally { ipBulkBusy.value = false; }
}

function ipSort(a: string, b: string): number {
  const toNum = (ip: string): bigint => {
    if (ip.includes(":")) {
      // IPv6 — 簡單字典序就好 (IPv6 全展開太麻煩，且常見場景多 IPv4)
      return BigInt(0);
    }
    return ip.split(".").reduce((acc, p) => (acc << 8n) + BigInt(p || 0), 0n);
  };
  const an = toNum(a);
  const bn = toNum(b);
  return an < bn ? -1 : an > bn ? 1 : a.localeCompare(b);
}

function openRow(row: IPAddress) {
  if ((row as any).__gap) return;   // 閒置區間列不開頁
  void router.push({ name: "address-detail", params: { id: row.id } });
}

// ── 閒置區間：在已登記 IP 之間插入「起 - 迄 (N)」灰列 ──
function _ipToInt(ip: string): number {
  const p = ip.split(".");
  if (p.length !== 4) return NaN;
  return ((+p[0] << 24) >>> 0) + ((+p[1] << 16) >>> 0) + ((+p[2] << 8) >>> 0) + (+p[3]);
}
function _intToIp(n: number): string {
  return [(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255].join(".");
}
function _gapRow(gs: number, ge: number): any {
  return { __gap: true, id: `gap:${gs}`, ip: _intToIp(gs), _gapEnd: _intToIp(ge), _gapCount: ge - gs + 1 };
}
const ipRows = computed<any[]>(() => {
  // 失聯篩選開啟時：只列符合的已登記 IP，不插入閒置區間列
  if (staleFilterOn.value) return [...staleMatches.value];
  const cidr = subnet.value?.cidr;
  const list = [...addresses.value];
  if (!cidr || cidr.includes(":")) return list;   // IPv6 暫不算閒置區間
  const m = /^(\d+\.\d+\.\d+\.\d+)\/(\d+)$/.exec(cidr);
  if (!m) return list;
  const prefix = Number(m[2]);
  const base = _ipToInt(m[1]);
  const total = prefix >= 32 ? 1 : 2 ** (32 - prefix);
  const netInt = prefix === 0 ? 0 : (base & ((~0 << (32 - prefix)) >>> 0)) >>> 0;
  let first = netInt, last = netInt + total - 1;
  if (prefix < 31) { first = netInt + 1; last = netInt + total - 2; }
  const used = list
    .map((a) => ({ a, n: _ipToInt(a.ip) }))
    .filter((x) => !Number.isNaN(x.n))
    .sort((x, y) => x.n - y.n);
  const out: any[] = [];
  let cursor = first;
  for (const { a, n } of used) {
    if (n < first || n > last) { out.push(a); continue; }
    if (n > cursor) out.push(_gapRow(cursor, n - 1));
    out.push(a);
    cursor = n + 1;
  }
  if (cursor <= last) out.push(_gapRow(cursor, last));
  return out;
});
function gapLabel(r: any): string {
  return r._gapCount > 1 ? t("subnet_detail.gap_range", { start: r.ip, end: r._gapEnd, n: r._gapCount }) : t("subnet_detail.gap_one", { ip: r.ip });
}

function onSaved(updated: IPAddress) {
  selected.value = updated;
  const idx = addresses.value.findIndex((r) => r.id === updated.id);
  if (idx >= 0) addresses.value[idx] = updated;
}

function onDeleted(id: string) {
  addresses.value = addresses.value.filter((r) => r.id !== id);
}

watch(
  () => route.params.id,
  (id) => {
    if (typeof id === "string") void load(id);
  },
);

onMounted(() => {
  const id = route.params.id;
  if (typeof id === "string") void load(id);
  void ensureCustomersLoaded();
  void ensurePinsLoaded();
  void loadDhcpRanges();
});
</script>

<template>
  <n-spin :show="loading">
    <n-space vertical :size="16">
      <n-card v-if="subnet">
        <template #header>
          <n-space align="center" :wrap-item="false">
            <n-icon :size="22"><SubnetsIcon /></n-icon>
            <span>{{ subnet.cidr }}</span>
          </n-space>
        </template>
        <template #header-extra>
          <n-space>
            <n-button v-if="subnet" size="small" @click="openSubnetEdit">
              <template #icon><n-icon><EditIcon /></n-icon></template>
              {{ t("common.edit") }}
            </n-button>
            <n-button v-if="subnet" size="small" @click="addChildSubnet">
              <template #icon><n-icon><SubnetsIcon /></n-icon></template>
              {{ t("subnet_detail.add_child") }}
            </n-button>
            <n-button
              v-if="subnet"
              size="small"
              :type="isPinned(subnet.id) ? 'primary' : 'default'"
              @click="togglePinned(subnet.id)"
            >
              <template #icon><n-icon><PinIcon /></n-icon></template>
              {{ isPinned(subnet.id) ? t('subnet_detail.unpin') : t('subnet_detail.pin') }}
            </n-button>
            <n-button
              v-if="section"
              size="small"
              @click="router.push({ name: 'section-detail', params: { id: section.id } })"
            >
              <template #icon><n-icon><ArrowLeftIcon /></n-icon></template>
              {{ section.name }}
            </n-button>
            <n-popover trigger="click" placement="bottom-end" :width="360">
              <template #trigger>
                <n-button size="small">{{ t("csv_import.button") }}</n-button>
              </template>
              <n-space vertical :size="12">
                <n-alert type="info" size="small">
                  <span v-html="t('csv_import.hint_html')" />
                </n-alert>
                <n-checkbox v-model:checked="dryRun">
                  {{ t("csv_import.dry_run") }}
                </n-checkbox>
                <n-upload
                  :custom-request="uploadCsv"
                  :show-file-list="false"
                  accept=".csv,text/csv"
                  :disabled="importBusy"
                >
                  <n-button :loading="importBusy" type="primary" size="small">
                    {{ t("csv_import.select_file") }}
                  </n-button>
                </n-upload>
                <n-card v-if="importResult" size="small" :title="t('csv_import.result_title')">
                  <pre>{{ JSON.stringify(importResult, null, 2) }}</pre>
                </n-card>
              </n-space>
            </n-popover>
            <n-button size="small" @click="handleExport">{{ t("csv_import.export_button") }}</n-button>
          </n-space>
        </template>
        <n-descriptions bordered :column="3" size="small" label-placement="left">
          <n-descriptions-item :label="t('subnets.cidr')">{{ subnet.cidr }}</n-descriptions-item>
          <n-descriptions-item :label="t('subnets.section')">
            <a v-if="section" href="#" class="entity-link"
               @click.prevent="router.push({ name: 'section-detail', params: { id: section.id } })">
              {{ section.name }}
            </a>
            <span v-else>—</span>
          </n-descriptions-item>
          <n-descriptions-item :label="t('subnets.vlan')">
            <a v-if="vlan" href="#" class="entity-link"
               @click.prevent="router.push({ name: 'vlans' })">
              {{ vlan.number }} · {{ vlan.name }}
            </a>
            <span v-else>—</span>
          </n-descriptions-item>
          <n-descriptions-item :label="t('subnets.vrf')">
            <a v-if="vrf" href="#" class="entity-link"
               @click.prevent="router.push({ name: 'vrfs' })">
              {{ vrf.name }}<span v-if="vrf.rd">({{ vrf.rd }})</span>
            </a>
            <span v-else>—</span>
          </n-descriptions-item>
          <n-descriptions-item :label="t('subnets.master')">
            <a
              v-if="masterSubnet"
              href="#" class="entity-link"
              @click.prevent="router.push({ name: 'subnet-detail', params: { id: masterSubnet.id } })"
            >{{ masterSubnet.cidr }}</a>
            <span v-else>—</span>
          </n-descriptions-item>
          <n-descriptions-item :label="t('subnets.pool_full')">
            <n-tag v-if="subnet.is_pool" type="info" size="small">{{ t("subnets.pool") }}</n-tag>
            <n-tag v-if="subnet.is_full" type="error" size="small" style="margin-left: 4px">{{ t("subnets.full") }}</n-tag>
            <span v-if="!subnet.is_pool && !subnet.is_full">—</span>
          </n-descriptions-item>
          <n-descriptions-item :label="t('subnets.scan')">
            <n-tag :type="subnet.scan_enabled ? 'success' : 'default'" size="small">
              {{ subnet.scan_enabled ? t("subnets.scan_enabled") : t("subnets.scan_disabled") }}
            </n-tag>
            <span v-if="subnet.scan_method?.length" style="margin-left: 6px; font-family: monospace; font-size: 12px;">
              {{ subnet.scan_method.join(", ") }}
            </span>
          </n-descriptions-item>
          <n-descriptions-item :label="t('subnets.threshold')">{{ subnet.threshold_pct != null ? `${subnet.threshold_pct}%` : "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('subnets.gateway')">
            <span v-if="subnet.gateway" style="font-family: monospace">{{ subnet.gateway }}</span>
            <span v-else>—</span>
          </n-descriptions-item>
          <n-descriptions-item :label="t('nav.customers')">
            <a v-if="subnet.customer_id" href="#" class="entity-link"
               @click.prevent="router.push({ name: 'customers' })">
              {{ customerLabelFor(subnet.customer_id) }}
            </a>
            <span v-else>—</span>
          </n-descriptions-item>
          <n-descriptions-item :label="t('common.description')" :span="2">
            {{ subnet.description ?? "—" }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('common.created_at')">{{ fmtDateTime(subnet.created_at) }}</n-descriptions-item>
          <n-descriptions-item :label="t('common.updated_at')" :span="2">{{ fmtDateTime(subnet.updated_at) }}</n-descriptions-item>
        </n-descriptions>
      </n-card>

      <n-card v-if="usage">
        <template #header>
          <n-space align="center" :wrap-item="false">
            <n-icon :size="22"><UsageIcon /></n-icon>
            <span>{{ t("subnets.usage") }}</span>
          </n-space>
        </template>
        <n-space vertical>
          <div>{{ t("subnets.used_summary", { used: usage.used, total: usage.total, pct: usage.used_pct }) }}</div>
          <n-progress
            type="line"
            :percentage="usage.used_pct"
            :status="usage.used_pct >= 90 ? 'error' : usage.used_pct >= 75 ? 'warning' : 'success'"
          />
        </n-space>
      </n-card>

      <n-card v-if="subnet">
        <template #header>
          <n-space align="center" :wrap-item="false">
            <n-icon :size="22"><GridIcon /></n-icon>
            <span>{{ t("subnets.visualisation") }}</span>
          </n-space>
        </template>
        <subnet-grid
          :cidr="subnet.cidr"
          :addresses="addresses"
          @open-ip="onGridOpen"
          @create-ip="onGridCreate"
        />
      </n-card>

      <n-card v-if="subnet">
        <template #header>
          <n-space align="center" :wrap-item="false">
            <n-icon :size="22"><ListIcon /></n-icon>
            <span>{{ t("addresses.ip_list_title") }} ({{ addresses.length }})</span>
          </n-space>
        </template>
        <template #header-extra>
          <n-space align="center">
            <n-button v-if="subnet" type="primary" size="small" @click="onAddAddress">
              <template #icon><n-icon><PlusIcon /></n-icon></template>
              {{ t("subnet_detail.add_address") }}
            </n-button>
            <n-button size="small" :type="staleFilterOn ? 'warning' : 'default'"
                      @click="staleFilterOn = !staleFilterOn">
              {{ t("stale.filter_label") }}
            </n-button>
            <ColumnPicker :all="ipColumnPickerItems" :visible="ipVisibleKeys"
                          @update:visible="setIpVisible" @reset="resetIpVisible" />
            <n-button @click="load(subnet.id)" :loading="loading">
              <template #icon><n-icon><RefreshIcon /></n-icon></template>
              {{ t("common.refresh") }}
            </n-button>
          </n-space>
        </template>
        <!-- 失聯 IP 篩選：按下表頭「只看失聯 IP」才展開 -->
        <div v-if="staleFilterOn" class="stale-bar">
          <div class="stale-row">
            <n-space :size="4" :wrap="true">
              <n-button v-for="p in STALE_PRESETS" :key="p" size="tiny"
                        :type="!staleNeverOnly && staleThreshold === p ? 'primary' : 'default'"
                        @click="applyPreset(p)">{{ t("stale.n_days", { n: p }) }}</n-button>
              <n-button size="tiny" :type="staleNeverOnly ? 'primary' : 'default'"
                        @click="applyNeverOnly">{{ t("stale.never") }}</n-button>
            </n-space>
          </div>
          <div v-if="!staleNeverOnly" class="stale-row">
            <span class="stale-slider-label">{{ t("stale.threshold_label", { n: staleThreshold }) }}</span>
            <n-slider v-model:value="staleThreshold" :min="1" :max="180" :step="1" style="flex: 1; max-width: 360px" />
          </div>
          <div class="stale-hint">
            {{ t("stale.match_count", { n: staleMatches.length }) }} · {{ t("stale.exclude_note") }}
          </div>
        </div>

        <n-space v-if="checkedIps.length" align="center" style="margin-bottom: 8px; padding: 8px 12px; background: rgba(127,127,127,0.08); border-radius: 6px;">
          <span>{{ t("common.selected_n", { n: checkedIps.length }) }}</span>
          <n-popconfirm @positive-click="bulkDeleteIps">
            <template #trigger>
              <n-button type="error" size="small" :loading="ipBulkBusy">
                {{ t("common.bulk_delete") }}
              </n-button>
            </template>
            {{ t("common.confirm_delete_n", { n: checkedIps.length }) }}
          </n-popconfirm>
          <n-dropdown trigger="click" :options="stateMenuOptions" @select="bulkSetState">
            <n-button size="small" :loading="ipBulkBusy">{{ t("stale.set_state") }}</n-button>
          </n-dropdown>
          <n-button size="small" :loading="ipBulkBusy" @click="bulkNotifyStale">
            {{ t("stale.notify") }}
          </n-button>
          <n-button size="small" @click="checkedIps = []">{{ t("common.clear_selection") }}</n-button>
        </n-space>
        <n-data-table
          :columns="ipColumns"
          :data="ipRows"
          :pagination="false"
          :bordered="false"
          size="small"
          :scroll-x="1180"
          :row-key="(row: any) => row.id"
          :checked-row-keys="checkedIps"
          @update:checked-row-keys="(keys: Array<string | number>) => checkedIps = keys"
          :row-class-name="(row: any) => row.__gap ? 'ip-gap-row' : ''"
          :row-props="(row: any) => ({
            style: row.__gap ? '' : 'cursor: pointer',
            onClick: (e: MouseEvent) => {
              if ((e.target as HTMLElement).closest('.n-checkbox')) return;
              openRow(row);
            },
          })"
        >
          <template #empty>
            <n-space justify="center">{{ t("common.no_data") }}</n-space>
          </template>
        </n-data-table>
      </n-card>

    </n-space>
  </n-spin>

  <IPAddressEditModal
    v-model:show="modalShow"
    :address="selected"
    :create-context="createCtx"
    @saved="onSaved"
    @deleted="onDeleted"
    @created="onCreated"
  />

  <n-modal v-model:show="editShow" preset="card"
           :title="`${t('common.edit')} ${subnet?.cidr ?? ''}`" style="width: 460px">
    <n-form label-placement="left" label-width="110">
      <n-form-item :label="t('common.description')">
        <n-input v-model:value="editForm.description" />
      </n-form-item>
      <n-form-item :label="t('subnets.gateway')">
        <n-input v-model:value="editForm.gateway" :placeholder="t('subnets.gateway_ph')" />
      </n-form-item>
      <n-form-item :label="t('subnets.dns_servers')">
        <n-input v-model:value="editForm.dns_servers" :placeholder="t('subnets.dns_servers_ph')" />
      </n-form-item>
      <n-form-item :label="t('nav.locations')">
        <n-select v-model:value="editForm.location_id" :options="locationOpts"
                  clearable :placeholder="t('nav.locations')" />
      </n-form-item>
      <n-form-item :label="t('nav.customers')">
        <n-select v-model:value="editForm.customer_id" :options="customerOptions"
                  clearable filterable placeholder="—" />
      </n-form-item>
      <n-form-item :label="t('subnets.pool_full')">
        <n-space>
          <n-checkbox v-model:checked="editForm.is_pool">{{ t("subnets.is_pool") }}</n-checkbox>
          <n-checkbox v-model:checked="editForm.is_full">{{ t("subnets.is_full") }}</n-checkbox>
        </n-space>
      </n-form-item>
      <n-form-item :label="t('subnets.scan_enable')">
        <n-switch v-model:value="editForm.scan_enabled" />
      </n-form-item>
      <n-form-item v-if="editForm.scan_enabled" :label="t('subnet_detail.scan_agent')">
        <n-select v-model:value="editForm.scan_agent_id" :options="scanAgentOpts"
                  clearable :placeholder="t('subnet_detail.scan_agent_ph')" />
      </n-form-item>
      <n-form-item :label="t('subnets.threshold_pct')">
        <n-input-number v-model:value="editForm.threshold_pct" :min="0" :max="100" clearable />
      </n-form-item>
    </n-form>
    <template #footer>
      <n-space justify="end">
        <n-button @click="editShow = false">
          <template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}
        </n-button>
        <n-button type="primary" :loading="editSaving" @click="saveSubnetEdit">
          <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
        </n-button>
      </n-space>
    </template>
  </n-modal>
</template>

<style scoped>
/* 卡片在 n-space(flex column) 內，若內含 scroll-x 寬表，min-width:auto 會把卡片
   撐到比視窗寬 → 整頁右側溢出（IP 清單表跑版）。強制 min-width:0，讓表格用自己的
   水平捲動吸收寬度，卡片不再被撐爆。 */
:deep(.n-card) { min-width: 0; }
:deep(.n-data-table) { max-width: 100%; }
/* 失聯篩選列 */
.stale-bar { margin-bottom: 10px; display: flex; flex-direction: column; gap: 6px; }
.stale-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.stale-slider-label { font-size: 12px; opacity: 0.8; min-width: 130px; }
.stale-hint { font-size: 12px; opacity: 0.6; }
/* 閒置區間列：灰底、不可點 */
:deep(.ip-gap-row td) {
  background: rgba(127, 127, 127, 0.06);
  cursor: default;
}
pre {
  font-size: 12px;
  background: rgba(127, 127, 127, 0.08);
  padding: 8px;
  border-radius: 4px;
  overflow-x: auto;
  max-height: 320px;
}
a {
  color: var(--primary-color, #18a058);
  text-decoration: none;
}
a:hover {
  text-decoration: underline;
}
</style>
