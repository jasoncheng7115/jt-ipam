<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from "vue";
import { fmtDateTime } from "@/utils/datetime";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NIcon, NButton, NModal, NForm, NFormItem,
  NInput, NSwitch, NPopconfirm, NTag, NInputGroup, NAlert, NSelect, NTooltip,
  NCheckbox, NCheckboxGroup, NInputNumber, NPopover, NText, NTabs, NTabPane,
  useMessage, useDialog, type DataTableColumns,
} from "naive-ui";
import {
  ScanAgentsIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SyncIcon, SaveIcon, CancelIcon,
  InfoIcon, CloneIcon, SnmpIcon,
} from "@/icons";
import {
  listScanAgents, createScanAgent, updateScanAgent, deleteScanAgent, rotateScanAgentKey, scanNowAgent,
  getAgentSubnets, setAgentSubnets, getAgentSubnetList, type AgentSubnetItem,
  type ScanAgent,
  type ScanAgentTool,
} from "@/api/phase3";
import { listSubnets } from "@/api/subnets";
import { useRoute, useRouter } from "vue-router";
import JumpHosts from "@/views/JumpHosts.vue";
import ScanAgentLoadPanel from "@/components/ScanAgentLoadPanel.vue";
import { useScanProbes, probeLabel } from "@/api/scanProbes";
import { autoSort } from "@/composables/useTableSort";
import { SUDO } from "@/utils/sudo";
import AgentTrustPicker from "@/components/AgentTrustPicker.vue";
import { renderTlsState, useAgentTrust } from "@/composables/useAgentTrust";
import { probeInstall, toolInstallCmd } from "@/utils/probeInstall";
import { listSnmpAgents, type SnmpAgentInfo } from "@/api/snmp";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
const { t, locale } = useI18n();
const { catalog } = useScanProbes();

// 所有欄位 = 預設可見（含新加的 tools「相依套件」）。tools 同時在 allKeys（才點得動/開得起來）
// 與 defaultVisible（才預設打開；在此 → withNewDefaults 讓舊用戶升級後也自動帶出這欄）。
const SA_COLS = ["name", "enabled", "has_key", "agent_version", "tls", "source_ip", "subnet_count", "load",
  "tools", "snmp", "last_seen_at", "last_error", "actions"];
const { visibleKeys: saVis, setVisible: saSet, reset: saReset,
  order: saOrder, setOrder: saSetOrder, orderColumns: saOrderCols } = useColumnPrefs(
  "scan_agents", SA_COLS, SA_COLS,
);
const saPicker = computed(() => [
  { key: "name", label: t("cols.name") },
  { key: "enabled", label: t("cols.enabled") },
  { key: "has_key", label: t("cols.key") },
  { key: "agent_version", label: t("cols.version") },
  { key: "tls", label: t("certTrust.col") },
  { key: "source_ip", label: t("cols.source_ip") },
  { key: "subnet_count", label: t("cols.subnet") },
  { key: "load", label: t("scan_load.col_load") },
  { key: "tools", label: t("scan_agent.deps") },
  { key: "snmp", label: "SNMP" },
  { key: "last_seen_at", label: t("cols.last_report") },
  { key: "last_error", label: t("cols.last_error") },
  { key: "actions", label: t("cols.actions") },
]);

const msg = useMessage();
const dialog = useDialog();
const route = useRoute();
const router = useRouter();

// ── 代理負責的子網路（點子網路數字彈出）──
const subnetListShow = ref(false);
const subnetListAgent = ref<ScanAgent | null>(null);
const subnetList = ref<AgentSubnetItem[]>([]);
const subnetListLoading = ref(false);
async function openSubnetList(a: ScanAgent) {
  subnetListAgent.value = a;
  subnetList.value = [];
  subnetListShow.value = true;
  subnetListLoading.value = true;
  try { subnetList.value = await getAgentSubnetList(a.id); } catch (e) { msg.error(apiErrMsg(e)); }
  finally { subnetListLoading.value = false; }
}
const subnetListCols = computed<DataTableColumns<AgentSubnetItem>>(() => [
  { title: "CIDR", key: "cidr", width: 170,
    render: (r) => h(NButton, { text: true, type: "primary", onClick: () => goSubnet(r.id) }, () => r.cidr) },
  { title: t("cols.description"), key: "description", render: (r) => r.description || "—" },
  { title: t("scan_agent.scan_enabled"), key: "scan_enabled", width: 110,
    render: (r) => h(NTag, { size: "small", bordered: false, type: r.scan_enabled ? "success" : "default" },
                     () => (r.scan_enabled ? t("scan_agent.scan_on") : t("scan_agent.scan_off"))) },
]);
function goSubnet(id: string) {
  subnetListShow.value = false;
  void router.push(`/subnets/${id}`);
}
// 頁籤跟著網址（?tab=jump）：舊的 /jump-hosts 書籤轉到這裡也落在跳板頁籤
const tab = ref<string>(route.query.tab === "jump" ? "jump" : "agents");
watch(tab, (v) => {
  void router.replace({ query: { ...route.query, tab: v === "jump" ? "jump" : undefined } });
});
const rows = ref<ScanAgent[]>([]);
// 負載面板（通知的連結帶 ?load=<代理 id>，進來就直接打開那一台）
const loadShow = ref(false);
const loadAgent = ref<ScanAgent | null>(null);
function openLoad(r: ScanAgent) { loadAgent.value = r; loadShow.value = true; }
import { useTableQuickFilter } from "@/composables/useTableQuickFilter";
const { query: filterQ, filtered: filteredRows } = useTableQuickFilter(rows);
import { useTablePagination } from "@/composables/useTablePagination";
import { apiErrMsg } from "@/api/client";
const pg = useTablePagination();
const loading = ref(false);
const show = ref(false);
const showHelp = ref(false);
const editing = ref<ScanAgent | null>(null);
const form = ref({ name: "", description: "", enabled: true, autoCreate: false,
                   subnet_ids: [] as string[], relayAllowed: false, relayMax: 4,
                   relayPorts: "22,3389,5900-5910" });
