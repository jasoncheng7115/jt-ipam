<script setup lang="ts">
/**
 * 通用 IP 編輯 modal。
 * SubnetDetail / Addresses 都用同一個。
 *
 * 預設 read-only 顯示完整欄位；按「編輯」進 edit 模式才能改。
 */
import { computed, h, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  NModal, NCard, NSpace, NButton, NDescriptions, NDescriptionsItem,
  NForm, NFormItem, NInput, NSelect, NSwitch, NPopconfirm, NTag, NIcon, NPagination,
  NCollapse, NCollapseItem, NTimeline, NTimelineItem, NText, NEmpty, NSpin,
  NTooltip, NCheckbox, NCheckboxGroup, NButtonGroup, NDivider, NDropdown,
  NInputGroup, NInputGroupLabel, NDataTable, NAlert, type DataTableColumns,
  useMessage,
} from "naive-ui";
import { useAuthStore } from "@/stores/auth";
import { apiClient, apiErrMsg } from "@/api/client";
import type { IPAddress } from "@/types";
import {
  getAddress, updateAddress, deleteAddress, createAddress, getDeviceSuggestion, applyDeviceSuggestion,
  getSameMacAddresses, type IPAddressUpdate, type DeviceSuggestion,
  type SameMacAddress,
} from "@/api/addresses";
import { classifyAddressLiveness, lastCountedAt, looksMoved } from "@/composables/useLivenessSettings";
import { listIPRanges, type IPRange } from "@/api/ipRanges";
import { rangeContaining } from "@/utils/ipColumns";
import { getAddressHistory, getAddressSwitchPort, type HistoryFacet, type IPChangeLog,
  type SwitchPortInfo } from "@/api/ip_history";
import { clearHostnameSource, getHostnameSources, type HostnameSources } from "@/api/hostname";
import { EditIcon, SaveIcon, CancelIcon, DeleteIcon, PlusIcon, LinkIcon, TerminalIcon, DisplayIcon, VncIcon, NoVncIcon, SearchIcon, FilesIcon, IdentifyIcon, RustDeskIcon, ChevronDownIcon, ChangeImpactIcon, CheckIcon } from "@/icons";
import CopyButton from "@/components/CopyButton.vue";
import MaintenanceQuickButton from "@/components/MaintenanceQuickButton.vue";
import InvestigateModal from "@/components/InvestigateModal.vue";
import ChangeImpactWizard from "@/components/ChangeImpactWizard.vue";
import { useChangeImpact } from "@/composables/useChangeImpact";
import ChangeValue from "@/components/ChangeValue.vue";
import IpRoleTags from "@/components/IpRoleTags.vue";
import LiveStatusDot from "@/components/LiveStatusDot.vue";
import { ArrowLeft as ArrowLeftIcon } from "@iconoir/vue";
import { fmtDateTime, fmtRelative } from "@/utils/datetime";
import { rustdeskOs } from "@/utils/rustdeskOs";
import { sourceLabel } from "@/utils/sourceLabel";
import { daysSince, differsFrom, reporterLabel } from "@/utils/hostnameReports";
import { useCustomers } from "@/composables/useCustomers";
import ConsoleEgressSelect from "@/components/ConsoleEgressSelect.vue";
import { virtTagText } from "@/utils/virt";
import { useChangeLogDim } from "@/composables/useChangeLogDim";
import { useRouter, type RouteLocationRaw } from "vue-router";
import { getDevice, listDevices, type Device } from "@/api/basic";
import { listDeviceGroups } from "@/api/deviceGroups";
import { getAddressRelations, type RelationNode } from "@/api/relations";
import { listDhcpRanges } from "@/api/integrations";
import { logRustDeskLocalOpen } from "@/api/rustdeskWeb";
import RelationChain from "@/components/RelationChain.vue";
import SwitchPortLabel from "@/components/SwitchPortLabel.vue";
import { useScanProbes, probeLabel, osDisplayLabel } from "@/api/scanProbes";
import OsIcon from "@/components/OsIcon.vue";
import DeviceKindIcon from "@/components/DeviceKindIcon.vue";
import { fwSeenLabel as fwSeenLabelOf } from "@/utils/investigateSections";

const router = useRouter();
const { options: customerOptions, labelFor: customerLabelFor, ensureLoaded: ensureCustomersLoaded } = useCustomers();
const { isOld: isOldLog } = useChangeLogDim();
const devices = ref<Device[]>([]);

async function loadDevices() {
  if (devices.value.length) return;
  try {
    const r = await listDevices();
    devices.value = r.items;
  } catch { /* silent */ }
}

/** 確保這一台在名稱查得到的清單裡。
 *
 *  `listDevices()` 只拿一頁（200 筆），所以剛建立、或排序落在後面的裝置不會在裡面，
 *  名稱就會退回顯示一段 UUID。缺的那一台直接照 id 取回來即可 —— 不必也不該
 *  為了顯示一個名稱去把整個裝置清單抓完。 */
async function ensureDeviceLoaded(id: string | null | undefined) {
  if (!id || devices.value.some((d) => d.id === id)) return;
  try {
    devices.value = [...devices.value, await getDevice(id)];
  } catch { /* 沒權限或已刪除 —— 讓 deviceLabel 走它的退路 */ }
}

function deviceLabel(id: string | null | undefined): string {
  if (!id) return "—";
  return devices.value.find((d) => d.id === id)?.name ?? id.slice(0, 8) + "…";
}
const deviceOptions = computed(() =>
  devices.value.map((d) => ({ label: d.name, value: d.id })));

// 共用給裝置群組的選項：只有管理員看得到這個欄位，所以只有管理員才去載
const groupOptions = ref<{ label: string; value: string }[]>([]);
let groupsLoaded = false;
async function ensureGroupsLoaded() {
  if (groupsLoaded || !auth.me?.is_admin) return;
  groupsLoaded = true;
  try {
    groupOptions.value = (await listDeviceGroups()).map((g) => ({
      label: `${g.name} · ${t(`device_groups.kind_${g.kind}`)}`, value: g.id }));
  } catch { groupsLoaded = false; }
}

// DHCP 發放範圍：標示本 IP 是否落在某 DHCP 主機的動態集區內
interface DhcpInfo { server: string; source: string; start: string; end: string; }
const dhcpRanges = ref<{ a: number; b: number; server: string; source: string; start: string; end: string }[]>([]);
function _ip2int(ip: string): number | null {
  const p = ip.trim().split(".");
  if (p.length !== 4) return null;
  let n = 0;
  for (const o of p) { const v = Number(o); if (!Number.isInteger(v) || v < 0 || v > 255) return null; n = n * 256 + v; }
  return n >>> 0;
}
async function loadDhcpRanges() {
  if (dhcpRanges.value.length) return;
  try {
    const rows = await listDhcpRanges();
    const out: typeof dhcpRanges.value = [];
    for (const r of rows) {
      const a = _ip2int(r.start_ip), b = _ip2int(r.end_ip);
      if (a != null && b != null) out.push({ a: Math.min(a, b), b: Math.max(a, b), server: r.source_name || "—", source: (r.source || "").toUpperCase(), start: r.start_ip, end: r.end_ip });
    }
    dhcpRanges.value = out;
  } catch { /* silent */ }
}
/** DHCP 固定分配的明細（後端在讀取端帶出來）。 */
const resv = computed<any | null>(() => (props.address as any)?.dhcp_reservation ?? null);
const resvLine = computed(() => {
  const r = resv.value;
  if (!r) return t("addresses.dhcp_reserved_tag");
  const parts = [r.mac, r.hostname, r.source_name ? `@${r.source_name}` : null]
    .filter(Boolean);
  return parts.length ? parts.join("　·　") : t("addresses.dhcp_reserved_tag");
});

/** DHCP 排除區間（後端在讀取端帶出來）：這台 DHCP 不會把這個位址發給任何人。 */
const excl = computed<any | null>(() => (props.address as any)?.dhcp_exclusion ?? null);
const exclLine = computed(() => {
  const x = excl.value;
  if (!x) return "";
  const span = x.start === x.end ? x.start : `${x.start} – ${x.end}`;
  return [span, x.source_name ? `@${x.source_name}` : null].filter(Boolean).join("　·　");
});

const investigating = ref(false);
// 探測是獨立頁面（結果要方便瀏覽、歷次都能點回來看），不是對話框
function openIdentify() {
  if (props.address?.id) void router.push({ name: "address-identify", params: { id: String(props.address.id) } });
}

const dhcpInfo = computed<DhcpInfo | null>(() => {
  const ip = (props.address?.ip ?? "").split("/")[0];
  const n = ip ? _ip2int(ip) : null;
  if (n == null) return null;
  const r = dhcpRanges.value.find((x) => n >= x.a && n <= x.b);
  return r ? { server: r.server, source: r.source, start: r.start, end: r.end } : null;
});

const relations = ref<RelationNode[]>([]);
async function loadRelations() {
  relations.value = [];
  if (!props.address?.id) return;
  try { relations.value = await getAddressRelations(props.address.id); } catch { /* silent */ }
}

// 此 IP 被哪些 NAT 規則引用（src/dst）
const relatedNat = ref<{ id: string; name: string; type: string; src_interface: string | null;
  src_port: number | null; dst_port: number | null; src_port_to?: number | null; dst_port_to?: number | null;
  src_ip?: string | null; dst_ip?: string | null; protocol?: string; redirect_alias?: string | null;
  source_label: string | null;
  source_kind: string | null; source_firewall_id: string | null;
  source_firewall_name?: string | null; disabled?: boolean }[]>([]);
async function loadRelatedNat() {
  relatedNat.value = [];
  if (!props.address?.id) return;
  try {
    const { listNATs } = await import("@/api/phase3");
    const res = await listNATs({ ipId: props.address.id });
    relatedNat.value = res.items as any;
  } catch { /* silent */ }
}
// 此 IP 被哪些防火牆規則／別名管到（反向查詢）。
// 防火牆規則屬全域基礎設施資料：無 has_global_read 的帳號不打這支、也不顯示區塊。
const fwInfo = ref<{ rules: any[]; nat: any[]; aliases: any[] } | null>(null);

/** 防火牆規則的「命中原因」：後端給結構，句子在這裡組。
 *
 * 後端原本直接回中文句子（「來源：網段 X 涵蓋」），結果**英文與日文介面也照樣出中文**
 * —— 伺服器產生的顯示文字沒有辦法跟著使用者的語言走。所以後端改回
 * `{side, code, value}`，翻譯留在這裡。新增命中種類時，三個語系的 `fw_why_*` 都要補。
 */
function fwMatchText(m: unknown): string {
  if (!m) return "";
  if (typeof m === "string") return m;            // 舊版後端（升級中的短暫並存）
  const { side, code, value } = m as { side?: string; code?: string; value?: string };
  if (!code) return "";
  const why = t(`addresses.fw_why_${code}`, { value: value ?? "" });
  return t(side === "dst" ? "addresses.fw_match_dst" : "addresses.fw_match_src", { why });
}
const auth = useAuthStore();
async function loadFirewall() {
  fwInfo.value = null;
  if (!props.address?.id) return;
  if (auth.me?.has_global_read === false) return;
  try {
    const { data } = await apiClient.get(`/api/v1/addresses/${props.address.id}/firewall`);
    fwInfo.value = data;
  } catch { /* silent — 沒有整合防火牆時就不顯示 */ }
}

// 放行／阻擋用顏色區分，一眼看得出規則的性質
function fwActionClass(a: unknown): string {
  const v = String(a ?? "").toLowerCase();
  if (["pass", "accept", "allow", "permit"].includes(v)) return "fw-pass";
  if (["block", "reject", "deny", "drop"].includes(v)) return "fw-block";
  return "";
}

// 點一列 → 帶到該廠牌的規則／別名頁，選好那一台、只顯示那一筆（使用者要求）。
// 目標頁讀 ?tab=&fw=&focus=（見 useFocusRow）；別名分頁的名稱各廠牌不同。
const FW_PAGES: Record<string, { route: string; rules?: string; aliases?: string }> = {
  opnsense: { route: "firewall", rules: "rules", aliases: "aliases" },
  pfsense: { route: "pfsense_fw", rules: "rules", aliases: "aliases" },
  fortigate: { route: "fortigate_fw" },
  paloalto: { route: "paloalto_fw" },
  checkpoint: { route: "checkpoint", rules: "rules", aliases: "objects" },
  mikrotik: { route: "mikrotik_fw", rules: "rules", aliases: "lists" },
};
function fwLink(kind: "rules" | "aliases", x: any): RouteLocationRaw | null {
  const page = FW_PAGES[x?.source_type];
  if (!page || !x.firewall_id || !x.ref) return null;
  const tab = kind === "rules" ? page.rules : page.aliases;
  if (kind === "aliases" && !tab) return null;
  const loc = { name: page.route, query: { ...(tab ? { tab } : {}), fw: x.firewall_id, focus: x.ref } };
  // 沒權限進的頁就不要做成可點（點了只會被導回首頁）
  if (router.resolve(loc).meta.admin && !auth.me?.is_admin) return null;
  return loc;
}
// 「外部 IP:埠 → 內部 IP:埠」：外部沒有位址時寫介面（例如 wan），內部是別名時寫別名名稱
const addrText = computed(() => String(props.address?.ip ?? "").split("/")[0]);
function natPorts(a: number | null | undefined, b: number | null | undefined): string {
  if (!a) return "";
  return b && b !== a ? `:${a}-${b}` : `:${a}`;
}
function natSide(ip: string | null | undefined, fallback: string | null | undefined,
                 port: number | null | undefined, portTo: number | null | undefined): string {
  return `${ip || fallback || "—"}${natPorts(port, portTo)}`;
}
function natExternal(n: (typeof relatedNat.value)[number]): string {
  return natSide(n.src_ip, n.src_interface, n.src_port, n.src_port_to);
}
function natInternal(n: (typeof relatedNat.value)[number]): string {
  return natSide(n.dst_ip, n.redirect_alias, n.dst_port, n.dst_port_to);
}
function natLink(n: { id: string }): RouteLocationRaw | null {
  return props.address?.id ? { name: "nat", query: { ip: props.address.id, focus: n.id } } : null;
}
function goTo(loc: RouteLocationRaw | null) {
  if (!loc) return;
  emit("update:show", false);
  void router.push(loc);
}

