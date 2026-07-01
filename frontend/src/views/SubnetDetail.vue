<script setup lang="ts">
import { useAuthStore } from "@/stores/auth";
const _authBtn = useAuthStore();
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
  NSlider,
  NDropdown,
  type UploadCustomRequestOptions,
  type DataTableColumns,
  useMessage,
} from "naive-ui";
import { EditIcon } from "@/icons";
import { fmtDateTime } from "@/utils/datetime";
import { autoSort } from "@/composables/useTableSort";
import { useCustomers } from "@/composables/useCustomers";
import { usePinnedSubnets } from "@/composables/usePinnedSubnets";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import SubnetEditModal from "@/components/SubnetEditModal.vue";
import SwitchPortLabel from "@/components/SwitchPortLabel.vue";
import IpRoleTags from "@/components/IpRoleTags.vue";
import OsIcon from "@/components/OsIcon.vue";
import { useScanProbes, osFamilyLabel } from "@/api/scanProbes";
const { t, locale } = useI18n();
const { catalog } = useScanProbes();

const { labelFor: customerLabelFor, ensureLoaded: ensureCustomersLoaded } = useCustomers();

// 子網路編輯（表單統一走共用元件 SubnetEditModal.vue）
const editShow = ref(false);
function openSubnetEdit() {
  if (!subnet.value) return;
  editShow.value = true;
}
function onSubnetSaved() {
  if (subnet.value) void load(subnet.value.id);
}
const { isPinned, toggle: togglePinned, ensureLoaded: ensurePinsLoaded } = usePinnedSubnets();

const { visibleKeys: ipVisibleKeys, setVisible: setIpVisible, reset: resetIpVisible } = useColumnPrefs(
  "subnet_detail_ips",
  ["live", "ip", "hostname", "state", "dhcp", "mac", "mac_vendor", "os", "owner", "switch_port", "device", "description", "last_seen", "stale_days", "note"],
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
  { key: "os", label: t("cols.os") },
  { key: "owner", label: t("cols.owner") },
  { key: "switch_port", label: t("cols.switch_port") },
  { key: "device", label: t("cols.device") },
  { key: "description", label: t("cols.description") },
  { key: "last_seen", label: t("cols.last_seen") },
  { key: "stale_days", label: t("stale.col_stale") },
  { key: "note", label: t("cols.note") },
];
import { SubnetsIcon, RefreshIcon, UsageIcon, GridIcon, ListIcon, PinIcon, PlusIcon, MissingIcon, SearchIcon, AddressesIcon, DeleteIcon } from "@/icons";
import { ArrowLeft as ArrowLeftIcon } from "@iconoir/vue";
import { apiClient } from "@/api/client";
import { listAddresses } from "@/api/addresses";
import { listDhcpRanges } from "@/api/integrations";
import { getSubnetUsage, deleteSubnet } from "@/api/subnets";
import { getSection } from "@/api/sections";
import { useSubnetTree } from "@/composables/useSubnetTree";
import { listVLANs, listVRFs, type VLAN, type VRF } from "@/api/basic";
import SubnetGrid from "@/components/SubnetGrid.vue";
import IPAddressEditModal from "@/components/IPAddressEditModal.vue";
import LiveStatusDot from "@/components/LiveStatusDot.vue";
import type { IPAddress, Section, Subnet, SubnetUsage } from "@/types";

const route = useRoute();
const router = useRouter();
const msg = useMessage();
const { bump: bumpSubnetTree } = useSubnetTree();

// 刪除此子網路（詳情頁工具列，帶確認框；刪除後刷新側邊樹並回子網路清單）
const deleting = ref(false);
async function delThisSubnet() {
  if (!subnet.value) return;
  deleting.value = true;
  try {
    await deleteSubnet(subnet.value.id);
    bumpSubnetTree();
    msg.success(t("common.deleted"));
    void router.push({ name: "subnets" });
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("common.delete_failed"));
  } finally {
    deleting.value = false;
  }
}

const subnet = ref<Subnet | null>(null);
const usage = ref<SubnetUsage | null>(null);
const addresses = ref<IPAddress[]>([]);
const loading = ref(false);

