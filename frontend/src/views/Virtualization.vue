<script setup lang="ts">
import { useAuthStore } from "@/stores/auth";
const _authBtn = useAuthStore();
import { computed, h, onMounted, reactive, ref, watch, type ComputedRef, type Ref } from "vue";
import { fmtDateTime } from "@/utils/datetime";
import { useI18n } from "vue-i18n";
import { trackTask } from "@/composables/useTaskTracker";
import ScopeOverlapWarning from "@/components/ScopeOverlapWarning.vue";
import {
  NCard, NTabs, NTabPane, NDataTable, NSpace, NIcon, NButton, NTag, NTooltip,
  NModal, NForm, NFormItem, NInput, NSelect, NSwitch, NInputNumber, NPopconfirm, NAlert,
  useMessage, type DataTableColumns,
} from "naive-ui";
import {
  VirtualizationIcon, RefreshIcon, SyncIcon, PlusIcon, TestIcon, DeleteIcon, FirewallIcon,
  EditIcon, CloneIcon, AdvancedIcon, DevicesIcon,
} from "@/icons";
import { Virt, type ProxmoxInstance } from "@/api/phase3";
import { listSubnets } from "@/api/subnets";
import { autoSort } from "@/composables/useTableSort";
import { useCustomers } from "@/composables/useCustomers";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { useTableQuickFilter } from "@/composables/useTableQuickFilter";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import { useRoute, useRouter } from "vue-router";
import { useTablePagination } from "@/composables/useTablePagination";
import { apiClient, apiErrMsg } from "@/api/client";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const pg = useTablePagination();
// 管理區（virt_admin）：只放 Proxmox 連線；功能/進階區（virt）：叢集 + VM
const adminMode = computed(() => route.name === "virt_admin");
const { options: customerOptions, ensureLoaded: ensureCustomerOptsLoaded } = useCustomers();

// VM 狀態翻譯（running/stopped/paused/suspended…）；沒對到就原樣顯示
function vmStatusLabel(s: string | null | undefined): string {
  if (!s) return "—";
  const key = `virt.vm_status.${s}`;
  const tr = t(key);
  return tr === key ? s : tr;
}
const msg = useMessage();
// ⚠️ 初始值要在 setup 就決定好，**不能等到 onMounted 才設**：管理模式只渲染 proxmox
// 那個分頁，第一次渲染時 value 若還是 "clusters" 就對不到任何分頁，naive-ui 會畫出
// 一個空的內容區，要再點一下分頁才會出現（使用者回報「有時點進來下面是空的」）。
const tab = ref<"clusters" | "vms" | "pvefw" | "proxmox">(
  route.name === "virt_admin" ? "proxmox" : "clusters");

/** 這一頁顯示哪個平台。虛擬化拆成「PVE」與「VMware」兩個選單項，同一個元件、不同 props。
 *  不給就顯示全部（管理區的 virt-admin 走這條）。 */
const props = defineProps<{ platform?: "proxmox" | "vmware" }>();

const allClusters = ref<any[]>([]);
const allVms = ref<any[]>([]);
const proxmox = ref<any[]>([]);
const loading = ref(false);

// 依平台篩：叢集看 type，VM 跟著自己的叢集走
const clusters = computed(() => (props.platform
  ? allClusters.value.filter((c) => c.type === props.platform)
  : allClusters.value));
const vms = computed(() => {
  if (!props.platform) return allVms.value;
  const ids = new Set(clusters.value.map((c) => c.id));
  return allVms.value.filter((v) => ids.has(v.cluster_id));
});

// 另一個平台有幾台 VM —— 用來把「空白頁」變成「你要的東西在另一頁」
const otherPlatformCount = computed(() => {
  if (!props.platform) return 0;
  const otherIds = new Set(allClusters.value
    .filter((c) => c.type !== props.platform).map((c) => c.id));
  return allVms.value.filter((v) => otherIds.has(v.cluster_id)).length;
});

function goOtherPlatform() {
  void router.push({ name: props.platform === "vmware" ? "virt" : "virt_vmware" });
}

// ── PVE 防火牆（東西向分段）──
// 重點不是「有幾條規則」，而是**那些規則到底生不生效**：要三個開關都開
// （叢集 / guest / 每張網卡的 firewall=1），生效後再看預設政策 policy_in。
// 這些判定後端已經收斂成 posture，前端只顯示、不重算。
interface FwState {
  instance_id: string; cluster: string | null;
  vmid: number; guest_kind: string | null; node: string | null;
  effective: boolean; posture: string;
  cluster_enabled: boolean; guest_enabled: boolean;
  nic_firewall: Record<string, boolean> | null;
  cluster_policy_in: string | null; guest_policy_in: string | null;
  guest_policy_in_explicit: boolean; guest_enabled_explicit: boolean;
}
interface FwRule {
  instance_id: string; cluster: string | null;
  scope: string; node: string | null; vmid: number | null; pos: number;
  direction: string | null; action: string | null; enabled: boolean;
  proto: string | null; dport: string | null; source: string | null;
  dest: string | null; macro: string | null; group_ref: string | null;
  comment: string | null;
}
const fwStates = ref<FwState[]>([]);
const fwRules = ref<FwRule[]>([]);
const fwGroups = ref<{ name: string; comment: string | null; rules: FwRule[] }[]>([]);
const fwIpsets = ref<{ scope: string; vmid: number | null; kind: string;
                       name: string; members: string[] }[]>([]);
