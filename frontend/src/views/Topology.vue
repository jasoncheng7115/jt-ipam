<script setup lang="ts">
/**
 * 網路拓樸圖 — Cytoscape.js + cose-bilkent layout。
 *
 * Phase 3 MVP：
 *  - 節點 = device，依 type 顏色編碼
 *  - 邊 = cable / wireless / vpn，三種樣式可區分
 *  - 點節點顯示資訊
 *  - 切換 wireless / vpn 顯示
 */
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { NAlert,
  NCard,
  NSpace,
  NCheckbox,
  NSpin,
  NButton,
  NButtonGroup,
  NDropdown,
  NSelect,
  useMessage,
  NTooltip,
} from "naive-ui";
import { NIcon } from "naive-ui";
import { TopologyIcon, RefreshIcon, FitIcon, ExportIcon, DevicesIcon, MacIcon, SubnetsIcon } from "@/icons";
import { useRouter } from "vue-router";
import cytoscape from "cytoscape";
import coseBilkent from "cytoscape-cose-bilkent";
import { getTopology, type TopologyData } from "@/api/topology";
import { listSubnets } from "@/api/subnets";
import { usePinnedSubnets } from "@/composables/usePinnedSubnets";
import { useExportBusy } from "@/composables/useExportBusy";
import { useFillHeight } from "@/composables/usePageFill";
import { saveBlob } from "@/utils/saveFile";
import { assignOwners, deviceGroupLinks, gridPositions, multiHomed, nodeLabels, packRows, ringPositions, segmentsFor } from "@/utils/topologyLayout";

cytoscape.use(coseBilkent as any);

const { t } = useI18n();
const msg = useMessage();
const containerRef = ref<HTMLDivElement | null>(null);
const includeWireless = ref(true);
const includeVpn = ref(true);
const includeL3 = ref(true);
// 預設不開：FDB 會把所有端點拉進來，圖一下子變密。要看存取層的人自己勾，
// 或直接選「只看存取層」視圖。
const includeFdb = ref(false);
// 虛擬機同樣預設不開：實機一次會多出上百顆節點。
const includeVms = ref(false);
// 只留「有人登記過」的線。圖上同時有登記的與推導的，把兩者畫成一樣等於宣稱我們對
// 它們有一樣的把握 —— 這個開關讓人一鍵看出「拿掉所有推測之後還剩什麼」。
const assertedOnly = ref(false);

/**
 * 視圖模式 —— 同一批資料有兩種完全不同的看法，硬要合成一張圖只會兩邊都難看：
 *   l3     子網路視角：誰在哪個網段（沒有 FDB 時唯一畫得出來的東西）
 *   l2     實體／存取層視角：誰插在哪台交換器的哪個埠、交換器之間怎麼接
 *   switch 混合，但版面以交換器為中心，子網路掛在它下面
 *   auto   混合，有存取層資料就用交換器為中心，沒有就退回子網路為中心
 */
type ViewMode = "auto" | "switch" | "l2" | "l3";
// 預設「只看子網路」：那是進來就看得懂的一張圖；要看實體位置再自己切。
const viewMode = ref<ViewMode>("l3");
const viewModeOptions = computed(() => [
  { label: t("topology.view_auto"), value: "auto" },
  { label: t("topology.view_switch"), value: "switch" },
  { label: t("topology.view_l2"), value: "l2" },
  { label: t("topology.view_l3"), value: "l3" },
]);
// 「只看…」的模式自己決定要抓什麼，勾選框在那兩種模式下沒有意義（會互相矛盾）
const modeDrivesSources = computed(() => viewMode.value === "l2" || viewMode.value === "l3");
const onlineOnly = ref(false);   // 預設：不管上線與否都畫
// 以交換器為中心時，主機到交換器的線預設不畫（只畫交換器到接入群組的一條），點了裝置才顯示它的線與埠號。
// 每台主機各拉一條會交叉成一團，埠號也碎得讀不下去（2026-10-21 分析者建議）
const showAllAccess = ref(false);
/** 目前選取的節點：它的接入連線要顯示出來 */
const focusId = ref<string | null>(null);
/** 這一張圖是不是「以交換器為中心」的分層版面（有存取層資料才是） */
const switchView = ref(false);
const loading = ref(false);
// 超大規模：裝置太多時後端不建圖，只回裝置數與上限
const tooLarge = ref<{ devices: number; limit: number } | null>(null);
// 只被授權部分物件的帳號：後端只回看得到的那一塊，畫面上講清楚少了什麼
const scopeLimited = ref(false);
const selected = ref<Record<string, any> | null>(null);
// 連線(edge)兩端資訊：name=裝置/子網路、ip、port=連接埠、endpoint=VPN 端點
type EdgeEnd = { name: string | null; ip: string | null; port: string | null; endpoint: string | null };
const edgeEnds = ref<{ a: EdgeEnd; b: EdgeEnd } | null>(null);

// 友善欄位名稱（中文），對應 cytoscape node/edge data 的 key
const FIELD_LABELS = computed<Record<string, string>>(() => ({
  label: t("cols.name"),
  type: t("topology.field_type"),
  vendor: t("topology.field_vendor"),
  model: t("topology.field_model"),
  kind: t("topology.field_kind"),
  b_endpoint: t("topology.field_b_endpoint"),
  ip: "IP",
  mac: "MAC",
  serial: t("topology.field_serial"),
  rack: t("topology.field_rack"),
  location: t("topology.field_location"),
  os: t("topology.field_os"),
  hardware: t("topology.field_hardware"),
  sw_version: t("topology.field_sw_version"),
  sysname: t("topology.field_sysname"),
  status: t("topology.field_status"),
  description: t("topology.field_description"),
  via: t("topology.field_via"),
  evidence: t("topology.field_evidence"),
  port: t("topology.port"),
  peer_port: t("topology.field_peer_port"),
  vlan: "VLAN",
  direct: t("topology.field_direct"),
  port_mac_count: t("topology.field_port_mac_count"),
  host: t("topology.field_host"),
  cluster: t("topology.field_cluster"),
  vcpus: "vCPU",
  memory_mb: t("topology.field_memory"),
  group_name: t("device_groups.group"),
  group_kind: t("device_groups.kind"),
  group_role: t("device_groups.role"),
  group_order: t("device_groups.order"),
}));
const EVIDENCE_LABELS = computed<Record<string, string>>(() => ({
  asserted: t("topology.ev_asserted"),
  monitored: t("topology.ev_monitored"),
  learned: t("topology.ev_learned"),
  inferred: t("topology.ev_inferred"),
}));
const VIA_LABELS = computed<Record<string, string>>(() => ({
  ip: t("topology.via_ip"),
  name: t("topology.via_name"),
  arp: t("topology.via_arp"),
  librenms: t("topology.via_librenms"),
  fdb: t("topology.via_fdb"),
  virtualization: t("topology.via_virtualization"),
  neighbor: t("topology.via_neighbor"),
}));
const TYPE_LABELS = computed<Record<string, string>>(() => ({
  router: t("topology.type_router"),
  switch: t("topology.type_switch"),
  firewall: t("topology.type_firewall"),
  ap: t("topology.type_ap"),
  server: t("topology.type_server"),
  storage: t("topology.type_storage"),
  workstation: t("topology.type_workstation"),
  ipmi: "IPMI",
  other: t("topology.type_other"),
  subnet: t("topology.type_subnet"),
  vpn_site: t("topology.type_vpn_site"),
}));
const KIND_LABELS = computed<Record<string, string>>(() => ({
  cable: t("topology.kind_cable"),
  wireless: t("topology.kind_wireless"),
  vpn: t("topology.kind_vpn"),
  l3: t("topology.kind_l3"),
  l2: t("topology.kind_l2"),
  l2_uplink: t("topology.kind_l2_uplink"),
  vm_host: t("topology.kind_vm_host"),
  device_group: t("device_groups.title"),
}));
// 內部欄位不顯示給使用者看
const HIDDEN_FIELDS = new Set([
  "id", "source", "target", "a_device_id", "b_device_id",
  "rack_id", "location_id", "subnet_uuid", "label",
  // 連線兩端資訊改用專屬區塊呈現，這裡不重複列出
  "source_port", "target_port", "a_endpoint", "b_endpoint", "ip",
  // 分層版面自己的欄位：接入資訊另外排在最上面（selectedRows）
  "dlabel", "parent", "isGroup", "pending", "own", "owner_switch", "owner_port", "owner_direct", "also_learned",
  "multi_homed", "switches",
  // 裝置群組：id 是內部值；群組連線的名稱已經畫在線上
  "group_id",
]);

function displayValue(key: string, val: any): string {
  if (key === "type") return TYPE_LABELS.value[val] ?? String(val);
  if (key === "kind") return KIND_LABELS.value[val] ?? String(val);
  if (key === "via") {
    return String(val).split(",").map((v) => VIA_LABELS.value[v] ?? v).join("、");
  }
  // 直連與否是這條線最重要的一件事，不能只印 true/false
  if (key === "direct") return val ? t("topology.direct_yes") : t("topology.direct_no");
  if (key === "evidence") return EVIDENCE_LABELS.value[String(val)] ?? String(val);
  if (key === "group_kind") return t(`device_groups.kind_${val}`);
  if (key === "group_role") return t(`device_groups.role_${val}`);
  if (key === "status") {
    if (val === "up") return t("topology.status_up");
    if (val === "down") return t("topology.status_down");
    // LibreNMS 停用輪詢的裝置（2026-10-13）：狀態不再更新，不是「離線」
    if (val === "disabled") return t("topology.status_disabled");
  }
  return String(val);
}

const selectedRows = computed(() => {
  const d = selected.value;
  if (!d) return [];
  const rows = Object.keys(d)
    .filter((k) => !HIDDEN_FIELDS.has(k) && d[k] != null && d[k] !== "")
    .map((k) => ({ key: k, label: FIELD_LABELS.value[k] ?? k, value: displayValue(k, d[k]) }));
  if (!d.owner_switch) return rows;
  // 接在哪裡：放最上面。FDB 只能證明「這個 MAC 在那個埠後面」，不等於實體接線 ——
  // 埠上只有這一台才說「埠上只有這台」，其餘標「待交叉確認」（參考設計）
  const access = [
    ...(d.multi_homed ? [{ key: "multi", label: t("topology.attach_mode"), value: t("topology.multi_homed", { n: d.multi_homed }) }] : []),
    { key: "owner_switch", label: t("topology.owner_switch"), value: String(d.owner_switch) },
    { key: "owner_port", label: t("topology.owner_port"), value: d.owner_port ? String(d.owner_port) : "—" },
    { key: "basis", label: t("topology.relation_basis"), value: t("topology.via_fdb") },
    { key: "confidence", label: t("topology.confidence"),
      value: d.owner_direct ? t("topology.conf_direct") : t("topology.conf_cross_check") },
  ];
  if (d.also_learned) access.push({ key: "also", label: t("topology.also_learned"), value: String(d.also_learned) });
  return [...access, ...rows];
});
const selectedTitle = computed(() =>
  selected.value ? (selected.value.label ?? selected.value.id ?? t("topology.element")) : "",
);
const router = useRouter();
// 是 device 節點（非 subnet:/vpnsite: 合成節點）→ 可連到裝置頁
const selectedDeviceId = computed<string | null>(() => {
  const d = selected.value;
  if (!d || typeof d.id !== "string") return null;
  if (d.id.includes(":")) return null;            // subnet:/vpnsite:
  if (d.type === "subnet" || d.type === "vpn_site") return null;
  return d.id;
});
const selectedSubnetId = computed<string | null>(() => {
  const d = selected.value;
  return d && d.type === "subnet" && d.subnet_uuid ? String(d.subnet_uuid) : null;
});
function goDevice() {
  if (selectedDeviceId.value) router.push({ name: "device-detail", params: { id: selectedDeviceId.value } });
}
function goMac() {
  const mac = selected.value?.mac;
  if (mac) router.push({ name: "mac-history", params: { mac: String(mac) } });
}
function goSubnet() {
  if (selectedSubnetId.value) router.push({ name: "subnet-detail", params: { id: selectedSubnetId.value } });
}
const subnetIds = ref<string[]>([]);
const subnetOptions = ref<{ label: string; value: string }[]>([]);