// ── DHCP 發放範圍：標示落在 pool 內的 IP（多段都涵蓋）──
interface DhcpRangeInfo { a: number; b: number; server: string; source: string; start: string; end: string; }
const dhcpRanges = ref<DhcpRangeInfo[]>([]);
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
    const out: DhcpRangeInfo[] = [];
    for (const r of rows) {
      const a = ipv4ToInt(r.start_ip), b = ipv4ToInt(r.end_ip);
      if (a != null && b != null) {
        out.push({
          a: Math.min(a, b), b: Math.max(a, b),
          server: r.firewall_name || "—",
          source: (r.source || "").toUpperCase(),
          start: r.start_ip, end: r.end_ip,
        });
      }
    }
    dhcpRanges.value = out;
  } catch { dhcpRanges.value = []; }
}
function dhcpInfoForIp(ip: string | null | undefined): DhcpRangeInfo | null {
  if (!ip) return null;
  const n = ipv4ToInt(String(ip).split("/")[0]);
  if (n == null) return null;
  return dhcpRanges.value.find((r) => n >= r.a && n <= r.b) ?? null;
}
function isDhcpIp(ip: string | null | undefined): boolean {
  return dhcpInfoForIp(ip) != null;
}
// 只屬於「本子網路」的 DHCP 發放範圍（給資訊欄顯示 + 只看 DHCP 用）；無資料就空
const subnetDhcpRanges = computed<DhcpRangeInfo[]>(() => {
  const cidr = subnet.value?.cidr;
  if (!cidr || cidr.includes(":")) return [];
  const m = /^(\d+\.\d+\.\d+\.\d+)\/(\d+)$/.exec(cidr);
  if (!m) return [];
  const prefix = Number(m[2]);
  const base = ipv4ToInt(m[1]);
  if (base == null) return [];
  const total = prefix >= 32 ? 1 : 2 ** (32 - prefix);
  const netInt = prefix === 0 ? 0 : (base & ((~0 << (32 - prefix)) >>> 0)) >>> 0;
  const lo = netInt, hi = netInt + total - 1;
  return dhcpRanges.value
    .filter((r) => r.a >= lo && r.a <= hi)
    .sort((x, y) => x.a - y.a);
});
const onlyDhcp = ref(false);

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
const IP_COL_ORDER = ["live", "ip", "hostname", "state", "dhcp", "mac", "mac_vendor", "os",
  "owner", "switch_port", "device", "description", "last_seen", "stale_days", "note"];
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
      : h("span", { style: "display:inline-flex;align-items:center;white-space:nowrap" }, [String(r.ip), h(IpRoleTags, { row: r, hideRange: true })]) },
  { title: t("addresses.hostname"), key: "hostname", minWidth: 120,
    ellipsis: { tooltip: true }, render: (r) => (r as any).__gap ? "" : (r.hostname ?? "") },
  { title: t("common.status"), key: "state", width: 100,
    render: (r) => (r as any).__gap ? "" : stateTag(r.state) },
  { title: "DHCP", key: "dhcp", width: 80,
    render: (r) => {
      if ((r as any).__gap) return "";
      const info = dhcpInfoForIp(r.ip);
      if (!info) return "";
      return h(NTooltip, { delay: 0 }, {
        trigger: () => h(NTag, { size: "tiny", type: "warning", bordered: false }, { default: () => "DHCP" }),
        default: () => h("div", { style: "max-width:260px;line-height:1.5" }, [
          h("div", t("addresses.dhcp_pool_hint")),
          h("div", { style: "margin-top:4px" }, [
            h("strong", `${t("addresses.dhcp_server")}：`),
            `${info.server}${info.source ? ` (${info.source})` : ""}`,
          ]),
          h("div", `${t("addresses.dhcp_range")}：${info.start} – ${info.end}`),
        ]),
      });
    } },
  { title: t("addresses.mac"), key: "mac", width: 150, render: (r) => r.mac ?? "" },
  { title: t("cols.vendor"), key: "mac_vendor", width: 140,
    ellipsis: { tooltip: true }, render: (r) => r.mac_vendor ?? "—" },
  { title: t("cols.os"), key: "os", width: 150,
    render: (r) => {
      if ((r as any).__gap || !r.os_family) return "—";
      const label = osFamilyLabel(catalog.value.os_families, r.os_family, locale.value);
      // 一行顯示，icon 永遠不縮；空間不夠時 label 被裁掉、只剩 icon
      return h("div", {
        style: "display:flex;align-items:center;gap:4px;min-width:0;white-space:nowrap",
        title: r.os_guess ?? label ?? undefined,
      }, [
        h("span", { style: "flex:none;display:inline-flex" }, h(OsIcon, { family: r.os_family, size: 16 })),
        label ? h("span", { style: "overflow:hidden;text-overflow:ellipsis" }, label) : null,
      ]);
    } },
  { title: t("addresses.owner"), key: "owner", width: 120,
    ellipsis: { tooltip: true }, render: (r) => r.owner ?? "" },
  { title: t("addresses.switch_port"), key: "switch_port", width: 210,
    ellipsis: { tooltip: false },   // 裁切但不開 cell tooltip，否則會跟下方 NTooltip 疊成兩個彈框
    render: (r) => !r.switch_port ? ""
      : h(NTooltip, null, {
          trigger: () => h(SwitchPortLabel, { value: r.switch_port, dim: r.switch_port_confident === false }),
          // 彈出文字一律含完整 裝置@連接埠 全文；低信心時再附上說明
          default: () => r.switch_port_confident === false
            ? h("div", { style: "max-width:320px;line-height:1.5" }, [
                h("div", { style: "font-family:monospace;word-break:break-all" }, (r.switch_port ?? "").replace(" / ", "@")),
                h("div", { style: "margin-top:4px" }, t("addresses.switch_port_uncertain")),
              ])
            : h("span", { style: "font-family:monospace;word-break:break-all" }, (r.switch_port ?? "").replace(" / ", "@")) }) },
  { title: t("cols.device"), key: "device", width: 150, ellipsis: { tooltip: true },
    render: (r) => {
      if ((r as any).__gap || !r.device_id) return "—";
      return h("a", {
        href: "#",
        style: "color: var(--primary-color, #18a058); text-decoration: none;",
        onClick: (e: MouseEvent) => { e.preventDefault(); e.stopPropagation(); router.push({ name: "device-detail", params: { id: r.device_id } }); },
      }, r.device_name || (r.device_id.slice(0, 8) + "…"));
    } },
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
const ipFilterText = ref("");
function ipMatchesFilter(a: IPAddress): boolean {
  const q = ipFilterText.value.trim().toLowerCase();
  if (!q) return true;
  return [a.ip, a.hostname, a.mac, a.mac_vendor, a.owner, a.description, a.note, a.device_name]
    .some((v) => !!v && String(v).toLowerCase().includes(q));
}