// 主控台中繼（issue #24 階段二）：要不要中繼、哪些埠都在這裡設定，代理主機不必做任何事。
// 標籤只講代理那一端的狀態：舊代理（沒回報）、代理主機的擁有者否決了（JT_IPAM_RELAY=0）、本機另有限縮
const relayState = computed(() => {
  const caps = editing.value?.relay_caps;
  if (!caps) return { tag: "default" as const, text: t("relay.agent_state_old") };
  if (!caps.enabled) return { tag: "warning" as const, text: t("relay.agent_state_off") };
  const limits: string[] = [];
  if (caps.ports?.length) limits.push(t("relay.agent_local_ports", { ports: fmtPorts(caps.ports) }));
  if (caps.max) limits.push(t("relay.agent_local_max", { max: caps.max }));
  if (caps.pinned) limits.push(t("relay.agent_local_cidrs"));
  return { tag: "success" as const,
           text: limits.length ? t("relay.agent_state_on_limited", { limits: limits.join("；") })
                               : t("relay.agent_state_on") };
});
function fmtPorts(ports: number[]): string {
  // 連續的埠縮成範圍：22, 3389, 5900–5910
  const out: string[] = [];
  let i = 0;
  while (i < ports.length) {
    let j = i;
    while (j + 1 < ports.length && ports[j + 1] === ports[j] + 1) j++;
    out.push(j > i ? `${ports[i]}–${ports[j]}` : String(ports[i]));
    i = j + 1;
  }
  return out.join(", ");
}
// 每代理探測設定
const enabledProbes = ref<string[]>([]);
const probeIntervals = ref<Record<string, number>>({});
// 取目前可用探測清單（編輯且有回報 available_probes 時用以反灰）
const availProbes = computed<string[] | null>(() => editing.value?.available_probes ?? null);
function probeAvailable(key: string): boolean {
  return availProbes.value === null || availProbes.value.includes(key);
}
// 探測所需的工具 / 安裝指令（代理主機上安裝後，下次回報即解鎖該探測）
// 已勾選的探測（每一種都可以設自己的間隔）。
// 原本只讓重型探測設間隔，但後端本來就吃全部七種 —— 輕型只是沒有畫面可設，
// 使用者只能用預設值，也看不出「多久掃一次」是怎麼決定的。
const intervalChecked = computed(() =>
  catalog.value.probes.filter((p) => enabledProbes.value.includes(p.key)),
);
const lightChecked = computed(() => intervalChecked.value.filter((p) => p.klass === "light"));

/** 快迴圈頻率＝所有輕型間隔的最小值（下限 60 秒）—— 與後端 fast_interval() 同一條規則。
 *  這個數字就是「多久掃一次」，把它算給使用者看，不然改了輕型間隔也不知道影響什麼。 */
const fastInterval = computed(() => {
  const light = lightChecked.value.map(
    (p) => probeIntervals.value[p.key] ?? p.default_interval_seconds);
  return light.length ? Math.max(60, Math.min(...light)) : 300;
});
// 秒數 → 人類可讀（天 / 小時 / 分 / 秒），給間隔輸入框旁的換算提示
function humanInterval(secs: number | null | undefined): string {
  const s = Number(secs || 0);
  if (s <= 0) return "—";
  if (s % 86400 === 0) return `${s / 86400} ${t("scan_probes.days")}`;
  if (s % 3600 === 0) return `${s / 3600} ${t("scan_probes.hours")}`;
  if (s % 60 === 0) return `${s / 60} ${t("scan_probes.mins")}`;
  return `${s} ${t("scan_probes.secs")}`;
}
// 確保某重型探測的間隔有預設值（勾選時補上）
function ensureInterval(key: string) {
  const p = catalog.value.probes.find((x) => x.key === key);
  if (p && probeIntervals.value[key] == null) {
    probeIntervals.value[key] = p.default_interval_seconds;
  }
}
function onProbesChange(vals: (string | number)[]) {
  enabledProbes.value = vals.map((v) => String(v));
  enabledProbes.value.forEach(ensureInterval);
}
// 子網路選項標出「目前由哪台代理掃描」：以前勾了就默默從別台搬過來（客戶 2026-11-05：高雄的網段被搬到
// 本機代理，本機到不了 → 整段判離線）。儲存前另外跳確認（見 submit）。
const subnetRaw = ref<{ id: string; cidr: string; description: string | null; scan_agent_id: string | null }[]>([]);
const agentName = (id: string | null) => (id ? rows.value.find((a) => a.id === id)?.name ?? id.slice(0, 8) : "");
const subnetOpts = computed(() => subnetRaw.value.map((s) => {
  const base = s.description ? `${s.cidr} (${s.description})` : s.cidr;
  const other = s.scan_agent_id && s.scan_agent_id !== editing.value?.id;
  return { label: other ? `${base} · ${t("scanAgentHelp.subnet_held_by", { agent: agentName(s.scan_agent_id) })}` : base,
           value: s.id };
}));
async function loadSubnetOpts() {
  try {
    const res = await listSubnets({ page: 1, pageSize: 500 });
    subnetRaw.value = res.items.map((s) => ({ id: s.id, cidr: s.cidr, description: s.description ?? null,
                                               scan_agent_id: s.scan_agent_id ?? null }));
  } catch { /* silent */ }
}
/** 這次要指派、但目前由別台代理掃描的子網路 */
function movesFromOthers(ids: string[]) {
  return subnetRaw.value.filter((s) => ids.includes(s.id) && s.scan_agent_id && s.scan_agent_id !== editing.value?.id);
}
function confirmMoves(moves: ReturnType<typeof movesFromOthers>): Promise<boolean> {
  return new Promise((resolve) => {
    dialog.warning({
      title: t("scanAgentHelp.move_title"),
      content: () => h("div", { "data-testid": "agent-move-confirm" }, [
        h("p", { style: "margin:0 0 8px" }, t("scanAgentHelp.move_body")),
        h("ul", { style: "margin:0;padding-left:20px" }, moves.map((m) =>
          h("li", t("scanAgentHelp.move_item", { subnet: m.cidr, agent: agentName(m.scan_agent_id) })))),
      ]),
      positiveText: t("scanAgentHelp.move_ok"),
      negativeText: t("common.cancel"),
      onPositiveClick: () => resolve(true),
      onNegativeClick: () => resolve(false),
      onClose: () => resolve(false),
      onMaskClick: () => resolve(false),
    });
  });
}