async function loadSubnetOptions() {
  try {
    const r = await listSubnets({ page: 1, pageSize: 500 });
    subnetOptions.value = r.items.map((s) => ({
      label: s.description ? `${s.cidr} — ${s.description}` : s.cidr,
      value: s.id,
    }));
  } catch { /* silent */ }
}

let cy: cytoscape.Core | null = null;
// 往下佔滿視窗（使用者 2026-10-08：IP 拓樸圖下面也留空）：工具列到頁面底部剛好等於可視高度，
// 圖例在畫布下方，量的時候一起算進去
const topoBarEl = ref<HTMLElement | null>(null);
const shellEl = ref<HTMLElement | null>(null);
const fill = useFillHeight(shellEl, topoBarEl, { min: 420 });
watch(fill.height, () => requestAnimationFrame(() => cy?.resize()));

/**
 * 類型用圖示分，不用填滿的顏色：紅色的圓點會被當成「異常」（分析者指出路由器是紅的，看不出是類型還是狀態）。
 * 圖示是 24×24 的線條圖，用類型色描邊，放在白底的圓角方塊裡；名稱放在方塊下方。
 */
const ICON_PATHS: Record<string, string> = {
  router: '<circle cx="12" cy="12" r="9"/><path d="M12 6v12M6 12h12M9.5 8.5 12 6l2.5 2.5M9.5 15.5 12 18l2.5-2.5M8.5 9.5 6 12l2.5 2.5M15.5 9.5 18 12l-2.5 2.5"/>',
  switch: '<rect x="2.5" y="7" width="19" height="10" rx="1.5"/><path d="M6 11h1M9 11h1M12 11h1M15 11h1M18 11h1M6 14h1M9 14h1M12 14h1M15 14h1M18 14h1"/>',
  firewall: '<rect x="3" y="4" width="18" height="16" rx="1"/><path d="M3 9.3h18M3 14.7h18M9 4v5.3M15 9.3v5.4M9 14.7V20"/>',
  ap: '<path d="M5 10a10 10 0 0 1 14 0M8 13.5a5.5 5.5 0 0 1 8 0"/><circle cx="12" cy="17" r="1.4"/>',
  server: '<rect x="4" y="3.5" width="16" height="7" rx="1.2"/><rect x="4" y="13.5" width="16" height="7" rx="1.2"/><path d="M8 7h.01M8 17h.01"/>',
  storage: '<ellipse cx="12" cy="5.5" rx="7.5" ry="2.5"/><path d="M4.5 5.5v13c0 1.4 3.4 2.5 7.5 2.5s7.5-1.1 7.5-2.5v-13M4.5 12c0 1.4 3.4 2.5 7.5 2.5s7.5-1.1 7.5-2.5"/>',
  workstation: '<rect x="3" y="4" width="18" height="12" rx="1.2"/><path d="M8 20h8M12 16v4"/>',
  ipmi: '<rect x="5" y="5" width="14" height="14" rx="1.5"/><path d="M9 9h6v6H9zM9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
  vm: '<rect x="3" y="5" width="18" height="14" rx="1.5" stroke-dasharray="3 2"/><path d="M8 10l4 4 4-4"/>',
  vpn_site: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
  other: '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M9 12h6"/>',
};
const ICON_COLOURS: Record<string, string> = {
  router: "#4f46e5", switch: "#16a34a", firewall: "#dc2626", ap: "#2563eb", server: "#475569",
  storage: "#d97706", workstation: "#0284c7", ipmi: "#db2777", vm: "#7c3aed", vpn_site: "#9333ea", other: "#64748b",
};
const iconCache = new Map<string, string>();
/** 子網路節點的寬度：照標籤實際字寬加左右各 12px（以前寫死 70px，203.0.113.0/24 這種會貼到邊框，10-23） */
let _measureCtx: CanvasRenderingContext2D | null = null;
function subnetNodeWidth(label: string): number {
  _measureCtx ??= document.createElement("canvas").getContext("2d");
  if (!_measureCtx) return Math.max(70, label.length * 6.5 + 24);
  _measureCtx.font = `bold 10px ${getComputedStyle(document.body).fontFamily || "sans-serif"}`;
  return Math.max(70, Math.ceil(_measureCtx.measureText(label).width) + 24);
}

