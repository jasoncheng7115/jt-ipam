<script setup lang="ts">
import { virtTagText } from "@/utils/virt";
import { computed, h, nextTick, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { usesLevels } from "@/utils/rackSlots";
import {
  NCard, NSpace, NIcon, NButton, NDescriptions, NDescriptionsItem,
  NTag, NDataTable, NSpin, NTooltip, NModal, NSelect, NPopconfirm, NAlert, NDropdown,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { ArrowLeft as ArrowLeftIcon } from "@iconoir/vue";
import { DevicesIcon, RefreshIcon, EditIcon, DeleteIcon, TopologyIcon, AddressesIcon, LibreNMSIcon, WazuhIcon, VirtualizationIcon, SubnetsIcon, LinkIcon , DhcpServerIcon, OpenNewWindowIcon, RustDeskIcon, ChangeImpactIcon } from "@/icons";
import ChangeImpactWizard from "@/components/ChangeImpactWizard.vue";
import MaintenanceQuickButton from "@/components/MaintenanceQuickButton.vue";
import type { ScenarioType } from "@/api/changeImpact";
import { useChangeImpact } from "@/composables/useChangeImpact";
import CopyButton from "@/components/CopyButton.vue";
import { apiClient, apiErrMsg } from "@/api/client";
import { listAddresses, updateAddress } from "@/api/addresses";
import { listLocations, listRacks, getDeviceVlans, getDeviceLibrenms, deleteDevice, type Device, type Location, type Rack, type DeviceVLAN, type DeviceLibreNMS } from "@/api/basic";
import { getDeviceRelations, type RelationNode } from "@/api/relations";
import RelationChain from "@/components/RelationChain.vue";
import UptimeBar from "@/components/UptimeBar.vue";
import RackDiagram from "@/components/RackDiagram.vue";
import DevicePortsPanel from "@/components/DevicePortsPanel.vue";
import DevicePowerPortsPanel from "@/components/DevicePowerPortsPanel.vue";
import DeviceGroupCard from "@/components/DeviceGroupCard.vue";
import DeviceLeaseCard from "@/components/DeviceLeaseCard.vue";
import SnmpDeviceCard from "@/components/SnmpDeviceCard.vue";
import { getSnmpTargetForDevice } from "@/api/snmp";
import SwitchPortLabel from "@/components/SwitchPortLabel.vue";
import { getRackDiagram } from "@/api/racks";
type RackDiagramData = Awaited<ReturnType<typeof getRackDiagram>>;
import IPAddressEditModal from "@/components/IPAddressEditModal.vue";
import DeviceEditModal from "@/components/DeviceEditModal.vue";
import LiveStatusDot from "@/components/LiveStatusDot.vue";
import type { IPAddress } from "@/types";
import { autoSort } from "@/composables/useTableSort";
import { fmtDateTime } from "@/utils/datetime";
import { rustdeskOs } from "@/utils/rustdeskOs";
import { lnmsStatusLabel, wazuhStatusLabel } from "@/utils/integrationStatus";
import { useCustomers } from "@/composables/useCustomers";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import ColumnPicker from "@/components/ColumnPicker.vue";
import { useAuthStore } from "@/stores/auth";
import { storeToRefs } from "pinia";
import { useTablePagination } from "@/composables/useTablePagination";
import { shortOcsAgent } from "@/utils/ocsAgent";
const pg = useTablePagination();
const { t, te } = useI18n();

const { me } = storeToRefs(useAuthStore());
const impact = useChangeImpact();
void impact.load();
const impactWizard = ref(false);
const impactScenario = ref<ScenarioType>("device_decommission");
// 網路設備可以評估維護、伺服器可以評估停機（虛擬化節點）；除役每種都有
const impactMenu = computed(() => {
  const type = device.value?.type ?? "";
  const out = [{ key: "device_decommission", label: t("change_impact.entry_decommission") }];
  if (["switch", "router", "firewall", "ap"].includes(type)) {
    out.push({ key: "switch_maintenance", label: t("change_impact.entry_maintenance") });
  }
  if (["server", "storage", "other"].includes(type)) {
    out.push({ key: "node_downtime", label: t("change_impact.entry_downtime") });
  }
  return out;
});
function openImpact(key: string) {
  impactScenario.value = key as ScenarioType;
  impactWizard.value = true;
}
const isAdmin = computed(() => !!me.value?.is_admin);
// 卡片標題：icon + 文字（NCard title 支援 render function）
/** 虛擬機狀態：平台回來的是 running / stopped 這種英文字，畫面上要說人話。
 *  對不上的就原樣顯示 —— 硬翻不認得的字只會變成假資訊。 */
function vmStatusLabel(v: string | null | undefined): string {
  if (!v) return "—";
  const key = `virt.vm_status_${String(v).toLowerCase()}`;
  return te(key) ? t(key) : v;
}

function cardHead(icon: any, text: string) {
  return h("span", { style: "display:inline-flex;align-items:center;gap:8px" },
    [h(NIcon, { size: 18 }, () => h(icon)), text]);
}

// 外部整合卡片右上角的「在該系統檢視」按鈕會呼叫這個，另開分頁連到該裝置在來源系統的頁面
function openExternal(url: string | null | undefined) {
  if (url) window.open(url, "_blank", "noopener");
}

// 從別頁帶 ?card=<id> 進來時，捲到該卡片並短暫highlight，讓使用者知道落在哪。
// 卡片來自兩支各自載入的 API（LibreNMS 一支、Wazuh／OCS 一支），兩邊載完都呼叫一次；
// 先到的那邊找不到卡片就略過，捲過一次就不再捲（不然後到的那支會把畫面又拉一次）。
const focusedCard = ref<string>("");
let cardFocusDone = false;
function focusRequestedCard() {
  const card = String(route.query.card || "");
  if (!card || cardFocusDone) return;
  void nextTick(() => {
    const el = document.getElementById(`card-${card}`);
    if (!el || cardFocusDone) return;
    cardFocusDone = true;
    const align = () => el.scrollIntoView({ behavior: "smooth", block: "start" });
    align();
    // 上方的卡片（機櫃圖、連接埠、IP 清單）可能比這張晚載入、把它往下推到畫面底部；
    // 版面穩定後若已經不在上半部就再對齊一次
    setTimeout(() => {
      const top = el.getBoundingClientRect().top;
      if (top < 0 || top > window.innerHeight * 0.4) align();
    }, 900);
    focusedCard.value = card;
    setTimeout(() => { focusedCard.value = ""; }, 2400);
  });
}
const { labelFor: customerLabelFor, ensureLoaded: ensureCustomersLoaded } = useCustomers();
const { visibleKeys: ipVisibleKeys, setVisible: setIpVisible, reset: resetIpVisible,
  order: ipColOrder, setOrder: setIpColOrder, orderColumns: orderIpColumns } = useColumnPrefs(
  "device_detail_ips",
  ["live", "ip", "hostname", "state", "mac", "mac_vendor", "switch_port", "description", "last_seen"],
  ["live", "ip", "hostname", "state", "mac", "mac_vendor", "switch_port", "last_seen"],
);
const ipColumnPickerItems = [
  { key: "live", label: t("cols.live") },
  { key: "ip", label: "IP" },
  { key: "hostname", label: t("cols.hostname") },
  { key: "state", label: t("cols.status") },
  { key: "mac", label: "MAC" },
  { key: "mac_vendor", label: t("cols.vendor") },
  { key: "switch_port", label: t("cols.switch_port") },
  { key: "description", label: t("cols.description") },
  { key: "last_seen", label: t("cols.last_seen") },
];

const route = useRoute();
const router = useRouter();
const msg = useMessage();

const device = ref<Device | null>(null);
const editShow = ref(false);
const relations = ref<RelationNode[]>([]);
const location = ref<Location | null>(null);
const rack = ref<Rack | null>(null);
/** 機櫃還是層架 —— 標籤與括號裡的單位都跟著換。 */
const rackUsesLevels = computed(() => usesLevels((rack.value as any)?.kind));

/**
 * 哪裡來、哪裡回。從機櫃圖點進來的會帶 `?from=rack&rack=<id>`；
 * 那台機櫃已經不存在（或根本不是從機櫃來的）就退回裝置清單。
 */
function goBack() {
  const from = String(route.query.from ?? "");
  const rackId = String(route.query.rack ?? "");
  if (from === "rack" && rackId && rack.value?.id === rackId) {
    void router.push({ name: "racks", query: { rack: rackId } });
    return;
  }
  void router.push({ name: "devices" });
}
const rackDiagram = ref<RackDiagramData | null>(null);
/** 帶著機櫃 id 去機櫃頁 —— 少了 query 只會停在「上次看的那一櫃」，看起來像點錯了。 */
function openRackPage() {
  const id = device.value?.rack_id;
  if (!id) return;
  void router.push({ name: "racks", query: { rack: id } });
}
const addresses = ref<IPAddress[]>([]);
// 手動標記（is_dhcp_server）與整合推導（dhcp_server_auto）都算 —— 與 IpRoleTags 判斷一致
const dhcpServerIps = computed(() =>
  addresses.value.filter((a: any) => a.is_dhcp_server || a.dhcp_server_auto).map((a) => a.ip));

// 新增 IP 對應：把一個現有 IP 指派給本裝置（一個裝置可多 IP）
const showLinkIp = ref(false);
const linkIpId = ref<string | null>(null);
const linkCandidates = ref<IPAddress[]>([]);
const linking = ref(false);
const linkLoading = ref(false);
const linkOptions = computed(() => linkCandidates.value.map((a) => ({
  label: `${a.ip}${a.hostname ? " — " + a.hostname : ""}`, value: a.id,
})));
/** 候選位址向後端要，關鍵字也送過去。
 *  原本一次載 2000 筆再由前端過濾，站台超過那個數量時，要連結的那筆就不在清單裡，
 *  而且打關鍵字也找不到（同 GitHub issue #27 的裝置表單）。 */
async function loadLinkCandidates(q?: string) {
  if (!device.value) return;
  linkLoading.value = true;
  try {
    const r = await listAddresses({ page: 1, pageSize: 200, q: q || undefined });
    // 已經掛在這台裝置上的不必再連一次
    linkCandidates.value = r.items.filter((a) => (a as any).device_id !== device.value!.id);
  } catch { msg.error(t("errors.network")); }
  finally { linkLoading.value = false; }
}
let linkSearchTimer: ReturnType<typeof setTimeout> | undefined;
function onLinkSearch(q: string) {
  clearTimeout(linkSearchTimer);
  linkSearchTimer = setTimeout(() => void loadLinkCandidates(q), 250);
}
async function openLinkIp() {
  if (!device.value) return;
  linkIpId.value = null;
  await loadLinkCandidates();
  showLinkIp.value = true;
}
async function doLinkIp() {
  if (!linkIpId.value || !device.value) return;
  linking.value = true;
  try {
    await updateAddress(linkIpId.value, { device_id: device.value.id });
    msg.success(t("common.ok"));
    showLinkIp.value = false;
    await load(device.value.id);
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.network")); }
  finally { linking.value = false; }
}
const vlans = ref<DeviceVLAN[]>([]);
const lnms = ref<DeviceLibreNMS | null>(null);
const hasSnmpTarget = ref(false);   // 這台有 SNMP 收集目標才顯示 SNMP 卡片
const integrations = ref<{ wazuh: any; vm: any; ocs: any; rustdesk?: any } | null>(null);

// OCS 硬體摘要（後端 services/ocs.hardware_summary）。記憶體與顯示記憶體是 MiB；
// 磁碟照 OCS 回報的 MB（廠商標示的容量是十進位，所以用 1000 進位比較對得上）。
const ocsHw = computed<any | null>(() => integrations.value?.ocs?.hw ?? null);
const ocsBoard = computed(() => {
  const b = ocsHw.value?.board;
  return b ? [b.vendor, b.model].filter(Boolean).join(" ") : "";
});
const ocsBios = computed(() => {
  const b = ocsHw.value?.bios;
  if (!b) return "";
  const main = [b.vendor, b.version].filter(Boolean).join(" ");
  return b.date ? `${main} (${b.date})`.trim() : main;
});
function fmtMemMB(mb: number): string {
  // 四捨五入到一位小數、去掉 .0：16380 MiB（顯示卡回報值）→ 16 GB，128564 → 125.6 GB
  const gb = Math.round((mb / 1024) * 10) / 10;
  return gb >= 1 ? `${gb} GB` : `${mb} MB`;
}
function fmtDiskMB(mb: number): string {
  if (mb >= 1_000_000) return `${(mb / 1_000_000).toFixed(2)} TB`;
  if (mb >= 1000) return `${Math.round(mb / 1000)} GB`;
  return `${mb} MB`;
}
function cpuLine(c: { model: string; cores?: number | null; threads?: number | null; mhz?: number | null; count?: number }): string {
  const parts = [(c.count ?? 1) > 1 ? `${c.count} × ${c.model}` : c.model];
  if (c.cores) parts.push(t("device_detail.ocs_cpu_cores", { cores: c.cores, threads: c.threads ?? c.cores }));
  if (c.mhz) parts.push(`${c.mhz} MHz`);
  return parts.join(" · ");
}
// 模組寫法：「4 × DDR5-5600」「1 × 16 GB DDR4-2133」；型別不明時「1 × 16 GB · 2133 MT/s」
function memLine(m: { size_mb?: number | null; type?: string | null; speed?: number | null; count?: number }): string {
  const kind = m.type ? (m.speed ? `${m.type}-${m.speed}` : m.type) : (m.speed ? `${m.speed} MT/s` : "");
  const size = m.size_mb ? fmtMemMB(m.size_mb) : "";
  const desc = size && kind ? (m.type ? `${size} ${kind}` : `${size} · ${kind}`) : (size || kind);
  return `${m.count ?? 1} × ${desc}`;
}
// 實裝容量：每條都有大小才加總（Linux 代理遇到新版 dmidecode 的「GiB」會回 0，那時不猜）
const memInstalledMB = computed<number | null>(() => {
  const mods = ocsHw.value?.memory?.modules ?? [];
  if (!mods.length || mods.some((m: any) => !m.size_mb)) return null;
  return mods.reduce((n: number, m: any) => n + m.size_mb * (m.count ?? 1), 0);
});
// 同型號同容量的磁碟合併成一行（實機一台 5 顆一樣的 SSD 列了 5 行），與處理器、記憶體一致
const ocsDisks = computed<{ model: string; size_mb: number; count: number }[]>(() => {
  const out: { model: string; size_mb: number; count: number }[] = [];
  for (const d of ocsHw.value?.disks ?? []) {
    const same = out.find((o) => o.model === d.model && o.size_mb === d.size_mb);
    if (same) same.count += 1;
    else out.push({ model: d.model, size_mb: d.size_mb, count: 1 });
  }
  return out;
});
const memSizeUnknown = computed(() => (ocsHw.value?.memory?.modules ?? []).some((m: any) => !m.size_mb));
// 可用量（作業系統回報、扣掉保留區）：沒有實裝容量時一定講；有的話差超過 2% 才補充
const memUsableShown = computed(() => {
  const usable = ocsHw.value?.memory?.total_mb;
  if (!usable) return false;
  const inst = memInstalledMB.value;
  return !inst || Math.abs(inst - usable) / inst > 0.02;
});
/** SCA 分數的顏色：低分＝很多項目不符基準。門檻取整數十位，避免給人「剛好及格」的錯覺。 */
function scaType(score: number): "error" | "warning" | "success" {
  if (score < 50) return "error";
  return score < 80 ? "warning" : "success";
}
const loading = ref(false);
const deleting = ref(false);

async function removeDevice() {
  if (!device.value) return;
  deleting.value = true;
  try {
    await deleteDevice(device.value.id);
    msg.success(t("common.deleted"));
    // 刪掉之後留在這一頁只會看到一個已經不存在的物件 → 回「點進來的地方」
    goBack();
  } catch (e) {
    // 裝置被別的東西參照時後端會擋（例如還有 IP 掛在上面）→ 把原因照實顯示
    msg.error(apiErrMsg(e));
  } finally {
    deleting.value = false;
  }
}

const selected = ref<IPAddress | null>(null);
const modalShow = ref(false);

async function load(id: string) {
  loading.value = true;
  try {
    const [dev, addrs] = await Promise.all([
      apiClient.get<Device>(`/api/v1/devices/${id}`).then((r) => r.data),
      listAddresses({ deviceId: id, page: 1, pageSize: 1000 }),
    ]);
    device.value = dev;
    addresses.value = addrs.items;
    getDeviceRelations(id).then((c) => { relations.value = c; }).catch(() => { relations.value = []; });
    getDeviceVlans(id).then((v) => { vlans.value = v; }).catch(() => { vlans.value = []; });
    hasSnmpTarget.value = false;
    if (isAdmin.value) {
      getSnmpTargetForDevice(id).then((tg) => { hasSnmpTarget.value = !!tg; }).catch(() => { hasSnmpTarget.value = false; });
    }
    cardFocusDone = false;
    getDeviceLibrenms(id).then((l) => { lnms.value = l; focusRequestedCard(); }).catch(() => { lnms.value = null; });
    apiClient.get(`/api/v1/devices/${id}/integrations`).then((r) => {
      integrations.value = r.data;
      focusRequestedCard();   // 從 IP 頁「各來源最後出現」點 LibreNMS／Wazuh／OCS 的時間進來時捲到該卡片
    }).catch(() => { integrations.value = null; });

    const tasks: Promise<unknown>[] = [];
    if (dev.location_id) {
      tasks.push(
        listLocations().then((res) => {
          location.value = res.items.find((l) => l.id === dev.location_id) ?? null;
        }).catch(() => { location.value = null; }),
      );
    } else { location.value = null; }
    if (dev.rack_id) {
      tasks.push(
        listRacks().then((res) => {
          rack.value = res.items.find((r) => r.id === dev.rack_id) ?? null;
        }).catch(() => { rack.value = null; }),
      );
      tasks.push(
        getRackDiagram(dev.rack_id)
          .then((d) => { rackDiagram.value = d; })
          .catch(() => { rackDiagram.value = null; }),
      );
    } else { rack.value = null; rackDiagram.value = null; }
    await Promise.all(tasks);
  } catch {
    msg.error(t("errors.network"));
  } finally {
    loading.value = false;
  }
}

function typeColor(type: string): "success" | "info" | "warning" | "error" | "default" {
  return ({
    router: "info",
    switch: "success",
    firewall: "error",
    server: "default",
    storage: "warning",
    ap: "info",
    ipmi: "warning",
    patch_panel: "default",
    pdu: "warning",
    ups: "warning",
    workstation: "info",
    other: "default",
  } as Record<string, "success" | "info" | "warning" | "error" | "default">)[type] ?? "default";
}

function stateTag(state: string) {
  const map: Record<string, "success" | "warning" | "error" | "default" | "info"> = {
    active: "success", reserved: "info", offline: "error", dhcp: "warning", used: "default",
  };
  const key = `addresses.state_${state}`;
  const label = t(key) === key ? state : t(key);
  return h(NTag, { type: map[state] ?? "default", size: "small" }, () => label);
}

function lastSeen(r: IPAddress): string {
  // last_seen_dns 不算（AdGuard 設定裡有＝每輪都是現在，見 useLivenessSettings）
  const arr = [r.last_seen_scanner, r.last_seen_librenms,
    (r as { last_seen_arp?: string | null }).last_seen_arp].filter(Boolean) as string[];
  if (!arr.length) return "—";
  return fmtDateTime(arr.sort().reverse()[0]);   // 轉本地時區（原本直接顯示 UTC）
}

function liveDot(r: IPAddress) {
  return h(LiveStatusDot, { address: r });
}

const allIpColumns = computed<DataTableColumns<IPAddress>>(() => autoSort([
  { title: "", key: "live", width: 28, render: (r) => liveDot(r) },
  { title: t("addresses.ip"), key: "ip", width: 130 },
  // 固定寬度 + ellipsis：不再吃掉多餘寬度（短主機名稱不留大片空白，長的省略號）
  { title: t("addresses.hostname"), key: "hostname", width: 130,
    ellipsis: { tooltip: true }, render: (r) => r.hostname ?? "" },
  { title: t("common.status"), key: "state", width: 92, render: (r) => stateTag(r.state) },
  { title: t("addresses.mac"), key: "mac", width: 160,
    render: (r) => r.mac ? h("span", { style: "white-space:nowrap" }, r.mac) : "" },
  { title: t("cols.vendor"), key: "mac_vendor", width: 110,
    ellipsis: { tooltip: true }, render: (r) => r.mac_vendor ?? "—" },
  // 全部固定寬度：空欄不再被撐大；表格比卡片窄時，多餘空白留在右側而非塞進某一欄
  { title: t("addresses.switch_port"), key: "switch_port", width: 180,
    ellipsis: { tooltip: false },
    render: (r) => !r.switch_port ? ""
      : h(NTooltip, null, {
          trigger: () => h(SwitchPortLabel, { value: r.switch_port, dim: r.switch_port_confident === false }),
          default: () => r.switch_port_confident === false
            ? t("addresses.switch_port_uncertain") : r.switch_port }) },
  { title: t("common.description"), key: "description", width: 160,
    ellipsis: { tooltip: true }, render: (r) => r.description ?? "" },
  { title: t("addresses.last_seen"), key: "last_seen", width: 172,
    render: (r) => h("span", { style: "white-space:nowrap" }, [lastSeen(r)]) },
]));

const ipColumns = computed<DataTableColumns<IPAddress>>(() =>
  orderIpColumns(allIpColumns.value.filter((c: any) => ipVisibleKeys.value.includes(c.key))),
);

function openRow(row: IPAddress) {
  void router.push({ name: "address-detail", params: { id: row.id } });
}

function onSaved(updated: IPAddress) {
  selected.value = updated;
  const idx = addresses.value.findIndex((r) => r.id === updated.id);
  if (idx >= 0) addresses.value[idx] = updated;
}

function onDeleted(id: string) {
  addresses.value = addresses.value.filter((r) => r.id !== id);
}

watch(() => route.params.id, (id) => {
  if (typeof id === "string") void load(id);
});

onMounted(() => {
  const id = route.params.id;
  if (typeof id === "string") void load(id);
  void ensureCustomersLoaded();
});
</script>

<template>
  <n-spin :show="loading">
    <n-space vertical :size="16">
      <n-card v-if="device">
        <template #header>
          <n-space align="center" :wrap-item="false">
            <n-icon :size="22"><DevicesIcon /></n-icon>
            <span>{{ device.name }}</span>
            <n-tag :type="typeColor(device.type)" size="small">{{ t(`devices.type_${device.type}`) }}</n-tag>
            <!-- 虛實：由虛擬化整合對應（名稱／IP／MAC 對到 VM）。對不到不標——不知道≠實體機 -->
            <n-tooltip v-if="(device as any).virt_vm" trigger="hover">
              <template #trigger>
                <n-tag type="info" size="small">{{ virtTagText((device as any).virt_vm, t) }}</n-tag>
              </template>
              {{ (device as any).virt_vm.vm }} @ {{ (device as any).virt_vm.cluster || (device as any).virt_vm.platform }}
            </n-tooltip>
            <n-tag v-else-if="device.is_virtual" type="info" size="small">{{ t("devices.vm_tag") }}</n-tag>
            <!-- 這台是不是 DHCP 伺服器，是由它名下的 IP 帶的旗標決定的；
                 只在 IP 那層顯示的話，看裝置時完全不知道它扮演這個角色。 -->
            <n-tooltip v-if="dhcpServerIps.length" trigger="hover">
              <template #trigger>
                <n-tag type="warning" size="small" :bordered="false">
                  <template #icon><n-icon :component="DhcpServerIcon" /></template>
                  {{ t("addresses.role_dhcp_server") }}
                </n-tag>
              </template>
              {{ t("devices.dhcp_server_via", { ips: dhcpServerIps.join(", ") }) }}
            </n-tooltip>
          </n-space>
        </template>
<!-- 控制元件移到卡片內文最上方（標題列不放控制元件） -->
        <n-space align="center" justify="end" style="margin-bottom: 10px">
          <!-- 維護：這台與它名下的 IP 暫時不告警（2026-11-01） -->
          <MaintenanceQuickButton object-type="device" :object-id="device.id" :label="device.name" />
          <!-- 變更評估：除役（拆之前看還有誰引用它）；網路設備多「維護」、伺服器多「停機」（M2：誰跟著停、服務還剩不剩依賴）。
               功能開啟才顯示，後端會再檢查權限 -->
          <n-dropdown v-if="impact.settings.value.enabled && me?.can_edit !== false" trigger="click"
                      :options="impactMenu" @select="openImpact">
            <n-button size="small" data-testid="device-change-impact-btn">
              <template #icon><n-icon><ChangeImpactIcon /></n-icon></template>
              {{ t("change_impact.entry_assess") }}
            </n-button>
          </n-dropdown>
          <n-button type="primary" size="small" @click="editShow = true">
            <template #icon><n-icon><EditIcon /></n-icon></template>
            {{ t("common.edit") }}
          </n-button>
          <!-- 刪除放在詳細資料頁：從清單點進來看完之後要刪，不該再退回清單找那一列 -->
          <n-popconfirm @positive-click="removeDevice">
            <template #trigger>
              <n-button type="error" ghost size="small" :loading="deleting">
                <template #icon><n-icon><DeleteIcon /></n-icon></template>
                {{ t("common.delete") }}
              </n-button>
            </template>
            {{ t("common.confirm_delete") }}
          </n-popconfirm>
          <n-button @click="goBack()" size="small">
            <template #icon><n-icon><ArrowLeftIcon /></n-icon></template>
            {{ t("common.back") }}
          </n-button>
        </n-space>
        <div class="dev-head-row">
          <div class="dev-head-info">
        <n-descriptions bordered :column="3" size="small" label-placement="left">
          <n-descriptions-item :label="t('common.name')">{{ device.name }}</n-descriptions-item>
          <n-descriptions-item :label="t('common.type')">{{ t(`devices.type_${device.type}`) }}</n-descriptions-item>
          <n-descriptions-item :label="t('devices.vendor')">{{ device.vendor ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('devices.model')">{{ device.model ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('devices.serial')">{{ device.serial ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('nav.locations')">
            <a v-if="location" href="#" class="entity-link"
               @click.prevent="router.push({ name: 'locations' })">{{ location.name }}</a>
            <span v-else>—</span>
          </n-descriptions-item>
          <n-descriptions-item :label="t('nav.racks')">
            <a v-if="rack" href="#" class="entity-link"
               @click.prevent="openRackPage()">{{ rack.name }} ({{ rackUsesLevels
                 ? t("racks.rows_levels", { n: rack.u_height }) : rack.u_height + "U" }})</a>
            <span v-else>—</span>
          </n-descriptions-item>
          <n-descriptions-item :label="rackUsesLevels ? t('devices.level_position') : t('devices.u_position')">{{ device.u_position ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="rackUsesLevels ? t('devices.level_size') : t('devices.u_size')">{{ device.u_size ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('devices.rack_face')">
            {{ (device as any).rack_face === "rear" ? t("devices.rack_face_rear")
               : (device as any).rack_face === "front" ? t("devices.rack_face_front") : "—" }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('nav.customers')" :span="3">
            <a v-if="device.customer_id" href="#" class="entity-link"
               @click.prevent="router.push({ name: 'customers' })">
              {{ customerLabelFor(device.customer_id) }}
            </a>
            <span v-else>—</span>
          </n-descriptions-item>
          <n-descriptions-item :label="t('common.description')" :span="3">
            {{ device.description ?? "—" }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('common.created_at')">{{ fmtDateTime(device.created_at) }}</n-descriptions-item>
          <n-descriptions-item :label="t('common.updated_at')" :span="2">{{ fmtDateTime(device.updated_at) }}</n-descriptions-item>
        </n-descriptions>
          </div>
          <!-- 縮圖就只是縮圖：正面／背面、縮放、匯出都留在機櫃頁。點它就過去看完整的。 -->
          <div v-if="rackDiagram" class="dev-head-rack" :title="t('racks.open_in_racks')"
               @click="openRackPage">
            <RackDiagram :diagram="rackDiagram" :show-legend="false" :highlight-id="device.id"
                         :compact="true" :bare="true" :controls="false" />
          </div>
        </div>
      </n-card>

      <!-- 存活狀況：合併此裝置名下所有 IP 的狀態轉換 -->
      <n-card v-if="device" size="small">
        <UptimeBar :device-id="device.id" :days="90" />
      </n-card>

      <n-card v-if="device && relations.length > 1" :title="() => cardHead(TopologyIcon, t('relations.title'))" size="small">
        <relation-chain :nodes="relations" :current-id="device.id" />
      </n-card>

      <DeviceGroupCard v-if="device && device.group" :device-id="device.id" :group="device.group" />

      <DevicePortsPanel v-if="device" :device-id="device.id" :device-name="device.name" :admin="isAdmin" />

      <DevicePowerPortsPanel v-if="device" :device-id="device.id" :device-name="device.name" :admin="isAdmin" />
      <DeviceLeaseCard v-if="device" :device-id="device.id" :admin="isAdmin" />

      <n-card v-if="device" :title="() => cardHead(AddressesIcon, `${t('addresses.ip_list_title')} (${addresses.length})`)">
<!-- 控制元件移到卡片內文最上方（標題列不放控制元件） -->
        <n-space align="center" justify="end" style="margin-bottom: 10px">
          <n-button size="small" type="primary" @click="openLinkIp">
            <template #icon><n-icon><LinkIcon /></n-icon></template>
            {{ t("devices.link_ip") }}
          </n-button>
          <ColumnPicker size="small" :all="ipColumnPickerItems" :visible="ipVisibleKeys"
                        @update:visible="setIpVisible" @reset="resetIpVisible"
                        :order="ipColOrder" @update:order="setIpColOrder" />
          <n-button size="small" @click="load(device.id)" :loading="loading">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>
            {{ t("common.refresh") }}
          </n-button>
        </n-space>
        <n-data-table
          :columns="ipColumns"
          :data="addresses"
          :pagination="pg"
          :bordered="false"
          size="small"
          :scroll-x="1162"
          :row-props="(row: IPAddress) => ({
            style: 'cursor: pointer',
            onClick: () => openRow(row),
          })"
        >
          <template #empty>
            <n-space justify="center">{{ t("common.no_data") }}</n-space>
          </template>
        </n-data-table>
      </n-card>


      <!-- SNMP 收集（限管理員）：只有這台設了收集目標才顯示（狀態與編輯／測試／立即收集）；
           新增收集目標到「管理 → 外部系統整合 → SNMP」（使用者 2026-10-16）。編輯表單與 SNMP 頁是同一個元件 -->
      <SnmpDeviceCard v-if="device && isAdmin && hasSnmpTarget" id="card-snmp"
                      :class="{ 'card-focus': focusedCard === 'snmp' }"
                      :device-id="device.id" :device-name="device.name" :device-type="device.type"
                      @loaded="focusRequestedCard" />

      <n-card v-if="device && lnms" id="card-librenms"
              :class="{ 'card-focus': focusedCard === 'librenms' }"
              :title="() => cardHead(LibreNMSIcon, 'LibreNMS')">
        <template v-if="lnms.url" #header-extra>
          <n-button size="small" quaternary type="primary" @click="openExternal(lnms.url)">
            <template #icon><n-icon><OpenNewWindowIcon /></n-icon></template>
            {{ t("device_detail.open_in", { sys: "LibreNMS" }) }}
          </n-button>
        </template>
        <n-descriptions bordered :column="2" size="small" label-placement="left"
                        :label-style="{ whiteSpace: 'nowrap' }">
          <n-descriptions-item :label="t('cols.hostname')">{{ lnms.hostname ?? "—" }}</n-descriptions-item>
          <n-descriptions-item label="OS">{{ lnms.os ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('device_detail.hardware')">{{ lnms.hardware ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('cols.version')">{{ lnms.version ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('devices.serial')">{{ lnms.serial ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('common.status')">{{ lnmsStatusLabel(t, lnms.status) }}</n-descriptions-item>
          <n-descriptions-item :label="t('device_detail.primary_ip')">{{ lnms.primary_ip ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('scanAgentHelp.col_last_seen')">{{ fmtDateTime(lnms.last_seen_at) }}</n-descriptions-item>
        </n-descriptions>
      </n-card>

      <!-- Wazuh agent（依裝置 IP 比對）-->
      <n-card v-if="integrations && integrations.wazuh" id="card-wazuh"
              :class="{ 'card-focus': focusedCard === 'wazuh' }"
              :title="() => cardHead(WazuhIcon, 'Wazuh')" style="margin-top: 16px">
        <template v-if="integrations.wazuh.url" #header-extra>
          <n-button size="small" quaternary type="primary" @click="openExternal(integrations.wazuh.url)">
            <template #icon><n-icon><OpenNewWindowIcon /></n-icon></template>
            {{ t("device_detail.open_in", { sys: "Wazuh" }) }}
          </n-button>
        </template>
        <n-descriptions bordered :column="2" size="small" label-placement="left"
                        :label-style="{ whiteSpace: 'nowrap' }">
          <n-descriptions-item :label="t('device_detail.wz_agent')">{{ integrations.wazuh.name ?? "—" }} ({{ integrations.wazuh.agent_id }})</n-descriptions-item>
          <n-descriptions-item :label="t('common.status')">{{ wazuhStatusLabel(t, integrations.wazuh.status) }}</n-descriptions-item>
          <!-- 產品名稱優先（Windows 11 的核心版本號仍是 10.0，只看平台＋版本會被當成 Windows 10） -->
          <n-descriptions-item label="OS">{{ integrations.wazuh.os || [integrations.wazuh.os_platform, integrations.wazuh.os_version].filter(Boolean).join(" ") || "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('device_detail.wz_agent_version')">{{ integrations.wazuh.agent_version ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('device_detail.wz_group')">{{ integrations.wazuh.group ?? "—" }}</n-descriptions-item>
          <!-- 資安體質用 SCA（資安組態評估）呈現。
               漏洞數（CVE）拿不到：Wazuh 4.8 起 manager API 已無漏洞端點，唯一來源是
               Wazuh Indexer —— 接上去要一組能讀取整個 SIEM 事件的憑證，代價與收益不成
               比例，所以不接，也就不顯示一個永遠空白（或假裝是 0）的欄位。 -->
          <n-descriptions-item v-if="integrations.wazuh.sca_score != null" :label="t('device_detail.wz_sca')">
            <n-tooltip :delay="150">
              <template #trigger>
                <span>
                  <n-tag size="small" :type="scaType(integrations.wazuh.sca_score)" :bordered="false">
                    {{ integrations.wazuh.sca_score }}
                  </n-tag>
                  <span style="margin-left:6px">
                    {{ t("device_detail.wz_sca_counts", {
                      pass: integrations.wazuh.sca_pass ?? 0,
                      fail: integrations.wazuh.sca_fail ?? 0 }) }}
                  </span>
                </span>
              </template>
              <div style="max-width:320px;line-height:1.6">
                <div>{{ integrations.wazuh.sca_policy }}</div>
                <div v-if="(integrations.wazuh.sca_policy_count ?? 1) > 1">
                  {{ t("device_detail.wz_sca_worst", { n: integrations.wazuh.sca_policy_count }) }}
                </div>
                <div v-if="integrations.wazuh.sca_scanned_at">
                  {{ fmtDateTime(integrations.wazuh.sca_scanned_at) }}
                </div>
              </div>
            </n-tooltip>
          </n-descriptions-item>
          <n-descriptions-item :label="t('device_detail.wz_instance')">{{ integrations.wazuh.instance ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('scanAgentHelp.col_last_seen')">{{ fmtDateTime(integrations.wazuh.last_keep_alive) }}</n-descriptions-item>
        </n-descriptions>
      </n-card>

      <!-- Proxmox VM（依裝置 IP 比對）-->
      <n-card v-if="integrations && integrations.vm" :title="() => cardHead(VirtualizationIcon, t('nav.virtualization'))" style="margin-top: 16px">
        <template v-if="integrations.vm.url" #header-extra>
          <n-button size="small" quaternary type="primary" @click="openExternal(integrations.vm.url)">
            <template #icon><n-icon><OpenNewWindowIcon /></n-icon></template>
            {{ t("device_detail.open_in", { sys: "Proxmox" }) }}
          </n-button>
        </template>
        <n-descriptions bordered :column="2" size="small" label-placement="left"
                        :label-style="{ whiteSpace: 'nowrap' }">
          <n-descriptions-item :label="t('device_detail.vm_name')">
            {{ integrations.vm.name ?? "—" }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('cols.status')">
            {{ vmStatusLabel(integrations.vm.status) }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('device_detail.vm_node')">
            {{ integrations.vm.node ?? "—" }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('device_detail.vm_cluster')">
            {{ integrations.vm.cluster ?? "—" }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('device_detail.vm_vcpu')">
            {{ integrations.vm.vcpus ?? "—" }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('device_detail.vm_memory')">
            {{ integrations.vm.memory_mb ?? "—" }}
          </n-descriptions-item>
        </n-descriptions>
      </n-card>

      <!-- OCS Inventory（透過網卡 MAC 比對到既有 IP；OCS 補作業系統／序號／型號／廠牌／標籤／備註）-->
      <n-card v-if="integrations && integrations.ocs" id="card-ocs"
              :class="{ 'card-focus': focusedCard === 'ocs' }"
              :title="() => cardHead(DevicesIcon, 'OCS Inventory')" style="margin-top: 16px">
        <template v-if="integrations.ocs.url" #header-extra>
          <n-button size="small" quaternary type="primary" @click="openExternal(integrations.ocs.url)">
            <template #icon><n-icon><OpenNewWindowIcon /></n-icon></template>
            {{ t("device_detail.open_in", { sys: "OCS" }) }}
          </n-button>
        </template>
        <div class="int-hint">{{ t("device_detail.ocs_match_hint") }}</div>
        <n-descriptions bordered :column="2" size="small" label-placement="left"
                        :label-style="{ whiteSpace: 'nowrap' }">
          <n-descriptions-item label="OS">{{ integrations.ocs.os ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('device_detail.ocs_last_inventory')">{{ fmtDateTime(integrations.ocs.last_inventory) }}</n-descriptions-item>
          <n-descriptions-item :label="t('device_detail.ocs_tag')">{{ integrations.ocs.tag ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('device_detail.ocs_agent')">
            <span v-if="integrations.ocs.agent" :title="integrations.ocs.agent">{{ shortOcsAgent(integrations.ocs.agent) }}</span>
            <template v-else>—</template>
          </n-descriptions-item>
          <!-- 製造商／型號／序號是 OCS 自己回報的，不是裝置欄位（那可能是別的來源寫的） -->
          <n-descriptions-item :label="t('device_detail.ocs_vendor')">{{ integrations.ocs.vendor ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('device_detail.ocs_model')">{{ integrations.ocs.model ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('device_detail.ocs_serial')">
            <template v-if="integrations.ocs.serial">
              {{ integrations.ocs.serial }}<span v-if="integrations.ocs.serial_from_board" class="ocs-sub"
                :title="t('device_detail.ocs_serial_board_tip')">{{ t("device_detail.ocs_serial_board") }}</span>
            </template>
            <template v-else>—</template>
          </n-descriptions-item>
          <n-descriptions-item :label="t('device_detail.ocs_chassis')">{{ ocsHw?.system?.chassis ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('device_detail.ocs_board')">{{ ocsBoard || "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('device_detail.ocs_bios')">{{ ocsBios || "—" }}</n-descriptions-item>
        </n-descriptions>
        <template v-if="ocsHw">
          <div class="ocs-sec-h">{{ t("device_detail.ocs_components") }}</div>
          <n-descriptions bordered :column="1" size="small" label-placement="left"
                          :label-style="{ whiteSpace: 'nowrap', width: '1%' }" class="ocs-parts">
            <n-descriptions-item :label="t('device_detail.ocs_cpu')">
              <div v-for="(c, i) in ocsHw.cpus" :key="i">{{ cpuLine(c) }}</div>
              <template v-if="!ocsHw.cpus?.length">—</template>
            </n-descriptions-item>
            <n-descriptions-item :label="t('device_detail.ocs_memory')">
              <template v-if="ocsHw.memory?.total_mb || ocsHw.memory?.modules?.length">
                <!-- 實裝容量只在每條都知道大小時才算；否則只講作業系統回報的可用量，不冒充實裝 -->
                <div>
                  <span v-if="memInstalledMB">{{ fmtMemMB(memInstalledMB) }}</span>
                  <span v-if="memUsableShown" :class="{ 'ocs-sub': memInstalledMB }">
                    {{ t("device_detail.ocs_mem_usable", { v: fmtMemMB(ocsHw.memory.total_mb) }) }}</span>
                </div>
                <div v-if="ocsHw.memory.modules?.length" class="ocs-sub-line">
                  {{ ocsHw.memory.modules.map(memLine).join("、") }}<span v-if="memSizeUnknown"
                    :title="t('device_detail.ocs_mem_size_unknown_tip')">{{ t("device_detail.ocs_mem_size_unknown") }}</span>
                </div>
              </template>
              <template v-else>—</template>
            </n-descriptions-item>
            <n-descriptions-item :label="t('device_detail.ocs_disks')">
              <div v-for="(d, i) in ocsDisks" :key="i">{{ d.count > 1 ? `${d.count} × ${d.model}` : d.model }}
                <span class="ocs-sub">{{ fmtDiskMB(d.size_mb) }}</span></div>
              <template v-if="!ocsDisks.length">—</template>
            </n-descriptions-item>
            <n-descriptions-item :label="t('device_detail.ocs_gpus')">
              <div v-for="(g, i) in ocsHw.gpus" :key="i">{{ g.name }}<span v-if="g.memory_mb" class="ocs-sub">{{ fmtMemMB(g.memory_mb) }}</span></div>
              <template v-if="!ocsHw.gpus?.length">—</template>
            </n-descriptions-item>
          </n-descriptions>
        </template>
        <div v-else class="int-hint" style="margin-top: 8px">{{ t("device_detail.ocs_hw_pending") }}</div>
        <div v-if="integrations.ocs.notes && integrations.ocs.notes.length" class="ocs-notes">
          <div class="ocs-notes-h">{{ t("device_detail.ocs_notes") }}</div>
          <div v-for="(n, i) in integrations.ocs.notes" :key="i" class="ocs-note">
            <span class="ocs-note-date">{{ n.date }}</span>
            <span class="ocs-note-user">{{ n.user }}</span>
            <span class="ocs-note-text">{{ n.comment }}</span>
          </div>
        </div>
      </n-card>

      <!-- RustDesk：客戶端裝在這台機器上（跟 Wazuh／OCS 代理同一類）。連線按鈕在 IP 頁（逐 IP 開關＋權限），這裡連回那個 IP -->
      <n-card v-if="integrations && integrations.rustdesk" id="card-rustdesk"
              :class="{ 'card-focus': focusedCard === 'rustdesk' }"
              :title="() => cardHead(RustDeskIcon, 'RustDesk')" style="margin-top: 16px" data-testid="device-rustdesk-card">
        <template #header-extra>
          <n-button size="small" quaternary type="primary"
                    @click="router.push({ name: 'address-detail', params: { id: integrations.rustdesk.address_id } })">
            <template #icon><n-icon><AddressesIcon /></n-icon></template>
            {{ integrations.rustdesk.ip }}
          </n-button>
        </template>
        <n-alert v-if="integrations.rustdesk.key_problem" type="error" :show-icon="true" style="margin-bottom: 8px"
                 data-testid="device-rustdesk-key-problem">
          {{ t(integrations.rustdesk.key_problem.scope === "hbbs" ? "rustdesk.key_problem_hint_hbbs" : "rustdesk.key_problem_hint",
               { at: fmtDateTime(integrations.rustdesk.key_problem.at), n: integrations.rustdesk.key_problem.count }) }}
          {{ t("rustdesk.key_problem_fix") }}
        </n-alert>
        <n-descriptions bordered :column="2" size="small" label-placement="left"
                        :label-style="{ whiteSpace: 'nowrap' }">
          <n-descriptions-item :label="t('rustdesk.id')">
            <span style="font-family: var(--jt-mono, monospace)">{{ integrations.rustdesk.id }}</span>
            <CopyButton :text="integrations.rustdesk.id" />
          </n-descriptions-item>
          <n-descriptions-item :label="t('rustdesk.server_pick')">{{ integrations.rustdesk.server_name ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('rustdesk.col_username')">{{ integrations.rustdesk.username ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('rustdesk.col_os')">
            {{ integrations.rustdesk.os ? rustdeskOs(integrations.rustdesk.os)[0] : "—" }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('rustdesk.col_client_version')">{{ integrations.rustdesk.version ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('rustdesk.col_last_heartbeat')">
            {{ fmtDateTime(integrations.rustdesk.last_heartbeat_at || integrations.rustdesk.last_online_at) }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('rustdesk.col_evidence')" :span="2">
            <n-space v-if="integrations.rustdesk.evidence?.length" :size="4">
              <n-tag v-for="e in integrations.rustdesk.evidence" :key="e" size="tiny" type="success" :bordered="false">
                {{ t(`rustdesk.ev_${e}`) }}
              </n-tag>
            </n-space>
            <template v-else>—</template>
          </n-descriptions-item>
        </n-descriptions>
      </n-card>

      <n-card v-if="device && vlans.length" :title="() => cardHead(SubnetsIcon, `VLAN (${vlans.length})`)">
        <n-space :size="8" style="flex-wrap: wrap">
          <n-tag v-for="v in vlans" :key="v.vlan_id" type="info" :bordered="false" size="small">
            {{ v.number }} · {{ v.name }}
            <span style="opacity: .55; margin-left: 4px; font-size: 11px">{{ v.source }}</span>
          </n-tag>
        </n-space>
      </n-card>
    </n-space>
  </n-spin>

  <n-modal v-model:show="showLinkIp" preset="card" :title="t('devices.link_ip')" style="width: 480px">
    <n-space vertical :size="12">
      <span style="font-size: 13px; opacity: .75">{{ t("devices.link_ip_hint") }}</span>
      <n-select v-model:value="linkIpId" :options="linkOptions" filterable clearable
                remote :loading="linkLoading" @search="onLinkSearch"
                :placeholder="t('devices.link_ip_ph')" />
      <n-space justify="end">
        <n-button @click="showLinkIp = false">{{ t("common.cancel") }}</n-button>
        <n-button type="success" :loading="linking" :disabled="!linkIpId" @click="doLinkIp">
          {{ t("common.ok") }}
        </n-button>
      </n-space>
    </n-space>
  </n-modal>
  <IPAddressEditModal
    v-model:show="modalShow"
    :address="selected"
    @saved="onSaved"
    @deleted="onDeleted"
  />
  <DeviceEditModal v-model:show="editShow" :device="device"
                   @saved="() => load(String(route.params.id))" />
  <ChangeImpactWizard v-if="device && impact.settings.value.enabled" v-model:show="impactWizard"
                      :preset-scenario="impactScenario" :preset-target-id="device.id" :preset-label="device.name" />
</template>

<style scoped>
/* 從別頁帶 ?card= 進來時短暫highlight，讓使用者知道落點在哪 */
/* ?card= 捲到的卡片：頂端留出固定標題列的高度 */
[id^="card-"] { scroll-margin-top: 72px; }
.card-focus {
  box-shadow: 0 0 0 2px var(--n-primary-color, #18a058);
  transition: box-shadow .35s ease;
}
.int-hint {
  font-size: 12px;
  opacity: .7;
  margin-bottom: 10px;
}
.ocs-sub { opacity: .65; margin-left: 8px; }
.ocs-sub-line { opacity: .65; font-size: 12px; }
.ocs-sec-h { font-weight: 600; margin: 14px 0 6px; font-size: 13px; }
.ocs-notes {
  margin-top: 12px;
}
.ocs-notes-h {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 6px;
}
.ocs-note {
  display: flex;
  gap: 10px;
  font-size: 13px;
  padding: 3px 0;
  border-top: 1px solid var(--n-border-color, rgba(128, 128, 128, .15));
}
.ocs-note-date { opacity: .6; white-space: nowrap; }
.ocs-note-user { opacity: .8; white-space: nowrap; font-weight: 500; }
.ocs-note-text { flex: 1; }
.entity-link {
  color: var(--primary-color, #18a058);
  text-decoration: none;
  cursor: pointer;
}
.entity-link:hover { text-decoration: underline; }
/* 第一張卡片：左資訊 + 右側機櫃圖（標示本機位置） */
.dev-head-row { display: flex; gap: 16px; align-items: flex-start; flex-wrap: wrap; }
/* 寬表(IP 清單)用內部水平捲動吸收寬度，卡片不被撐爆溢出 */
:deep(.n-card) { min-width: 0; }
:deep(.n-data-table) { max-width: 100%; }
.dev-head-info { flex: 1 1 420px; min-width: 0; }
.dev-head-rack { flex: 0 0 auto; max-width: 320px; cursor: pointer; }
@media (max-width: 900px) { .dev-head-rack { max-width: 100%; flex: 1 1 100%; } }
</style>