const fwCounts = ref<Record<string, number>>({});
const fwPosture = ref<string | null>(null);
const fwLoading = ref(false);

const POSTURE_TYPE: Record<string, "error" | "warning" | "success" | "info"> = {
  unprotected: "error", open: "warning", blocked: "info", filtered: "success",
};
const postureType = (k: string) => POSTURE_TYPE[k] ?? "default";
// 固定順序呈現（依風險由高到低），否則每次載入順序會跟著物件鍵值變動
const POSTURE_ORDER = ["unprotected", "open", "blocked", "filtered"] as const;
const postureLabel = (k: string) => t(`virt.posture_${k}`);

async function loadFw() {
  fwLoading.value = true;
  try {
    const { data } = await apiClient.get("/api/v1/virt/pve-firewall");
    fwStates.value = data.states ?? [];
    fwCounts.value = data.posture_counts ?? {};
    fwRules.value = data.rules ?? [];
    fwGroups.value = data.groups ?? [];
    fwIpsets.value = data.ipsets ?? [];
  } catch { /* silent */ } finally { fwLoading.value = false; }
}

// 「這台到底設了哪些規則」——只給判定結果不給規則，等於只講結論不給證據。
// guest 自己的規則之外，叢集與節點層的規則也會作用在它身上，所以一併列出並標明來源層。
/** 某台 guest 適用的規則＝它自己的 ＋ 同一座叢集的上層（節點／資料中心）規則。
 *  ⚠️ 一定要連 `instance_id` 一起比：VMID 只在單一叢集內唯一，多叢集時
 *  只比 vmid 會把另一座叢集同號 guest 的規則算進來（規則數與展開內容都會錯）。 */
function rulesForGuest(vmid: number, instanceId?: string): FwRule[] {
  const sameCluster = (r: FwRule) => !instanceId || r.instance_id === instanceId;
  const own = fwRules.value.filter(
    (r) => r.scope === "guest" && r.vmid === vmid && sameCluster(r));
  const inherited = fwRules.value.filter((r) => r.scope !== "guest" && sameCluster(r));
  return [...own, ...inherited];
}
const SCOPE_LABEL: Record<string, string> = {
  guest: "guest", node: "node", datacenter: "datacenter",
};
/** 規則裡引用到的名稱（安全群組／IPSet／alias）→ 展開後的內容，給 popover 用 */
function refDetail(r: FwRule): string | null {
  if (r.group_ref) {
    const g = fwGroups.value.find((x) => x.name === r.group_ref);
    if (!g) return null;
    return (g.rules ?? []).map((x) =>
      `${x.direction ?? "?"} ${x.action ?? "?"} ${x.proto ?? ""}${x.dport ? "/" + x.dport : ""}`
        .trim()).join("\n") || t("virt.empty_group");
  }
  const name = r.source || r.dest;
  if (!name) return null;
  // guest 層同名會遮蔽叢集層，查找順序要一致
  const set = fwIpsets.value.find((x) => x.name === name && x.scope === "guest"
                                         && x.vmid === r.vmid)
    ?? fwIpsets.value.find((x) => x.name === name && x.scope === "datacenter");
  return set ? (set.members ?? []).join("\n") || t("virt.empty_set") : null;
}

const postureOptions = computed(() => POSTURE_ORDER.map((k) => ({
  label: `${postureLabel(k)}（${fwCounts.value[k] ?? 0}）`, value: k })));
const fwRows = computed(() =>
  fwPosture.value ? fwStates.value.filter((s) => s.posture === fwPosture.value)
                  : fwStates.value);