// 一次性金鑰揭露 modal
const showKey = ref(false);
const revealedKey = ref("");
const revealedName = ref("");

const serverOrigin = window.location.origin;
// 代理怎麼確認連到的是真的 jt-ipam（2026-11-04，見 useAgentTrust）。安裝程式重跑會沿用舊設定，
// 所以「用系統 CA 驗證」要明講（explicitCa），舊的指紋才會被清掉
const trust = useAgentTrust({ explicitCa: true });
const installerOneLiner = computed(() =>
  `curl ${trust.linux.value[0]} ${serverOrigin}/api/v1/scan-agents/installer.sh | ${SUDO} env `
  + `JT_IPAM_URL=${serverOrigin} JT_IPAM_AGENT_KEY=${revealedKey.value || "<KEY>"}${trust.linux.value[1]} bash`,
);
// 已安裝的主機：金鑰與其他設定沿用主機上的設定檔，只換伺服器驗證（或單純升級）
const reinstallOneLiner = computed(() =>
  `curl ${trust.linux.value[0]} ${serverOrigin}/api/v1/scan-agents/installer.sh | ${SUDO} env `
  + `JT_IPAM_URL=${serverOrigin}${trust.linux.value[1]} bash`,
);

// SNMP 收集（0217）：這裡只顯示能力與被指派幾個收集目標，設定在 SNMP 頁（憑證不是代理的屬性，換代理不該重填）
const snmpInfo = ref<Record<string, SnmpAgentInfo>>({});
const snmpMinVersion = ref("");
async function loadSnmpInfo() {
  try {
    const r = await listSnmpAgents();
    snmpInfo.value = Object.fromEntries(r.items.map((a) => [a.agent_id, a]));
    snmpMinVersion.value = r.min_agent_version;
  } catch { snmpInfo.value = {}; }   // 拿不到（舊後端）就整欄顯示「—」，不影響代理清單
}

async function refresh() {
  loading.value = true;
  try {
    const [r] = await Promise.all([listScanAgents(), loadSnmpInfo()]);
    rows.value = r.items;
  }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}