function goDevice(id: string | null | undefined, card?: string) {
  if (!id) return;
  void router.push({
    name: "device-detail", params: { id },
    ...(card ? { query: { card } } : {}),
  });
}

// 後端原始值 → i18n 顯示；找不到 key 就回原值
function labelState(v: string | null | undefined): string {
  if (!v) return "—";
  const key = `addresses.state_${v}`;
  const out = t(key);
  return out === key ? v : out;
}
function labelSource(v: string | null | undefined): string {
  return sourceLabel(t, v);
}
function labelEffective(v: string | null | undefined): string {
  if (!v) return "—";
  // 後端可能塞 "online (scanner)" 之類有附註的字串；只翻譯主詞
  const m = /^(\w+)(.*)$/.exec(v);
  if (!m) return v;
  const base = m[1].toLowerCase();
  let rest = m[2];
  // 來源附註本地化：(scanner) → (掃描代理)
  rest = rest.replace(/\(([^)]+)\)/g, (_full, src: string) => {
    const sk = `addresses.source_${src.trim().toLowerCase()}`;
    const sv = t(sk);
    return `(${sv === sk ? src : sv})`;
  });
  const key = `addresses.effective_${base}`;
  const out = t(key);
  return (out === key ? m[1] : out) + rest;
}

// 不偵測（exclude_from_ping）或 subnet 沒掃描時，後端的「離線」不該照搬 → 顯示未知，與狀態燈一致
const effectiveDisplay = computed(() => {
  const a = props.address;
  const v = a?.effective_status;
  if (!v) return "—";
  const noProbe = !!a?.exclude_from_ping || a?.subnet_scan_enabled === false
    || (!!a?.liveness_from_device && !a?.device_id);
  if (noProbe && /^offline/i.test(v)) return t("addresses.effective_unknown");
  return labelEffective(v);
});

const props = defineProps<{
  show: boolean;
  address: IPAddress | null;
  // create 模式：address 留 null，傳 createContext = { subnet_id, ip }
  createContext?: { subnet_id: string; ip: string } | null;
  // inline：當成獨立頁面內容渲染（不包 n-modal）；給 IPDetail 頁用
  inline?: boolean;
}>();

const emit = defineEmits<{
  (e: "update:show", v: boolean): void;
  (e: "saved", v: IPAddress): void;
  (e: "deleted", id: string): void;
  (e: "created", v: IPAddress): void;
  (e: "back"): void;
  (e: "ssh-open"): void;
  (e: "sftp-open"): void;
  (e: "ssh-popout"): void;
  (e: "rdp-open"): void;
  (e: "rdp-popout"): void;
  (e: "vnc-open"): void;
  (e: "vnc-popout"): void;
  (e: "novnc-open"): void;
  (e: "novnc-popout"): void;
  (e: "bmc-open"): void;
  (e: "bmc-popout"): void;
  (e: "rustdesk-open"): void;
  (e: "rustdesk-files-open"): void;
}>();

// RustDesk 分割按鈕的下拉：用本機的 RustDesk 客戶端軟體開啟（rustdesk:// 網址，不帶密碼）。
// 滑過去要有說明（使用者 2026-10-05）
// 「檔案傳輸」（規格附錄 J.6）：伺服器開了「允許網頁檔案傳輸」而且有權限（file_available）才出現，開新分頁
const rustdeskMenu = computed(() => [
  ...(props.address?.rustdesk?.file_available ? [{
    key: "files",
    label: () => h(NTooltip, { placement: "left", delay: 200 }, {
      trigger: () => h("span", { "data-testid": "rustdesk-open-files" }, t("rustdesk.open_files")),
      default: () => t("rustdesk.open_files_tip", { id: props.address?.rustdesk?.id ?? "" }),
    }),
    icon: () => h(NIcon, null, { default: () => h(FilesIcon) }),
  }] : []),
  ...(props.address?.rustdesk?.connect_uri ? [{
    key: "local",
    label: () => h(NTooltip, { placement: "left", delay: 200 }, {
      trigger: () => h("span", { "data-testid": "rustdesk-open-local" }, t("rustdesk.open_local_client")),
      default: () => t("rustdesk.open_local_client_tip", { id: props.address?.rustdesk?.id ?? "" }),
    }),
    icon: () => h(NIcon, null, { default: () => h(RustDeskIcon) }),
  }] : []),
]);
function onRustdeskMenu(key: string) {
  const uri = props.address?.rustdesk?.connect_uri;
  if (key === "files") {
    emit("rustdesk-files-open");
  } else if (key === "local" && uri) {
    logLocalOpen();
    window.location.href = uri;
  }
}
// 用本機客戶端開啟：連線不經 jt-ipam，至少記一筆稽核（「調查」的遠端連線記錄看得到）。記錄失敗不擋開啟
function logLocalOpen() {
  if (props.address?.id) logRustDeskLocalOpen(props.address.id).catch(() => undefined);
}

const { t, te, locale } = useI18n();
const msg = useMessage();
const { catalog } = useScanProbes();

// 此 IP 略過的探測項目（取代舊的單一 exclude_from_ping 開關）
const excludedProbes = ref<string[]>([]);

const editMode = ref(false);
const saving = ref(false);
const deleting = ref(false);

// 卡片寬度不足時，SSH/RDP/VNC 連線按鈕收成只有 icon（inline 詳細資料頁用）
const rootEl = ref<any>(null);
const consoleCompact = ref(false);
let cro: ResizeObserver | null = null;
onMounted(() => {
  void loadSuggestion();
  void loadSameMac();
  void ensureDeviceLoaded(props.address?.device_id);
  void ensureGroupsLoaded();
  const el = (rootEl.value?.$el ?? rootEl.value) as HTMLElement | undefined;
  if (el instanceof HTMLElement) {
    cro = new ResizeObserver(() => { consoleCompact.value = el.clientWidth < 900; });
    cro.observe(el);
  }
});
onBeforeUnmount(() => { cro?.disconnect(); cro = null; });

const isCreate = computed(() => !props.address && !!props.createContext);
const impact = useChangeImpact();
void impact.load();
const impactWizard = ref(false);

interface FormState {
  hostname: string;
  description: string;
  state: string;
  mac: string;
  owner: string;
  switch_port: string;
  ptr_ignore: boolean;
  note: string;
  customer_id: string | null;
  jump_host_id: string | null;
  console_agent_id: string | null;
  device_id: string | null;
  /** 共用給裝置群組（堆疊管理 IP、HA 虛擬 IP）：設了之後裝置欄跟著群組的代表裝置，不能在這裡改 */
  device_group_id: string | null;
  hostname_source_pin: string;  // "" = 自動 (跟全域優先序)
  ssh_enabled: boolean;
  sftp_enabled: boolean;
  rdp_enabled: boolean;
  vnc_enabled: boolean;
  novnc_enabled: boolean;
  bmc_enabled: boolean;
  rustdesk_enabled: boolean;
  is_dhcp_server: boolean;
  liveness_from_device: boolean;
}

const form = ref<FormState>(emptyForm());
// create 模式要填的 IP（FormState 不含 ip；新增時用此欄，可帶入 createContext.ip 預設值）
const createIp = ref("");
// 新增時：這個子網路裡用途是保留或 DHCP 的範圍（輸入的 IP 落在裡面就警告，不擋；GitHub issue #56）
const createRanges = ref<IPRange[]>([]);
const createRangeHit = computed(() => (isCreate.value && createIp.value.trim()
  ? rangeContaining(createIp.value.trim(), createRanges.value) : null));
watch(() => [props.show, props.createContext?.subnet_id] as const, async ([show, sid]) => {
  createRanges.value = [];
  if (!show || !sid || props.address) return;
  try {
    createRanges.value = (await listIPRanges(sid)).filter((r) => r.purpose === "reserved" || r.purpose === "dhcp");
  } catch { /* 只是提醒，讀不到就不提醒 */ }
}, { immediate: true });


function emptyForm(): FormState {
  return {
    hostname: "", description: "", state: "active", mac: "",
    owner: "", switch_port: "",
    ptr_ignore: false, note: "",
    customer_id: null,
    jump_host_id: null,
    console_agent_id: null,
    device_id: null,
    device_group_id: null,
    hostname_source_pin: "",
    ssh_enabled: false,
    sftp_enabled: false,
    rdp_enabled: false,
    vnc_enabled: false,
    novnc_enabled: false,
    bmc_enabled: false,
    rustdesk_enabled: false,
    is_dhcp_server: false,
    liveness_from_device: false,
  };
}

// 目前編輯中的 IP（去 /prefix）；找名稱或管理 IP 等於本 IP、但尚未連結的裝置 → 一鍵關聯
const currentIpHost = computed(() => (props.address?.ip ?? "").split("/")[0].trim());
const matchingDevice = computed<Device | null>(() => {
  if (form.value.device_id) return null;
  // 後端建議（下面的 suggestion）已經判斷過：它還看得到 MAC、連接埠，對到多台也不猜，而且會一併處理同名的 IP。
  // 有它的答案就只顯示它的 —— 兩邊各自判斷時常常指向同一台，畫面上就出現兩個意思一樣的按鈕
  // （使用者 2026-10-06：「這兩個差在那」）。這裡只補後端沒看到的情況：新增 IP、或主機名稱改了還沒存
  if (suggestion.value && hostnameUnchanged.value) return null;
  const ip = currentIpHost.value;
  // 以「主機名稱」或「IP」找尚未連結的裝置：hostname=nas2 → 裝置 nas2
  const hn = (form.value.hostname || props.address?.hostname || "").trim().toLowerCase();
  if (!ip && !hn) return null;
  return devices.value.find((d) => {
    const dip = d.ip ? d.ip.split("/")[0].trim() : "";
    const dname = (d.name || "").trim().toLowerCase();
    const dfqdn = String((d as any).fqdn || "").trim().toLowerCase();
    return (!!ip && dip === ip)            // 裝置主要 IP == 本 IP
      || (!!hn && dname === hn)            // 裝置名稱 == 本 IP 主機名稱
      || (!!hn && !!dfqdn && dfqdn === hn) // 裝置 FQDN == 本 IP 主機名稱
      || (!!ip && dname === ip);           // 舊行為：裝置名稱剛好是 IP 字串
  }) ?? null;
});
// ── 「還沒有裝置」時的建議：由後端判斷（它看得到 MAC、連接埠、同名 IP 有幾筆）。
//    **只是建議** —— 不按就什麼都不會發生。
const suggestion = ref<DeviceSuggestion | null>(null);
/** 表單上的主機名稱跟存著的一樣：後端建議就是用這個名稱算的 */
const hostnameUnchanged = computed(() =>
  (form.value.hostname || "").trim().toLowerCase() === (props.address?.hostname || "").trim().toLowerCase());
const applyingSuggestion = ref(false);
/** 要一併關聯的其他 IP —— **預設只勾 MAC 相同的那些**。
 *
 *  同主機名稱不等於同一台機器：DHCP 把位址回收給別台之後，IP 記錄上的舊主機名稱
 *  還留著。實機上一台筆電的名字散在九筆 IP 上，裡面有 Proxmox 的 VM 和一顆 ESP32 ——
 *  「全部掛上」那種開關等於把猜測當成事實。 */
const linkIds = ref<string[]>([]);

async function loadSuggestion() {
  suggestion.value = null;
  if (isCreate.value || !props.address?.id || form.value.device_id) return;
  try {
    suggestion.value = await getDeviceSuggestion(props.address.id);
    linkIds.value = (suggestion.value?.siblings ?? [])
      .filter((s) => s.same_mac).map((s) => s.id);
  } catch { /* 建議缺了不影響編輯 */ }
}

/** 按下去才會建立／關聯。建完直接反映在表單上，不用再存一次。 */
async function applySuggestion(create: boolean) {
  const sug = suggestion.value;
  if (!sug || !props.address?.id) return;
  applyingSuggestion.value = true;
  try {
    const updated = await applyDeviceSuggestion(props.address.id, {
      ...(create ? { create_name: sug.suggested_name ?? "" }
                 : { device_id: sug.existing_device_id ?? "" }),
      link_ip_ids: linkIds.value,
    });
    form.value.device_id = updated.device_id ?? null;
    suggestion.value = null;
    // 剛建出來的那一台不會在既有清單裡 —— 直接補進去，否則畫面只顯示得出 UUID
    await ensureDeviceLoaded(updated.device_id);
    emit("saved", updated);
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? String(e));
  } finally {
    applyingSuggestion.value = false;
  }
}

async function linkMatchingDevice() {
  if (!matchingDevice.value) return;
  form.value.device_id = matchingDevice.value.id;
  // 一鍵關聯即存：避免使用者漏按儲存、或編輯狀態問題導致「關聯沒生效」
  if (!isCreate.value && props.address) await save();
}

function fromAddress(a: IPAddress): FormState {
  return {
    hostname: a.hostname ?? "",
    description: a.description ?? "",
    state: a.state ?? "active",
    mac: a.mac ?? "",
    owner: a.owner ?? "",
    switch_port: a.switch_port ?? "",
    ptr_ignore: !!a.ptr_ignore,
    note: a.note ?? "",
    customer_id: a.customer_id ?? null,
    jump_host_id: a.jump_host_id ?? null,
    console_agent_id: a.console_agent_id ?? null,
    device_id: (a as any).device_id ?? null,
    device_group_id: a.device_group_id ?? null,
    hostname_source_pin: a.hostname_source_pin ?? "",
    ssh_enabled: !!a.ssh_enabled,
    sftp_enabled: !!a.sftp_enabled,
    rdp_enabled: !!a.rdp_enabled,
    vnc_enabled: !!a.vnc_enabled,
    novnc_enabled: !!a.novnc_enabled,
    bmc_enabled: !!a.bmc_enabled,
    rustdesk_enabled: !!a.rustdesk_enabled,
    is_dhcp_server: !!a.is_dhcp_server,
    liveness_from_device: !!a.liveness_from_device,
  };
}