function iconUri(type: string): string {
  const k = ICON_PATHS[type] ? type : "other";
  let u = iconCache.get(k);
  if (!u) {
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="48" height="48" fill="none" stroke="${ICON_COLOURS[k]}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${ICON_PATHS[k]}</svg>`;
    u = `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
    iconCache.set(k, u);
  }
  return u;
}
const isDark = () => document.documentElement.dataset.theme === "dark";

const NODE_COLOURS: Record<string, string> = {
  router: "#6366f1",
  switch: "#22c55e",
  firewall: "#ef4444",
  ap: "#3b82f6",
  server: "#6b7280",
  storage: "#f59e0b",
  workstation: "#0ea5e9",
  ipmi: "#ec4899",
  other: "#9ca3af",
  subnet: "#0ea5e9",  // L3 subnet 節點 — 青藍色，跟 device 區分
  vpn_site: "#9333ea",  // site-to-site VPN 遠端站點 — 紫色（跟 vpn 邊一致）
};

const { pinned, ensureLoaded } = usePinnedSubnets();

// 圖例可點：點暗某類別 → 圖上隱藏該類節點（及其連線）
// 預設先把「伺服器 / 其他」這類點暗——它們數量最多、最會把網路骨幹（防火牆/路由器/
// 交換器/AP/子網路/VPN）洗掉。使用者點圖例即可重新顯示。
// 端點類節點預設不畫：一個實際環境有上百台，全畫進來就沒人看得懂了。
const hiddenTypes = ref<Set<string>>(new Set(["server", "storage", "workstation", "ipmi", "other"]));
// ...但「查得出插在哪台交換器哪個埠」的端點是例外：它在網路裡有明確位置，不是雜訊。
// 這是 FDB 存取層的重點，如果照樣被藏起來，那個功能等於沒有。
// 使用者一旦自己動過「伺服器 / 其他」圖例，就以他的選擇為準，不再自動放行。
const fdbPlaced = ref<Set<string>>(new Set());
const endpointGroupTouched = ref(false);
const LEGEND_GROUPS: Record<string, string[]> = {
  firewall: ["firewall"], router: ["router"], switch: ["switch"], ap: ["ap"],
  server: ["server", "storage", "workstation", "ipmi", "other"], vpn_site: ["vpn_site"], subnet: ["subnet"],
  vm: ["vm"],
};
function isGroupOff(group: string): boolean {
  // 虛擬機比較特別：它不是「畫了再隱藏」，而是**根本沒抓進來**（實機一次會多出上百顆）。
  // 所以圖例上的狀態要看資料有沒有載入，否則使用者按了會覺得沒反應。
  if (group === "vm") return !includeVms.value;
  return (LEGEND_GROUPS[group] || [group]).every((ty) => hiddenTypes.value.has(ty));
}
function toggleGroup(group: string) {
  if (group === "vm") {
    // 切換「要不要載入虛擬機」，watch 會重抓資料並重畫。
    // 先前這裡只把節點藏起來，但虛擬機根本還沒被抓進來 —— 按了什麼都不會發生。
    includeVms.value = !includeVms.value;
    const next = new Set(hiddenTypes.value);
    next.delete("vm");
    hiddenTypes.value = next;
    return;
  }
  if (group === "server") endpointGroupTouched.value = true;
  const types = LEGEND_GROUPS[group] || [group];
  const off = isGroupOff(group);
  const next = new Set(hiddenTypes.value);
  for (const ty of types) { if (off) next.delete(ty); else next.add(ty); }
  hiddenTypes.value = next;
  applyVisibility();
  if (!switchView.value) spreadSubnets();     // 依子網路排版只排看得到的，顯示的類別變了要重排
}
function applyVisibility() {
  if (!cy) return;
  cy.batch(() => {
    cy!.nodes().forEach((n) => {
      // 「只看存取層」時，查不出插在哪裡的裝置在這個視角下沒有東西可說 —— 畫成一顆
      // 飄在旁邊的點只是雜訊。實機上這差別很大：105 台裝置裡只有約 10 台有 FDB 資訊，
      // 其餘 95 顆孤點會把整張圖的縮放拉爆、也讓人以為那是「未連線」。
      if (viewMode.value === "l2") {
        const linked = n.connectedEdges('[kind = "l2"], [kind = "l2_uplink"]').length > 0;
        n.style("display", linked ? "element" : "none");
        return;
      }
      const hiddenByType = hiddenTypes.value.has(n.data("type") as string);
      const placed = !endpointGroupTouched.value && fdbPlaced.value.has(n.id());
      n.style("display", hiddenByType && !placed ? "none" : "element");
    });
    cy!.edges().forEach((e) => {
      const endHidden = e.source().style("display") === "none"
        || e.target().style("display") === "none";
      const notAsserted = assertedOnly.value && e.data("evidence") !== "asserted";
      // 分層版面：主機到交換器的線只畫選取中那台的（或勾了「顯示全部接入連線」）
      const folded = switchView.value && e.data("kind") === "l2" && !e.data("backbone") && !showAllAccess.value
        && focusId.value !== e.source().id() && focusId.value !== e.target().id();
      e.style("display", endHidden || notAsserted || folded ? "none" : "element");
    });
  });
}
function zoomBy(f: number) {
  if (!cy) return;
  cy.zoom({ level: cy.zoom() * f, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
}
function fitView() { if (cy) cy.fit(undefined, 30); }

// ── 匯出：PNG(cytoscape 內建) / SVG / draw.io(依節點座標重建) ──
const EDGE_STYLE: Record<string, { color: string; width: number; dash: string }> = {
  cable: { color: "#475569", width: 2, dash: "" },
  wireless: { color: "#3b82f6", width: 2, dash: "6,4" },
  vpn: { color: "#9333ea", width: 4, dash: "10,5" },
  l3: { color: "#0ea5e9", width: 1.5, dash: "5,3" },
  device_group: { color: "#d9822b", width: 5, dash: "" },
};
function dlBlob(blob: Blob, name: string) {
  saveBlob(name, blob, blob.type, { source: "topology", rows: cy ? cy.nodes().length : 0 });
}
function escXml(s: string): string {
  return String(s ?? "").replace(/[<>&'"]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", "'": "&apos;", '"': "&quot;" }[c] as string));
}
function visibleEls(): { nodes: any[]; edges: any[] } {
  const nodes = cy!.nodes().filter((n) => n.style("display") !== "none").toArray() as any[];
  const edges = cy!.edges().filter((e) => e.style("display") !== "none").toArray() as any[];
  return { nodes, edges };
}
function exportPng() {
  if (!cy) return;
  const blob = cy.png({ full: true, scale: 2, bg: "#ffffff", output: "blob" }) as Blob;
  dlBlob(blob, "ip-topology.png");
}
/** 匯出跟畫面同一套樣子：群組框（網段／接入群組／接入位置未確認）、白底圖示卡片、名稱在下方、
 *  線依種類與證據畫實線或虛線。PNG 直接拿 cytoscape 畫的，SVG／draw.io 在這裡重建（不然又會跟畫面不一樣）。 */
function boxesOf(): { id: string; label: string; x: number; y: number; w: number; h: number; type: string }[] {
  return cy!.nodes().filter((n) => n.isParent() && n.style("display") !== "none").map((n) => {
    const bb = n.boundingBox({});
    return { id: n.id(), label: String(n.data("label") ?? ""), x: bb.x1, y: bb.y1, w: bb.w, h: bb.h,
             type: String(n.data("type") ?? "subnet") };
  });
}
function edgeLook(e: any): { color: string; width: number; dash: string } {
  const kind = e.data("kind") as string;
  if (e.data("pending")) return { color: "#94a3b8", width: 1.6, dash: "6,4" };
  if (kind === "trunk") return { color: "#14b8a6", width: 2, dash: e.data("evidence") === "monitored" ? "" : "5,3" };
  if (kind === "l2" || kind === "l2_uplink") {
    const solid = ["asserted", "monitored"].includes(String(e.data("evidence"))) || (kind === "l2" && e.data("direct"));
    return { color: "#0d9488", width: e.data("backbone") || kind === "l2_uplink" ? 2.6 : 1.6, dash: solid ? "" : "5,3" };
  }
  return EDGE_STYLE[kind] || { color: "#94a3b8", width: 2, dash: "" };
}
function exportSvg() {
  if (!cy) return;
  const { nodes, edges } = visibleEls();
  const leaves = nodes.filter((n: any) => !n.isParent());
  if (!leaves.length) return;
  const boxes = boxesOf();
  const bb = cy.elements().filter((x) => x.style("display") !== "none").boundingBox({});
  const pad = 40;
  const W = bb.w + pad * 2, H = bb.h + pad * 2;
  const X = (x: number) => x - bb.x1 + pad, Y = (y: number) => y - bb.y1 + pad;
  let s = `<svg xmlns="http://www.w3.org/2000/svg" width="${Math.round(W)}" height="${Math.round(H)}" viewBox="0 0 ${Math.round(W)} ${Math.round(H)}" font-family="sans-serif"><rect width="100%" height="100%" fill="#ffffff"/>`;
  for (const b of boxes) {
    const c = b.type === "access" ? "#14b8a6" : b.type === "unplaced" ? "#94a3b8" : "#0ea5e9";
    s += `<rect x="${X(b.x)}" y="${Y(b.y)}" width="${b.w}" height="${b.h}" rx="8" fill="${c}" fill-opacity="0.05" stroke="${c}" stroke-dasharray="5,3"/>`;
    s += `<text x="${X(b.x) + 10}" y="${Y(b.y) - 6}" font-size="12" font-weight="700" fill="${c}">${escXml(b.label)}</text>`;
  }
  edges.forEach((e) => {
    const sp = e.source().position();
    // 連到群組框的（交換器 → 接入群組）停在框的上緣，跟畫面一樣，不畫進框的正中間
    const tp = e.target().isParent() ? { x: e.target().position().x, y: e.target().boundingBox({}).y1 }
      : e.target().position();
    const st = edgeLook(e);
    const pts = e.data("kind") === "vpn" && e.style("curve-style") === "segments"
      ? (e as any).segmentPoints?.() ?? [] : [];
    const path = [sp, ...pts, tp].map((p: any, k: number) => `${k ? "L" : "M"}${X(p.x)},${Y(p.y)}`).join(" ");
    s += `<path d="${path}" fill="none" stroke="${st.color}" stroke-width="${st.width}"${st.dash ? ` stroke-dasharray="${st.dash}"` : ""}/>`;
    const lbl = e.data("kind") === "l2" || e.data("kind") === "l2_uplink" ? (e.data("label") || e.data("port")) : "";
    if (lbl) s += `<text x="${(X(sp.x) + X(tp.x)) / 2}" y="${(Y(sp.y) + Y(tp.y)) / 2 - 4}" font-size="10" text-anchor="middle" fill="#0f766e">${escXml(lbl)}</text>`;
  });
  leaves.forEach((n: any) => {
    const p = n.position(); const ty = String(n.data("type") || "other");
    const col = ICON_COLOURS[ICON_PATHS[ty] ? ty : "other"];
    s += `<rect x="${X(p.x) - 21}" y="${Y(p.y) - 21}" width="42" height="42" rx="7" fill="#ffffff" stroke="${col}" stroke-width="1.5"/>`;
    s += `<g transform="translate(${X(p.x) - 13},${Y(p.y) - 13}) scale(1.0833)" fill="none" stroke="${col}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${ICON_PATHS[ICON_PATHS[ty] ? ty : "other"]}</g>`;
    String(n.data("dlabel") ?? n.data("label") ?? "").split("\n").forEach((line, k) => {
      s += `<text x="${X(p.x)}" y="${Y(p.y) + 36 + k * 13}" font-size="11" text-anchor="middle" fill="#0f172a">${escXml(line)}</text>`;
    });
  });
  s += `</svg>`;
  dlBlob(new Blob([s], { type: "image/svg+xml" }), "ip-topology.svg");
}
function exportDrawio() {
  if (!cy) return;
  const { nodes, edges } = visibleEls();
  const leaves = nodes.filter((n: any) => !n.isParent());
  if (!leaves.length) return;
  const bb = cy.elements().filter((x) => x.style("display") !== "none").boundingBox({});
  const ox = (x: number) => Math.round(x - bb.x1 + 40), oy = (y: number) => Math.round(y - bb.y1 + 40);
  const cells: string[] = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>'];
  const id: Record<string, string> = {};
  boxesOf().forEach((b, i) => {
    id[b.id] = `b${i}`;                // 交換器 → 接入群組的線接在框上
    const c = b.type === "access" ? "#14b8a6" : b.type === "unplaced" ? "#94a3b8" : "#0ea5e9";
    cells.push(`<mxCell id="b${i}" value="${escXml(b.label)}" style="rounded=1;arcSize=4;dashed=1;fillColor=none;strokeColor=${c};fontColor=${c};fontStyle=1;verticalAlign=top;align=left;spacingLeft=8;" vertex="1" parent="1"><mxGeometry x="${ox(b.x)}" y="${oy(b.y)}" width="${Math.round(b.w)}" height="${Math.round(b.h)}" as="geometry"/></mxCell>`);
  });
  leaves.forEach((n: any, i: number) => {
    id[n.id()] = `n${i}`; const p = n.position();
    const ty = String(n.data("type") || "other");
    const col = ICON_COLOURS[ICON_PATHS[ty] ? ty : "other"];
    const label = escXml(String(n.data("dlabel") ?? n.data("label") ?? "")).replace(/\n/g, "&#10;");
    cells.push(`<mxCell id="n${i}" value="${label}" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#ffffff;strokeColor=${col};fontColor=#0f172a;labelPosition=center;verticalLabelPosition=bottom;verticalAlign=top;" vertex="1" parent="1"><mxGeometry x="${ox(p.x) - 21}" y="${oy(p.y) - 21}" width="42" height="42" as="geometry"/></mxCell>`);
  });
  edges.forEach((e, i) => {
    const st = edgeLook(e);
    if (!id[e.source().id()] || !id[e.target().id()]) return;
    const style = `endArrow=none;html=1;strokeColor=${st.color};strokeWidth=${st.width};${st.dash ? "dashed=1;" : ""}${e.data("kind") === "vpn" ? "edgeStyle=orthogonalEdgeStyle;" : ""}`;
    cells.push(`<mxCell id="e${i}" value="${escXml(String(e.data("kind") === "l2_uplink" || e.data("backbone") ? (e.data("label") || e.data("port") || "") : ""))}" style="${style}" edge="1" parent="1" source="${id[e.source().id()]}" target="${id[e.target().id()]}"><mxGeometry relative="1" as="geometry"/></mxCell>`);
  });
  const xml = `<mxfile host="jt-ipam"><diagram name="IP topology"><mxGraphModel dx="800" dy="600" grid="1" gridSize="10" guides="1" connect="1" arrows="1" page="1" pageScale="1"><root>${cells.join("")}</root></mxGraphModel></diagram></mxfile>`;
  dlBlob(new Blob([xml], { type: "application/xml" }), "ip-topology.drawio");
}
const exportOptions = [
  { label: "PNG", key: "png" },
  { label: "SVG", key: "svg" },
  { label: "draw.io", key: "drawio" },
];
const { exporting, run: runExport } = useExportBusy();
function onExport(key: string) { void runExport(() => doExport(key)); }
function doExport(key: string) {
  if (!cy) { msg.warning(t("errors.network")); return; }
  if (key === "png") exportPng();
  else if (key === "svg") exportSvg();
  else if (key === "drawio") exportDrawio();
}

async function refresh() {
  loading.value = true;
  try {
    const m = viewMode.value;
    const data = await getTopology({
      includeWireless: m === "l3" ? false : includeWireless.value,
      // 「只看子網路」是第三層的圖：站對站 VPN 本來就是第三層的連線，要畫（使用者回報兩台 WireGuard
      // 對接卻沒有線）。只有「只看存取層」不畫
      includeVpn: m === "l2" ? false : m === "l3" ? true : includeVpn.value,
      includeL3: m === "l2" ? false : m === "l3" ? true : includeL3.value,
      // 「以交換器為中心」與「只看存取層」都得有 FDB 才畫得出中心，不看勾選框
      includeFdb: m === "l3" ? false : m === "l2" || m === "switch" ? true : includeFdb.value,
      includeVms: includeVms.value,
      onlineOnly: onlineOnly.value,
      subnetIds: subnetIds.value,
    });
    tooLarge.value = data.too_large ?? null;
    scopeLimited.value = data.scope === "limited";
    render(data);
  } catch {
    msg.error(t("errors.network"));
  } finally {
    loading.value = false;
  }
}

/**
 * 混合視角的資料轉換：子網路不再是「另一顆節點加一整組線」，而是**一個框**，
 * 它的成員（含交換器）畫在框裡面。
 *
 * 不這樣做的話，每台主機會有兩條線 —— 一條到交換器、一條到網段 —— 同一件事講兩次，
 * 線還互相打結。網段成員本來就是一種「屬於」關係，用包含畫比用連線畫誠實也乾淨。
 *
 * 跨多個網段的裝置（路由器 / 防火牆）**不塞進任何一個框**：塞進去等於宣稱它只屬於
 * 其中一個。它們維持原本的 L3 連線，一眼就看得出來是跨網段的那幾台。
 */