const fwCols = computed<DataTableColumns<FwState>>(() => autoSort([
  // key "_" 是 useVirtPrefs 保留鍵：不進欄位選擇清單，但一定保留（展開欄不該被藏掉）
  { type: "expand", key: "_", expandable: () => true,
    renderExpand: (r: FwState) => h("div", { class: "fw-rules" },
      rulesForGuest(r.vmid, r.instance_id).length === 0
        ? [h("span", { style: "opacity:.6;font-size:12.5px" }, t("virt.no_rules"))]
        : rulesForGuest(r.vmid, r.instance_id).map((x) => h("div", { class: "fw-rule" }, [
            h(NTag, { size: "tiny", bordered: false,
                      type: x.scope === "guest" ? "success" : "default" },
              { default: () => SCOPE_LABEL[x.scope] ?? x.scope }),
            h("span", { class: "fw-rule__body" }, [
              `${x.direction ?? "?"} · ${x.action ?? "?"}`,
              x.proto ? ` · ${x.proto}` : "",
              x.dport ? `/${x.dport}` : "",
              x.source ? ` · ${t("virt.from")} ${x.source}` : "",
              x.dest ? ` · ${t("virt.to")} ${x.dest}` : "",
              x.macro ? ` · ${x.macro}` : "",
            ]),
            // 引用到群組／IPSet 時，內容用 popover 展開（不展開等於沒說）
            refDetail(x) ? h(NTooltip, null, {
              trigger: () => h(NTag, { size: "tiny", type: "info", bordered: false,
                                       style: "cursor:pointer" },
                               { default: () => t("virt.expand_ref") }),
              default: () => h("pre", { style: "margin:0;font-size:12px" },
                                refDetail(x) ?? ""),
            }) : null,
            x.enabled ? null : h(NTag, { size: "tiny", type: "warning", bordered: false },
                                 { default: () => t("virt.rule_disabled") }),
          ]))) } as any,
  { title: () => t("virt.cluster_col"), key: "cluster", width: 150,
    ellipsis: { tooltip: true },
    render: (r: FwState) => r.cluster ?? "—" },
  { title: "VMID", key: "vmid", width: 90 },
  { title: () => t("virt.rule_count"), key: "rule_count", width: 100,
    render: (r: FwState) => rulesForGuest(r.vmid, r.instance_id)
      .filter((x) => x.scope === "guest").length },
  { title: t("virt.kind"), key: "guest_kind", width: 90,
    render: (r: FwState) => r.guest_kind ?? "—" },
  { title: t("virt.node"), key: "node", width: 140, render: (r: FwState) => r.node ?? "—" },
  { title: t("virt.posture"), key: "posture", width: 150,
    render: (r: FwState) => h(NTag, { size: "small", type: postureType(r.posture),
                                      bordered: false },
                              { default: () => postureLabel(r.posture) }) },
  // 為什麼是這個防護狀態：把三個開關攤開，使用者才知道要去哪一層開
  { title: t("virt.why"), key: "why", minWidth: 320,
    render: (r: FwState) => h("span", { style: "font-size:12.5px" }, [
      `${t("virt.cluster")}: ${r.cluster_enabled ? "on" : "off"}`,
      ` · ${t("virt.guest")}: ${r.guest_enabled ? "on" : "off"}`,
      r.guest_enabled_explicit ? "" : `（${t("virt.inherited")}）`,
      ` · NIC: ${Object.entries(r.nic_firewall ?? {})
        .map(([k, v]) => `${k}=${v ? "on" : "off"}`).join(" ") || "—"}`,
      ` · policy_in: ${r.guest_policy_in ?? "—"}`,
      r.guest_policy_in_explicit ? "" : `（${t("virt.inherited")}）`,
    ]) },
]));