const stateOptions = computed(() => [
  { label: labelState("active"), value: "active" },
  { label: labelState("reserved"), value: "reserved" },
  { label: labelState("offline"), value: "offline" },
  { label: labelState("dhcp"), value: "dhcp" },
  { label: labelState("used"), value: "used" },
  { label: labelState("deprecated"), value: "deprecated" },
  { label: labelState("quarantine"), value: "quarantine" },
]);

watch(
  () => [props.show, props.address?.id, props.createContext?.ip],
  () => {
    // create 模式自動進 edit form；既有 IP 進 view
    editMode.value = isCreate.value;
    form.value = props.address ? fromAddress(props.address) : emptyForm();
    createIp.value = (props.createContext?.ip ?? "").trim();
    // 略過探測初始化：優先用 excluded_probes；空但舊 exclude_from_ping=true → 回填 ['icmp']
    const a = props.address;
    if (a) {
      const ex = Array.isArray(a.excluded_probes) ? [...a.excluded_probes] : [];
      excludedProbes.value = ex.length ? ex : (a.exclude_from_ping ? ["icmp"] : []);
    } else {
      excludedProbes.value = [];
    }
    if (props.show) {
      void ensureCustomersLoaded();
      void loadDevices();
      void loadDhcpRanges();
      void loadRelations();
      void loadRelatedNat();
      void loadFirewall();
    }
  },
  { immediate: true },
);

const stateType = computed<"success" | "info" | "warning" | "error" | "default">(() => {
  const s = props.address?.state ?? "active";
  return s === "active" ? "success"
       : s === "reserved" ? "info"
       : s === "offline" ? "error"
       : s === "dhcp" ? "warning"
       : "default";
});

// ── 異動記錄：展開時才載入。**分頁 + 篩選 + 總數** ──
// 實機單一 IP 最多 1,838 筆、且幾乎全是同一種事件（整合互相覆寫主機名稱），
// 原本的「載入更多」要按 18 次、也看不出總共幾筆 → 改成先篩選再翻頁。
const HISTORY_PAGE = 50;
const history = ref<IPChangeLog[]>([]);
const historyTotal = ref(0);
const historyPage = ref(1);
const historyEventOpts = ref<HistoryFacet[]>([]);
const historySourceOpts = ref<HistoryFacet[]>([]);
const historyEvent = ref<string | null>(null);
const historySource = ref<string | null>(null);
const historyLoading = ref(false);
const historyLoaded = ref(false);
const historyPageCount = computed(() =>
  Math.max(1, Math.ceil(historyTotal.value / HISTORY_PAGE)));

async function loadHistory() {
  if (!props.address?.id) return;
  historyLoading.value = true;
  try {
    const page = await getAddressHistory(props.address.id, {
      limit: HISTORY_PAGE,
      offset: (historyPage.value - 1) * HISTORY_PAGE,
      event_type: historyEvent.value ?? undefined,
      source: historySource.value ?? undefined,
    });
    history.value = page.items;
    historyTotal.value = page.total;
    // 選項以「未篩選」為母體，選了之後其他選項不會消失
    historyEventOpts.value = page.event_types;
    historySourceOpts.value = page.sources;
    historyLoaded.value = true;
  } catch { /* silent */ } finally {
    historyLoading.value = false;
  }
}

function onHistoryFilter() {
  historyPage.value = 1;      // 換篩選條件一律回第一頁，否則會停在不存在的頁
  void loadHistory();
}

function onHistoryToggle(names: Array<string | number>) {
  if (names.includes("history") && !historyLoaded.value) void loadHistory();
}

/** 防火牆逐來源證據，時間新的排前面。 */
const fwSeen = computed<[string, string][]>(() => {
  const raw = (props.address as any)?.arp_seen as Record<string, string> | undefined;
  if (!raw) return [];
  return Object.entries(raw).sort((a, b) => (a[1] < b[1] ? 1 : -1));
});

/** `arp:opnsense` → 「ARP 表（OPNsense）」（與調查共用一份廠牌名稱，MikroTik 以前沒列到、印成原始的 mikrotik） */
const fwSeenLabel = (key: string) => fwSeenLabelOf(key, t);

// 「各來源最後出現」一區的列：固定順序（每個 IP 都同一個位置，比較時不用重新找；標題可點排序），
// 掃描代理／LibreNMS／ARP／DNS 一律列出（「—」本身就是資訊：這個來源沒看過它），
// Wazuh／Zabbix／OCS 只有比對得到才列；jump＝裝置頁上對應卡片的 id；
// live＝這個來源在上線判定裡的名字（系統設定「上線判定來源」用的鍵）
interface SeenRow { key: string; label: string; at: string | null | undefined; jump?: string; live: string[]; why: string }
const seenRows = computed<SeenRow[]>(() => {
  const a = props.address as any;
  if (!a) return [];
  // 依整合而來的來源：沒設定那個整合、也沒有資料就不列（使用者 2026-10-08：沒設定 AdGuard 卻有一列「AdGuard 設定」）。
  // 舊後端沒帶 seen_integrations 時照舊全列
  const has = (k: string, at: unknown) => !!at || !a.seen_integrations || a.seen_integrations[k] === true;
  const rows: SeenRow[] = [
    { key: "scanner", label: t("addresses.seen_src_scanner"), at: a.last_seen_scanner, live: ["scanner"],
      why: t("addresses.seen_why_scanner") },
  ];
  if (has("librenms", a.last_seen_librenms)) rows.push({ key: "librenms", label: "LibreNMS", at: a.last_seen_librenms,
    jump: "librenms", live: ["librenms"], why: t("addresses.seen_why_librenms") });
  // ARP 這一列是 LibreNMS 讀到的交換器 ARP 快取
  if (has("librenms", a.last_seen_arp)) rows.push({ key: "arp", label: "ARP", at: a.last_seen_arp,
    live: ["arp", "arp:librenms"], why: t("addresses.seen_why_arp") });
  if (a.last_seen_wazuh) rows.push({ key: "wazuh", label: t("addresses.seen_src_wazuh"), at: a.last_seen_wazuh,
    jump: "wazuh", live: ["wazuh"], why: t("addresses.seen_why_wazuh") });
  if (a.last_seen_zabbix) rows.push({ key: "zabbix", label: "Zabbix", at: a.last_seen_zabbix, live: ["zabbix"],
    why: t("addresses.seen_why_zabbix") });
  if (a.last_seen_ocs) rows.push({ key: "ocs", label: t("addresses.seen_src_ocs"), at: a.last_seen_ocs,
    jump: "ocs", live: ["ocs"], why: t("addresses.seen_why_ocs") });
  // RustDesk 客戶端的最後心跳（沒有心跳過的用 jt-ipam 最後看到它上線的時間）。不列入上線判定
  const rd = a.rustdesk;
  if (rd && (rd.last_heartbeat_at || rd.last_online_at)) rows.push({ key: "rustdesk", label: t("addresses.seen_src_rustdesk"),
    at: rd.last_heartbeat_at || rd.last_online_at, live: ["rustdesk"], why: t("addresses.seen_why_rustdesk") });
  for (const [k, v] of fwSeen.value) {
    const kind = k.split(":", 1)[0];
    const why = ["arp", "vpn", "lease"].includes(kind) ? t(`addresses.seen_why_fw_${kind}`) : "";
    rows.push({ key: k, label: fwSeenLabel(k), at: v, live: [k], why });
  }
  if (has("adguard", a.last_seen_dns)) rows.push({ key: "dns", label: t("addresses.seen_src_dns"), at: a.last_seen_dns,
    live: ["dns"], why: t("addresses.seen_why_dns") });
  return rows;
});
const seenTs = (r: SeenRow) => (r.at ? Date.parse(r.at) || 0 : 0);
const seenLatestKey = computed<string | null>(() => {
  let best: string | null = null;
  let bestTs = 0;
  for (const r of seenRows.value) {
    const ts = seenTs(r);
    if (ts > bestTs) { best = r.key; bestTs = ts; }
  }
  return best;
});
// 上線判定：與後端 recompute_effective_status 同一份規則（IP 詳細資料帶的 liveness_rule）。
// fresh＝採信且在時限內（會讓 IP 判為上線）、weak＝只有 ARP（「上線（ARP）」等級）、stale＝採信但過期、
// ignored＝系統設定沒有採信這個來源、none＝這個來源沒看過它
type SeenVerdict = "fresh" | "weak" | "stale" | "ignored" | "none";
const VERDICT_RANK: Record<SeenVerdict, number> = { fresh: 0, weak: 1, stale: 2, ignored: 3, none: 4 };
const seenRule = computed(() => (props.address as any)?.liveness_rule as { minutes: number; sources: string[] } | undefined);
function seenVerdict(r: SeenRow): SeenVerdict {
  const rule = seenRule.value;
  if (!r.at || !rule) return "none";
  if (!r.live.some((n) => rule.sources.includes(n))) return "ignored";
  const fresh = Date.now() - seenTs(r) <= rule.minutes * 60_000;
  if (!fresh) return "stale";
  return r.key === "arp" ? "weak" : "fresh";
}
const VERDICT_TAG: Record<SeenVerdict, "success" | "info" | "warning" | "default"> =
  { fresh: "success", weak: "info", stale: "warning", ignored: "default", none: "default" };
// 手機：「多久以前」與「說明」兩欄整欄拿掉（只用 CSS 藏起來，表格仍會保留那兩欄的寬度而左右捲動）
const seenNarrow = ref(typeof window !== "undefined" && window.matchMedia("(max-width: 600px)").matches);
let seenMq: MediaQueryList | null = null;
const onSeenMq = (e: MediaQueryListEvent) => { seenNarrow.value = e.matches; };
onMounted(() => {
  seenMq = window.matchMedia("(max-width: 600px)");
  seenNarrow.value = seenMq.matches;
  seenMq.addEventListener("change", onSeenMq);
});
onBeforeUnmount(() => seenMq?.removeEventListener("change", onSeenMq));
const SEEN_NARROW_MIN: Record<string, number> = { label: 76, at: 100, verdict: 92 };
const seenColumns = computed<DataTableColumns<SeenRow>>(() => seenColumnsAll.value
  .filter((c: any) => !(seenNarrow.value && (c.key === "ago" || c.key === "why")))
  .map((c: any) => (seenNarrow.value ? { ...c, minWidth: SEEN_NARROW_MIN[c.key] ?? c.minWidth } : c)));