function groupBySubnet(data: TopologyData): TopologyData {
  const subnetIdSet = new Set(
    data.nodes.filter((n) => n.data.type === "subnet").map((n) => n.data.id),
  );
  if (!subnetIdSet.size) return data;

  const subsOfDevice = new Map<string, Set<string>>();
  for (const e of data.edges) {
    if (e.data.kind !== "l3") continue;
    const sub = subnetIdSet.has(e.data.source) ? e.data.source : e.data.target;
    const dev = sub === e.data.source ? e.data.target : e.data.source;
    if (!subnetIdSet.has(sub) || subnetIdSet.has(dev)) continue;
    (subsOfDevice.get(dev) ?? subsOfDevice.set(dev, new Set()).get(dev)!).add(sub);
  }
  const parentOf = new Map<string, string>();
  for (const [dev, subs] of subsOfDevice) {
    if (subs.size === 1) parentOf.set(dev, [...subs][0]);
  }
  if (!parentOf.size) return data;

  // 虛擬機沒有自己的 L3 邊，會落在框外面、線卻連進框裡的主機 —— 跟著主機進同一個框。
  for (const e of data.edges) {
    if (e.data.kind !== "vm_host") continue;
    const box = parentOf.get(e.data.target);
    if (box && !parentOf.has(e.data.source)) parentOf.set(e.data.source, box);
  }

  // 有 FDB、但自己沒有 IP 記錄的交換器（沒有 L3 邊）會落在框外面，它的主機卻在框裡，
  // 於是一堆線穿過框線 —— 看起來像壞掉。若它的主機**全都**屬於同一個框，就把它併進去：
  // 「它服務的機器全在這個廣播域」是真的證據。主機跨多個框時維持在外面 ——
  // 那是真的跨網段交換器，挑一個框就是編造。
  const l2Edges = data.edges.filter((e) => e.data.kind === "l2");
  for (const sw of new Set(l2Edges.map((e) => e.data.target))) {
    if (parentOf.has(sw)) continue;
    const mine = l2Edges.filter((x) => x.data.target === sw);
    const hostBoxes = new Set(
      mine.map((x) => parentOf.get(x.data.source)).filter(Boolean) as string[],
    );
    if (hostBoxes.size === 1 && mine.length > 0) parentOf.set(sw, [...hostBoxes][0]);
  }

  // 有存取層位置的成員（插在某台交換器上、或跑在某台主機上）
  const located = new Set<string>();
  for (const e of data.edges) {
    if (e.data.kind === "l2" || e.data.kind === "l2_uplink" || e.data.kind === "vm_host") {
      located.add(e.data.source);
      located.add(e.data.target);
    }
  }

  // 其餘成員只知道「在這個網段」。單純放進框裡看起來就是一排斷線的裝置 ——
  // 它們不是沒連線，是**我們查不出它接在哪台交換器的哪個埠**。用一個講明白的
  // 次區塊把這件事說出來，比讓人自己猜好。
  // 只有在該框裡確實有「查得出位置」的東西時才分區：整框都查不出來時分區沒有意義。
  const unplacedBox = new Map<string, string>();      // subnet id → 次區塊 id
  const boxHasLocated = new Set<string>();
  for (const [dev, box] of parentOf) if (located.has(dev)) boxHasLocated.add(box);
  const extraNodes: TopologyData["nodes"] = [];
  for (const [dev, box] of [...parentOf]) {
    if (located.has(dev) || !boxHasLocated.has(box)) continue;
    let gid = unplacedBox.get(box);
    if (!gid) {
      gid = `unplaced:${box}`;
      unplacedBox.set(box, gid);
      extraNodes.push({ data: { id: gid, label: t("topology.unplaced_group"),
                                type: "unplaced", isGroup: true, parent: box } } as never);
    }
    parentOf.set(dev, gid);
  }

  return {
    nodes: [
      ...data.nodes.map((n) =>
        subnetIdSet.has(n.data.id)
          ? { data: { ...n.data, isGroup: true } }
          : parentOf.has(n.data.id)
            ? { data: { ...n.data, parent: parentOf.get(n.data.id) } }
            : n,
      ),
      ...extraNodes,
    ],
    // 已經用「在框裡」表達的 L3 邊就不要再畫一次；跨網段那幾條留著
    edges: data.edges.filter(
      (e) =>
        e.data.kind !== "l3" ||
        !(parentOf.has(e.data.source) || parentOf.has(e.data.target)),
    ),
  };
}

/**
 * 以交換器為中心的分層：每台交換器一個「接入群組」框，插在它上面的主機放進去，交換器只拉一條線到自己的群組。
 *
 * 同一台主機在兩台交換器的 FDB 都學得到時只歸一個群組（直連的埠優先，見 utils/topologyLayout.assignOwners），
 * 另一條標成待確認 —— 兩邊都學到多半只是一邊是存取埠、一邊是上聯埠，不能當成雙接。
 * 主機與交換器不在同一個網段框裡時不搬（不把它拉進別人的網段）。
 */
function buildAccessGroups(input: TopologyData): TopologyData {
  // 節點與連線的資料在這裡會多出版面用的欄位（parent、owner_*），型別放寬
  const data = input as unknown as { nodes: { data: any }[]; edges: { data: any }[] };
  const byId = new Map(data.nodes.map((n) => [n.data.id, n]));
  // 交換器、路由器、防火牆不進接入群組：別台交換器在某個埠學到它，是上聯／骨幹，不是一台終端。
  // 這種線常駐顯示、帶埠號（以前一台交換器被當成主機塞進另一台交換器的群組）
  const INFRA = new Set(["switch", "router", "firewall"]);
  const isInfra = (id: string) => INFRA.has(String(byId.get(id)?.data.type ?? ""));
  const backbone = new Set(data.edges.filter((e) => e.data.kind === "l2" && isInfra(e.data.source))
    .map((e) => e.data.id));
  const l2 = data.edges.filter((e) => e.data.kind === "l2" && !backbone.has(e.data.id));
  if (!l2.length && !backbone.size) return data;
  const order: string[] = [];
  for (const e of l2) if (!order.includes(e.data.target)) order.push(e.data.target);
  const links = l2.map((e) => ({
    id: e.data.id, source: e.data.source, target: e.data.target, port: e.data.port,
    direct: e.data.direct, port_mac_count: e.data.port_mac_count, mac: e.data.mac,
  }));
  // 不同網卡各接一台交換器的（雙接）另外成一組，放在那幾台交換器之間；其餘照舊只歸一台
  const multi = multiHomed(links, order);
  const owners = assignOwners(links.filter((l) => !multi.has(l.source)), order);
  const pendingEdges = new Set<string>();
  const ownEdges = new Set<string>();
  const groupOf = new Map<string, string>();
  const groups: { data: any }[] = [];
  const trunks: { data: any }[] = [];
  const nodes = data.nodes.map((n) => ({ data: { ...n.data } }));
  const nodeById = new Map(nodes.map((n) => [n.data.id, n]));
  for (const [host, o] of owners) {
    ownEdges.add(o.edge);
    o.others.forEach((id) => pendingEdges.add(id));
    const hn = nodeById.get(host);
    const sw = byId.get(o.switch);
    if (!hn || !sw) continue;
    hn.data.owner_switch = sw.data.label;
    hn.data.owner_port = o.port;
    hn.data.owner_direct = o.direct;
    hn.data.also_learned = o.others.map((id) => {
      const e = l2.find((x) => x.data.id === id);
      const s2 = e ? byId.get(e.data.target) : null;
      return s2 ? `${s2.data.label}${e?.data.port ? ` ${e.data.port}` : ""}` : "";
    }).filter(Boolean).join("、") || null;
    if ((hn.data.parent ?? null) !== (sw.data.parent ?? null)) continue;
    let gid = groupOf.get(o.switch);
    if (!gid) {
      gid = `acc:${o.switch}`;
      groupOf.set(o.switch, gid);
      groups.push({ data: { id: gid, label: t("topology.access_group", { name: sw.data.label }), type: "access",
                            isGroup: true, parent: sw.data.parent } } as never);
      trunks.push({ data: { id: `trunk:${o.switch}`, source: o.switch, target: gid, kind: "trunk",
                            evidence: "learned" } } as never);
    }
    hn.data.parent = gid;
  }
  // 整組都是直連的埠 → 交換器到群組這條畫實線
  for (const tr of trunks) {
    const sw = tr.data.source;
    const allDirect = [...owners.values()].filter((o) => o.switch === sw).every((o) => o.direct);
    if (allDirect) tr.data.evidence = "monitored";
  }
  // 雙接群組：一組交換器一個框（「switch-a + switch-b 雙接群組」），每台交換器各拉一條線到框
  const edgeById = new Map(l2.map((e) => [e.data.id, e]));
  for (const [host, m] of multi) {
    m.edges.forEach((id) => ownEdges.add(id));
    const hn = nodeById.get(host);
    const sws = m.switches.map((id) => byId.get(id));
    if (!hn || sws.some((x) => !x)) continue;
    const es = m.edges.map((id) => edgeById.get(id)!.data);
    hn.data.multi_homed = m.switches.length;
    hn.data.owner_switch = sws.map((x) => x!.data.label).join("、");
    hn.data.owner_port = es.map((e, i) => `${sws[i]!.data.label} ${e.port ?? "—"}${e.nic ? `（${e.nic}）` : ""}`).join("\n");
    hn.data.owner_direct = es.every((e) => e.direct);
    hn.data.also_learned = null;
    if (sws.some((x) => (x!.data.parent ?? null) !== (hn.data.parent ?? null))) continue;
    const gid = `acc2:${m.switches.join("+")}`;
    if (!groupOf.has(gid)) {
      groupOf.set(gid, gid);
      groups.push({ data: { id: gid, label: t("topology.access_group_multi", { names: sws.map((x) => x!.data.label).join(" + ") }),
                            type: "access", isGroup: true, parent: sws[0]!.data.parent, switches: m.switches } } as never);
      for (const sw of m.switches) {
        trunks.push({ data: { id: `trunk:${sw}:${gid}`, source: sw, target: gid, kind: "trunk", evidence: "learned" } } as never);
      }
    }
    hn.data.parent = gid;
  }
  for (const tr of trunks) {
    if (!String(tr.data.target).startsWith("acc2:")) continue;
    const members = [...multi.entries()].filter(([, m]) => `acc2:${m.switches.join("+")}` === tr.data.target);
    const allDirect = members.every(([, m]) => m.edges.map((id) => edgeById.get(id)!.data)
      .filter((e) => e.target === tr.data.source).every((e) => e.direct));
    tr.data.evidence = allDirect ? "monitored" : "learned";
  }
  return {
    nodes: [...nodes, ...groups] as never,
    edges: [
      ...data.edges.map((e) => (e.data.kind !== "l2" ? e
        : { data: { ...e.data, pending: pendingEdges.has(e.data.id) || undefined, own: ownEdges.has(e.data.id) || undefined,
                    backbone: backbone.has(e.data.id) || undefined } })),
      ...trunks,
    ] as never,
  };
}