function openCreate() {
  editing.value = null;
  form.value = { name: "", description: "", enabled: true, autoCreate: false, subnet_ids: [],
                 relayAllowed: false, relayMax: 4, relayPorts: "22,3389,5900-5910" };
  enabledProbes.value = ["icmp"];
  probeIntervals.value = {};
  enabledProbes.value.forEach(ensureInterval);
  void loadSubnetOpts();
  show.value = true;
}
function openEdit(r: ScanAgent) {
  editing.value = r;
  form.value = { name: r.name, description: r.description ?? "", enabled: r.enabled,
                 autoCreate: !!r.auto_create_ips, subnet_ids: [],
                 relayAllowed: !!r.relay_allowed, relayMax: r.relay_max_sessions ?? 4,
                 relayPorts: r.relay_ports ?? "22,3389,5900-5910" };
  enabledProbes.value = [...(r.enabled_probes ?? [])];
  probeIntervals.value = { ...(r.probe_intervals ?? {}) };
  enabledProbes.value.forEach(ensureInterval);
  void loadSubnetOpts();
  void getAgentSubnets(r.id).then((ids) => { form.value.subnet_ids = ids; }).catch(() => {});
  show.value = true;
}
// 送出所有已勾選探測的間隔（後端會再對各項的最小值做把關）
function buildProbeIntervals(): Record<string, number> {
  const out: Record<string, number> = {};
  for (const p of intervalChecked.value) {
    const v = probeIntervals.value[p.key];
    out[p.key] = v != null ? v : p.default_interval_seconds;
  }
  return out;
}
async function submit() {
  try {
    if (editing.value) {
      await updateScanAgent(editing.value.id, {
        name: form.value.name.trim() || undefined,       // 可以改名（10-22）：代理靠金鑰辨識，名稱只是顯示用
        // 空字串也要送：以前 `|| undefined` 讓「刪光說明再儲存」等於沒改（10-22 使用者回報）
        description: form.value.description ?? "",
        enabled: form.value.enabled,
        auto_create_ips: form.value.autoCreate,
        enabled_probes: enabledProbes.value,
        probe_intervals: buildProbeIntervals(),
        relay_allowed: form.value.relayAllowed,
        relay_max_sessions: form.value.relayMax,
        relay_ports: form.value.relayPorts,
      });
      const moves = movesFromOthers(form.value.subnet_ids);
      if (moves.length && !(await confirmMoves(moves))) return;
      await setAgentSubnets(editing.value.id, form.value.subnet_ids, moves.length > 0);
      show.value = false;
    } else {
      const created = await createScanAgent({
        name: form.value.name,
        description: form.value.description ?? "",
        enabled: form.value.enabled,
        auto_create_ips: form.value.autoCreate,
        enabled_probes: enabledProbes.value,
        probe_intervals: buildProbeIntervals(),
      });
      show.value = false;
      revealedKey.value = created.enroll_key;
      revealedName.value = created.name;
      showKey.value = true;   // 顯示一次性金鑰 + 安裝指令
    }
    await refresh();
  } catch (e: any) { msg.error(apiErrMsg(e)); }
}
async function rotate(r: ScanAgent) {
  try {
    const res = await rotateScanAgentKey(r.id);
    revealedKey.value = res.enroll_key;
    revealedName.value = res.name;
    showKey.value = true;
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function del(r: ScanAgent) {
  try { await deleteScanAgent(r.id); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
/** 對話框裡的刪除：刪完要關掉對話框，否則會停在一個已經不存在的代理上。 */
async function delFromModal() {
  if (!editing.value) return;
  await del(editing.value);
  show.value = false;
  editing.value = null;
}

async function scanNow(r: ScanAgent) {
  try {
    const res = await scanNowAgent(r.id);
    msg.success(t("scan_agent.scan_now_done", { n: res.eta_seconds }));
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
function copy(text: string) {
  void navigator.clipboard?.writeText(text);
  msg.success(t("scanAgentHelp.copied"));
}

function iconAction(icon: any, label: string, onClick: () => void, type?: any) {
  return h(NTooltip, null, {
    // 只有圖示的按鈕：補可讀名稱（螢幕報讀與自動化測試都讀不到 tooltip）
    trigger: () => h(NButton, { size: "small", quaternary: true, type, "aria-label": label,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}
const allCols = computed<DataTableColumns<ScanAgent>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 140, ellipsis: { tooltip: true } },
  {
    title: t("common.enabled"), key: "enabled", width: 76,
    render: (r) => h(NTag, { size: "small", type: r.enabled ? "success" : "default" },
      () => r.enabled ? t("common.enabled") : t("common.disabled")),
  },
  {
    title: t("scanAgentHelp.col_key"), key: "has_key", width: 82,
    render: (r) => h(NTag, { size: "small", type: r.has_key ? "info" : "warning" },
      () => r.has_key ? t("scanAgentHelp.key_set") : t("scanAgentHelp.key_none")),
  },
  {
    title: t("scanAgentHelp.col_version"), key: "agent_version", width: 110,
    render: (r) => {
      if (!r.agent_version) return "—";
      const outdated = !!r.server_agent_version && r.agent_version !== r.server_agent_version;
      const verTag = h(NTag, { size: "small", type: outdated ? "warning" : "success", bordered: false },
        () => `v${r.agent_version}`);
      if (!outdated) return verTag;
      return h(NTooltip, null, {
        trigger: () => h(NSpace, { size: 4, wrapItem: false, align: "center", wrap: false }, () => [
          verTag,
          h(NTag, { size: "small", type: "warning", bordered: false }, () => t("scan_agent.outdated")),
        ]),
        default: () => t("scan_agent.outdated_hint", { v: r.server_agent_version }),
      });
    },
  },
  {
    title: t("certTrust.col"), key: "tls", width: 130,
    sorter: (a, b) => (a.tls_mode ?? "").localeCompare(b.tls_mode ?? ""),
    render: (r) => renderTlsState(!!r.last_seen_at, r.tls_mode, "scan-agent-tls",
      (st) => (st === "pinned" || st === "ca" ? t(`certTrust.hint_${st}`) : t(`scanAgentHelp.trust_fix_${st}`)), t,
      () => { showHelp.value = true; }),
  },
  {
    title: t("cols.source_ip"), key: "source_ip", width: 150,
    render: (r) => r.last_source_ip
      ? h("span", { style: "font-family:monospace;white-space:nowrap" }, r.last_source_ip) : "—",
  },
  {
    title: t("scanAgentHelp.col_subnets"), key: "subnet_count", width: 64,
    // 數字可以點：彈出這台代理負責哪些子網路（使用者 10-22）
    render: (r) => (r.subnet_count
      ? h(NButton, { text: true, type: "primary", "data-testid": "agent-subnet-count",
                     onClick: (e: MouseEvent) => { e.stopPropagation(); void openSubnetList(r); } },
          () => String(r.subnet_count))
      : 0),
  },
  {
    // 負載＝上線偵測一輪耗時 ÷ 週期；點一下看逐子網路細節與建議（代理 1.10.0 起才有）
    title: t("scan_load.col_load"), key: "load", width: 128,
    render: (r) => {
      const l = r.load;
      if (!l) return h(NText, { depth: 3 }, () => "—");
      const parts = [`${Math.round(l.ratio * 100)}%`];
      if (l.heavy_backlog) parts.push(t("scan_load.backlog_short", { n: l.heavy_backlog }));
      return h(NButton, { text: true, type: l.level === "overloaded" ? "error" : l.level === "busy" ? "warning" : "success",
                          "data-testid": "scan-load-cell", onClick: () => openLoad(r) },
        () => (l.truncated || l.coverage_gap ? `${parts.join(" · ")} ⚠` : parts.join(" · ")));
    },
  },
  {
    title: t("scan_agent.deps"), key: "tools", width: 96,
    render: (r) => {
      const ts = r.tools ?? [];
      if (!ts.length) return h("span", { style: "opacity:.5" }, "—");
      // 以「可用」為準：已裝 + 替代可略 都算 OK；只有真正缺工具的探測才算問題
      const missing = ts.filter((x) => toolState(r, x) === "missing").length;
      const ok = ts.length - missing;
      return h(NTag, {
        size: "small", round: true, style: "cursor:pointer",
        type: missing ? "warning" : "success",
        onClick: () => openTools(r),
      }, () => `${ok}/${ts.length}`);
    },
  },
  {
    // SNMP 能力＋被指派的收集目標數；點了到 SNMP 頁、只看這台代理負責的收集目標
    title: "SNMP", key: "snmp", width: 104,
    sorter: (a, b) => (snmpInfo.value[a.id]?.target_count ?? -1) - (snmpInfo.value[b.id]?.target_count ?? -1),
    render: (r) => {
      const s = snmpInfo.value[r.id];
      if (!s) return h("span", { style: "opacity:.5" }, "—");
      const reason = s.capable ? t("scan_agent.snmp_capable_hint", { n: s.target_count })
        : !s.version_ok ? t("scan_agent.snmp_old_agent", { v: snmpMinVersion.value })
          : t("scan_agent.snmp_no_tool");
      return h(NTooltip, null, {
        trigger: () => h(NTag, {
          // 圓角標籤＋圖示預設左邊太貼（10-22 使用者回報）：左右各留一點
          size: "small", round: true, bordered: false, style: "cursor:pointer;padding:0 11px 0 10px;gap:2px",
          "data-testid": "scan-agent-snmp",
          type: s.capable ? (s.target_count ? "success" : "info") : "default",
          onClick: () => void router.push({ name: "snmp", query: { agent: r.id } }),
        }, {
          icon: () => h(NIcon, null, () => h(SnmpIcon)),
          default: () => (s.capable ? String(s.target_count) : t("scan_agent.snmp_unavailable")),
        }),
        default: () => reason,
      });
    },
  },
  { title: t("scanAgentHelp.col_last_seen"), key: "last_seen_at", width: 168,
    render: (r) => h("span", { style: "white-space:nowrap" }, fmtDateTime(r.last_seen_at)) },
  { title: t("scanAgentHelp.col_last_error"), key: "last_error", minWidth: 150, ellipsis: { tooltip: true }, render: (r) => r.last_error ?? "—" },
  {
    // 釘在右側 + 放得下四顆按鈕：欄位一多，表格就會橫向溢出，刪除鈕整個被推到畫面外
    // （實機回報「缺少刪除功能」，其實是看不到）。與使用者／憑證頁的作法一致。
    title: t("common.actions"), key: "actions", className: "col-actions",
    width: 180, fixed: "right",
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      h(NPopconfirm, { onPositiveClick: () => scanNow(r) }, {
        trigger: () => iconAction(SyncIcon, t("scan_agent.scan_now"), () => {}, "primary"),
        default: () => t("scan_agent.scan_now_confirm"),
      }),
      iconAction(EditIcon, t("common.edit"), () => openEdit(r)),
      iconAction(RefreshIcon, t("scanAgentHelp.rotate"), () => rotate(r)),
      h(NPopconfirm, { onPositiveClick: () => del(r) }, {
        trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
        // 掃描一律要有代理：刪掉它，指派給它的子網路就沒有人掃了 —— 要先講
        default: () => (r.subnet_count
          ? t("scan_agent.delete_confirm_with_subnets", { name: r.name, n: r.subnet_count })
          : t("scan_agent.delete_confirm", { name: r.name })),
      }),
    ]),
  },
]));
const cols = computed<DataTableColumns<ScanAgent>>(() =>
  saOrderCols(allCols.value.filter((c: any) => saVis.value.includes(c.key))),
);

// 相依套件詳細資料
const toolsShow = ref(false);
const toolsRow = ref<ScanAgent | null>(null);
function openTools(r: ScanAgent) { toolsRow.value = r; toolsShow.value = true; }

// 工具狀態（以「探測是否可用」為準）：
//  installed＝已裝；redundant＝沒裝但它負責的探測已被同類工具滿足（如有 nmblookup 時的 nbtscan）→ 可略；
//  missing＝沒裝且該探測還缺工具 → 真正要裝。available_probes 已是 server 端「任一工具即可」的判定結果。
type ToolState = "installed" | "redundant" | "missing";
function toolState(agent: ScanAgent | null, tdep: ScanAgentTool): ToolState {
  if (tdep.installed) return "installed";
  const probes = tdep.probes ?? [];
  const avail = agent?.available_probes ?? [];
  if (probes.length && probes.every((p: string) => avail.includes(p))) return "redundant";
  return "missing";
}
const TOOL_STATE_TYPE: Record<ToolState, "success" | "default" | "error"> = {
  installed: "success", redundant: "default", missing: "error",
};
function toolStateLabel(s: ToolState): string {
  return s === "installed" ? t("scan_agent.dep_installed")
    : s === "redundant" ? t("scan_agent.dep_redundant")
      : t("scan_agent.dep_missing");
}

onMounted(async () => {
  void trust.load();
  await refresh();
  const want = String(route.query.load || "");
  const hit = want ? rows.value.find((a) => a.id === want) : undefined;
  if (hit) openLoad(hit);
});
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><ScanAgentsIcon /></n-icon>
        <span>{{ t("nav.scan_agents") }}</span>
      </n-space>
    </template>
    <!-- 主控台出口集中在這一頁：掃描代理（客戶端只能往外連時中繼）與 SSH 跳板（後端連得到站台上的 SSH 主機、
         站台不能裝代理時用）。跳板原本是左側選單的獨立一項，看起來像重疊功能（使用者 2026-10-04） -->
    <n-tabs v-model:value="tab" type="line" animated data-testid="agent-tabs">
      <n-tab-pane name="agents" :tab="t('nav.scan_agents')">
        <n-space style="margin-bottom: 12px" align="center">
          <n-input v-model:value="filterQ" :placeholder="t('common.filter')" clearable style="width: 160px" />
          <n-button @click="refresh" :loading="loading">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>
            {{ t("common.refresh") }}
          </n-button>
          <n-button type="primary" @click="openCreate">
            <template #icon><n-icon><PlusIcon /></n-icon></template>
            {{ t("common.create") }}
          </n-button>
          <n-button quaternary @click="showHelp = true">
            <template #icon><n-icon><InfoIcon /></n-icon></template>
            {{ t("scanAgentHelp.button") }}
          </n-button>
          <ColumnPicker :all="saPicker" :visible="saVis"
                        @update:visible="saSet" @reset="saReset"
                        :order="saOrder" @update:order="saSetOrder" />
          <ExportButton :columns="cols" :rows="rows" filename="scan-agents" :title="t('nav.scan_agents')" />
        </n-space>
        <n-data-table :columns="cols" :data="filteredRows" :loading="loading" :bordered="false" :scroll-x="1190" :pagination="pg" />
      </n-tab-pane>
      <n-tab-pane name="jump" :tab="t('jump_hosts.tab')" display-directive="show:lazy">
        <JumpHosts embedded />
      </n-tab-pane>
    </n-tabs>

    <!-- 相依套件詳細資料 -->
    <n-modal v-model:show="toolsShow" preset="card" :title="t('scan_agent.deps_title')" style="width: 720px; max-width: 94vw">
      <p class="hint" style="margin-top:0">{{ t("scan_agent.deps_hint") }}</p>
      <table v-col-resize class="dep-tbl">
        <thead>
          <tr>
            <th>{{ t("scan_agent.dep_tool") }}</th>
            <th>{{ t("common.status") }}</th>
            <th>{{ t("scanAgentHelp.col_version") }}</th>
            <th>{{ t("scan_agent.dep_probes") }}</th>
            <th>{{ t("scan_agent.dep_install") }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="tdep in (toolsRow?.tools ?? [])" :key="tdep.name">
            <td><code>{{ tdep.name }}</code></td>
            <td>
              <n-tag size="tiny" :bordered="false" :type="TOOL_STATE_TYPE[toolState(toolsRow, tdep)]">
                {{ toolStateLabel(toolState(toolsRow, tdep)) }}
              </n-tag>
            </td>
            <td>{{ tdep.version || "—" }}</td>
            <td>{{ tdep.probes.length ? tdep.probes.join(", ") : "—" }}</td>
            <td>
              <code v-if="toolState(toolsRow, tdep) === 'missing' && tdep.package">{{ toolInstallCmd(tdep.name, tdep.package, t) }}</code>
              <span v-else style="opacity:.4">—</span>
            </td>
          </tr>
        </tbody>
      </table>
      <p class="hint">{{ t("scan_agent.deps_note") }}</p>
    </n-modal>

    <!-- 建立 / 編輯 -->
    <n-modal v-model:show="show" preset="card" style="width: 460px">
      <template #header>
        <n-space align="center">
          <n-icon :size="20"><component :is="editing ? EditIcon : PlusIcon" /></n-icon>
          <span>{{ editing ? t("common.edit") : t("common.create") }}</span>
        </n-space>
      </template>
      <n-form>
        <n-form-item :label="t('common.name')">
          <n-input v-model:value="form.name" data-testid="agent-name" />
        </n-form-item>
        <n-form-item :label="t('sections.description')">
          <n-input v-model:value="form.description" type="textarea" :rows="2" />
        </n-form-item>
        <n-form-item :label="t('common.enabled')">
          <n-switch v-model:value="form.enabled" />
        </n-form-item>
        <!-- 自動收錄未登錄的位址：預設關閉，而且要把代價講清楚 —— 收錄之後那個位址
             就不會再被「未授權 IP」偵測列出來，等於私接的機器會安靜地變成正式紀錄。 -->
        <n-form-item :label="t('scan_agents.auto_create_ips')">
          <n-space vertical size="small" style="width:100%">
            <n-switch v-model:value="form.autoCreate" />
            <n-text depth="3" style="font-size:12px">
              {{ t("scan_agents.auto_create_ips_hint") }}
            </n-text>
          </n-space>
        </n-form-item>
        <!-- 主控台中繼：打開＝這台代理變成通往客戶網路的跳點。系統設定與這裡兩道開關都開才會中繼；
             代理主機不必設定任何東西（升級後自動可用），擁有者要否決才在代理主機寫 JT_IPAM_RELAY=0 -->
        <n-form-item v-if="editing" :label="t('relay.agent_allow')">
          <n-space vertical size="small" style="width:100%" data-testid="agent-relay">
            <n-space align="center" :size="12">
              <n-switch v-model:value="form.relayAllowed" data-testid="agent-relay-switch" />
              <template v-if="form.relayAllowed">
                <span style="font-size:12px">{{ t("relay.agent_max") }}</span>
                <n-input-number v-model:value="form.relayMax" :min="1" :max="64" size="small" style="width:90px" />
              </template>
            </n-space>
            <n-space v-if="form.relayAllowed" align="center" :size="8" :wrap="false">
              <span style="font-size:12px;white-space:nowrap">{{ t("relay.agent_ports") }}</span>
              <n-input v-model:value="form.relayPorts" size="small" style="max-width:240px"
                       placeholder="22,3389,5900-5910" data-testid="agent-relay-ports" />
            </n-space>
            <n-tag :type="relayState.tag" size="small" :bordered="false" data-testid="agent-relay-state">
              {{ relayState.text }}
            </n-tag>
            <n-text v-if="editing.relay_active" depth="3" style="font-size:12px">
              {{ t("relay.agent_active", { n: editing.relay_active }) }}
            </n-text>
            <n-text depth="3" style="font-size:12px">{{ t("relay.agent_allow_hint") }}</n-text>
          </n-space>
        </n-form-item>
        <n-form-item :label="t('scanAgentHelp.assign_subnets')">
          <n-select v-model:value="form.subnet_ids" :options="subnetOpts"
                    multiple filterable clearable
                    :placeholder="t('scanAgentHelp.assign_subnets_ph')" />
        </n-form-item>
        <n-form-item :label="t('scan_probes.agent_probes')">
          <n-space vertical size="small" style="width: 100%">
            <span class="probe-hint">{{ t("scan_probes.agent_probes_hint") }}</span>
            <n-checkbox-group :value="enabledProbes" @update:value="onProbesChange">
              <n-space vertical size="small">
                <div v-for="p in catalog.probes" :key="p.key" class="probe-row">
                  <n-tooltip v-if="!probeAvailable(p.key)" trigger="hover">
                    <template #trigger>
                      <n-checkbox :value="p.key" disabled>
                        {{ probeLabel(p, locale) }}
                      </n-checkbox>
                    </template>
                    {{ t("scan_probes.unavailable") }}
                  </n-tooltip>
                  <n-checkbox v-else :value="p.key">
                    {{ probeLabel(p, locale) }}
                  </n-checkbox>
                  <n-popover v-if="!probeAvailable(p.key)" trigger="click" placement="right">
                    <template #trigger>
                      <n-button text size="tiny" type="primary" class="probe-help-btn"><template #icon><n-icon><InfoIcon /></n-icon></template>
                        {{ t("scan_probes.install_help") }}
                      </n-button>
                    </template>
                    <div class="probe-help-pop">
                      <div class="probe-help-intro">{{ t("scan_probes.install_help_intro") }}</div>
                      <code class="probe-help-cmd">{{ probeInstall(p.key, t) }}</code>
                    </div>
                  </n-popover>
                  <n-tooltip v-if="p.intrusive" trigger="hover">
                    <template #trigger>
                      <n-tag size="small" type="warning" :bordered="false" round>
                        {{ t("scan_probes.intrusive") }}
                      </n-tag>
                    </template>
                    {{ t("scan_probes.intrusive_warn") }}
                  </n-tooltip>
                </div>
              </n-space>
            </n-checkbox-group>
          </n-space>
        </n-form-item>
        <!-- 掃描頻率：輕型每輪都跑，快迴圈＝其中最小的間隔；重型只在自己的間隔到了才跑。
             六個間隔各佔一整列會把對話框拉得很長，改成「名稱｜輸入｜換算」三欄對齊，
             一眼就能左右比較各探測的間隔。 -->
        <n-form-item v-if="intervalChecked.length" :label="t('scan_probes.cadence')">
          <div class="iv-wrap">
            <div class="probe-hint iv-hint">
              {{ t("scan_probes.cadence_hint", { n: humanInterval(fastInterval) }) }}
            </div>
            <div class="iv-grid">
              <template v-for="p in intervalChecked" :key="p.key">
                <span class="iv-k">{{ probeLabel(p, locale) }}</span>
                <n-input-number
                  v-model:value="probeIntervals[p.key]"
                  :min="p.min_interval_seconds"
                  :placeholder="String(p.default_interval_seconds)"
                  size="small"
                  style="width: 148px"
                >
                  <template #suffix>{{ t("scan_probes.secs") }}</template>
                </n-input-number>
                <span class="iv-approx">
                  ≈ {{ humanInterval(probeIntervals[p.key] ?? p.default_interval_seconds) }}
                </span>
              </template>
            </div>
          </div>
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="space-between" align="center">
          <!-- 刪除放左側、與儲存分開：這是不可復原的動作，不該緊鄰主要按鈕 -->
          <n-popconfirm v-if="editing" @positive-click="delFromModal">
            <template #trigger>
              <n-button type="error" ghost>
                <template #icon><n-icon><DeleteIcon /></n-icon></template>{{ t("common.delete") }}
              </n-button>
            </template>
            {{ editing.subnet_count
              ? t("scan_agent.delete_confirm_with_subnets", { name: editing.name, n: editing.subnet_count })
              : t("scan_agent.delete_confirm", { name: editing.name }) }}
          </n-popconfirm>
          <span v-else />
          <n-space>
            <n-button @click="show = false">
              <template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}
            </n-button>
            <n-button type="primary" @click="submit">
              <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
            </n-button>
          </n-space>
        </n-space>
      </template>
    </n-modal>

    <!-- 一次性金鑰 + 安裝指令 -->
    <n-modal v-model:show="showKey" preset="card"
             :title="t('scanAgentHelp.key_title')" style="width: 680px; max-width: 92vw">
      <div class="agent-help">
        <p class="warn">{{ t("scanAgentHelp.key_warn", { name: revealedName }) }}</p>
        <h4>{{ t("scanAgentHelp.key_label") }}</h4>
        <n-input-group>
          <n-input :value="revealedKey" readonly />
          <n-button @click="copy(revealedKey)"><template #icon><n-icon><CloneIcon /></n-icon></template>{{ t("scanAgentHelp.copy") }}</n-button>
        </n-input-group>
        <AgentTrustPicker :trust="trust" testid="scan-trust" style="margin-top: 12px" />
        <h4>{{ t("scanAgentHelp.oneliner_label") }}</h4>
        <pre data-testid="scan-install-oneliner">{{ installerOneLiner }}</pre>
        <n-button size="small" @click="copy(installerOneLiner)"><template #icon><n-icon><CloneIcon /></n-icon></template>{{ t("scanAgentHelp.copy") }}</n-button>
        <div class="paths-box">
          <div class="paths-title">{{ t("scanAgentHelp.paths_title") }}</div>
          <div class="path-row"><span class="pl">{{ t('scanAgentHelp.path_program') }}</span><code>/opt/jt-ipam-agent/jt_ipam_agent.py</code></div>
          <div class="path-row"><span class="pl">{{ t('scanAgentHelp.path_config') }}</span><code>/etc/jt-ipam-agent.env</code></div>
          <div class="path-row"><span class="pl">{{ t('scanAgentHelp.path_service') }}</span><code>jt-ipam-scan-agent</code></div>
          <div class="path-row"><span class="pl">{{ t('scanAgentHelp.path_logs') }}</span><code>journalctl -u jt-ipam-scan-agent -f</code></div>
          <div class="paths-note">{{ t("scanAgentHelp.path_python") }}</div>
        </div>
        <p class="muted">{{ t("scanAgentHelp.key_note") }}</p>
      </div>
    </n-modal>

    <!-- 安裝說明 -->
    <n-modal v-model:show="showHelp" preset="card"
             :title="t('scanAgentHelp.title')" style="width: 680px; max-width: 92vw">
      <div class="agent-help">
        <n-alert type="info" :bordered="false" :show-icon="true" style="margin-bottom: 16px">
          {{ t("scanAgentHelp.intro") }}
        </n-alert>

        <ol class="help-steps">
          <li><span class="sn">1</span><span>{{ t("scanAgentHelp.step1") }}</span></li>
          <li><span class="sn">2</span><span>{{ t("scanAgentHelp.step2") }}</span></li>
          <li><span class="sn">3</span><span>{{ t("scanAgentHelp.step3") }}</span></li>
        </ol>

        <AgentTrustPicker :trust="trust" testid="scan-trust" />
        <h4>{{ t("scanAgentHelp.oneliner_label") }}</h4>
        <div class="code-row">
          <pre>{{ installerOneLiner }}</pre>
          <n-button size="small" secondary @click="copy(installerOneLiner)">
            <template #icon><n-icon><CloneIcon /></n-icon></template>
            {{ t("scanAgentHelp.copy") }}
          </n-button>
        </div>
        <h4>{{ t("scanAgentHelp.reinstall_label") }}</h4>
        <div class="code-row">
          <pre data-testid="scan-reinstall-oneliner">{{ reinstallOneLiner }}</pre>
          <n-button size="small" secondary @click="copy(reinstallOneLiner)">
            <template #icon><n-icon><CloneIcon /></n-icon></template>
            {{ t("scanAgentHelp.copy") }}
          </n-button>
        </div>

        <div class="paths-box">
          <div class="paths-title">{{ t("scanAgentHelp.paths_title") }}</div>
          <div class="path-row"><span class="pl">{{ t('scanAgentHelp.path_program') }}</span><code>/opt/jt-ipam-agent/jt_ipam_agent.py</code></div>
          <div class="path-row"><span class="pl">{{ t('scanAgentHelp.path_config') }}</span><code>/etc/jt-ipam-agent.env</code></div>
          <div class="path-row"><span class="pl">{{ t('scanAgentHelp.path_service') }}</span><code>jt-ipam-scan-agent</code></div>
          <div class="path-row"><span class="pl">{{ t('scanAgentHelp.path_logs') }}</span><code>journalctl -u jt-ipam-scan-agent -f</code></div>
          <div class="paths-note">{{ t("scanAgentHelp.path_python") }}</div>
        </div>
        <n-alert type="default" :bordered="false" :show-icon="true" style="margin-top: 12px">
          {{ t("scanAgentHelp.note") }}
        </n-alert>
      </div>
    </n-modal>

    <n-modal v-model:show="subnetListShow" preset="card" style="width:560px;max-width:95vw"
             :title="t('scan_agent.subnets_of', { name: subnetListAgent?.name ?? '' })" data-testid="agent-subnet-list">
      <n-data-table size="small" :loading="subnetListLoading" :data="subnetList" :max-height="420"
                    :row-key="(r: AgentSubnetItem) => r.id" :columns="subnetListCols" />
    </n-modal>
    <ScanAgentLoadPanel v-model:show="loadShow" :agent="loadAgent" :agents="rows"
                        @changed="refresh" @create="loadShow = false; openCreate()" />
  </n-card>
</template>

<style scoped>
.agent-help h4 { margin: 16px 0 6px; font-size: 14px; }
.agent-help .help-steps { list-style: none; padding: 0; margin: 0; }
.agent-help .help-steps li {
  display: flex; align-items: flex-start; gap: 10px;
  margin: 8px 0; line-height: 1.6; font-size: 14px;
}
.agent-help .help-steps .sn {
  flex: 0 0 auto;
  width: 22px; height: 22px; margin-top: 1px;
  display: inline-flex; align-items: center; justify-content: center;
  border-radius: 50%; background: var(--primary-color, #18a058);
  color: #fff; font-size: 12px; font-weight: 600;
}
.agent-help .code-row { display: flex; align-items: flex-start; gap: 8px; }
.agent-help pre {
  flex: 1 1 auto; margin: 0;
  background: rgba(127,127,127,0.12);
  padding: 10px 12px; border-radius: 6px; overflow-x: auto;
  font-size: 12px; line-height: 1.5; white-space: pre-wrap; word-break: break-all;
}
.agent-help .muted { opacity: .7; font-size: 12px; margin-top: 10px; }
.agent-help .warn { color: #e0a23c; font-weight: 500; }
.agent-help .paths-box {
  margin-top: 14px;
  border: 1px solid rgba(127,127,127,0.2);
  border-radius: 8px;
  padding: 12px 14px;
  background: rgba(127,127,127,0.04);
}
.agent-help .paths-title { font-size: 13px; font-weight: 600; margin-bottom: 8px; opacity: .85; }
.agent-help .path-row {
  display: flex; align-items: center; gap: 10px;
  padding: 3px 0; font-size: 12.5px;
}
.agent-help .path-row .pl {
  flex: 0 0 52px; text-align: right;
  opacity: .6;
}
.agent-help .path-row code {
  background: rgba(127,127,127,0.14);
  padding: 2px 8px; border-radius: 5px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px;
  word-break: break-all;
}
.agent-help .paths-note { margin-top: 8px; font-size: 12px; opacity: .6; }
.probe-hint { font-size: 12px; opacity: .65; line-height: 1.5; }
.probe-row { display: flex; align-items: center; gap: 6px; }
.probe-help-btn { font-size: 12px; }
.probe-help-pop { max-width: 320px; }
.probe-help-intro { font-size: 12.5px; line-height: 1.6; margin-bottom: 6px; }
.probe-help-cmd {
  display: block;
  padding: 6px 8px;
  border-radius: 4px;
  background: var(--n-code-color, rgba(0, 0, 0, 0.05));
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
}
.dep-tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.dep-tbl th, .dep-tbl td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--n-border-color, rgba(128,128,128,.18)); white-space: nowrap; }
/* 只讓「安裝指令」那欄（最後一欄）可換行；工具名/版本/用於探測不換行 */
.dep-tbl td:last-child, .dep-tbl th:last-child { white-space: normal; }
.dep-tbl th { font-weight: 600; opacity: .7; font-size: 12px; }
.dep-tbl code { font-size: 12px; background: rgba(128,128,128,.1); border-radius: 4px; padding: 1px 5px; }
.hint { font-size: 12px; opacity: .65; line-height: 1.5; margin: 8px 0; }
.iv-wrap { width: 100%; }
.iv-hint { margin-bottom: 10px; }
/* 名稱｜輸入｜換算 —— 名稱欄用 max-content，最長的那個決定寬度，全部靠左對齊 */
.iv-grid { display: grid; grid-template-columns: max-content 148px max-content;
  gap: 8px 12px; align-items: center; }
.iv-k { font-size: 13px; }
.iv-approx { font-size: 12px; opacity: 0.55; white-space: nowrap; }
</style>