async function refresh() {
  loading.value = true;
  try {
    [allClusters.value, allVms.value, proxmox.value]
      = await Promise.all([Virt.clusters(), Virt.vms(), Virt.proxmox()]);
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}
async function syncProxmox(id: string) {
  try {
    const r = await Virt.syncProxmox(id);
    trackTask(r.task_id, { onDone: () => void refresh() });
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function testProxmox(id: string) {
  try { await Virt.testProxmox(id); msg.success(t("virt.test_ok")); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function delProxmox(id: string) {
  try { await Virt.deleteProxmox(id); msg.success(t("common.ok")); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function delCluster(id: string) {
  try { await Virt.deleteCluster(id); msg.success(t("common.ok")); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

// ── 新增 / 編輯叢集（含所屬單位）──
const showCluster = ref(false);
const editingClusterId = ref<string | null>(null);
const clusterForm = ref({ name: "", description: "", customer_id: null as string | null });
function openClusterCreate() {
  editingClusterId.value = null;
  clusterForm.value = { name: "", description: "", customer_id: null };
  showCluster.value = true;
}
function openClusterEdit(r: any) {
  editingClusterId.value = r.id;
  clusterForm.value = { name: r.name, description: r.description ?? "", customer_id: r.customer_id ?? null };
  showCluster.value = true;
}
async function submitCluster() {
  if (!clusterForm.value.name.trim()) { msg.error(t("virt.err_cluster_name")); return; }
  try {
    if (editingClusterId.value) {
      await Virt.updateCluster(editingClusterId.value, {
        name: clusterForm.value.name.trim(),
        description: clusterForm.value.description || undefined,
        customer_id: clusterForm.value.customer_id ?? null,
      });
    } else {
      await Virt.createCluster({ name: clusterForm.value.name.trim(), type: "proxmox",
        description: clusterForm.value.description || undefined,
        customer_id: clusterForm.value.customer_id ?? null });
    }
    showCluster.value = false;
    editingClusterId.value = null;
    clusterForm.value = { name: "", description: "", customer_id: null };
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

// ── 新增 / 編輯 / 複製 Proxmox 連線 ──
const showPx = ref(false);
const editingPxId = ref<string | null>(null);  // null = 新增/複製；有值 = 編輯
function emptyPxForm() {
  return {
    cluster_id: null as string | null,   // 留空 → 同步時依 PVE 叢集名稱自動建立
    api_url: "https://", extra_urls: "",
    auth_username: "root@pam", auth_token_id: "", token_secret: "",
    verify_tls: false, enabled: true, sync_interval_seconds: 600,
    scope_subnet_ids: [] as string[],
    auto_create_ips: false,
  };
}
const pxForm = ref(emptyPxForm());
const clusterOptions = computed(() => clusters.value.map((c) => ({ label: c.name, value: c.id })));

const subnetOptions = ref<{ label: string; value: string }[]>([]);
async function loadSubnetOptions() {
  try {
    const r = await listSubnets({ page: 1, pageSize: 500 });
    subnetOptions.value = r.items.map((s) => ({
      label: s.description ? `${s.cidr} — ${s.description}` : s.cidr, value: s.id }));
  } catch { /* silent */ }
}

function openPxCreate() {
  editingPxId.value = null;
  pxForm.value = emptyPxForm();
  showPx.value = true;
}
function fillFromRow(r: ProxmoxInstance) {
  pxForm.value = {
    cluster_id: (r as any).cluster_id ?? clusters.value[0]?.id ?? null,
    api_url: r.api_url,
    extra_urls: (r.extra_api_urls ?? []).join("\n"),
    auth_username: r.auth_username,
    auth_token_id: r.auth_token_id,
    token_secret: "",
    verify_tls: r.verify_tls,
    enabled: r.enabled,
    sync_interval_seconds: r.sync_interval_seconds,
    scope_subnet_ids: r.scope_subnet_ids ?? [],
    auto_create_ips: !!(r as any).auto_create_ips,
  };
}
function openPxEdit(r: ProxmoxInstance) {
  editingPxId.value = r.id;
  fillFromRow(r);
  showPx.value = true;
}
function openPxClone(r: ProxmoxInstance) {
  editingPxId.value = null;       // 當新增處理
  fillFromRow(r);
  pxForm.value.api_url = "https://";   // 換手新節點 → 清空主 URL 讓使用者填
  showPx.value = true;
}

const pxModalTitle = computed(() =>
  editingPxId.value ? t("virt.edit_proxmox") : t("virt.add_proxmox"));

async function submitPx() {
  const f = pxForm.value;
  if (!editingPxId.value && f.token_secret.length < 8) { msg.error(t("virt.err_token_secret")); return; }
  const extra = f.extra_urls.split(/[\n,]/).map((s) => s.trim()).filter(Boolean);
  try {
    if (editingPxId.value) {
      const payload: any = {
        api_url: f.api_url, extra_api_urls: extra,
        auth_username: f.auth_username, auth_token_id: f.auth_token_id,
        verify_tls: f.verify_tls, enabled: f.enabled,
        sync_interval_seconds: f.sync_interval_seconds,
        scope_subnet_ids: f.scope_subnet_ids,
        auto_create_ips: f.auto_create_ips,
      };
      if (f.token_secret) payload.token_secret = f.token_secret;  // 留空＝不變
      await Virt.updateProxmox(editingPxId.value, payload);
    } else {
      await Virt.createProxmox({
        cluster_id: f.cluster_id ?? undefined, api_url: f.api_url, extra_api_urls: extra,
        auth_username: f.auth_username, auth_token_id: f.auth_token_id,
        token_secret: f.token_secret, verify_tls: f.verify_tls,
        enabled: f.enabled, sync_interval_seconds: f.sync_interval_seconds,
        scope_subnet_ids: f.scope_subnet_ids,
        auto_create_ips: f.auto_create_ips,
      });
    }
    showPx.value = false;
    msg.success(t("common.ok"));
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

// 圖示 + tooltip 動作鈕（避免操作欄換行；窄螢幕也只顯示 icon）
function iconAction(icon: any, label: string, onClick: () => void, type?: any) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type, onClick },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}

const clusterCols = computed<DataTableColumns<any>>(() => autoSort([
  { title: t("common.name"), key: "name" },
  { title: t("virt.type"), key: "type" },
  { title: t("cols.unit"), key: "customer_name", width: 160, ellipsis: { tooltip: true },
    render: (r) => r.customer_name ?? "—" },
  {
    title: t("virt.cluster_mode"), key: "is_standalone", width: 120,
    render: (r) => h(NTag, { size: "small", type: r.is_standalone ? "warning" : "success" },
      () => r.is_standalone ? t("virt.standalone") : t("virt.clustered")),
  },
  { title: t("sections.description"), key: "description" },
  {
    title: t("common.actions"), key: "actions", width: 100, className: "col-actions",
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openClusterEdit(r)),
      h(NPopconfirm, { onPositiveClick: () => delCluster(r.id) }, {
        trigger: () => h(NTooltip, null, {
          trigger: () => h(NButton, { size: "small", quaternary: true, type: "error" },
            { icon: () => h(NIcon, null, () => h(DeleteIcon)) }),
          default: () => t("common.delete"),
        }),
        default: () => t("virt.cluster_delete_confirm"),
      }),
    ]),
  },
]));
// 每個 NIC 一行（IP / bridge / MAC 三欄同 index 對齊）— 多 IP 一看就知道對應關係
function stackedCell(arr?: string[] | null, links?: Record<string, string> | null) {
  if (!arr || !arr.length) return "—";
  return h("div", { class: "nic-stack" }, arr.map((v) => {
    const id = links?.[v];
    return h("div", { class: "nic-line" }, id
      ? h("a", {
          class: "nic-link",
          onClick: () => router.push({ name: "address-detail", params: { id } }),
        }, v)
      : v);
  }));
}
const vmCols = computed<DataTableColumns<any>>(() => autoSort([
  { title: t("common.name"), key: "name" },
  {
    title: t("virt.kind"), key: "kind", width: 70,
    render: (r) => h(NTag, { size: "small", type: r.kind === "ct" ? "warning" : "info" },
      () => r.kind === "ct" ? "CT" : "VM"),
  },
  // VMID（Proxmox 的 VM/CT 編號）；預設不顯示，可在「欄位」勾選
  { title: "VMID", key: "legacy_vmid", width: 90, render: (r) => r.legacy_vmid ?? "—" },
  {
    title: t("virt.cluster"), key: "cluster_id",
    render: (r) => clusters.value.find((c) => c.id === r.cluster_id)?.name ?? "—",
  },
  { title: t("virt.node"), key: "node", render: (r) => r.node ?? "—" },
  {
    title: "IP", key: "ips", minWidth: 150,
    // 在 IPAM 裡找得到的位址就做成連結 —— 資料早就在系統裡，不該要人複製那串數字、
    // 切到 IP 位址頁再貼上搜尋。找不到、或重疊網段下分不出是哪一筆時維持純文字
    // （後端不給 id）：給錯的連結比沒有連結更糟，因為使用者會信它。
    render: (r) => stackedCell(r.ips, r.ip_links),
  },
  {
    title: t("virt.bridge"), key: "bridges", minWidth: 100,
    render: (r) => stackedCell(r.bridges),
  },
  {
    title: "MAC", key: "macs", minWidth: 160,
    render: (r) => stackedCell(r.macs),
  },
  {
    title: t("common.status"), key: "status",
    render: (r) => h(NTag, {
      size: "small",
      type: r.status === "running" ? "success" : r.status === "stopped" ? "default" : "warning",
    }, () => vmStatusLabel(r.status)),
  },
]));
const proxmoxCols = computed<DataTableColumns<ProxmoxInstance>>(() => autoSort([
  {
    title: "API URL", key: "api_url",
    render: (r) => h(NSpace, { size: 6, align: "center", wrapItem: false }, () => [
      h("span", null, r.api_url),
      r.extra_api_urls && r.extra_api_urls.length
        ? h(NTooltip, null, {
            trigger: () => h(NTag, { size: "small", type: "info", bordered: false },
              () => `+${r.extra_api_urls.length}`),
            default: () => r.extra_api_urls.join("\n"),
          })
        : null,
    ]),
  },
  {
    title: t("common.status"), key: "enabled",
    render: (r) => h(NTag, { size: "small", type: r.enabled ? "success" : "default" },
      () => r.enabled ? t("common.enabled") : t("common.disabled")),
  },
  { title: t("virt.last_sync"), key: "last_sync_at", render: (r) => fmtDateTime(r.last_sync_at) },
  { title: t("wazuh_admin.col_last_error"), key: "last_error", render: (r) => r.last_error ?? "—" },
  {
    title: t("common.actions"), key: "_", width: 184, className: "col-actions",
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(TestIcon, t("common.test"), () => testProxmox(r.id)),
      iconAction(SyncIcon, t("common.pull"), () => syncProxmox(r.id), "primary"),
      iconAction(EditIcon, t("common.edit"), () => openPxEdit(r)),
      iconAction(CloneIcon, t("virt.clone"), () => openPxClone(r)),
      h(NPopconfirm, { onPositiveClick: () => delProxmox(r.id) }, {
        trigger: () => h(NTooltip, null, {
          trigger: () => h(NButton, { size: "small", quaternary: true, type: "error" },
            { icon: () => h(NIcon, null, () => h(DeleteIcon)) }),
          default: () => t("common.delete"),
        }),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]));

// 每張表的欄位顯示偏好 + 即時篩選。操作欄(key="actions"/"_")永遠保留。
// rows 收 Ref 或 ComputedRef 都要能用：clusters/vms 依平台篩選後變成 computed，
// proxmox 仍是一般的 ref
function useVirtPrefs(name: string, cols: typeof clusterCols,
                      rows: Ref<any[]> | ComputedRef<any[]>, defaultHidden: string[] = []) {
  const allKeys = cols.value
    .filter((c: any) => c.key && c.key !== "actions" && c.key !== "_")
    .map((c: any) => String(c.key));
  const defaults = allKeys.filter((k: string) => !defaultHidden.includes(k));
  // 第二個參數是「全部選用欄位」、第三個是「預設可見」（原本兩者寫反：預設隱藏的欄位反而一開始就顯示、
  // 而且一旦存過偏好就再也勾不回來，拖拉順序時也排不到它）
  const { visibleKeys, setVisible, reset, order, setOrder, orderColumns } =
    useColumnPrefs(`virt_${name}`, allKeys, defaults);
  const items = computed(() => cols.value
    .filter((c: any) => c.key && c.key !== "actions" && c.key !== "_")
    .map((c: any) => ({ key: String(c.key), label: typeof c.title === "string" ? c.title : String(c.key) })));
  const visibleCols = computed<DataTableColumns<any>>(() =>
    orderColumns(cols.value.filter((c: any) => c.key === "actions" || c.key === "_" || visibleKeys.value.includes(String(c.key)))));
  // 只比對「目前顯示的欄位」，避免查數字（如 102）誤中 memory_mb / disk_gb 等內部欄位
  const { query, filtered } = useTableQuickFilter(rows, () => visibleKeys.value);
  return reactive({ visibleKeys, setVisible, reset, order, setOrder, items, visibleCols, query, filtered });
}
const clusterP = useVirtPrefs("clusters", clusterCols, clusters);
const vmP = useVirtPrefs("vms", vmCols, vms, ["legacy_vmid"]);
const proxmoxP = useVirtPrefs("proxmox", proxmoxCols, proxmox);
// 防火牆分頁比照其他分頁：共用搜尋 / 欄位選擇 / 排序 / 匯出，不另做一套
const fwP = useVirtPrefs("pvefw", fwCols as any, fwRows);

// 兩個選單項共用這個元件 → 在它們之間切換時**不會重新掛載**，只有路由變。
// 沒有這個 watch 的話，分頁值會停在上一個模式、同樣對不到任何分頁。
watch(adminMode, (isAdmin) => { tab.value = isAdmin ? "proxmox" : "clusters"; });

onMounted(() => {
  void loadFw();
  void refresh();
  void ensureCustomerOptsLoaded();
  void loadSubnetOptions();
});
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><VirtualizationIcon /></n-icon>
        <!-- 兩個選單項共用這個元件；標題不寫平台的話，使用者根本分不出自己在哪一頁 ——
             實機回報「vCenter 設定成功、說讀到 169 台，但虛擬化頁面看不到 VM」，就是
             人在 PVE 那一頁找 VMware 的資料。 -->
        <span>{{ adminMode ? t("virt.proxmox_admin_title")
                 : (platform === "vmware" ? t("nav.virt_vmware")
                    : platform === "proxmox" ? t("nav.virt_pve") : t("nav.virtualization")) }}</span>
      </n-space>
    </template>
    <!-- 空頁面要能自己解釋。這一頁沒東西、別的平台卻有，那不是「沒有資料」，
         是「資料不在這一頁」—— 兩者對使用者的意義完全不同。 -->
    <n-alert v-if="otherPlatformCount > 0" type="info" :bordered="false"
             style="margin-bottom: 12px">
      {{ t("virt.other_platform_hint", { n: otherPlatformCount,
            name: platform === "vmware" ? t("nav.virt_pve") : t("nav.virt_vmware") }) }}
      <n-button text type="primary" size="small" style="margin-left:6px"
                @click="goOtherPlatform">{{ t("virt.other_platform_go") }}</n-button>
    </n-alert>
    <n-tabs v-model:value="tab" type="line">
      <n-tab-pane v-if="!adminMode" name="clusters">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><AdvancedIcon /></n-icon>{{ `${t('virt.clusters')} (${clusters.length})` }}</span>
        </template>
        <n-space align="center" style="margin: 8px 0">
          <n-input v-model:value="clusterP.query" clearable style="width:180px" :placeholder="t('common.filter')" />
          <n-button @click="refresh" :loading="loading">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}
          </n-button>
          <n-button type="primary" :disabled="_authBtn.me?.can_edit === false" @click="openClusterCreate">
            <template #icon><n-icon><PlusIcon /></n-icon></template>
            {{ t("virt.add_cluster") }}
          </n-button>
          <ColumnPicker :all="clusterP.items" :visible="clusterP.visibleKeys"
                        @update:visible="clusterP.setVisible" @reset="clusterP.reset"
                        :order="clusterP.order" @update:order="clusterP.setOrder" />
          <ExportButton :columns="clusterP.visibleCols" :rows="clusterP.filtered" filename="virt-clusters" :title="t('virt.clusters')" />
        </n-space>
        <n-data-table :columns="clusterP.visibleCols" :data="clusterP.filtered" :loading="loading" :bordered="false" :pagination="pg" :scroll-x="720" />
      </n-tab-pane>
      <n-tab-pane v-if="!adminMode" name="vms">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><VirtualizationIcon /></n-icon>{{ `${t('virt.vms')} (${vms.length})` }}</span>
        </template>
        <n-space align="center" style="margin: 8px 0">
          <n-input v-model:value="vmP.query" clearable style="width:180px" :placeholder="t('common.filter')" />
          <n-button @click="refresh" :loading="loading">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}
          </n-button>
          <ColumnPicker :all="vmP.items" :visible="vmP.visibleKeys"
                        @update:visible="vmP.setVisible" @reset="vmP.reset"
                        :order="vmP.order" @update:order="vmP.setOrder" />
          <ExportButton :columns="vmP.visibleCols" :rows="vmP.filtered" filename="virt-vms" :title="t('virt.vms')" />
        </n-space>
        <n-data-table :columns="vmP.visibleCols" :data="vmP.filtered" :loading="loading" :bordered="false" :pagination="pg" :scroll-x="960" />
      </n-tab-pane>
      <!-- PVE 防火牆：東西向／主機層分段。刻意不與「對外開放服務」混在一起 ——
           PVE 規則不代表對外可達，混談會製造假的曝險警訊 -->
      <n-tab-pane v-if="!adminMode" name="pvefw">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><FirewallIcon /></n-icon>{{ `${t('virt.pve_fw')} (${fwStates.length})` }}</span>
        </template>
        <n-alert type="info" :bordered="false" size="small" style="margin-bottom: 10px">
          {{ t("virt.pve_fw_hint") }}
        </n-alert>
        <!-- 防護狀態分布：規則存在不等於生效，這一列才是重點。
             四張等寬卡片對齊，點一下即篩選（與異常偵測頁的統計卡同一套視覺） -->
        <div class="fw-postures">
          <div v-for="k in POSTURE_ORDER" :key="k" class="fw-posture"
               :class="[`fw-posture--${k}`, { 'is-active': fwPosture === k }]"
               role="button" tabindex="0"
               @click="fwPosture = fwPosture === k ? null : k"
               @keyup.enter="fwPosture = fwPosture === k ? null : k">
            <div class="fw-posture__label">{{ postureLabel(k) }}</div>
            <div class="fw-posture__value">{{ fwCounts[k] ?? 0 }}</div>
          </div>
        </div>
        <!-- 工具列與其他分頁一致：搜尋 / 防護狀態篩選 / 重新整理 / 欄位 / 匯出 -->
        <n-space align="center" style="margin: 10px 0">
          <n-input v-model:value="fwP.query" clearable style="width:180px"
                   :placeholder="t('common.filter')" />
          <n-select v-model:value="fwPosture" clearable style="width:170px"
                    :placeholder="t('virt.all_postures')" :options="postureOptions" />
          <n-button size="small" :loading="fwLoading" @click="loadFw">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}
          </n-button>
          <ColumnPicker :all="fwP.items" :visible="fwP.visibleKeys"
                        @update:visible="fwP.setVisible" @reset="fwP.reset"
                        :order="fwP.order" @update:order="fwP.setOrder" />
          <ExportButton :columns="fwP.visibleCols" :rows="fwP.filtered"
                        filename="pve-firewall" :title="t('virt.pve_fw')" />
        </n-space>
        <!-- row-key 必要：沒有它，展開一列會把每一列都展開（naive-ui 分不出是哪一列） -->
        <n-data-table :columns="fwP.visibleCols" :data="fwP.filtered" :loading="fwLoading"
                      :row-key="(r: FwState) => r.vmid"
                      :bordered="false" :pagination="pg" :scroll-x="900" />
      </n-tab-pane>
      <n-tab-pane v-if="adminMode" name="proxmox">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><DevicesIcon /></n-icon>{{ `${t('virt.proxmox')} (${proxmox.length})` }}</span>
        </template>
        <n-space align="center" style="margin: 8px 0">
          <n-input v-model:value="proxmoxP.query" clearable style="width:180px" :placeholder="t('common.filter')" />
          <n-button @click="refresh" :loading="loading">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}
          </n-button>
          <n-button type="primary" :disabled="_authBtn.me?.can_edit === false" @click="openPxCreate">
            <template #icon><n-icon><PlusIcon /></n-icon></template>
            {{ t("virt.add_proxmox") }}
          </n-button>
          <ColumnPicker :all="proxmoxP.items" :visible="proxmoxP.visibleKeys"
                        @update:visible="proxmoxP.setVisible" @reset="proxmoxP.reset"
                        :order="proxmoxP.order" @update:order="proxmoxP.setOrder" />
          <ExportButton :columns="proxmoxP.visibleCols" :rows="proxmoxP.filtered" filename="proxmox" :title="t('virt.proxmox')" />
        </n-space>
        <n-data-table :columns="proxmoxP.visibleCols" :data="proxmoxP.filtered" :loading="loading" :bordered="false" :scroll-x="760" />
      </n-tab-pane>
    </n-tabs>

    <!-- 新增叢集 -->
    <n-modal v-model:show="showCluster" preset="card"
             :title="editingClusterId ? t('common.edit') : t('virt.add_cluster')" style="width: 420px">
      <n-form>
        <n-form-item :label="t('common.name')"><n-input v-model:value="clusterForm.name" /></n-form-item>
        <n-form-item :label="t('cols.unit')">
          <n-select v-model:value="clusterForm.customer_id" :options="customerOptions"
                    :placeholder="t('common.not_specified')" clearable filterable />
        </n-form-item>
        <n-form-item :label="t('sections.description')">
          <n-input v-model:value="clusterForm.description" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showCluster = false">{{ t("common.cancel") }}</n-button>
          <n-button type="primary" @click="submitCluster">{{ t("common.save") }}</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 新增 / 編輯 Proxmox 連線 -->
    <n-modal v-model:show="showPx" preset="card" :title="pxModalTitle" style="width: 520px">
      <n-form label-placement="top">
        <n-form-item v-if="!editingPxId && clusterOptions.length" :label="t('virt.cluster')">
          <n-select v-model:value="pxForm.cluster_id" :options="clusterOptions"
                    clearable :placeholder="t('virt.cluster_auto_ph')" />
        </n-form-item>
        <n-form-item :label="t('virt.api_url_primary')">
          <n-input v-model:value="pxForm.api_url" placeholder="https://pve.example.com:8006" />
        </n-form-item>
        <n-form-item :label="t('virt.extra_urls')">
          <n-input v-model:value="pxForm.extra_urls" type="textarea" :rows="2"
                   :placeholder="t('virt.extra_urls_ph')" />
        </n-form-item>
        <n-form-item :label="t('virt.auth_username')">
          <n-input v-model:value="pxForm.auth_username" placeholder="root@pam" />
        </n-form-item>
        <n-form-item :label="t('virt.token_id')">
          <n-input v-model:value="pxForm.auth_token_id" placeholder="ipam" />
        </n-form-item>
        <n-form-item :label="t('virt.token_secret')">
          <n-input v-model:value="pxForm.token_secret" type="password" show-password-on="click"
                   :placeholder="editingPxId ? t('virt.secret_keep') : 'xxxxxxxx-xxxx-...'" />
        </n-form-item>
        <n-space align="center" :size="24">
          <n-form-item :label="t('common.enabled')"><n-switch v-model:value="pxForm.enabled" /></n-form-item>
          <n-form-item :label="t('virt.verify_tls')"><n-switch v-model:value="pxForm.verify_tls" /></n-form-item>
          <n-form-item :label="t('virt.interval')">
            <n-input-number v-model:value="pxForm.sync_interval_seconds" :min="60" :max="86400" />
          </n-form-item>
        </n-space>
        <n-form-item :label="t('virt.scope_subnets')">
          <div style="width: 100%">
            <n-select v-model:value="pxForm.scope_subnet_ids" :options="subnetOptions"
                      multiple filterable clearable :placeholder="t('virt.scope_all')" />
            <ScopeOverlapWarning :scope-empty="!pxForm.scope_subnet_ids?.length" />
          </div>
        </n-form-item>
        <div style="margin: -8px 0 4px">
          <span style="font-size: 11px; opacity: .7">{{ t("virt.scope_hint") }}</span>
        </div>
        <n-form-item :label="t('virt_autocreate.label')">
          <div style="width:100%">
            <n-switch v-model:value="pxForm.auto_create_ips" />
            <div style="font-size:11px;opacity:.65;margin-top:4px">{{ t("virt_autocreate.hint") }}</div>
            <!-- 開了就等於放棄一道偵測，這件事必須在開關旁邊講 -->
            <n-alert v-if="pxForm.auto_create_ips" type="warning" :show-icon="false" :bordered="false"
                     style="margin-top:6px">
              {{ t("virt_autocreate.risk") }}
            </n-alert>
          </div>
        </n-form-item>

        <n-alert type="info" :title="t('virt.help_title')" :bordered="false"
                 style="margin-top: 4px">
          <ol class="px-help">
            <li>{{ t("virt.help_step1") }}</li>
            <li>
              {{ t("virt.help_step2") }}
              <span class="px-tag">{{ t("virt.help_path") }}</span>
              <span class="px-tag">PVEAuditor</span>
              <span class="px-tag">Propagate</span>
            </li>
            <li>{{ t("virt.help_step3") }}</li>
          </ol>
        </n-alert>
        <n-alert type="success" :bordered="false" :show-icon="true"
                 style="margin-top: 8px">
          {{ t("virt.multinode_hint") }}
        </n-alert>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showPx = false">{{ t("common.cancel") }}</n-button>
          <n-button type="primary" @click="submitPx">{{ t("common.save") }}</n-button>
        </n-space>
      </template>
    </n-modal>
  </n-card>
</template>

<style scoped>
/* 展開後的規則列：一行一條，來源層用標籤標示（guest / node / datacenter） */
.fw-rules { display: flex; flex-direction: column; gap: 4px; padding: 4px 2px; }
.fw-rule { display: flex; align-items: center; gap: 6px; font-size: 12.5px; flex-wrap: wrap; }
.fw-rule__body { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
/* 防護狀態統計卡：等寬對齊。原本用大小不一的 tag 排一列，視覺很亂（回報） */
.fw-postures { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.fw-posture {
  border: 1px solid var(--n-border-color, rgba(128,128,128,.28));
  border-radius: 10px; padding: 8px 14px; cursor: pointer;
  background: rgba(127,127,127,.04);
  transition: border-color .15s ease, background .15s ease, transform .1s ease;
}
.fw-posture:hover { transform: translateY(-1px); }
.fw-posture.is-active { box-shadow: 0 0 0 2px rgba(24,160,88,.35) inset; }
.fw-posture__label { font-size: 12.5px; opacity: .72; }
.fw-posture__value { font-size: 22px; font-weight: 600; line-height: 1.3; }
.fw-posture--unprotected { border-color: rgba(208,48,80,.5); background: rgba(208,48,80,.08); }
.fw-posture--unprotected .fw-posture__value { color: #d03050; }
.fw-posture--open { border-color: rgba(240,160,32,.55); background: rgba(240,160,32,.09); }
.fw-posture--open .fw-posture__value { color: #d97706; }
.fw-posture--blocked { border-color: rgba(32,128,240,.4); background: rgba(32,128,240,.07); }
.fw-posture--filtered { border-color: rgba(24,160,88,.45); background: rgba(24,160,88,.08); }
.fw-posture--filtered .fw-posture__value { color: #18a058; }
.px-help {
  margin: 0;
  padding-left: 18px;
  line-height: 1.9;
  font-size: 13px;
}
.px-help li { margin-bottom: 2px; }
.px-tag {
  display: inline-block;
  margin: 0 2px;
  padding: 0 6px;
  border-radius: 4px;
  font-family: monospace;
  font-size: 12px;
  background: rgba(24, 160, 88, 0.16);
  color: var(--n-text-color, inherit);
}
.nic-stack { display: flex; flex-direction: column; }
.nic-line {
  line-height: 1.8;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.nic-line + .nic-line { border-top: 1px dashed rgba(127, 127, 127, 0.18); }
.nic-link { color: var(--primary-color, #18a058); cursor: pointer; }
.nic-link:hover { text-decoration: underline; }
</style>