function render(data: TopologyData) {
  if (!containerRef.value) return;
  if (cy) {
    cy.destroy();
    cy = null;
  }
  cy = cytoscape({
    container: containerRef.value,
    elements: (() => {
      const m = viewMode.value;
      let grouped = m === "l2" || m === "l3" ? data
        : groupBySubnet(data);   // auto / switch：網段與交換器合成一組
      const hasL2 = grouped.edges.some((e) => e.data.kind === "l2" || e.data.kind === "l2_uplink");
      switchView.value = m !== "l3" && hasL2;
      if (switchView.value) grouped = buildAccessGroups(grouped);
      // 名稱放在圖示下方、最多兩行；同名的補 IP（例：兩台 AP 同名）
      const labels = nodeLabels((grouped.nodes as { data: any }[]).filter((n) => !n.data.isGroup && n.data.type !== "subnet")
        .map((n) => ({ id: n.data.id, label: n.data.label, ip: n.data.ip })),
        (i, n) => t("topology.same_name", { i, n }));
      grouped = { ...grouped, nodes: grouped.nodes.map((n) => (labels.has(n.data.id)
        ? { data: { ...n.data, dlabel: labels.get(n.data.id) } } : n)) as never };
      // 裝置群組（堆疊／HA）：同組成員之間一條粗線，標群組名稱。是人工設定的，算「已確認」
      const groupLinks = deviceGroupLinks((grouped.nodes as { data: any }[]).filter((n) => !n.data.isGroup)
        .map((n) => n.data)).map((l) => ({ data: { ...l, evidence: "asserted" } }));
      return [...grouped.nodes, ...grouped.edges, ...groupLinks];
    })(),
    style: [
      {
        // 圖示卡片：白底圓角方塊＋類型圖示，名稱放在下方（最多兩行，見 nodeLabels）。
        // 以前名稱直接壓在小圓點上，長名稱比節點還寬、讀不出來
        selector: "node",
        style: {
          shape: "round-rectangle" as any,
          width: 42,
          height: 42,
          "background-color": isDark() ? "#1e293b" : "#ffffff",
          "border-width": 1.5,
          "border-color": isDark() ? "#475569" : "#cbd5e1",
          "background-image": ((node: any) => iconUri(String(node.data("type") || "other"))) as any,
          "background-fit": "none",
          "background-width": "26px",
          "background-height": "26px",
          label: "data(dlabel)",
          color: isDark() ? "#e2e8f0" : "#0f172a",
          "font-size": 11,
          "text-valign": "bottom",
          "text-halign": "center",
          "text-margin-y": 5,
          "text-wrap": "wrap",
          "text-max-width": "150px",
          "text-background-color": isDark() ? "#0f172a" : "#ffffff",
          "text-background-opacity": 0.85,
          "text-background-padding": "1px",
          "text-background-shape": "roundrectangle",
        },
      },
      {
        selector: "edge",
        style: {
          width: 2,
          "curve-style": "bezier",
          "line-color": "#94a3b8",
          "target-arrow-shape": "none",
        },
      },
      {
        selector: 'edge[kind = "cable"]',
        style: {
          "line-color": "#475569",
          width: 2,
        },
      },
      {
        selector: 'edge[kind = "wireless"]',
        style: {
          "line-color": "#3b82f6",
          "line-style": "dashed",
        },
      },
      {
        selector: 'edge[kind = "vpn"]',
        style: {
          "line-color": "#9333ea",
          "line-style": "dashed",
          "line-dash-pattern": [10, 5],
          width: 4,
          // 中間標 tunnel 類型；兩端各自標「該端的對外位址」，貼在各自節點旁，不互蓋
          label: "data(type)",
          "source-label": "data(a_endpoint)",
          "target-label": "data(b_endpoint)",
          "source-text-offset": 46 as any,
          "target-text-offset": 46 as any,
          "font-size": 10,
          "font-weight": "bold",
          color: "#6b21a8",
          "text-rotation": 0 as any,
          "text-background-color": "#ffffff",
          "text-background-opacity": 1,
          "text-background-padding": "3px",
          "text-background-shape": "roundrectangle",
          "text-border-color": "#9333ea",
          "text-border-width": 1,
          "text-border-opacity": 1,
          "z-index": 9999,
        },
      },
      {
        // 存取層（FDB）：這台機器實際插在某台交換器的某個埠上 —— 比 L3 那種
        // 「有介面在這個網段」的推導確定，所以用實線、不用虛線。
        selector: 'edge[kind = "l2"]',
        style: {
          "line-color": "#14b8a6",
          width: 1.6,
          "line-style": "dashed",   // 預設是「在這個埠後面」，直連的在下一條規則轉實線
          label: "data(port)",
          "font-size": 9,
          color: "#0f766e",
          "text-background-color": "#ffffff",
          "text-background-opacity": 0.85,
          "text-background-padding": "2px",
          "text-rotation": "autorotate" as any,
        },
      },
      {
        // 埠上只有這一個 MAC → 真的插在上面，畫實線。有好幾個 MAC 的維持虛線：
        // 那些機器在這個埠「後面」，中間可能還隔著一台笨集線器或一台虛擬化主機。
        selector: 'edge[kind = "l2"][?direct]',
        style: { "line-style": "solid" },
      },
      {
        // 交換器之間的骨幹：畫得比存取層重，兩端標各自的埠名
        selector: 'edge[kind = "l2_uplink"]',
        style: {
          "line-color": "#0d9488",
          width: 3.5,
          label: "data(label)",
          "text-margin-y": -12,
          "font-size": 10,
          "font-weight": "bold",
          color: "#0f766e",
          "text-background-color": "#ffffff",
          "text-background-opacity": 0.9,
          "text-background-padding": "3px",
          "text-rotation": "autorotate" as any,
        },
      },
      {
        // 推導出來的線畫淡一點。不動顏色與虛實 —— 那兩個維度已經各有意義（種類、
        // 是否直連），再疊上去只會讓人讀不出來。
        selector: 'edge[evidence = "inferred"]',
        style: { opacity: 0.45, "line-style": "dotted" },
      },
      {
        // VM 跑在哪台實體主機上：細線、不搶戲（它是包含關係不是網路連線）
        selector: 'edge[kind = "vm_host"]',
        style: {
          "line-color": "#a78bfa",
          width: 1.2,
          opacity: 0.75,
        },
      },
      {
        selector: 'edge[kind = "l3"]',
        style: {
          "line-color": "#0ea5e9",
          "line-style": "dashed",
          width: 1.5,
          opacity: 0.7,
        },
      },
      {
        selector: 'node[type = "subnet"]',
        style: {
          shape: "round-rectangle",
          width: ((node: any) => subnetNodeWidth(String(node.data("label") ?? ""))) as any,
          height: 28,
          "background-image": "none",
          "background-color": NODE_COLOURS.subnet,
          "border-width": 0,
          label: "data(label)",
          color: "#fff",
          "text-valign": "center",
          "text-margin-y": 0,
          "text-background-opacity": 0,
          "font-size": 10,
          "font-weight": "bold",
        },
      },
      {
        // 網段框（混合視角）：標題放在框上緣，框本身淡到不搶戲
        selector: "node[?isGroup]",
        style: {
          "background-image": "none",
          "text-background-opacity": 0,
          "text-wrap": "none",
          shape: "round-rectangle",
          width: "label" as any,
          height: "label" as any,
          "background-color": "#0ea5e9",
          "background-opacity": 0.06,
          "border-width": 1.5,
          "border-color": "#0ea5e9",
          "border-style": "dashed",
          "border-opacity": 0.7,
          label: "data(label)",
          "text-valign": "top",
          "text-halign": "center",
          "text-margin-y": -6,
          "font-size": 13,
          "font-weight": "bold",
          color: "#0369a1",
          "text-outline-width": 0,
          padding: "26px" as any,
        },
      },
      {
        // 「同網段但查不出位置」的次區塊：比網段框更淡，標題說明白
        selector: 'node[type = "unplaced"]',
        style: {
          "background-color": "#94a3b8",
          "background-opacity": 0.07,
          "border-color": "#94a3b8",
          "border-style": "dashed",
          "border-width": 1,
          "border-opacity": 0.6,
          "font-size": 11,
          "font-weight": "normal",
          color: "#64748b",
          "text-margin-y": -4,
          padding: "18px" as any,
        },
      },
      {
        // 虛擬機：畫得比實體小一點、虛線框，一眼分得出這不是實體機
        selector: 'node[type = "vm"]',
        style: { width: 32, height: 32, "background-width": "20px", "background-height": "20px",
                 "border-style": "dashed", "font-size": 9 },
      },
      {
        // 以交換器為中心：每台交換器的接入群組框（比網段框淡，標題說明是哪台的）
        selector: 'node[type = "access"]',
        style: {
          "background-color": "#14b8a6",
          "background-opacity": 0.05,
          "border-color": "#14b8a6",
          "border-style": "dashed",
          "border-width": 1,
          "border-opacity": 0.7,
          "font-size": 11,
          "font-weight": "bold",
          color: isDark() ? "#5eead4" : "#0f766e",
          "text-halign": "center",
          "text-margin-y": -4,
          padding: "20px" as any,
        },
      },
      {
        // 裝置群組（堆疊／HA／叢集）：成員之間一條粗的橘線，像堆疊線；線上標群組名稱
        selector: 'edge[kind = "device_group"]',
        style: { "line-color": "#d9822b", width: 5, "line-opacity": 0.85, "curve-style": "straight",
                 label: "data(label)", "font-size": 10, "font-weight": "bold", color: isDark() ? "#fbbf77" : "#9a4f0c",
                 "text-rotation": "autorotate" as any, "text-background-color": isDark() ? "#1f2937" : "#ffffff",
                 "text-background-opacity": 0.85, "text-background-padding": "2px" as any },
      },
      {
        // 交換器 → 自己的接入群組：一條線代表整組。整組都是直連的埠才畫實線，否則是 FDB 推導（虛線）
        selector: 'edge[kind = "trunk"]',
        style: { "line-color": "#14b8a6", width: 2, "line-style": "dashed", "curve-style": "straight" },
      },
      { selector: 'edge[kind = "trunk"][evidence = "monitored"]', style: { "line-style": "solid" } },
      {
        // 同一台在別台交換器也被學到：待確認（不是雙接的證據）
        selector: "edge[?pending]",
        style: { "line-color": "#94a3b8", "line-style": "dashed", "line-dash-pattern": [6, 4], opacity: 0.9 },
      },
      {
        // 分層版面：交換器／路由器在別台交換器的埠上被學到 → 上聯，常駐顯示、帶埠號、畫得比接入線重
        selector: "edge[?backbone]",
        style: { width: 2.6, "line-color": "#0d9488", "font-weight": "bold", "font-size": 10 },
      },
      {
        // 骨幹是 FDB 推得的（兩台互相學到對方）：虛線；鄰居探索回報的維持實線
        selector: 'edge[kind = "l2_uplink"][evidence = "learned"]',
        style: { "line-style": "dashed" },
      },
      {
        selector: "node:selected",
        style: {
          "border-width": 3,
          "border-color": "#f59e0b",
        },
      },
      {
        selector: "edge:selected",
        style: {
          "line-color": "#fbbf24",
          "target-arrow-color": "#fbbf24",
          "source-arrow-color": "#fbbf24",
          width: 5,
          opacity: 1,
          "z-index": 9999,
        },
      },
    ],
    layout: {
      name: "cose-bilkent",
      // 拉長邊、加大斥力，並把「標籤尺寸」算進節點碰撞 → 節點與邊上的文字
      // （VPN 端點 / tunnel 名 / IP）才不會疊在一起。
      idealEdgeLength: 150,
      nodeRepulsion: 9000,
      edgeElasticity: 0.4,
      nodeDimensionsIncludeLabels: true,
      gravity: 0.25,
      animate: false,
    } as any,
  });

  cy.on("tap", "node", (evt) => {
    evt.cy.elements().unselect();
    evt.target.select();                 // 維持選定高亮
    edgeEnds.value = null;
    selected.value = { ...evt.target.data() };
    focusId.value = evt.target.data("isGroup") ? null : evt.target.id();
    applyVisibility();
  });
  cy.on("tap", "edge", (evt) => {
    evt.cy.elements().unselect();
    evt.target.select();                 // 點線後維持選定狀態（放開滑鼠不會消失）
    const d = { ...evt.target.data() };
    const sn = (evt.cy.getElementById(d.source)?.data() ?? {}) as Record<string, any>;
    const tn = (evt.cy.getElementById(d.target)?.data() ?? {}) as Record<string, any>;
    edgeEnds.value = {
      a: { name: sn.label ?? null, ip: sn.ip ?? null, port: d.source_port ?? null, endpoint: d.a_endpoint ?? null },
      b: { name: tn.label ?? null, ip: tn.ip ?? null, port: d.target_port ?? null, endpoint: d.b_endpoint ?? null },
    };
    selected.value = d;
  });
  cy.on("tap", (evt) => {
    if (evt.target === evt.cy) {
      evt.cy.elements().unselect();
      edgeEnds.value = null;
      selected.value = null;
      focusId.value = null;
      applyVisibility();
    }
  });
  // 有 FDB 存取層邊的端點＝查得出位置的，預設放行（見 hiddenTypes 上方說明）
  fdbPlaced.value = new Set(
    (data.edges ?? [])
      .filter((e) => e.data.kind === "l2" || e.data.kind === "l2_uplink")
      .flatMap((e) => [e.data.source, e.data.target]),
  );
  applyVisibility();   // 套用圖例的點暗（隱藏類別）狀態
  arrangeLayout();
}

/** 有沒有存取層資料，決定 auto 模式要走哪一種版面。 */
function hasAccessLayer(): boolean {
  return !!cy && cy.edges('[kind = "l2"], [kind = "l2_uplink"]').length > 0;
}

function arrangeLayout() {
  if (!cy) return;
  const m = viewMode.value;
  const switchCentric =
    m === "l2" || m === "switch" || (m === "auto" && hasAccessLayer());
  // 選了交換器為中心卻沒有存取層資料時退回子網路版面：畫不出來的東西不要硬畫，
  // 一個沒有中心的「以中心為版面」只會變成一團亂。
  if (switchCentric && hasAccessLayer()) arrangeSwitchCentric();
  else spreadSubnets();
}