const ipRows = computed<any[]>(() => {
  // 失聯篩選開啟時：只列符合的已登記 IP，不插入閒置區間列
  if (staleFilterOn.value) return staleMatches.value.filter(ipMatchesFilter);
  // 只看 DHCP：只列落在 DHCP 發放範圍內的已登記 IP，不插入閒置區間列
  if (onlyDhcp.value) return addresses.value.filter((a) => isDhcpIp(a.ip)).filter(ipMatchesFilter);
  // 有搜尋字時：只列符合的已登記 IP，不插入閒置區間列
  if (ipFilterText.value.trim()) return addresses.value.filter(ipMatchesFilter);
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
            <n-popconfirm v-if="subnet" @positive-click="delThisSubnet">
              <template #trigger>
                <n-button size="small" type="error" :loading="deleting">
                  <template #icon><n-icon><DeleteIcon /></n-icon></template>
                  {{ t("common.delete") }}
                </n-button>
              </template>
              {{ t("subnet_detail.delete_confirm") }}
            </n-popconfirm>
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
          <n-descriptions-item v-if="subnetDhcpRanges.length" :label="t('subnets.dhcp_ranges')" :span="3">
            <div style="display: flex; flex-wrap: wrap; gap: 6px">
              <n-tag v-for="(r, i) in subnetDhcpRanges" :key="i" size="small" type="warning" :bordered="false"
                     style="white-space: normal; height: auto; max-width: 100%">
                <span style="font-family: monospace">{{ r.start }} – {{ r.end }}</span>
                <span style="opacity: .7; margin-left: 6px">{{ r.server }}{{ r.source ? ` · ${r.source}` : "" }}</span>
              </n-tag>
            </div>
          </n-descriptions-item>
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
            <n-input v-model:value="ipFilterText" size="small" clearable
                     :placeholder="t('common.filter')" style="width: 200px">
              <template #prefix><n-icon><SearchIcon /></n-icon></template>
            </n-input>
            <n-button v-if="subnet" type="primary" size="small" :disabled="_authBtn.me?.can_edit === false" @click="onAddAddress">
              <template #icon><n-icon><PlusIcon /></n-icon></template>
              {{ t("subnet_detail.add_address") }}
            </n-button>
            <n-button size="small" :type="staleFilterOn ? 'warning' : 'default'"
                      @click="staleFilterOn = !staleFilterOn">
              <template #icon><n-icon><MissingIcon /></n-icon></template>
              {{ t("stale.filter_label") }}
            </n-button>
            <n-button v-if="subnetDhcpRanges.length" size="small"
                      :type="onlyDhcp ? 'warning' : 'default'"
                      @click="onlyDhcp = !onlyDhcp">
              <template #icon><n-icon><AddressesIcon /></n-icon></template>
              {{ t("subnets.only_dhcp") }}
            </n-button>
            <ColumnPicker :all="ipColumnPickerItems" :visible="ipVisibleKeys"
                          @update:visible="setIpVisible" @reset="resetIpVisible" />
            <ExportButton v-if="subnet" size="small" :columns="ipColumns" :rows="addresses"
                          :filename="`ip-${subnet.cidr.replace('/', '_')}`"
                          :title="`${t('addresses.ip_list_title')} ${subnet.cidr}`" />
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

  <SubnetEditModal v-model:show="editShow" :editing="subnet" @saved="onSubnetSaved" />
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