const seenColumnsAll = computed<DataTableColumns<SeenRow>>(() => [
  {
    title: t("addresses.seen_col_source"), key: "label", minWidth: 150,
    sorter: (a, b) => a.label.localeCompare(b.label),
    // 放不下時只在括號前換行（「設備介面位址（LibreNMS）」以前不換行、溢出壓到時間欄）
    render: (r) => h("span", { class: "seen-label" },
      r.label.split(/(?=[（(])/).map((part) => h("span", { class: "nowrap" }, part))),
  },
  {
    title: t("addresses.seen_col_time"), key: "at", minWidth: 150,
    sorter: (a, b) => seenTs(a) - seenTs(b),
    render: (r) => h("div", { class: "mono" }, [
      r.jump && r.at && props.address?.device_id
        ? h("a", { class: "ocs-jump", "data-testid": `seen-jump-${r.jump}`,
                   title: t("addresses.seen_jump", { sys: r.label }),
                   onClick: () => goDevice(props.address?.device_id, r.jump) }, fmtDateTime(r.at))
        : fmtDateTime(r.at),
      // 手機：「多久以前」欄放不下，改成時間下方第二行
      r.at ? h("div", { class: "seen-ago-inline dim" },
        fmtRelative(r.at) + (r.key === seenLatestKey.value ? ` · ${t("addresses.seen_latest")}` : "")) : null,
    ]),
  },
  {
    title: t("addresses.seen_col_ago"), key: "ago", className: "seen-ago-col", minWidth: 110,
    sorter: (a, b) => seenTs(b) - seenTs(a),
    render: (r) => h("span", { class: "nowrap dim" }, [
      r.at ? fmtRelative(r.at) : "—",
      r.key === seenLatestKey.value
        ? h(NTag, { size: "tiny", type: "success", bordered: false, style: "margin-left: 6px" },
            () => t("addresses.seen_latest"))
        : null,
    ]),
  },
  {
    title: () => h(NTooltip, { trigger: "hover" }, {
      trigger: () => h("span", { class: "seen-th-help" }, t("addresses.seen_col_verdict")),
      default: () => t("addresses.seen_verdict_help", { minutes: seenRule.value?.minutes ?? "—" }),
    }),
    key: "verdict", minWidth: 120,
    sorter: (a, b) => VERDICT_RANK[seenVerdict(a)] - VERDICT_RANK[seenVerdict(b)],
    render: (r) => {
      const v = seenVerdict(r);
      if (v === "none") return h("span", { class: "dim" }, "—");
      return h(NTooltip, { trigger: "hover" }, {
        trigger: () => h(NTag, { size: "small", type: VERDICT_TAG[v], bordered: false, "data-testid": `seen-verdict-${r.key}` },
          () => t(`addresses.seen_verdict_${v}`)),
        default: () => t(`addresses.seen_verdict_${v}_tip`, { minutes: seenRule.value?.minutes ?? "—" }),
      });
    },
  },
  {
    title: t("addresses.seen_col_why"), key: "why", className: "seen-why-col", minWidth: 200,
    render: (r) => h("span", { class: "seen-why" }, r.why || "—"),
  },
]);

function eventLabel(e: string): string {
  const key = `ipChanges.event.${e}`;
  const out = t(key);
  return out === key ? e : out;
}

const HISTORY_TYPE: Record<string, "default" | "info" | "success" | "warning" | "error"> = {
  created: "success", deleted: "error", online: "success", offline: "warning",
  hostname_changed: "info", mac_changed: "info", arp_changed: "info",
  os_changed: "warning", kind_changed: "warning",
  state_changed: "warning", edited: "default",
};

// ── hostname 多來源 (feature A)：開 modal 時載入，給 pin 下拉用 ──
const hostnameSources = ref<HostnameSources | null>(null);
const hostnameSourcesLoaded = ref(false);

async function loadHostnameSources() {
  if (hostnameSourcesLoaded.value || !props.address?.id) return;
  try {
    hostnameSources.value = await getHostnameSources(props.address.id);
    hostnameSourcesLoaded.value = true;
  } catch { /* silent */ }
}

// 刪掉手動主機名稱：重算後的有效名稱要回到畫面（父層靠 saved 更新列表／詳情）
const clearingSource = ref<string | null>(null);
async function clearManualHostname() {
  const id = props.address?.id;
  clearingSource.value = null;
  if (!id) return;
  try {
    await clearHostnameSource(id, "manual");
    hostnameSourcesLoaded.value = false;
    const [updated] = await Promise.all([getAddress(id), loadHostnameSources()]);
    emit("saved", updated);
    msg.success(t("hostnameSrc.manual_cleared"));
  } catch (e) {
    msg.error(apiErrMsg(e));
  }
}

// pin 下拉選項：auto + 有觀測的來源 (顯示該來源回報的 hostname)
const pinOptions = computed(() => {
  const opts: Array<{ label: string; value: string }> = [
    { label: t("hostnameSrc.auto"), value: "" },
  ];
  for (const o of hostnameSources.value?.observations ?? []) {
    opts.push({ label: `${labelSource(o.source)} — ${o.hostname}`, value: o.source });
  }
  return opts;
});

// 交換器位置：儲存格式是「交換器 / 埠」（LibreNMS 同步就是這樣寫），
// 顯示與編輯都以 @ 呈現。這裡把單一字串拆成兩格、存檔前再組回去。
const swName = ref("");
const swPort = ref("");
function splitSwitchPort(v: string): [string, string] {
  const i = (v || "").indexOf(" / ");
  return i < 0 ? [v || "", ""] : [v.slice(0, i), v.slice(i + 3)];
}
watch(() => form.value.switch_port, (v) => {
  const [a, b] = splitSwitchPort(v || "");
  if (a !== swName.value) swName.value = a;
  if (b !== swPort.value) swPort.value = b;
}, { immediate: true });
watch([swName, swPort], ([a, b]) => {
  const name = (a || "").trim();
  const port = (b || "").trim();
  form.value.switch_port = port ? `${name} / ${port}` : name;
});

// 同一個 MAC 的另一個位址在線、這一個沒有（客戶 2026-10-14）：多 LAN 路由器各網段的閘道常是同一個 MAC，
// 多半是同一台設備的介面位址 → 提示可以勾「上線狀態跟著設備」。只是提示，套不套用由人決定
const sameMac = ref<SameMacAddress[]>([]);
async function loadSameMac() {
  sameMac.value = [];
  const a = props.address;
  if (!a?.id || !a.mac || a.liveness_from_device) return;
  try { sameMac.value = await getSameMacAddresses(a.id); } catch { /* 提示而已，讀不到就不顯示 */ }
}
const sameMacHint = computed(() => {
  const a = props.address;
  if (!a || a.liveness_from_device || classifyAddressLiveness(a) === "online") return null;
  return sameMac.value.find((x) => classifyAddressLiveness(x) === "online") ?? null;
});
// 機器換了位址（主機重灌、DHCP 換址）：MAC 是在這裡停了之後才出現在那裡 → 這筆多半是舊位址，
// 不建議「跟著設備」（照做的話舊位址會一直顯示上線；正式環境 2026-10-20）
const sameMacMoved = computed(() => !!props.address && !!sameMacHint.value
  && looksMoved(props.address, sameMacHint.value));
const applyingSameMac = ref(false);
async function applySameMac() {
  const a = props.address;
  const sib = sameMacHint.value;
  if (!a || !sib) return;
  applyingSameMac.value = true;
  try {
    // 還沒掛設備、而在線的那個有掛 → 一起掛到同一台；已經掛了就不動（不替人改關聯）
    const payload: IPAddressUpdate = { liveness_from_device: true };
    if (!a.device_id && sib.device_id) payload.device_id = sib.device_id;
    const updated = await updateAddress(a.id, payload);
    msg.success(t("common.ok"));
    emit("saved", updated);
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { applyingSameMac.value = false; }
}

// 換 IP 時清掉舊快取
watch(() => props.address?.id, () => {
  void loadSuggestion();
  void loadSameMac();
  void ensureDeviceLoaded(props.address?.device_id);
  history.value = [];
  historyLoaded.value = false;
  historyTotal.value = 0;
  historyPage.value = 1;
  historyEvent.value = null;
  historySource.value = null;
  hostnameSources.value = null;
  hostnameSourcesLoaded.value = false;
  switchPort.value = null;
});

// FDB 推得的 switch port(feature E)
const switchPort = ref<SwitchPortInfo | null>(null);
async function loadSwitchPort() {
  if (!props.address?.id) return;
  try { switchPort.value = await getAddressSwitchPort(props.address.id); }
  catch { switchPort.value = null; }
}

// immediate：inline（IP 詳細資料頁）模式下 show 一開始就是 true、id 一開始就有值，
// 沒有 immediate 這個 watch 永遠不觸發 → 直接開網址／重新整理時「主機名稱來源」
// 與 FDB 標籤整列消失，只有從清單開彈窗（show false→true）才看得到（使用者回報）。
watch(() => [props.show, props.address?.id], () => {
  if (props.show && props.address?.id) { void loadHostnameSources(); void loadSwitchPort(); }
}, { immediate: true });

function close() {
  // inline(頁面)模式：檢視中按取消＝返回上一頁；編輯中＝退出編輯
  if (props.inline) {
    if (editMode.value && !isCreate.value) { editMode.value = false; return; }
    emit("back");
    return;
  }
  emit("update:show", false);
}

async function save() {
  saving.value = true;
  try {
    if (isCreate.value && props.createContext) {
      const ipv = createIp.value.trim();
      if (!ipv) { msg.warning(t("addresses.ip_required")); saving.value = false; return; }
      const created = await createAddress({
        subnet_id: props.createContext.subnet_id,
        ip: ipv,
        hostname: form.value.hostname.trim() || null,
        description: form.value.description.trim() || null,
        state: form.value.state,
        mac: form.value.mac.trim() || null,
        owner: form.value.owner.trim() || null,
        switch_port: form.value.switch_port.trim() || null,
        note: form.value.note.trim() || null,
        customer_id: form.value.customer_id ?? null,
        jump_host_id: form.value.jump_host_id ?? null,
        device_id: form.value.device_id ?? null,
      });
      msg.success(t("common.ok"));
      emit("created", created);
      emit("update:show", false);
      return;
    }
    if (!props.address) return;
    const payload: IPAddressUpdate = {
      hostname: form.value.hostname.trim() || null,
      description: form.value.description.trim() || null,
      state: form.value.state,
      mac: form.value.mac.trim() || null,
      owner: form.value.owner.trim() || null,
      switch_port: form.value.switch_port.trim() || null,
      excluded_probes: excludedProbes.value,
      ptr_ignore: form.value.ptr_ignore,
      note: form.value.note.trim() || null,
      customer_id: form.value.customer_id ?? null,
      device_id: form.value.device_id ?? null,
      // 主控台出口：以前更新時沒有送這一欄，IP 層級的跳板覆寫在畫面上選了也不會存
      jump_host_id: form.value.jump_host_id ?? null,
      console_agent_id: form.value.console_agent_id ?? null,
      hostname_source_pin: form.value.hostname_source_pin || null,
      ssh_enabled: form.value.ssh_enabled,
      sftp_enabled: form.value.sftp_enabled,
      rdp_enabled: form.value.rdp_enabled,
      vnc_enabled: form.value.vnc_enabled,
      novnc_enabled: form.value.novnc_enabled,
      bmc_enabled: form.value.bmc_enabled,
      rustdesk_enabled: form.value.rustdesk_enabled,
      is_dhcp_server: form.value.is_dhcp_server,
      liveness_from_device: form.value.liveness_from_device,
    };
    // 裝置群組：只有管理員能改、而且只在真的改了才送（一般使用者送了會被 403 擋下整筆）。
    // 共用給群組的 IP，裝置欄由後端跟著代表裝置走，這裡不送。
    if (auth.me?.is_admin && (form.value.device_group_id ?? null) !== (props.address.device_group_id ?? null))
      payload.device_group_id = form.value.device_group_id ?? null;
    if (form.value.device_group_id) delete payload.device_id;
    const updated = await updateAddress(props.address?.id, payload);
    hostnameSourcesLoaded.value = false;  // 重新整理來源/有效 hostname
    msg.success(t("common.ok"));
    emit("saved", updated);
    editMode.value = false;
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.network"));
  } finally {
    saving.value = false;
  }
}

async function remove() {
  if (!props.address) return;
  deleting.value = true;
  try {
    const id = props.address?.id;
    await deleteAddress(id);
    msg.success(t("common.ok"));
    emit("deleted", id);
    close();
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.network"));
  } finally {
    deleting.value = false;
  }
}
</script>

<template>
  <component :is="inline ? 'div' : NModal" ref="rootEl" v-bind="inline ? {} : { show: props.show, 'onUpdate:show': (v: boolean) => emit('update:show', v) }">
    <n-card
      :style="inline ? 'width: 100%' : 'width: 880px; max-width: 95vw'"
      :bordered="false"
      :role="inline ? undefined : 'dialog'"
      :aria-modal="inline ? undefined : 'true'"
    >
      <!-- 標題：IP + 狀態標籤並排（比照裝置詳細資料的 名稱+類型標籤）-->
      <template #header>
        <span style="display:inline-flex;align-items:center;gap:10px;flex-wrap:wrap">
          <!-- 燈號：一眼看出上線／離線（與清單同一個元件、同一套判斷；滑過看各來源最後看到的時間） -->
          <span style="display:inline-flex;align-items:center;gap:8px">
            <LiveStatusDot v-if="!isCreate && props.address" :address="props.address" :size="12" />
            <span>{{ props.address?.ip ?? props.createContext?.ip ?? '' }}</span>
          </span>
          <n-tag v-if="isCreate" type="info" size="small">{{ t("common.create") }}</n-tag>
          <n-tag v-else :type="stateType" size="small">{{ labelState(props.address?.state) }}</n-tag>
          <!-- 「真的有 DHCP 租約」與「只是落在 DHCP 集區範圍內」是兩回事：
               後者常見於在集區範圍內設固定 IP 的機器，標成 DHCP 會誤導，改用中性的「DHCP 範圍」。 -->
          <!-- 固定分配（DHCP reservation）：這個位址被綁給某張網卡，不會被回收給別台。
               與上面的「DHCP／DHCP 範圍」是不同的事實，所以分開標。 -->
          <n-tooltip v-if="props.address?.dhcp_reserved" :delay="0">
            <template #trigger>
              <!-- 標題列的標籤一律有框線（與狀態標籤一致；使用者要求） -->
              <n-tag type="success" size="small">
                {{ t("addresses.dhcp_reserved_tag") }}
              </n-tag>
            </template>
            <div style="max-width:300px;line-height:1.5">
              <div>{{ t("addresses.dhcp_reserved_hint") }}</div>
              <template v-if="resv">
                <div v-if="resv.mac" style="margin-top:4px">
                  <strong>{{ t("addresses.dhcp_reserved_mac") }}：</strong>{{ resv.mac }}
                </div>
                <div v-if="resv.source_name">
                  <strong>{{ t("addresses.dhcp_server") }}：</strong>{{ resv.source_name }}
                  <span v-if="resv.engine">（{{ resv.engine }}）</span>
                </div>
                <div v-if="resv.hostname">{{ t("addresses.hostname") }}：{{ resv.hostname }}</div>
              </template>
            </div>
          </n-tooltip>
          <!-- DHCP 排除：在發放範圍裡、但伺服器不發給任何人（通常是設固定 IP 的設備）。
               發放範圍已經扣掉排除區間，所以不會同時出現「DHCP 範圍」。 -->
          <n-tooltip v-if="excl" :delay="0">
            <template #trigger>
              <n-tag size="small">{{ t("addresses.dhcp_excluded_tag") }}</n-tag>
            </template>
            <div style="max-width:300px;line-height:1.5">
              <div>{{ t("addresses.dhcp_excluded_hint") }}</div>
              <div style="margin-top:4px"><strong>{{ t("addresses.dhcp_excluded_range") }}：</strong>{{ exclLine }}</div>
            </div>
          </n-tooltip>
          <n-tooltip v-if="dhcpInfo || props.address?.in_dhcp_lease" :delay="0">
            <template #trigger>
              <n-tag :type="props.address?.in_dhcp_lease ? 'warning' : 'default'" size="small">
                {{ props.address?.in_dhcp_lease ? "DHCP" : t("addresses.dhcp_in_range_tag") }}
              </n-tag>
            </template>
            <div style="max-width:280px;line-height:1.5">
              <div v-if="props.address?.in_dhcp_lease">{{ t("addresses.dhcp_has_lease") }}</div>
              <template v-if="dhcpInfo">
                <div>{{ t("addresses.dhcp_pool_hint") }}</div>
                <div v-if="!props.address?.in_dhcp_lease" style="margin-top:4px">{{ t("addresses.dhcp_no_lease_hint") }}</div>
                <div style="margin-top:4px"><strong>{{ t("addresses.dhcp_server") }}：</strong>{{ dhcpInfo.server }}{{ dhcpInfo.source ? ` (${dhcpInfo.source})` : "" }}</div>
                <div>{{ t("addresses.dhcp_range") }}：{{ dhcpInfo.start }} – {{ dhcpInfo.end }}</div>
              </template>
            </div>
          </n-tooltip>
        </span>
      </template>
      <!-- inline(頁面)模式：操作工具列自標題列搬到內文最上方 -->
      <n-space v-if="inline && !isCreate" align="center" justify="end" :size="8" :wrap-item="false"
               style="margin-bottom: 10px">
          <template v-if="!editMode">
            <!-- SSH 連線分割按鈕：主鍵嵌入終端機、下箭頭可另開視窗（僅在啟用且有權限時顯示） -->
            <template v-if="props.address?.ssh_available">
              <n-tooltip :delay="200">
                <template #trigger>
                  <n-button-group key="hx-ssh">
                    <n-button type="info" size="small" @click="emit('ssh-open')">
                      <template #icon><n-icon><TerminalIcon /></n-icon></template>
                      <span v-if="!consoleCompact">{{ t("ssh.connect") }}</span>
                    </n-button>
                  </n-button-group>
                </template>
                {{ t("ssh.connect") }}
              </n-tooltip>
            </template>
            <!-- SFTP：自己的開關（sftp_enabled）與自己的權限判定，與 SSH 各自獨立顯示 ——
                 有些主機只想開放傳檔、不想開終端機。分成兩顆而不是塞進同一顆的下拉：
                 上下傳檔案跟開終端機是兩件不同的事，要用的人一開始就知道自己要哪一個。 -->
            <template v-if="props.address?.sftp_available">
              <n-tooltip :delay="200">
                <template #trigger>
                  <n-button-group key="hx-sftp">
                    <n-button size="small" @click="emit('sftp-open')">
                      <template #icon><n-icon><FilesIcon /></n-icon></template>
                      <span v-if="!consoleCompact">{{ t("sftp.connect_btn") }}</span>
                    </n-button>
                  </n-button-group>
                </template>
                {{ t("sftp.connect_hint") }}
              </n-tooltip>
            </template>
            <!-- RDP 連線分割按鈕：主鍵新分頁、下箭頭另開視窗（僅在啟用且有權限時顯示） -->
            <span v-if="props.address?.rdp_available" key="hx-rdp" class="conn-beta-wrap">
              <n-tooltip :delay="200">
                <template #trigger>
                  <n-button-group>
                    <n-button type="info" size="small" @click="emit('rdp-open')">
                      <template #icon><n-icon><DisplayIcon /></n-icon></template>
                      <span v-if="!consoleCompact">{{ t("rdp.connect") }}</span>
                    </n-button>
                  </n-button-group>
                </template>
                {{ t("rdp.connect") }}
              </n-tooltip>
            </span>
            <!-- VNC 連線分割按鈕：主鍵新分頁、下箭頭另開視窗（僅在啟用且有權限時顯示） -->
            <span v-if="props.address?.vnc_available" key="hx-vnc" class="conn-beta-wrap">
              <n-tooltip :delay="200">
                <template #trigger>
                  <n-button-group>
                    <n-button type="info" size="small" @click="emit('vnc-open')">
                      <template #icon><n-icon><VncIcon /></n-icon></template>
                      <span v-if="!consoleCompact">{{ t("vnc.connect") }}</span>
                    </n-button>
                  </n-button-group>
                </template>
                {{ t("vnc.connect") }}
              </n-tooltip>
            </span>
            <!-- PVE 主控台連線按鈕（noVNC/xterm；僅在該 IP 是 PVE VM/CT 且有權限時顯示），右上小標 PVE -->
            <span v-if="props.address?.novnc_available" key="hx-novnc" class="conn-beta-wrap">
              <n-tooltip :delay="200">
                <template #trigger>
                  <n-button-group>
                    <n-button type="warning" size="small" @click="emit('novnc-open')">
                      <template #icon>
                        <n-icon><TerminalIcon v-if="props.address?.pve?.kind === 'ct'" /><NoVncIcon v-else /></n-icon>
                      </template>
                      <span v-if="!consoleCompact">{{ props.address?.pve?.kind === 'ct' ? 'xterm' : 'noVNC' }}</span>
                    </n-button>
                  </n-button-group>
                </template>
                {{ `${props.address?.pve?.kind === 'ct' ? 'xterm' : 'noVNC'} ${t('novnc.connect')}` }}
              </n-tooltip>
              <span class="conn-beta-badge conn-pve-badge">PVE</span>
            </span>
            <!-- BMC 主控台連線按鈕（IPMI SOL；該 IP 啟用 BMC 且有權限時顯示），右上小標 SOL -->
            <span v-if="props.address?.bmc_available" key="hx-bmc" class="conn-beta-wrap">
              <n-tooltip :delay="200">
                <template #trigger>
                  <n-button-group>
                    <n-button type="warning" size="small" @click="emit('bmc-open')">
                      <template #icon><n-icon><TerminalIcon /></n-icon></template>
                      <span v-if="!consoleCompact">BMC</span>
                    </n-button>
                  </n-button-group>
                </template>
                {{ t("bmc.connect") }}
              </n-tooltip>
              <span class="conn-beta-badge conn-sol-badge">SOL</span>
            </span>
            <!-- RustDesk 伺服器開放網頁連線時：主按鈕在網頁裡直接連（相容 RustDesk 的網頁連線，新分頁），沒有小標 -->
            <!-- RustDesk 分割按鈕（照 Proxmox 主控台的樣式，使用者 2026-10-05）：主鍵在網頁裡連線；
                 右邊的 ▾ 展開「用本機 RustDesk 客戶端開啟」（rustdesk:// 網址，不帶密碼，連線在客戶端與對方之間） -->
            <span v-if="props.address?.rustdesk?.web_available" key="hx-rustdesk-web" class="conn-beta-wrap">
              <n-button-group>
                <n-tooltip :delay="200">
                  <template #trigger>
                    <n-button type="info" size="small" data-testid="rustdesk-web-connect" @click="emit('rustdesk-open')">
                      <template #icon><n-icon><RustDeskIcon /></n-icon></template>
                      <span v-if="!consoleCompact">{{ t("rustdesk.connect") }}</span>
                    </n-button>
                  </template>
                  {{ t("rustdesk.web_connect_hint", { id: props.address.rustdesk.id }) }}
                </n-tooltip>
                <n-dropdown v-if="rustdeskMenu.length" trigger="click" placement="bottom-end"
                            :options="rustdeskMenu" @select="onRustdeskMenu">
                  <n-tooltip :delay="200">
                    <template #trigger>
                      <n-button type="info" size="small" class="split-caret" data-testid="rustdesk-more"
                                :aria-label="t('rustdesk.more_ways')">
                        <template #icon><n-icon><ChevronDownIcon /></n-icon></template>
                      </n-button>
                    </template>
                    {{ t("rustdesk.more_ways") }}
                  </n-tooltip>
                </n-dropdown>
              </n-button-group>
            </span>
            <!-- 沒有網頁連線時：叫出使用者電腦上的 RustDesk 客戶端；右上小標「本機」＝不是在網頁裡連線 -->
            <span v-else-if="props.address?.rustdesk?.connect_uri" key="hx-rustdesk" class="conn-beta-wrap">
              <n-tooltip :delay="200">
                <template #trigger>
                  <n-button tag="a" size="small" :href="props.address.rustdesk.connect_uri" data-testid="rustdesk-connect"
                            @click="logLocalOpen">
                    <template #icon><n-icon><RustDeskIcon /></n-icon></template>
                    <span v-if="!consoleCompact">{{ t("rustdesk.connect") }}</span>
                  </n-button>
                </template>
                {{ t("rustdesk.connect_hint", { id: props.address.rustdesk.id }) }}（{{ t("rustdesk.badge_local_tip") }}）
              </n-tooltip>
              <span class="conn-beta-badge conn-local-badge" data-testid="rustdesk-local-badge">{{ t("rustdesk.badge_local") }}</span>
            </span>
            <!-- 連線鈕（SSH/RDP/VNC/PVE/BMC/RustDesk）與編輯/刪除間只留一條分隔線 -->
            <n-divider v-if="props.address?.ssh_available || props.address?.sftp_available || props.address?.rdp_available || props.address?.vnc_available || props.address?.novnc_available || props.address?.bmc_available || props.address?.rustdesk?.connect_uri || props.address?.rustdesk?.web_available"
                       key="hx-conn-div" vertical />
            <!-- 探測（只有管理員）：由負責這個子網路的掃描代理主動識別這是什麼主機 -->
            <n-button v-if="auth.me?.is_admin" key="hx-idf" size="small" data-testid="ip-identify-btn"
                      @click="openIdentify">
              <template #icon><n-icon><IdentifyIcon /></n-icon></template>{{ t("identify.title") }}
            </n-button>
            <!-- 調查：把這個位址散在各處的線索收在一起（追問題時最花時間的就是到處翻） -->
            <n-button key="hx-inv" size="small" @click="investigating = true">
              <template #icon><n-icon><SearchIcon /></n-icon></template>{{ t("investigate.title") }}
            </n-button>
            <!-- 預演改址：改之前先看哪些地方引用了這個位址（功能開啟、而且有修改權才顯示；後端會再檢查） -->
            <n-button v-if="impact.settings.value.enabled && auth.me?.can_edit !== false" key="hx-cip" size="small"
                      data-testid="ip-change-impact-btn" @click="impactWizard = true">
              <template #icon><n-icon><ChangeImpactIcon /></n-icon></template>{{ t("change_impact.entry_renumber") }}
            </n-button>
            <!-- 維護：臨時要停這台，按一下就靜音它的告警（2026-11-01） -->
            <MaintenanceQuickButton v-if="props.address" key="hx-maint" object-type="ip" :object-id="props.address.id"
                                    :label="String(props.address.ip).split('/')[0]" />
            <n-button key="hx-edit" type="primary" size="small" @click="editMode = true">
              <template #icon><n-icon><EditIcon /></n-icon></template>{{ t("common.edit") }}
            </n-button>
            <n-popconfirm key="hx-del-view" @positive-click="remove">
              <template #trigger>
                <n-button type="error" ghost size="small" :loading="deleting">
                  <template #icon><n-icon><DeleteIcon /></n-icon></template>{{ t("common.delete") }}
                </n-button>
              </template>
              {{ t("common.confirm_delete") }}
            </n-popconfirm>
            <n-button key="hx-back" size="small" @click="emit('back')">
              <template #icon><n-icon><ArrowLeftIcon /></n-icon></template>{{ t("common.back") }}
            </n-button>
          </template>
          <template v-else>
            <n-popconfirm key="hx-del-edit" @positive-click="remove">
              <template #trigger>
                <n-button type="error" ghost size="small" :loading="deleting">
                  <template #icon><n-icon><DeleteIcon /></n-icon></template>{{ t("common.delete") }}
                </n-button>
              </template>
              {{ t("common.confirm_delete") }}
            </n-popconfirm>
            <n-button key="hx-cancel" size="small" @click="close">
              <template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}
            </n-button>
            <n-button key="hx-save" type="success" size="small" :loading="saving" @click="save">
              <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
            </n-button>
          </template>
      </n-space>

      <div v-if="props.address || isCreate">
        <!-- view mode -->
        <n-descriptions v-if="!editMode" bordered :column="2" size="small" label-placement="left"
                        label-align="left"
                        :label-style="{ width: '132px', whiteSpace: 'nowrap', verticalAlign: 'top' }"
                        :content-style="{ verticalAlign: 'top', wordBreak: 'break-word', minWidth: '160px' }">
          <n-descriptions-item :label="t('addresses.ip')">{{ props.address?.ip }}</n-descriptions-item>
          <n-descriptions-item :label="t('common.status')">
            {{ labelState(props.address?.state) }}
            <!-- 角色旗標（閘道 / DHCP 伺服器 / 在 DHCP 範圍內…）清單頁早就顯示了，
                 詳細資料頁卻看不到 —— 同一個 IP 在兩個畫面資訊不一致很容易誤判。 -->
            <IpRoleTags v-if="props.address" :row="props.address" style="margin-left:8px" />
          </n-descriptions-item>
          <n-descriptions-item :label="t('addresses.hostname')">
            <span>{{ props.address?.hostname ?? "—" }}</span>
            <n-tag v-if="hostnameSources?.pin" size="tiny" type="warning" :bordered="false"
                   style="margin-left: 6px">{{ t("hostnameSrc.pinned", { src: hostnameSources.pin }) }}</n-tag>
          </n-descriptions-item>
          <n-descriptions-item :label="t('addresses.mac')">
            <!-- 點 MAC 看它的完整歷程（用過哪些 IP、出現在哪個交換器埠） -->
            <router-link v-if="props.address?.mac" :to="{ name: 'mac-history', params: { mac: props.address.mac } }"
                         class="mac-history-link" :title="t('mac_history.open_hint')">{{ props.address.mac }}</router-link>
            <span v-else>—</span>
            <n-tag v-if="props.address?.mac_vendor" size="tiny" type="info" bordered
                   style="margin-left: 6px">{{ props.address.mac_vendor }}</n-tag>
          </n-descriptions-item>
          <n-descriptions-item :label="t('cols.os')">
            <n-tooltip v-if="props.address?.os_family" :disabled="!props.address?.os_guess">
              <template #trigger>
                <span style="display:inline-flex;align-items:center;gap:6px">
                  <os-icon :family="props.address.os_family" :size="16" />
                  <span>{{ osDisplayLabel(catalog.os_families, props.address.os_family, props.address.os_guess, locale) }}</span>
                  <span v-if="props.address?.os_source" style="opacity:0.6;font-size:0.85em">
                    {{ "（" + t("os_precedence.source_label") + ": " + t("os_precedence.src_" + props.address.os_source) + "）" }}
                  </span>
                </span>
              </template>
              {{ props.address?.os_guess }}
            </n-tooltip>
            <span v-else>—</span>
          </n-descriptions-item>
          <!-- 掃描代理定期偵測判讀出的設備類型（含 Recog 指紋庫）；只有判讀過才顯示這一列 -->
          <n-descriptions-item v-if="props.address?.device_kind" :label="t('cols.device_kind')">
            <span style="display:inline-flex;align-items:center;gap:6px" data-testid="ip-device-kind">
              <device-kind-icon :kind="props.address.device_kind" :size="16" />
              <span>{{ te(`identify.type.${props.address.device_kind}`)
                ? t(`identify.type.${props.address.device_kind}`) : props.address.device_kind }}</span>
              <span v-if="props.address?.device_model" style="opacity:0.7">· {{ props.address.device_model }}</span>
            </span>
          </n-descriptions-item>
          <n-descriptions-item :label="t('addresses.owner')">{{ props.address?.owner ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('addresses.switch_port')">
            <template v-if="props.address?.switch_port">
              <n-tooltip v-if="props.address?.switch_port_confident === false">
                <template #trigger>
                  <switch-port-label :value="props.address.switch_port" dim />
                </template>
                {{ t("addresses.switch_port_uncertain") }}
              </n-tooltip>
              <switch-port-label v-else :value="props.address.switch_port" />
            </template>
            <span v-else>—</span>
            <n-tag v-if="switchPort?.likely_access_port?.port" size="tiny" type="info"
                   style="margin-left: 6px">
              FDB: <switch-port-label :value="`${switchPort.likely_access_port.switch} / ${switchPort.likely_access_port.port}`" />
            </n-tag>
          </n-descriptions-item>
          <n-descriptions-item :label="t('nav.devices')" :span="2">
            <a v-if="props.address?.device_id" href="#"
               style="color: var(--primary-color, #18a058); text-decoration: none;"
               @click.prevent="goDevice(props.address?.device_id)">
              {{ deviceLabel(props.address?.device_id) }}
            </a>
            <span v-else>—</span>
            <n-tag v-if="props.address?.device_group_name" size="tiny" type="info" :bordered="false"
                   style="margin-left: 6px" data-testid="ip-group-shared-tag">
              {{ t("device_groups.ip_shared_by", { name: props.address.device_group_name }) }}
            </n-tag>
          </n-descriptions-item>
          <!-- 上線狀態與最後回報在「各來源最後出現」、主機名稱在「主機名稱來源」，這裡不重複（使用者要求） -->
          <n-descriptions-item
            v-if="hostnameSources && hostnameSources.observations.length"
            :label="t('hostnameSrc.sources')" :span="2"
          >
            <n-space :size="6" style="flex-wrap: wrap">
              <!-- 這裡是「各來源分別回報了什麼」的觀測記錄，實際採用哪一個由主機名稱優先序決定。
                   只有「手動」可以刪：它不會自己過期，換了設備後舊名字就一直掛著；
                   其他來源刪了下次同步又會回來（換設備時由 MAC 異動自動清掉，見 forget_device_names）。 -->
              <n-popconfirm
                v-for="o in hostnameSources.observations" :key="o.source"
                trigger="manual" :show="clearingSource === o.source"
                @clickoutside="clearingSource = null"
                @positive-click="clearManualHostname"
                @negative-click="clearingSource = null"
              >
                <template #trigger>
                  <n-tooltip :delay="200">
                    <template #trigger>
                      <!-- 綠＝目前採用；黃＝名字和目前採用的不同（例如 NetBIOS 名稱與 DNS 主機名稱不一樣）；
                           超過一天沒人回報的標「N 天前」，殘留的舊名字才看得出來 -->
                      <n-tag
                        size="small" :bordered="false"
                        :type="o.hostname === props.address?.hostname ? 'success'
                          : differsFrom(o.hostname, props.address?.hostname) ? 'warning' : 'default'"
                        :closable="o.source === 'manual' && auth.me?.can_edit !== false"
                        :data-testid="`hostname-src-${o.source}`"
                        @close="clearingSource = o.source"
                      >
                        {{ labelSource(o.source) }}: {{ o.hostname }}
                        <span v-if="o.source !== 'manual' && daysSince(o.last_seen_at ?? o.observed_at) >= 1"
                              style="opacity: .7" :data-testid="`hostname-src-age-${o.source}`">
                          · {{ t("hostnameSrc.days_ago", { n: daysSince(o.last_seen_at ?? o.observed_at) }) }}
                        </span>
                      </n-tag>
                    </template>
                    <div :data-testid="`hostname-src-tip-${o.source}`">
                      <div v-if="differsFrom(o.hostname, props.address?.hostname)">
                        {{ t("hostnameSrc.differs", { name: props.address?.hostname ?? "" }) }}
                      </div>
                      <div v-if="o.source === 'manual'">{{ t("hostnameSrc.manual_at", { at: fmtDateTime(o.observed_at) }) }}</div>
                      <template v-else-if="o.reports?.length">
                        <div v-for="(r, i) in o.reports" :key="i">
                          {{ reporterLabel(t, r) }} · {{ r.hostname }} · {{ t("hostnameSrc.last_seen", { at: fmtDateTime(r.last_seen_at) }) }}
                        </div>
                      </template>
                      <div v-else>{{ t("hostnameSrc.changed_at", { at: fmtDateTime(o.observed_at) }) }}</div>
                    </div>
                  </n-tooltip>
                </template>
                {{ t("hostnameSrc.clear_manual_confirm", { name: o.hostname }) }}
              </n-popconfirm>
            </n-space>
          </n-descriptions-item>
          <n-descriptions-item :label="t('common.description')" :span="2">
            {{ props.address?.description ?? "—" }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('addresses.note')" :span="2">
            {{ props.address?.note ?? "—" }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('nav.customers')" :span="2">
            {{ customerLabelFor(props.address?.customer_id) }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('addresses.exclude_from_ping')">{{ props.address?.exclude_from_ping ? "✓" : "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('addresses.ptr_ignore')">{{ props.address?.ptr_ignore ? "✓" : "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('addresses.source')">{{ labelSource(props.address?.discovery_source) }}</n-descriptions-item>
          <!-- 勾了「上線狀態跟著設備」才顯示：依據是同設備的哪個位址（每輪同步更新） -->
          <n-descriptions-item v-if="props.address?.liveness_from_device" :label="t('addresses.liveness_from_device')">
            <span data-testid="ip-liveness-from-device-row">
              ✓ {{ props.address?.device_evidence_ip ? t("addresses.liveness_from_device_via", { ip: props.address.device_evidence_ip })
                : !props.address?.device_id ? t("addresses.liveness_from_device_no_device_short") : "" }}
            </span>
          </n-descriptions-item>
          <!-- 有固定分配才顯示這一列：沒有的話多一列「否」只是噪音 -->
          <n-descriptions-item v-if="props.address?.dhcp_reserved" :label="t('addresses.dhcp_reserved_tag')">
            {{ resvLine }}
          </n-descriptions-item>
          <n-descriptions-item v-if="excl" :label="t('addresses.dhcp_excluded_tag')">
            {{ exclLine }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('addresses.effective_status')">{{ effectiveDisplay }}</n-descriptions-item>
          <!-- 虛擬化對應：比對到 VM 網卡才顯示「虛擬機」；比對不到不顯示——
               「查無」是「不知道」，不是「實體機」，反向斷言是誤導 -->
          <n-descriptions-item v-if="(props.address as any)?.virt_vm" :label="t('addresses.virt')">
            <n-tag size="small" type="info">{{ virtTagText((props.address as any).virt_vm, t) }}</n-tag>
            <span style="margin-left:6px">{{ (props.address as any).virt_vm.vm }}
              <span style="opacity:.65">@ {{ (props.address as any).virt_vm.cluster || (props.address as any).virt_vm.platform }}</span></span>
          </n-descriptions-item>
          <n-descriptions-item :label="t('common.created_at')">{{ fmtDateTime(props.address?.created_at) }}</n-descriptions-item>
          <n-descriptions-item :label="t('common.updated_at')" :span="2">{{ fmtDateTime(props.address?.updated_at) }}</n-descriptions-item>
        </n-descriptions>

        <!-- 各來源最後出現時間：獨立成一區、同一欄對齊並附「多久以前」，方便一眼比較哪個來源還在看到它
             （使用者要求）。以前散在上面的欄位之間，要上下找、自己心算差多久。
             LibreNMS／Wazuh／OCS 的時間可點 → 帶到裝置頁並捲到該系統的卡片。 -->
        <!-- RustDesk 獨立一區（使用者要求），不擠在上面的欄位表裡；上線狀態與最後心跳在「各來源最後出現」、
             主機名稱在「主機名稱來源」。同一份資訊在裝置頁也有一張卡片（客戶端裝在機器上） -->
        <div v-if="!editMode && props.address?.rustdesk" class="rdx-sec" data-testid="ip-rustdesk-section">
          <div class="detail-sec-title">RustDesk</div>
          <!-- Key 設錯：照樣註冊、回報、區網直連，只有走中繼被拒（網頁連線一定走中繼）→ 一定要講出來 -->
          <n-alert v-if="props.address.rustdesk.key_problem" type="error" :show-icon="true" style="margin-bottom: 8px"
                   data-testid="ip-rustdesk-key-problem">
            {{ t(props.address.rustdesk.key_problem.scope === "hbbs" ? "rustdesk.key_problem_hint_hbbs" : "rustdesk.key_problem_hint",
                 { at: fmtDateTime(props.address.rustdesk.key_problem.at), n: props.address.rustdesk.key_problem.count }) }}
            {{ t("rustdesk.key_problem_fix") }}
          </n-alert>
          <n-descriptions bordered :column="seenNarrow ? 1 : 2" size="small" label-placement="left"
                          :label-style="{ whiteSpace: 'nowrap' }" data-testid="ip-rustdesk-reported">
            <n-descriptions-item :label="t('rustdesk.id')">
              <span class="rd-id" data-testid="ip-rustdesk-id">{{ props.address.rustdesk.id }}</span>
              <CopyButton :text="props.address.rustdesk.id" />
            </n-descriptions-item>
            <n-descriptions-item :label="t('rustdesk.server_pick')">{{ props.address.rustdesk.server_name ?? "—" }}</n-descriptions-item>
            <n-descriptions-item :label="t('rustdesk.col_username')">{{ props.address.rustdesk.username ?? "—" }}</n-descriptions-item>
            <!-- 客戶端回報的是「windows / Windows 11 Pro - 11 (26200)」：前面的平台名稱重複，拿掉 -->
            <n-descriptions-item :label="t('rustdesk.col_os')">
              {{ props.address.rustdesk.os ? rustdeskOs(props.address.rustdesk.os)[0] : "—" }}
            </n-descriptions-item>
            <n-descriptions-item :label="t('rustdesk.col_client_version')">{{ props.address.rustdesk.version ?? "—" }}</n-descriptions-item>
            <n-descriptions-item :label="t('rustdesk.col_evidence')">
              <span v-if="props.address.rustdesk.evidence?.length" class="rdx-ev">
                <n-tag v-for="e in props.address.rustdesk.evidence" :key="e" size="tiny" type="success" :bordered="false">
                  {{ t(`rustdesk.ev_${e}`) }}
                </n-tag>
              </span>
              <template v-else>—</template>
            </n-descriptions-item>
          </n-descriptions>
        </div>

        <n-alert v-if="!editMode && sameMacHint && sameMacMoved" type="warning" :bordered="false"
                 style="margin-top: 12px" data-testid="ip-same-mac-moved">
          {{ t("addresses.same_mac_moved", { ip: sameMacHint.ip.split("/")[0],
                                             since: fmtDateTime(sameMacHint.mac_since ?? ""),
                                             last: fmtDateTime(new Date(lastCountedAt(props.address!) ?? 0).toISOString()) }) }}
        </n-alert>
        <n-alert v-else-if="!editMode && sameMacHint" type="info" :bordered="false" style="margin-top: 12px"
                 data-testid="ip-same-mac-hint">
          {{ t("addresses.same_mac_hint", { ip: sameMacHint.ip.split("/")[0] }) }}
          <div style="margin-top: 6px">
            <n-button size="small" type="primary" :loading="applyingSameMac" :disabled="auth.me?.can_edit === false"
                      data-testid="ip-same-mac-apply" @click="applySameMac">
              <template #icon><n-icon><CheckIcon /></n-icon></template>
              {{ !props.address?.device_id && sameMacHint.device_id ? t("addresses.same_mac_apply_link")
                : t("addresses.same_mac_apply") }}
            </n-button>
          </div>
        </n-alert>
        <div v-if="!editMode && seenRows.length" data-testid="ip-seen-section">
          <div class="detail-sec-title">{{ t("addresses.seen_title") }}</div>
          <n-data-table class="seen-table" size="small" :bordered="true" :single-line="false"
                        :columns="seenColumns" :data="seenRows" :row-key="(r: any) => r.key"
                        :row-class-name="(r: any) => (r.key === seenLatestKey ? 'seen-latest' : '')"
                        :scroll-x="seenNarrow ? undefined : 720" />
        </div>

        <!-- 上下關係鏈：區段 → 子網路 → 位址 → 裝置 → 機櫃 → 機房 -->
        <div v-if="!editMode && relations.length > 1" style="margin-top: 14px">
          <div class="detail-sec-title">{{ t("relations.title") }}</div>
          <relation-chain :nodes="relations" :current-id="props.address?.id" />
        </div>

        <!-- 防火牆反查：這個 IP 被哪些規則／別名管到（any 規則不列，另以一句話註明） -->
        <div v-if="!editMode && fwInfo && (fwInfo.rules.length || fwInfo.aliases.length)"
             style="margin-top: 14px">
          <div class="detail-sec-title">
            {{ t("addresses.fw_title", { n: fwInfo.rules.length }) }}
          </div>
          <!-- 表格排法（使用者要求「欄位對好」）：以前一條規則擠成一行字，來源／目的／說明長短不一，
               上下完全對不齊，很難一眼比較 -->
          <div v-if="fwInfo.rules.length" class="fw-table-wrap">
            <table v-col-resize class="fw-table">
              <thead><tr>
                <th></th><th>{{ t("cols.fw") }}</th><th>{{ t("cols.action") }}</th>
                <th>{{ t("cols.source") }}</th><th></th><th>{{ t("cols.destination") }}</th>
                <th>{{ t("cols.port") }}</th><th>{{ t("addresses.fw_col_why") }}</th>
                <th>{{ t("cols.description") }}</th>
              </tr></thead>
              <tbody>
                <tr v-for="(r, i) in fwInfo.rules.slice(0, 12)" :key="i"
                    :class="{ 'fw-row-link': fwLink('rules', r) }" :title="fwLink('rules', r) ? t('addresses.fw_row_goto') : undefined"
                    @click="goTo(fwLink('rules', r))">
                  <td><n-tag size="tiny">{{ r.source_type }}</n-tag></td>
                  <td class="nowrap">{{ r.firewall }}</td>
                  <td class="nowrap" :class="fwActionClass(r.action)">{{ r.action }}</td>
                  <td class="mono">{{ r.src }}</td>
                  <td class="dim">→</td>
                  <td class="mono">{{ r.dst }}</td>
                  <td class="mono">{{ r.dst_port || "—" }}</td>
                  <td class="dim nowrap">{{ fwMatchText(r.match) }}</td>
                  <td class="dim">{{ r.descr || "—" }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <!-- 這句是在說明上面的規則清單，要緊貼著它，不要被別名隔開 -->
          <div v-if="fwInfo.rules.length" style="font-size: 11.5px; opacity: 0.55; margin-top: 4px">
            {{ t("addresses.fw_any_note") }}
          </div>
          <!-- 所屬別名：跟規則同一種排法 —— 區塊標題獨立一行，每筆一行、廠牌標籤在前、
               寫出是哪一台防火牆。以前是一行「所屬別名：[名稱（廠牌）]」夾在規則與註記中間，
               標題樣式、廠牌位置都跟上下兩段不一樣。 -->
          <template v-if="fwInfo.aliases.length">
            <div class="detail-sec-title">
              {{ t("addresses.fw_aliases_title", { n: fwInfo.aliases.length }) }}
            </div>
            <div class="fw-table-wrap">
              <table v-col-resize class="fw-table">
                <thead><tr>
                  <th></th><th>{{ t("cols.fw") }}</th><th>{{ t("cols.name") }}</th>
                  <th>{{ t("cols.description") }}</th>
                </tr></thead>
                <tbody>
                  <tr v-for="a in fwInfo.aliases" :key="`${a.source_type}:${a.firewall}:${a.name}`"
                      :class="{ 'fw-row-link': fwLink('aliases', a) }" :title="fwLink('aliases', a) ? t('addresses.fw_row_goto') : undefined"
                      @click="goTo(fwLink('aliases', a))">
                    <td><n-tag size="tiny">{{ a.source_type }}</n-tag></td>
                    <td class="nowrap">{{ a.firewall || "—" }}</td>
                    <td class="mono">{{ a.name }}</td>
                    <td class="dim">{{ a.descr || "—" }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </template>
        </div>

        <!-- 關聯的 NAT 規則 -->
        <div v-if="!editMode && relatedNat.length" style="margin-top: 14px">
          <div class="detail-sec-title">
            {{ t("addresses.related_nat", { n: relatedNat.length }) }}
          </div>
          <div class="fw-table-wrap">
            <!-- 欄位順序跟防火牆規則一樣：廠牌、設備名稱在最前面（使用者要求） -->
            <table v-col-resize class="fw-table">
              <thead><tr>
                <th></th><th>{{ t("cols.fw") }}</th><th>{{ t("cols.type") }}</th>
                <th>{{ t("cols.name") }}</th><th>{{ t("addresses.nat_mapping") }}</th><th>{{ t("cols.protocol") }}</th>
              </tr></thead>
              <tbody>
                <tr v-for="n in relatedNat" :key="n.id" class="fw-row-link"
                    :class="{ 'fw-row-disabled': n.disabled }"
                    :title="t('addresses.fw_row_goto')" @click="goTo(natLink(n))">
                  <td><n-tag size="tiny">{{ n.source_firewall_id ? n.source_kind : (n.source_label || "—") }}</n-tag></td>
                  <td class="nowrap">{{ n.source_firewall_name || "—" }}</td>
                  <td><n-tag size="tiny" type="info" :bordered="false">{{ n.type }}</n-tag></td>
                  <td class="nat-ref-name nowrap">
                    {{ n.name }}
                    <n-tag v-if="n.disabled" size="tiny" :bordered="false" data-testid="ip-nat-disabled">{{ t("nat.status_disabled") }}</n-tag>
                  </td>
                  <td class="mono nowrap" data-testid="ip-nat-mapping">
                    <span :class="{ 'nat-self': n.src_ip === addrText }">{{ natExternal(n) }}</span>
                    <span class="dim"> → </span>
                    <span :class="{ 'nat-self': n.dst_ip === addrText }">{{ natInternal(n) }}</span>
                  </td>
                  <td class="mono">{{ n.protocol || "—" }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- 異動記錄 (feature B)，展開才載入 -->
        <n-collapse v-if="!editMode && props.address" style="margin-top: 12px" @update:expanded-names="onHistoryToggle">
          <n-collapse-item name="history">
            <!-- 標題帶總數：不然使用者不知道自己看到的是全部還是一頁 -->
            <template #header>
              {{ t("ipChanges.history") }}
              <n-text v-if="historyLoaded" depth="3" style="font-size: 12.5px">
                （{{ historyTotal }}）
              </n-text>
            </template>
            <n-spin :show="historyLoading">
              <!-- 篩選：整合互相覆寫會讓單一事件類型灌爆清單，先篩再看才有意義 -->
              <n-space v-if="historyLoaded && (historyEventOpts.length > 1 || historySourceOpts.length > 1)"
                       :size="8" style="margin-bottom: 8px">
                <n-select v-model:value="historyEvent" clearable size="small" style="width: 190px"
                          :placeholder="t('ipChanges.all_events')"
                          :options="historyEventOpts.map((o) => ({
                            label: `${eventLabel(o.value)}（${o.count}）`, value: o.value }))"
                          @update:value="onHistoryFilter" />
                <n-select v-model:value="historySource" clearable size="small" style="width: 190px"
                          :placeholder="t('ipChanges.all_sources')"
                          :options="historySourceOpts.map((o) => ({
                            label: `${labelSource(o.value)}（${o.count}）`, value: o.value }))"
                          @update:value="onHistoryFilter" />
              </n-space>
              <n-empty v-if="historyLoaded && !history.length" :description="t('ipChanges.empty')" size="small" />
              <n-timeline v-else style="padding: 4px 0">
                <n-timeline-item
                  v-for="h in history" :key="h.id"
                  :type="HISTORY_TYPE[h.event_type] ?? 'default'"
                  :time="fmtDateTime(h.created_at)"
                  :class="{ 'log-dim': isOldLog(h.created_at) }"
                >
                  <template #header>
                    <n-space align="center" :size="6">
                      <strong>{{ eventLabel(h.event_type) }}</strong>
                      <n-tag size="tiny" :bordered="false">{{ labelSource(h.source) }}</n-tag>
                      <n-text v-if="h.actor_username" depth="3" style="font-size: 12px">{{ h.actor_username }}</n-text>
                    </n-space>
                  </template>
                  <n-text v-if="h.old_value != null || h.new_value != null" style="font-size: 13px">
                    <span v-if="h.field">{{ h.field }}: </span>
                    <n-text depth="3" delete><ChangeValue :field="h.field" :value="h.old_value" /></n-text>
                    →
                    <n-text strong><ChangeValue :field="h.field" :value="h.new_value" /></n-text>
                  </n-text>
                  <n-text v-if="h.note" depth="3" style="font-size: 12px; display: block">{{ h.note }}</n-text>
                </n-timeline-item>
              </n-timeline>
              <div v-if="historyPageCount > 1" style="display:flex; justify-content:center; margin-top: 10px">
                <n-pagination v-model:page="historyPage" :page-count="historyPageCount"
                              size="small" @update:page="loadHistory" />
              </div>
            </n-spin>
          </n-collapse-item>
        </n-collapse>

        <!-- edit mode -->
        <n-form v-else label-placement="top">
          <n-form-item v-if="isCreate" :label="t('addresses.ip')" required style="margin-bottom: 12px">
            <div style="width: 100%">
              <n-input v-model:value="createIp" placeholder="198.51.100.10" />
              <!-- 落在保留或 DHCP 範圍：自動配發會跳過，手動仍可以用，但要讓人知道（GitHub issue #56） -->
              <n-alert v-if="createRangeHit" type="warning" :bordered="false" style="margin-top: 6px"
                       data-testid="ip-create-in-range">
                {{ t(createRangeHit.purpose === "dhcp" ? "addresses.in_dhcp_range" : "addresses.in_reserved_range",
                     { name: createRangeHit.name || `${createRangeHit.start_ip} – ${createRangeHit.end_ip}` }) }}
              </n-alert>
            </div>
          </n-form-item>
          <n-space :size="12" :wrap-item="false" style="flex-wrap: wrap">
            <n-form-item :label="t('addresses.hostname')" style="flex: 1 1 300px">
              <n-input v-model:value="form.hostname" placeholder="host.example.com" />
            </n-form-item>
            <n-form-item :label="t('common.status')" style="flex: 0 0 160px">
              <n-select v-model:value="form.state" :options="stateOptions" />
            </n-form-item>
          </n-space>
          <n-form-item v-if="!isCreate" :label="t('hostnameSrc.pin_label')" style="margin-bottom: 12px">
            <n-select
              v-model:value="form.hostname_source_pin"
              :options="pinOptions" :placeholder="t('hostnameSrc.auto')"
            />
            <template #feedback>
              <span style="font-size: 11px; opacity: .7; display: block; padding-bottom: 4px">{{ t("hostnameSrc.pin_hint") }}</span>
            </template>
          </n-form-item>
          <n-space :size="12" :wrap-item="false" style="flex-wrap: wrap">
            <n-form-item :label="t('addresses.mac')" style="flex: 1 1 240px">
              <n-input v-model:value="form.mac" placeholder="aa:bb:cc:dd:ee:ff" />
            </n-form-item>
            <n-form-item :label="t('addresses.owner')" style="flex: 1 1 240px">
              <n-input v-model:value="form.owner" />
            </n-form-item>
            <!-- 交換器位置：拆成兩格。單一輸入框會讓人照著唯讀畫面打成 sw@port，
                 但實際儲存格式是「交換器 / 埠」，打錯就顯示成一整團字串 -->
            <n-form-item :label="t('addresses.switch_port')" style="flex: 1 1 320px">
              <!-- 一行到底：交換器｜@｜埠。先前用 n-space 排，欄寬不夠時會把第二個
                   輸入框擠到下一行，「@」孤零零留在右邊 —— 看起來像壞掉。
                   input-group 會自己縮，不會換行。 -->
              <n-input-group>
                <n-input v-model:value="swName" :placeholder="t('addresses.switch_name_ph')"
                         style="width: 58%" />
                <n-input-group-label style="padding: 0 8px">@</n-input-group-label>
                <n-input v-model:value="swPort" :placeholder="t('addresses.switch_port_ph')"
                         style="width: 42%" />
              </n-input-group>
              <template #feedback>
                <span style="font-size: 11.5px; opacity: .7">
                  {{ t("addresses.switch_port_hint") }}
                </span>
              </template>
            </n-form-item>
          </n-space>
          <n-form-item :label="t('common.description')">
            <n-input v-model:value="form.description" type="textarea" :rows="2" />
          </n-form-item>
          <n-form-item :label="t('addresses.note')">
            <n-input v-model:value="form.note" type="textarea" :rows="2" />
          </n-form-item>
          <n-form-item :label="t('nav.customers')">
            <n-select v-model:value="form.customer_id" :options="customerOptions"
                      :placeholder="t('common.not_specified')" clearable filterable />
          </n-form-item>
          <ConsoleEgressSelect v-model:jump-host-id="form.jump_host_id"
                               v-model:console-agent-id="form.console_agent_id"
                               :subnet-id="address?.subnet_id ?? createContext?.subnet_id ?? null" inherit />
          <n-form-item :label="t('nav.devices')">
            <n-space vertical :size="4" style="width: 100%">
              <n-select v-model:value="form.device_id" :options="deviceOptions" :disabled="!!form.device_group_id"
                        :placeholder="t('common.not_specified')" clearable filterable data-testid="ip-device-select" />
              <div v-if="form.device_group_id" class="sug-note" data-testid="ip-group-locked">{{ t("device_groups.ip_group_locked") }}</div>
              <n-button v-if="matchingDevice" size="tiny" dashed type="primary"
                        @click="linkMatchingDevice">
                <template #icon><n-icon><LinkIcon /></n-icon></template>
                {{ t("addresses.link_matching_device", { name: matchingDevice.name }) }}
              </n-button>
              <!-- 主機名稱改了還沒存：後端建議是照舊名稱算的，前端比對到裝置時就不顯示它 -->
              <template v-if="!form.device_id && suggestion && !matchingDevice">
                <n-button v-if="suggestion.existing_device_id" size="tiny" dashed type="primary"
                          :loading="applyingSuggestion" @click="applySuggestion(false)">
                  <template #icon><n-icon><LinkIcon /></n-icon></template>
                  {{ t("addresses.suggest_link", { name: suggestion.existing_device_name }) }}
                </n-button>
                <n-button v-else-if="suggestion.suggested_name && suggestion.can_create"
                          size="tiny" dashed type="primary"
                          :loading="applyingSuggestion" @click="applySuggestion(true)">
                  <template #icon><n-icon><PlusIcon /></n-icon></template>
                  {{ t("addresses.suggest_create", { name: suggestion.suggested_name }) }}
                </n-button>
                <template v-if="suggestion.siblings.length
                            && (suggestion.existing_device_id || suggestion.can_create)">
                  <div class="sug-note">
                    {{ t("addresses.suggest_siblings_hint", { n: suggestion.siblings.length }) }}
                  </div>
                  <n-checkbox-group v-model:value="linkIds" class="sug-siblings">
                    <n-space vertical :size="2">
                      <n-checkbox v-for="s in suggestion.siblings" :key="s.id" :value="s.id"
                                  size="small">
                        <span class="sug-ip">{{ s.ip }}</span>
                        <span class="sug-mac">{{ s.mac || "—" }}</span>
                        <n-tag v-if="s.mac_vendor" size="tiny" type="info" :bordered="false" class="sug-vendor">{{ s.mac_vendor }}</n-tag>
                        <n-tag v-if="s.same_mac" size="tiny" type="success" :bordered="false">
                          {{ t("addresses.suggest_same_mac") }}
                        </n-tag>
                      </n-checkbox>
                    </n-space>
                  </n-checkbox-group>
                </template>
              </template>
            </n-space>
          </n-form-item>
          <n-form-item v-if="auth.me?.is_admin && !isCreate" :label="t('device_groups.ip_group_field')">
            <n-space vertical :size="4" style="width: 100%">
              <n-select v-model:value="form.device_group_id" :options="groupOptions" clearable filterable
                        :placeholder="t('common.not_specified')" data-testid="ip-group-select" />
              <div class="sug-note">{{ t("device_groups.ip_group_hint") }}</div>
            </n-space>
          </n-form-item>
          <n-form-item :label="t('scan_probes.excluded')">
            <n-space vertical :size="4" style="width: 100%">
              <n-checkbox-group v-model:value="excludedProbes">
                <n-space :size="[16, 8]" style="flex-wrap: wrap">
                  <n-checkbox v-for="p in catalog.probes" :key="p.key" :value="p.key">
                    {{ probeLabel(p, locale) }}
                    <n-tooltip v-if="p.intrusive" trigger="hover">
                      <template #trigger>
                        <n-tag size="tiny" type="warning" style="margin-left: 4px;">
                          {{ t("scan_probes.intrusive") }}
                        </n-tag>
                      </template>
                      {{ t("scan_probes.intrusive_warn") }}
                    </n-tooltip>
                  </n-checkbox>
                </n-space>
              </n-checkbox-group>
              <span style="font-size: 11px; opacity: .7">{{ t("scan_probes.excluded_hint") }}</span>
            </n-space>
          </n-form-item>
          <!-- 上線狀態跟著所屬設備（客戶 2026-10-14：多 LAN 路由器的閘道不回 ping 但在用，顯示紅燈） -->
          <n-form-item :label="t('addresses.liveness_from_device')">
            <n-space vertical :size="2" style="width:100%">
              <n-switch v-model:value="form.liveness_from_device" data-testid="ip-liveness-from-device" />
              <span style="font-size: 11px; opacity: .7">{{ t("addresses.liveness_from_device_hint") }}</span>
              <span v-if="form.liveness_from_device && !form.device_id" class="lfd-warn" data-testid="ip-liveness-no-device">
                {{ t("addresses.liveness_from_device_no_device") }}
              </span>
            </n-space>
          </n-form-item>
          <n-form-item :label="t('addresses.ptr_ignore')">
            <n-switch v-model:value="form.ptr_ignore" />
          </n-form-item>
          <n-form-item :label="t('ssh.enable_label')">
            <n-space vertical :size="2" style="width:100%">
              <n-switch v-model:value="form.ssh_enabled" />
              <span style="font-size: 11px; opacity: .7">{{ t("ssh.enable_hint") }}</span>
            </n-space>
          </n-form-item>
          <n-form-item :label="t('sftp.enable_label')">
            <n-space vertical :size="2" style="width:100%">
              <n-switch v-model:value="form.sftp_enabled" />
              <span style="font-size: 11px; opacity: .7">{{ t("sftp.enable_hint") }}</span>
            </n-space>
          </n-form-item>
          <n-form-item :label="t('rdp.enable_label')">
            <n-space vertical :size="2" style="width:100%">
              <n-switch v-model:value="form.rdp_enabled" />
              <span style="font-size: 11px; opacity: .7">{{ t("rdp.enable_hint") }}</span>
            </n-space>
          </n-form-item>
          <n-form-item :label="t('vnc.enable_label')">
            <n-space vertical :size="2" style="width:100%">
              <n-switch v-model:value="form.vnc_enabled" />
              <span style="font-size: 11px; opacity: .7">{{ t("vnc.enable_hint") }}</span>
            </n-space>
          </n-form-item>
          <!-- PVE 主控台開關：僅在此 IP 對應到 Proxmox VE 的 VM/CT 時出現 -->
          <n-form-item v-if="props.address?.pve">
            <template #label>
              {{ t("novnc.enable") }}
              <n-tag size="tiny" type="warning" :bordered="false" style="margin-left:6px">PVE</n-tag>
            </template>
            <n-space vertical :size="2" style="width:100%">
              <n-switch v-model:value="form.novnc_enabled" />
              <span style="font-size: 11px; opacity: .7">{{ t("novnc.enable_hint") }}（{{ props.address.pve.kind === 'ct' ? 'LXC → xterm' : 'QEMU → noVNC' }} · vmid {{ props.address.pve.vmid }}）</span>
            </n-space>
          </n-form-item>
          <n-form-item>
            <template #label>
              {{ t("bmc.enable_label") }}
              <n-tag size="tiny" type="warning" :bordered="false" style="margin-left:6px">Beta</n-tag>
            </template>
            <n-space vertical :size="2" style="width:100%">
              <n-switch v-model:value="form.bmc_enabled" />
              <span style="font-size: 11px; opacity: .7">{{ t("bmc.enable_hint") }}</span>
            </n-space>
          </n-form-item>
          <!-- RustDesk 連線按鈕開關：只有這個 IP 已對應到 RustDesk 裝置時才出現（比照 PVE 主控台） -->
          <n-form-item v-if="props.address?.rustdesk" :label="t('rustdesk.enable_label')">
            <n-space vertical :size="2" style="width:100%">
              <n-switch v-model:value="form.rustdesk_enabled" data-testid="ip-rustdesk-enable" />
              <span style="font-size: 11px; opacity: .7">{{ t("rustdesk.enable_hint") }}</span>
            </n-space>
          </n-form-item>
          <!-- 這個 IP 沒有 RustDesk 裝置、同一台裝置的另一個 IP 有（兩張網卡）：講清楚對應在哪，
               不然看起來像選項不見了（使用者 2026-10-05 問） -->
          <n-form-item v-else-if="props.address?.rustdesk_elsewhere?.length" :label="t('rustdesk.enable_label')">
            <n-space vertical :size="2" style="width:100%" data-testid="ip-rustdesk-elsewhere">
              <n-switch :value="false" disabled />
              <span style="font-size: 11px; opacity: .7">{{ t("rustdesk.elsewhere_hint") }}</span>
              <span v-for="o in props.address.rustdesk_elsewhere" :key="o.address_id" style="font-size: 12px">
                <router-link :to="{ name: 'address-detail', params: { id: o.address_id } }"
                             data-testid="ip-rustdesk-elsewhere-link">{{ o.ip }}</router-link>
                <span style="opacity: .7">（{{ t("rustdesk.elsewhere_item", { id: o.rustdesk_id }) }}，{{
                  o.enabled ? t("rustdesk.elsewhere_on") : t("rustdesk.elsewhere_off") }}）</span>
              </span>
            </n-space>
          </n-form-item>
          <n-form-item :label="t('addresses.is_dhcp_server')">
            <n-space vertical :size="2" style="width:100%">
              <n-switch v-model:value="form.is_dhcp_server" />
              <span style="font-size: 11px; opacity: .7">{{ t("addresses.is_dhcp_server_hint") }}</span>
            </n-space>
          </n-form-item>
        </n-form>
      </div>

      <template v-if="!inline" #footer>
        <n-space justify="space-between">
          <n-popconfirm v-if="!isCreate && (!inline || editMode)" key="ft-del" @positive-click="remove">
            <template #trigger>
              <n-button type="error" ghost size="small" :loading="deleting" :disabled="!props.address">
                <template #icon><n-icon><DeleteIcon /></n-icon></template>
                {{ t("common.delete") }}
              </n-button>
            </template>
            {{ t("common.confirm_delete") }}
          </n-popconfirm>
          <span v-else></span>
          <n-space>
            <n-button v-if="!inline || editMode || isCreate" key="ft-cancel" @click="close">
              <template #icon><n-icon><CancelIcon /></n-icon></template>
              {{ t("common.cancel") }}
            </n-button>
            <n-button v-if="isCreate" key="ft-create" type="primary" :loading="saving" @click="save">
              <template #icon><n-icon><PlusIcon /></n-icon></template>
              {{ t("common.create") }}
            </n-button>
            <n-button v-else-if="!editMode" key="ft-edit" type="primary" @click="editMode = true">
              <template #icon><n-icon><EditIcon /></n-icon></template>
              {{ t("common.edit") }}
            </n-button>
            <n-button v-else key="ft-save" type="success" :loading="saving" @click="save">
              <template #icon><n-icon><SaveIcon /></n-icon></template>
              {{ t("common.save") }}
            </n-button>
          </n-space>
        </n-space>
      </template>
    </n-card>
  </component>
  <InvestigateModal v-if="props.address?.ip"
                    v-model:show="investigating" :ip="String(props.address.ip)" />
  <ChangeImpactWizard v-if="props.address?.id && impact.settings.value.enabled" v-model:show="impactWizard"
                      preset-scenario="ip_renumber" :preset-target-id="props.address.id"
                      :preset-label="String(props.address.ip).split('/')[0]" />
</template>

<style scoped>
.rd-id { font-family: ui-monospace, monospace; cursor: pointer; }
.ocs-jump {
  cursor: pointer;
  color: var(--n-primary-color, #18a058);
  text-decoration: underline dotted;
  text-underline-offset: 2px;
}
/* 詳情下半部各區塊（關係鏈／防火牆規則／所屬別名／NAT）的標題：左側色條＋粗體，
   一眼分得出區塊。以前是跟表頭同樣的灰色小字，整片看起來黏在一起（使用者回報）。 */
.detail-sec-title {
  font-size: 13px; font-weight: 600; line-height: 1.3;
  /* 區塊之間留多一點（使用者回報各區太靠近）：上方 32px、標題與內容 10px */
  margin: 32px 0 10px; padding-left: 8px;
  border-left: 3px solid #18a058;
}
/* 防火牆規則／所屬別名／NAT：同一種表格，欄位上下對齊 */
.fw-table-wrap { overflow-x: auto; }
/* 寬度跟著內容走：撐滿整列會把多出來的空間平均塞進每一欄，箭頭那欄會被拉得很寬 */
.fw-table { border-collapse: collapse; font-size: 12.5px; line-height: 1.5; }
.fw-table th {
  text-align: left; font-weight: 400; font-size: 11.5px; opacity: .55;
  padding: 2px 14px 4px 0; white-space: nowrap;
  border-bottom: 1px solid rgba(127, 127, 127, 0.2);
}
/* 各來源最後出現：對齊比較；最新那一列加粗，一眼看出誰最近還看得到它 */
.seen-table :deep(.nowrap) { white-space: nowrap; }
.seen-table :deep(.seen-label) { white-space: normal; }
.seen-table :deep(.mono) { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; white-space: nowrap; }
.seen-table :deep(.dim) { color: color-mix(in srgb, currentColor 70%, transparent); }
.seen-table :deep(tr.seen-latest td) { font-weight: 600; }
.seen-table :deep(.seen-why) { font-size: 12px; opacity: .75; line-height: 1.5; }
.seen-table :deep(.seen-th-help) { border-bottom: 1px dotted currentColor; cursor: help; }
.seen-table :deep(.seen-ago-inline) { display: none; font-family: inherit; font-size: 12px; }
@media (max-width: 600px) {
  .seen-table :deep(.seen-ago-inline) { display: block; }
  .seen-table :deep(.mono) { white-space: normal; }
}
.fw-table td {
  padding: 4px 14px 4px 0; vertical-align: top;
  border-bottom: 1px solid rgba(127, 127, 127, 0.08);
}
/* 第一欄留一點左邊距，滑過時整行光棒才不會貼著標籤邊緣 */
.fw-table td:first-child, .fw-table th:first-child { padding-left: 6px; }
/* 整行光棒：欄位多、列長時跟著看不會跳行 */
.fw-table tbody tr { transition: background .12s; }
.fw-table tbody tr:hover { background: rgba(24, 160, 88, 0.12); }
.fw-table .nowrap, .fw-table .mono { white-space: nowrap; }
.fw-table .nat-self { font-weight: 600; }
.fw-table .dim { opacity: .6; }
.fw-table td:last-child { min-width: 12em; }
.fw-table .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
/* 淡色用文字顏色而不是 opacity：opacity 會連同儲存格背景一起變淡，滑過時光棒會斷成一塊一塊 */
.fw-table .dim { color: color-mix(in srgb, currentColor 65%, transparent); }
.fw-table .fw-pass { color: #18a058; }
.fw-table .fw-block { color: #d03050; }
.fw-table tbody tr.fw-row-link { cursor: pointer; }
/* 防火牆上停用的 NAT 規則：整列變淡（使用者 2026-10-11） */
.fw-table tbody tr.fw-row-disabled > td { opacity: .5; }
/* 三張表的前兩欄（廠牌、設備名稱）固定起始寬度，上下三區塊的欄位也對得齊 */
.fw-table th:nth-child(1) { width: 80px; }
.fw-table th:nth-child(2) { width: 110px; }
.nat-ref-name { font-weight: 500; }
/* RDP/VNC Beta 角落小標：疊在按鈕右上角，不佔橫向空間 */
.conn-beta-wrap { position: relative; display: inline-flex; }
.conn-beta-badge {
  position: absolute; top: -7px; right: -6px; z-index: 2; pointer-events: none;
  font-size: 9px; font-weight: 700; line-height: 1; letter-spacing: .2px;
  padding: 1px 4px; border-radius: 999px;
  color: #fff; background: #d99812; box-shadow: 0 0 0 1.5px var(--n-color, #fff);
}
.conn-sol-badge { background: #909399; }
.conn-local-badge { background: #4b5563; }
/* IP 頁的 RustDesk 區 */
.rdx-sec { margin-top: 14px; }
.rdx-ev { display: inline-flex; flex-wrap: wrap; gap: 4px; }
/* 「另有 N 筆 IP 用同一個主機名稱」：IP、MAC、廠商各自一欄，對得齊（以前三段黏在一起） */
.sug-ip { display: inline-block; min-width: 128px; font-family: var(--jt-mono, monospace); }
.sug-mac { display: inline-block; min-width: 150px; margin-left: 10px; font-family: var(--jt-mono, monospace); opacity: .8; }
.sug-vendor { margin-left: 8px; }
/* 異動記錄超過 N 天（系統設定）的項目以淡色顯示 */
.log-dim { opacity: .45; }

/* ARP 時間旁的提醒標記：這個時間不代表機器當下活著 */
.arp-caveat {
  display: inline-block; margin-left: 6px; width: 14px; height: 14px; line-height: 14px;
  text-align: center; border-radius: 50%; font-size: 10px; cursor: help;
  background: rgba(251, 191, 36, .18); color: #b45309;
}
.mac-history-link { color: var(--primary-color, #18a058); text-decoration: none; font-family: var(--jt-mono, monospace); }
.mac-history-link:hover { text-decoration: underline; }
.split-caret { padding: 0 6px; }
.lfd-warn { font-size: 12px; color: #d97706; }
</style>