/**
 * 以交換器為中心的版面。
 *
 * 交換器排成一列在中間，插在它上面的機器呈扇形排在**上方**，該網段的子網路節點放在
 * **下方**、和交換器算同一組（使用者的說法是「放在 switch 下面當做一起」），只連到
 * 子網路、沒有存取層資訊的節點再排在子網路下面。
 *
 * 這裡是自己算座標而不是換一種力導向版面：力導向沒有「上下」的概念，
 * 「交換器在中間、機器在上、網段在下」這種語意它表達不出來。
 */
function arrangeSwitchCentric() {
  if (!cy) return;
  /*
   * 分層（2026-10-21 依分析者建議與參考設計改版）：
   *   第一層  路由器／防火牆（閘道）
   *   第二層  交換器一排，骨幹線在這一層橫向相連
   *   第三層  每台交換器自己的接入群組（框），主機排成格子
   *   第四層  同網段但查不出接在哪台交換器的（「接入位置未確認」）
   * 其他網段的框排在下方；VPN 從整張圖的右外側繞（routeVpnOutside）。
   * 力導向版面沒有上下的概念，所以座標自己算。
   */
  const visible = (n: any) => n.style("display") !== "none";
  const centres: string[] = [];
  const seen = new Set<string>();
  const addCentre = (id: string) => { if (!seen.has(id)) { seen.add(id); centres.push(id); } };
  cy.nodes('[type = "access"]').forEach((g) => {
    if (String(g.id()).startsWith("acc:")) addCentre(String(g.id()).slice(4));
    else (g.data("switches") as string[] | undefined)?.forEach(addCentre);
  });
  cy.edges('[kind = "l2"]').forEach((e) => {
    addCentre(e.target().id());
    if (e.data("backbone") && e.source().data("type") === "switch") addCentre(e.source().id());
  });
  cy.edges('[kind = "l2_uplink"]').forEach((e) => { addCentre(e.source().id()); addCentre(e.target().id()); });
  if (!centres.length) { spreadSubnets(); return; }

  // 有骨幹相連的交換器排在一起，骨幹線才不會穿過第三台
  const adj = new Map<string, string[]>();
  cy.edges('[kind = "l2_uplink"]').forEach((e) => {
    const [a, b] = [e.source().id(), e.target().id()];
    (adj.get(a) ?? adj.set(a, []).get(a)!).push(b);
    (adj.get(b) ?? adj.set(b, []).get(b)!).push(a);
  });
  const ordered: string[] = [];
  const queued = new Set<string>();
  for (const start of centres) {
    const stack = [start];
    while (stack.length) {
      const id = stack.pop()!;
      if (queued.has(id) || !centres.includes(id)) continue;
      queued.add(id);
      ordered.push(id);
      for (const nb of adj.get(id) ?? []) if (!queued.has(nb)) stack.push(nb);
    }
  }

  const hasVms = cy.edges('[kind = "vm_host"]').length > 0;
  const HOST_DX = hasVms ? 190 : 150;          // 名稱在圖示下方佔兩行，左右要留得下約 140px
  const HOST_DY = hasVms ? 150 : 110;
  const GROUP_TOP = 150;                       // 交換器到接入群組第一列
  const SW_Y = 0;
  const GW_Y = -190;                           // 閘道那一排
  const placed = new Set<string>();

  // 每台交換器那一欄的寬度依它的主機數決定（格子 2～4 欄），欄與欄之間留群組框的間距
  const hostsOf = new Map<string, any[]>();
  for (const id of ordered) {
    const g = cy.getElementById(`acc:${id}`);
    const kids = g.nonempty() ? g.children().filter((n: any) => visible(n) && n.data("type") !== "vm").toArray() : [];
    kids.sort((a: any, b: any) => String(a.data("label")).localeCompare(String(b.data("label"))));
    hostsOf.set(id, kids);
  }
  const colsOf = (n: number) => (n <= 2 ? Math.max(n, 1) : n <= 6 ? 3 : 4);
  // 雙接群組自成一欄，插在它接的交換器中排最後那台的前面（兩台相鄰時就是兩台中間），
  // 兩台交換器的線斜斜地下來會合、不穿過別的群組框（10-23）
  const multiOf = new Map<string, any[]>();
  const columns: { key: string; sw: string | null }[] = [];
  const multiBefore = new Map<string, string[]>();
  cy.nodes('[type = "access"]').filter((g) => String(g.id()).startsWith("acc2:")).forEach((g) => {
    const kids = g.children().filter((n: any) => visible(n) && n.data("type") !== "vm").toArray()
      .sort((a: any, b: any) => String(a.data("label")).localeCompare(String(b.data("label"))));
    if (!kids.length) return;
    const idx = (g.data("switches") as string[]).map((id) => ordered.indexOf(id)).filter((i) => i >= 0);
    if (!idx.length) return;
    const last = ordered[Math.max(...idx)];
    multiOf.set(g.id(), kids);
    (multiBefore.get(last) ?? multiBefore.set(last, []).get(last)!).push(g.id());
  });
  for (const id of ordered) {
    for (const gid of multiBefore.get(id) ?? []) columns.push({ key: gid, sw: null });
    columns.push({ key: id, sw: id });
  }
  const kidsOf = (c: { key: string; sw: string | null }) => (c.sw ? hostsOf.get(c.sw)! : multiOf.get(c.key)!);
  const widthOf = (c: { key: string; sw: string | null }) => Math.max(1, colsOf(kidsOf(c).length)) * HOST_DX;
  const GAP = 90;
  const total = columns.reduce((a, c) => a + widthOf(c), 0) + GAP * (columns.length - 1);
  let x = -total / 2;
  let lowestY = SW_Y;
  for (const c of columns) {
    const w = widthOf(c);
    const cx = x + w / 2;
    x += w + GAP;
    if (c.sw) {
      cy.getElementById(c.sw).position({ x: cx, y: SW_Y });
      placed.add(c.sw);
    }
    const hosts = kidsOf(c);
    const cols = colsOf(hosts.length);
    hosts.forEach((h: any, i: number) => {
      const row = Math.floor(i / cols);
      const inRow = Math.min(cols, hosts.length - row * cols);
      const col = (i % cols) - (inRow - 1) / 2;
      const y = SW_Y + GROUP_TOP + row * HOST_DY;
      h.position({ x: cx + col * HOST_DX, y });
      placed.add(h.id());
      lowestY = Math.max(lowestY, y);
    });
  }

  // 閘道：路由器與防火牆排在交換器上方（在網段框裡的就留在框裡的最上面）
  const gws = cy.nodes().filter((n) => !placed.has(n.id()) && !n.isParent() && visible(n)
    && ["router", "firewall"].includes(String(n.data("type"))) && !hasOnlyVpnInOtherBox(n));
  const gn = gws.length;
  gws.forEach((n, i) => {
    n.position({ x: (i - (gn - 1) / 2) * 220, y: GW_Y });
    placed.add(n.id());
  });

  // 同網段、查不出接在哪台交換器的：整排放在接入群組下面（置中在整個網段框底下）
  const unplacedY = lowestY + 170;
  let nextY = unplacedY;
  cy.nodes('[type = "unplaced"]').forEach((g) => {
    const kids = g.children().filter((c: any) => visible(c) && !placed.has(c.id()) && c.data("type") !== "vm");
    const kn = kids.length;
    const perRow = Math.max(4, Math.ceil(Math.sqrt(kn * 2)));
    kids.forEach((m2: any, j: number) => {
      const row = Math.floor(j / perRow);
      const inRow = Math.min(perRow, kn - row * perRow);
      const col = (j % perRow) - (inRow - 1) / 2;
      const y = unplacedY + row * 105;
      m2.position({ x: col * HOST_DX, y });
      placed.add(m2.id());
      nextY = Math.max(nextY, y);
    });
  });
  // 有交換器的網段框裡，其餘還沒排到的成員（沒有被分進群組的）也放這一區
  const strayBox = cy.nodes().filter((n) => !placed.has(n.id()) && !n.isParent() && visible(n)
    && n.data("type") !== "vm" && n.ancestors().some((a: any) => a.descendants().some((d: any) => centres.includes(d.id()))));
  const sn = strayBox.length;
  strayBox.forEach((n, j) => {
    const row = Math.floor(j / 6);
    const col = (j % 6) - (Math.min(6, sn - row * 6) - 1) / 2;
    const y = nextY + 120 + row * 105;
    n.position({ x: col * HOST_DX, y });
    placed.add(n.id());
    nextY = Math.max(nextY, y);
  });

  // 虛擬機：貼在所在實體主機的正下方
  cy.edges('[kind = "vm_host"]').forEach((e) => {
    const host = e.target();
    if (!placed.has(host.id())) return;
    const vms = host.connectedEdges('[kind = "vm_host"]').sources()
      .filter((n: any) => n.id() !== host.id() && visible(n));
    vms.forEach((vm: any, i: number) => {
      if (placed.has(vm.id())) return;
      placed.add(vm.id());
      const row = Math.floor(i / 2);
      const col = (i % 2) - (Math.min(2, vms.length - row * 2) - 1) / 2;
      vm.position({ x: host.position("x") + col * 60, y: host.position("y") + 62 + row * 46 });
    });
  });

  // 其他網段（沒有交換器的框）與框外的節點：依框分組排在下方，一框一列
  const restY = nextY + 230;
  const rest = cy.nodes().filter((n) => !placed.has(n.id()) && !n.isParent() && visible(n));
  const byBox = new Map<string, any[]>();
  rest.forEach((n) => {
    const box = n.parent().nonempty() ? n.parent()[0].id() : "";
    (byBox.get(box) ?? byBox.set(box, []).get(box)!).push(n);
  });
  let rowY = restY;
  for (const [, ns] of byBox) {
    ns.sort((a: any, b: any) => String(a.data("label")).localeCompare(String(b.data("label"))));
    const k = ns.length;
    const perRow = Math.max(5, Math.ceil(Math.sqrt(k * 2)));
    let maxRow = 0;
    ns.forEach((n: any, i: number) => {
      const row = Math.floor(i / perRow);
      const col = (i % perRow) - (Math.min(perRow, k - row * perRow) - 1) / 2;
      n.position({ x: col * HOST_DX, y: rowY + row * 110 });
      maxRow = row;
    });
    rowY += (maxRow + 1) * 110 + 140;
  }

  routeVpnOutside();
  // 接入群組的標題放在框的左上角：置中的話，交換器到群組那條線剛好壓在標題上
  cy.nodes('[type = "access"], [type = "unplaced"]').forEach((g) => {
    const w = g.boundingBox({}).w;
    const lw = String(g.data("label") ?? "").length * 7;
    g.style("text-margin-x", -w / 2 + lw / 2 + 14);
  });
  cy.fit(undefined, 40);
}

/** VPN 對端（另一個網段框裡、只靠 VPN 連過來的防火牆）不拉到閘道那一排：留在它自己的網段框 */
function hasOnlyVpnInOtherBox(n: any): boolean {
  if (!n.parent().nonempty()) return false;
  const box = n.parent()[0];
  const hasSwitch = box.descendants().some((d: any) => d.data("type") === "switch");
  return !hasSwitch;
}

/** VPN 從整張圖的右外側繞（折成ㄈ字），不從中間斜穿過網段框與節點 */
function routeVpnOutside() {
  if (!cy) return;
  const vpns = cy.edges('[kind = "vpn"]').filter((e) => e.style("display") !== "none");
  if (!vpns.length) return;
  const bb = cy.nodes().filter((n) => n.style("display") !== "none").boundingBox({});
  let lane = bb.x2 + 70;
  let top = bb.y1 - 30;
  vpns.forEach((e) => {
    const s = e.source().position();
    const t = e.target().position();
    // 上面那一端先往上繞到整張圖的上緣，再往右走到外側車道，往下到另一端的高度再進去：
    // 水平段才不會穿過同一排的其他節點（例：閘道那一排），也不會從中間直直穿過網段框
    const pts = s.y <= t.y
      ? [{ x: s.x, y: top }, { x: lane, y: top }, { x: lane, y: t.y }]
      : [{ x: lane, y: s.y }, { x: lane, y: top }, { x: t.x, y: top }];
    const { weights, distances } = segmentsFor(s, t, pts);
    e.style({
      "curve-style": "segments",
      "edge-distances": "node-position",
      "segment-weights": weights,
      "segment-distances": distances,
    } as any);
    lane += 26;                      // 好幾條 VPN 各走一條外側車道，不疊在一起
    top -= 18;
  });
}

// 把多個 subnet 中心節點沿水平等距推開；只屬單一 subnet 的節點跟著平移，
// 同時連多個 subnet 的裝置留在原處（落在兩團中間），避免被夾擠。
function spreadSubnets() {
  /*
   * 依子網路排版（10-22 改寫）：同一個網段收成一團，網段與網段之間才拉開。
   * 以前只把子網路節點左右推 760px，成員留在力導向散開的位置（整張圖一開始就很開）；
   * 混合模式的子網路是框（複合節點），推框不會帶著成員走，框就互相重疊（使用者截圖）。
   *   只看子網路：子網路節點在中間，成員繞一圈（ringPositions）
   *   混合（框）：成員在框裡排成格子（gridPositions），框的大小跟著成員
   *   跨兩個以上網段的（路由器、防火牆）：放在它連到的那幾團上方中間
   * 純函式在 utils/topologyLayout.ts（有 vitest）。
   */
  if (!cy) return;
  const isSub = (n: any) => String(n.id()).startsWith("subnet:");
  const subs: any[] = cy.nodes().filter((n: any) => isSub(n) && n.style("display") !== "none").toArray();
  if (!subs.length) return;
  // 只排看得到的：被圖例或篩選藏起來的節點不可以佔位置（否則一團還是散得很開）
  const shown = (n: any) => n.style("display") !== "none";
  const home = new Map<string, string>();                   // 節點 → 它所屬的網段
  for (const sn of subs) {
    if (sn.isParent()) sn.descendants().forEach((d: any) => { if (!d.isParent() && shown(d)) home.set(d.id(), sn.id()); });
  }
  const leaves = cy.nodes().filter((n: any) => !isSub(n) && !n.isParent() && shown(n));
  leaves.forEach((n: any) => {
    if (home.has(n.id())) return;
    const ss = n.openNeighborhood().nodes().filter((x: any) => isSub(x));
    if (ss.length === 1) home.set(n.id(), ss[0].id());
  });
  // 自己沒有網段、只連到同一團的（例如虛擬機跟著主機）：跟著那一團
  leaves.forEach((n: any) => {
    if (home.has(n.id()) || n.openNeighborhood().nodes().filter((x: any) => isSub(x)).length) return;
    const gs = new Set(n.openNeighborhood().nodes().map((x: any) => home.get(x.id())).filter(Boolean));
    if (gs.size === 1) home.set(n.id(), [...gs][0] as string);
  });

  const members = new Map<string, any[]>();
  for (const sn of subs) members.set(sn.id(), []);
  leaves.forEach((n: any) => { const h = home.get(n.id()); if (h) members.get(h)!.push(n); });
  const label = (n: any) => String(n.data("label") ?? n.id());
  const groups = subs.map((sn) => {
    const list = members.get(sn.id())!.sort((a, b) => label(a).localeCompare(label(b)));
    const boxed = sn.isParent();
    const rel = boxed ? gridPositions(list.length, 130, 110) : ringPositions(list.length, 96);
    const xs = rel.map((p) => p.x), ys = rel.map((p) => p.y);
    const pad = boxed ? 70 : 80;                             // 名稱在圖示下方，四周留得下
    const minX = Math.min(0, ...xs) - pad, maxX = Math.max(0, ...xs) + pad;
    const minY = Math.min(0, ...ys) - pad, maxY = Math.max(0, ...ys) + pad + 20;
    return { sn, list, rel, boxed, minX, minY, w: maxX - minX, h: maxY - minY };
  }).sort((a, b) => b.list.length - a.list.length || label(a.sn).localeCompare(label(b.sn)));

  const area = groups.reduce((a, g) => a + g.w * g.h, 0);
  const GAP = 220;                                           // 不同網段之間明顯拉開
  const pos = packRows(groups.map((g) => ({ id: g.sn.id(), w: g.w, h: g.h })), GAP,
                       Math.max(1600, Math.sqrt(area) * 1.8));
  const centre = new Map<string, { x: number; y: number; top: number }>();
  for (const g of groups) {
    const tl = pos.get(g.sn.id())!;
    centre.set(g.sn.id(), { x: tl.x - g.minX, y: tl.y - g.minY, top: tl.y });
  }
  // VPN 端點排到面向對方的那一側，對連線才不會穿過同一團的其他節點
  for (const g of groups) {
    const me = centre.get(g.sn.id())!;
    const used = new Set<number>();
    g.list.forEach((n: any, k: number) => {
      const peers = n.connectedEdges('[kind = "vpn"]').connectedNodes().filter((x: any) => x.id() !== n.id());
      const far = peers.map((x: any) => centre.get(home.get(x.id()) ?? "")).filter((c: any) => c && c !== me);
      if (!far.length) return;
      const dx = far[0].x - me.x, dy = far[0].y - me.y;
      let best = k, score = -Infinity;
      g.rel.forEach((r, i) => { const v = r.x * dx + r.y * dy; if (!used.has(i) && v > score) { score = v; best = i; } });
      used.add(best);
      [g.list[k], g.list[best]] = [g.list[best], g.list[k]];
    });
  }
  cy.batch(() => {
    for (const g of groups) {
      const tl = pos.get(g.sn.id())!;
      const ox = tl.x - g.minX, oy = tl.y - g.minY;          // (0,0) 的位置
      if (!g.boxed) g.sn.position({ x: ox, y: oy });
      g.list.forEach((n: any, k: number) => n.position({ x: ox + g.rel[k].x, y: oy + g.rel[k].y }));
    }
    // 跨網段的：放在連到的那幾團上方中間；同一個位置有人了就往右挪
    const taken: { x: number; y: number }[] = [];
    let spare = 0;
    leaves.forEach((n: any) => {
      if (home.has(n.id())) return;
      const linked = n.openNeighborhood().nodes().map((x: any) => centre.get(x.id()) ?? centre.get(home.get(x.id()) ?? ""))
        .filter(Boolean) as { x: number; y: number; top: number }[];
      let p: { x: number; y: number };
      if (linked.length) {
        p = { x: linked.reduce((a, c) => a + c.x, 0) / linked.length, y: Math.min(...linked.map((c) => c.top)) - 120 };
      } else {
        p = { x: spare++ * 150, y: -240 };                   // 什麼都沒連到的排在最上面
      }
      while (taken.some((q) => Math.abs(q.x - p.x) < 140 && Math.abs(q.y - p.y) < 100)) p = { x: p.x + 150, y: p.y };
      taken.push(p);
      n.position(p);
    });
  });
  cy.fit(undefined, 40);
}

watch(subnetIds, () => { void refresh(); });
watch([includeWireless, includeVpn, includeL3, includeFdb, includeVms, onlineOnly, viewMode], () => {
  void refresh();
});

onMounted(async () => {
  await loadSubnetOptions();
  // 有設常用子網路 → 進來預設只畫這些；否則畫全部
  let usedPinned = false;
  try {
    await ensureLoaded();
    if (pinned.value.length) { subnetIds.value = [...pinned.value]; usedPinned = true; }
  } catch { /* ignore */ }
  // 設了 subnetIds 會觸發 watch → refresh；沒設才在這裡主動畫一次
  if (!usedPinned) void refresh();
});
onUnmounted(() => {
  if (cy) cy.destroy();
});
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><TopologyIcon /></n-icon>
        <span>{{ t("nav.topology") }}</span>
      </n-space>
    </template>
    <!-- 動作靠右、與篩選同一列。原本它們自己獨占一列靠右對齊，畫面上多一條空帶，
         視窗一窄又會跟篩選擠在一起；卡片標題列則只放標題（使用者回報兩次）。 -->
    <div ref="topoBarEl">
    <n-space class="topo-toolbar" align="center" :wrap="true"
             style="margin-bottom: 12px; row-gap: 8px">
      <n-select
        v-model:value="subnetIds"
        :options="subnetOptions"
        multiple filterable clearable
        :placeholder="t('topology.filter_subnets')"
        style="width: 250px"
        :consistent-menu-width="false"
        max-tag-count="responsive"
      />
        <n-select v-model:value="viewMode" :options="viewModeOptions"
                  style="width: 190px" :consistent-menu-width="false" />
        <n-checkbox v-if="!modeDrivesSources" v-model:checked="includeWireless">{{ t("topology.wireless") }}</n-checkbox>
        <n-checkbox v-if="!modeDrivesSources" v-model:checked="includeVpn">{{ t("topology.vpn") }}</n-checkbox>
        <n-checkbox v-if="!modeDrivesSources" v-model:checked="includeL3">{{ t("topology.l3") }}</n-checkbox>
        <n-checkbox v-if="!modeDrivesSources" v-model:checked="includeFdb">{{ t("topology.fdb") }}</n-checkbox>
        <n-tooltip trigger="hover">
          <template #trigger>
            <n-checkbox v-model:checked="assertedOnly" @update:checked="applyVisibility">
              {{ t("topology.asserted_only") }}
            </n-checkbox>
          </template>
          {{ t("topology.asserted_only_hint") }}
        </n-tooltip>
        <n-checkbox v-model:checked="onlineOnly">{{ t("topology.online_only") }}</n-checkbox>
        <n-tooltip v-if="switchView" trigger="hover">
          <template #trigger>
            <n-checkbox v-model:checked="showAllAccess" data-testid="topology-all-access" @update:checked="applyVisibility">
              {{ t("topology.all_access_links") }}
            </n-checkbox>
          </template>
          {{ t("topology.all_access_links_hint") }}
        </n-tooltip>
        <n-button-group>
          <n-button @click="zoomBy(1.2)" :title="t('topology.zoom_in')">＋</n-button>
          <n-button @click="zoomBy(0.83)" :title="t('topology.zoom_out')">－</n-button>
          <n-button @click="fitView">
            <template #icon><n-icon><FitIcon /></n-icon></template>
            {{ t("topology.fit") }}
          </n-button>
        </n-button-group>
        <!-- 兩顆動作綁成同一個 flex 子項：換行時一起換 -->
        <n-space align="center" :size="8" :wrap-item="false">
          <n-dropdown trigger="click" :options="exportOptions" :disabled="exporting" @select="onExport">
            <n-button :loading="exporting" :disabled="exporting">
              <template #icon><n-icon><ExportIcon /></n-icon></template>
              {{ t("common.export") }}
            </n-button>
          </n-dropdown>
          <n-button @click="refresh">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>
            {{ t("common.refresh") }}
          </n-button>
        </n-space>
      </n-space>
    </div>
    <n-alert v-if="scopeLimited" type="info" :bordered="false" style="margin-bottom: 10px" data-testid="topology-scope-limited">
      {{ t("topology.scope_limited") }}
    </n-alert>
    <n-alert v-if="tooLarge" type="warning" :bordered="false" style="margin-bottom: 10px" data-testid="topology-too-large">
      {{ t("topology.too_large", { n: tooLarge.devices.toLocaleString(), limit: tooLarge.limit.toLocaleString() }) }}
    </n-alert>
    <n-spin :show="loading">
      <div ref="shellEl" class="topology-shell" :style="fill.height.value ? { height: `${fill.height.value}px` } : undefined">
        <div ref="containerRef" class="cy"></div>
        <n-card v-if="selected" size="small" class="info-pane" :title="selectedTitle" closable @close="selected = null">
          <table class="info-table">
            <tbody>
              <tr v-for="row in selectedRows" :key="row.key">
                <th>{{ row.label }}</th>
                <!-- 雙接的連接埠一條一行（換行字元），不在名稱中間折行 -->
                <td style="white-space: pre-line">{{ row.value }}</td>
              </tr>
            </tbody>
          </table>
          <div v-if="edgeEnds" class="edge-ends">
            <div class="ee-title">{{ t("topology.connection_ends") }}</div>
            <div class="ee-grid">
              <div v-for="(e, i) in [{ label: t('topology.end_a'), d: edgeEnds.a }, { label: t('topology.end_b'), d: edgeEnds.b }]"
                   :key="i" class="ee-col">
                <div class="ee-h">{{ e.label }}</div>
                <div v-if="e.d.name" class="ee-row"><span>{{ t('topology.end_name') }}</span><b>{{ e.d.name }}</b></div>
                <div v-if="e.d.ip" class="ee-row"><span>IP</span><b>{{ e.d.ip }}</b></div>
                <div v-if="e.d.port" class="ee-row"><span>{{ t('topology.port') }}</span><b>{{ e.d.port }}</b></div>
                <div v-if="e.d.endpoint" class="ee-row"><span>{{ t('topology.endpoint') }}</span><b>{{ e.d.endpoint }}</b></div>
                <div v-if="!e.d.name && !e.d.ip && !e.d.port && !e.d.endpoint" class="ee-empty">{{ t('topology.end_unknown') }}</div>
              </div>
            </div>
          </div>
          <template v-if="selectedDeviceId || selectedSubnetId" #action>
            <!-- 按鈕之間留 8px（以前第二顆貼著第一顆，10-23「靠太近」） -->
            <div class="detail-actions">
              <n-button v-if="selectedDeviceId" size="small" type="primary" ghost block @click="goDevice">
                <template #icon><n-icon :component="DevicesIcon" /></template>{{ t("topology.open_device") }}
              </n-button>
              <n-button v-if="selectedDeviceId && selected?.mac" size="small" block @click="goMac">
                <template #icon><n-icon :component="MacIcon" /></template>{{ t("topology.open_mac_history") }}
              </n-button>
              <n-button v-else size="small" type="primary" ghost block @click="goSubnet">
                <template #icon><n-icon :component="SubnetsIcon" /></template>{{ t("topology.open_subnet") }}
              </n-button>
            </div>
          </template>
        </n-card>
      </div>
    </n-spin>
    <!-- 分層版面的圖例：只講這張圖用到的四種線（參考設計） -->
    <div v-if="switchView" class="topo-legend" data-testid="topology-legend-layered">
      <span class="lg lg-head">{{ t("topology.legend_links") }}</span>
      <span class="lg"><svg width="26" height="10"><line x1="0" y1="5" x2="26" y2="5" stroke="#0d9488" stroke-width="2.5"/></svg>{{ t("topology.legend_neighbor") }}</span>
      <span class="lg"><svg width="26" height="10"><line x1="0" y1="5" x2="26" y2="5" stroke="#14b8a6" stroke-width="2" stroke-dasharray="5,3"/></svg>{{ t("topology.legend_fdb") }}</span>
      <span class="lg"><svg width="26" height="10"><line x1="0" y1="5" x2="26" y2="5" stroke="#94a3b8" stroke-width="2" stroke-dasharray="6,4"/></svg>{{ t("topology.legend_pending") }}</span>
      <!-- 雙接群組：兩台交換器各拉一條線到同一個框（10-23） -->
      <span class="lg"><svg width="30" height="14"><line x1="3" y1="0" x2="12" y2="8" stroke="#14b8a6" stroke-width="1.6" stroke-dasharray="3,2"/><line x1="27" y1="0" x2="18" y2="8" stroke="#14b8a6" stroke-width="1.6" stroke-dasharray="3,2"/><rect x="8" y="8" width="14" height="5" rx="1.5" fill="none" stroke="#14b8a6" stroke-dasharray="2,1.5"/></svg>{{ t("topology.legend_multi") }}</span>
      <span class="lg"><svg width="26" height="10"><line x1="0" y1="5" x2="26" y2="5" stroke="#9333ea" stroke-width="3" stroke-dasharray="8,4"/></svg>{{ t("topology.legend_vpn") }}</span>
      <span class="lg"><svg width="26" height="10"><line x1="0" y1="5" x2="26" y2="5" stroke="#d9822b" stroke-width="5"/></svg>{{ t("device_groups.title") }}</span>
      <span class="lg muted">{{ t("topology.layered_hint") }}</span>
    </div>
    <div v-else class="topo-legend">
      <span class="lg lg-head">{{ t("topology.legend_links") }}</span>
      <span class="lg"><svg width="26" height="10"><line x1="0" y1="5" x2="26" y2="5" stroke="#475569" stroke-width="2"/></svg>{{ t("topology.kind_cable") }}</span>
      <span class="lg"><svg width="26" height="10"><line x1="0" y1="5" x2="26" y2="5" stroke="#3b82f6" stroke-width="2" stroke-dasharray="5,3"/></svg>{{ t("topology.kind_wireless") }}</span>
      <span class="lg"><svg width="26" height="10"><line x1="0" y1="5" x2="26" y2="5" stroke="#9333ea" stroke-width="4" stroke-dasharray="8,4"/></svg>{{ t("topology.kind_vpn") }}</span>
      <span class="lg"><svg width="26" height="10"><line x1="0" y1="5" x2="26" y2="5" stroke="#0ea5e9" stroke-width="1.5" stroke-dasharray="5,3"/></svg>{{ t("topology.kind_l3") }}</span>
      <span class="lg"><svg width="26" height="10"><line x1="0" y1="5" x2="26" y2="5" stroke="#14b8a6" stroke-width="1.6"/></svg>{{ t("topology.kind_l2") }}</span>
      <span class="lg"><svg width="26" height="10"><line x1="0" y1="5" x2="26" y2="5" stroke="#14b8a6" stroke-width="1.6" stroke-dasharray="5,3"/></svg>{{ t("topology.kind_l2_behind") }}</span>
      <span class="lg"><svg width="26" height="10"><line x1="0" y1="5" x2="26" y2="5" stroke="#0d9488" stroke-width="3.5"/></svg>{{ t("topology.kind_l2_uplink") }}</span>
      <span class="lg"><svg width="26" height="10"><line x1="0" y1="5" x2="26" y2="5" stroke="#a78bfa" stroke-width="1.2"/></svg>{{ t("topology.kind_vm_host") }}</span>
      <span class="lg"><svg width="26" height="10"><line x1="0" y1="5" x2="26" y2="5" stroke="#d9822b" stroke-width="5"/></svg>{{ t("device_groups.title") }}</span>
      <span class="lg lg-sep"></span>
      <span class="lg lg-head">{{ t("topology.legend_nodes") }}</span>
      <span class="lg clickable" :class="{ off: isGroupOff('firewall') }" @click="toggleGroup('firewall')"><i class="dot" style="background:#ef4444"></i>{{ t("topology.type_firewall") }}</span>
      <span class="lg clickable" :class="{ off: isGroupOff('router') }" @click="toggleGroup('router')"><i class="dot" style="background:#6366f1"></i>{{ t("topology.type_router") }}</span>
      <span class="lg clickable" :class="{ off: isGroupOff('switch') }" @click="toggleGroup('switch')"><i class="dot" style="background:#22c55e"></i>{{ t("topology.type_switch") }}</span>
      <span class="lg clickable" :class="{ off: isGroupOff('ap') }" @click="toggleGroup('ap')"><i class="dot" style="background:#3b82f6"></i>AP</span>
      <span class="lg clickable" :class="{ off: isGroupOff('server') }" @click="toggleGroup('server')"><i class="dot" style="background:#9ca3af"></i>{{ t("topology.server_other") }}</span>
      <span class="lg clickable" :class="{ off: isGroupOff('vm') }" @click="toggleGroup('vm')"><i class="dot dot-rect" style="background:#8b5cf6"></i>{{ t("topology.type_vm") }}</span>
      <span class="lg clickable" :class="{ off: isGroupOff('vpn_site') }" @click="toggleGroup('vpn_site')"><svg width="14" height="14"><rect x="2" y="2" width="9" height="9" transform="rotate(45 7 7)" fill="#9333ea"/></svg>{{ t("topology.type_vpn_site") }}</span>
      <span class="lg clickable" :class="{ off: isGroupOff('subnet') }" @click="toggleGroup('subnet')"><i class="dot dot-rect" style="background:#0ea5e9"></i>{{ t("topology.type_subnet") }}</span>
      <span class="lg"><svg width="26" height="10"><line x1="0" y1="5" x2="26" y2="5" stroke="#94a3b8" stroke-width="1.6" stroke-dasharray="1,3"/></svg>{{ t("topology.kind_inferred") }}</span>
      <span class="lg muted">{{ t("topology.toggle_hint") }}</span>
    </div>
  </n-card>
</template>

<style scoped>
.topo-legend {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 16px;
  margin-top: 8px;
  font-size: 12px;
  opacity: 0.85;
}
.topo-legend .lg { display: inline-flex; align-items: center; gap: 6px; }
.topo-legend .lg.clickable { cursor: pointer; user-select: none; padding: 1px 4px; border-radius: 4px; }
.topo-legend .lg.clickable:hover { background: rgba(127,127,127,0.12); }
.topo-legend .lg.off { opacity: 0.32; text-decoration: line-through; }
.topo-legend .lg.muted { opacity: 0.6; margin-left: auto; }
.topo-legend .lg-head { font-weight: 600; opacity: 0.55; }
.topo-legend .lg-sep { width: 1px; height: 14px; background: rgba(127,127,127,0.3); }
.topo-legend .dot { width: 11px; height: 11px; border-radius: 50%; display: inline-block; }
.topo-legend .dot-rect { width: 16px; height: 9px; border-radius: 3px; }
.info-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.info-table th {
  text-align: left;
  white-space: nowrap;
  padding: 3px 10px 3px 0;
  color: var(--n-text-color-3, #888);
  font-weight: 500;
  vertical-align: top;
}
.info-table td { padding: 3px 0; word-break: break-all; }
.topology-shell {
  position: relative;
  width: 100%;
  /* 自動延展到接近視窗底部（扣掉頂列／卡片頭／工具列／圖例的概略高度） */
  height: calc(100vh - 270px);
  min-height: 420px;
  background: rgba(127, 127, 127, 0.04);
  border-radius: 6px;
}
.cy {
  width: 100%;
  height: 100%;
}
.info-pane {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 320px;
  max-height: 60vh;
  overflow: auto;
  z-index: 10;
}
.edge-ends { margin-top: 10px; }
.ee-title { font-size: 12px; font-weight: 600; opacity: .75; margin-bottom: 6px; }
.ee-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.ee-col { border: 1px solid rgba(127,127,127,.2); border-radius: 6px; padding: 6px 8px; min-width: 0; }
.ee-h { font-size: 12px; font-weight: 600; margin-bottom: 4px; }
.ee-row { display: flex; justify-content: space-between; gap: 6px; font-size: 11.5px; line-height: 1.6; }
.ee-row span { opacity: .6; flex: none; }
.ee-row b { font-weight: 600; text-align: right; word-break: break-all; }
.ee-empty { font-size: 11.5px; opacity: .5; }
.info-pane pre {
  font-size: 11px;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
}
.detail-actions { display: flex; flex-direction: column; gap: 8px; }
</style>
