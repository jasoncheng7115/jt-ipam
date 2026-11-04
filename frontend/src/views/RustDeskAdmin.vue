<script setup lang="ts">
/**
 * RustDesk Server（開源版）整合，版面照 Wazuh 整合頁：頁籤切換「RustDesk 伺服器／裝置／連線稽核」。
 * jt-ipam 不連過去：RustDesk 主機上裝專用的 RustDesk 代理（不是掃描代理），每台伺服器一把代理金鑰。
 * 代理每幾秒輪詢一次，所以「立即同步」「測試」是設旗標、等代理下次輪詢取走（見 endpoints/rustdesk_agent.py）。
 * 「刪除舊註冊」也一樣是排入請求：網頁上允許（allow_peer_delete）＋主機端以 --allow-delete 安裝的代理才做得到。
 */
import { computed, h, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { fmtDateTime } from "@/utils/datetime";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import {
  NCard, NDataTable, NSpace, NButton, NTag, NIcon, NTooltip, NAlert, NText, NTabs, NTabPane, NSpin,
  NModal, NForm, NFormItem, NInput, NInputNumber, NSwitch, NSelect, NPopconfirm, NCheckbox,
  useMessage, type DataTableColumns, type DataTableSortState, type DataTableRowKey,
} from "naive-ui";
import CopyButton from "@/components/CopyButton.vue";
import { rustdeskOs } from "@/utils/rustdeskOs";
import RustDeskAuditTable from "@/components/RustDeskAuditTable.vue";
import {
  listRustDesk, createRustDesk, updateRustDesk, deleteRustDesk, listRustDeskPeers, getRustDeskAgentKey,
  rotateRustDeskAgentKey, syncRustDeskNow, startRustDeskTest, getRustDeskTest, requestRustDeskPeerDelete,
  listRustDeskPeerDeletes,
  type RustDeskServer, type RustDeskServerWrite, type RustDeskPeer, type RustDeskTestState, type RustDeskKeyProblem,
  type RustDeskPeerDelete, type RustDeskPeerDeleteStatus,
} from "@/api/rustdesk";
import {
  RustDeskIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SaveIcon, CancelIcon, CopyIcon, TestIcon, SyncIcon,
  TerminalIcon, DevicesIcon, AuditIcon, ListIcon, SelectAllIcon,
} from "@/icons";
import { autoSort } from "@/composables/useTableSort";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { withExportValue } from "@/utils/tableExport";
import { deviceKindLabel, renderDeviceKind } from "@/utils/deviceKindCell";
import { apiErrMsg } from "@/api/client";
import { SUDO } from "@/utils/sudo";
import AgentTrustPicker from "@/components/AgentTrustPicker.vue";
import { renderTlsState, useAgentTrust } from "@/composables/useAgentTrust";

const { t, te } = useI18n();
const msg = useMessage();
const router = useRouter();
const route = useRoute();

type Tab = "servers" | "devices" | "audit";
const TABS: Tab[] = ["servers", "devices", "audit"];
// 告警通知帶 ?tab=audit 進來
const tab = ref<Tab>(TABS.includes(route.query.tab as Tab) ? (route.query.tab as Tab) : "servers");
watch(tab, (v) => { void router.replace({ query: { ...route.query, tab: v === "servers" ? undefined : v } }); });

const COLS = ["name", "agent", "agent_hostname", "agent_source_ip", "agent_version", "agent_tls", "enabled", "server_version",
  "public_key", "devices", "db", "reports", "last_report_at", "last_error", "actions"];
const { visibleKeys: vis, setVisible: setVis, reset: resetVis, order, setOrder, orderColumns } =
  useColumnPrefs("rustdesk", COLS, COLS);
const picker = computed(() => [
  { key: "name", label: t("cols.name") },
  { key: "agent", label: t("rustdesk.col_agent_state") },
  { key: "agent_hostname", label: t("rustdesk.col_agent_host") },
  { key: "agent_source_ip", label: t("rustdesk.col_agent_ip") },
  { key: "agent_version", label: t("rustdesk.col_agent_version") },
  { key: "agent_tls", label: t("certTrust.col") },
  { key: "enabled", label: t("cols.status") },
  { key: "server_version", label: t("rustdesk.col_hbbs_version") },
  { key: "public_key", label: t("rustdesk.public_key") },
  { key: "devices", label: t("rustdesk.devices") },
  { key: "db", label: t("rustdesk.db") },
  { key: "reports", label: t("rustdesk.reports") },
  { key: "last_report_at", label: t("rustdesk.last_report") },
  { key: "last_error", label: t("cols.last_error") },
  { key: "actions", label: t("cols.actions") },
]);

const rows = ref<RustDeskServer[]>([]);
const loading = ref(false);
const show = ref(false);
const editing = ref<RustDeskServer | null>(null);

function blankForm() {
  return { name: "", enabled: true, report_interval_seconds: 300, client_address: "", description: "",
           receive_reports: true, api_port: 21114,
           web_enabled: false, hbbs_host: "", relay_host: "", transport: "tcp" as "tcp" | "ws",
           allow_peer_delete: false,
           web_file_transfer: false, web_file_max_file_mb: 2048, web_file_max_total_mb: 10240 };
}
const transportOptions = [
  { label: "TCP (21116/21117)", value: "tcp" },
  { label: "WebSocket (21118/21119)", value: "ws" },
];
const form = ref(blankForm());

async function loadServers() {
  const srv = await listRustDesk();
  rows.value = srv.items;
  if (!selected.value || !rows.value.some((r) => r.id === selected.value)) {
    selected.value = rows.value[0]?.id ?? null;
  }
}
async function refresh() {
  loading.value = true;
  try {
    await loadServers();
    await loadPeers();
    auditRef.value?.reload();
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}

function openCreate() {
  editing.value = null;
  form.value = blankForm();
  show.value = true;
}

function openEdit(r: RustDeskServer) {
  editing.value = r;
  form.value = {
    name: r.name, enabled: r.enabled, report_interval_seconds: r.report_interval_seconds,
    client_address: r.client_address ?? "", description: r.description ?? "",
    receive_reports: r.receive_reports, api_port: r.api_port,
    web_enabled: r.web_enabled, hbbs_host: r.hbbs_host ?? "", relay_host: r.relay_host ?? "",
    transport: r.transport === "ws" ? "ws" : "tcp",
    allow_peer_delete: !!r.allow_peer_delete,
    web_file_transfer: !!r.web_file_transfer,
    web_file_max_file_mb: r.web_file_max_file_mb ?? 2048,
    web_file_max_total_mb: r.web_file_max_total_mb ?? 10240,
  };
  show.value = true;
}

async function submit() {
  const f = form.value;
  const payload: RustDeskServerWrite = {
    name: f.name, enabled: f.enabled, report_interval_seconds: f.report_interval_seconds,
    client_address: f.client_address.trim() || null, description: f.description || null,
    receive_reports: f.receive_reports, api_port: f.api_port,
    web_enabled: f.web_enabled, hbbs_host: f.hbbs_host.trim() || null, relay_host: f.relay_host.trim() || null,
    transport: f.transport, allow_peer_delete: f.allow_peer_delete,
    web_file_transfer: f.web_file_transfer,
    web_file_max_file_mb: f.web_file_max_file_mb || 2048,
    web_file_max_total_mb: f.web_file_max_total_mb || 10240,
  };
  try {
    if (editing.value) {
      await updateRustDesk(editing.value.id, payload);
      show.value = false;
      msg.success(t("common.ok"));
      await refresh();
    } else {
      const created = await createRustDesk(payload);
      show.value = false;
      await refresh();
      // 新增完直接給安裝指令（金鑰只在這個回應裡出現一次，之後要看走 agent-key）
      openInstall(created, created.agent_key, true);
    }
  } catch (e) { msg.error(apiErrMsg(e)); }
}

async function del(id: string) {
  try { await deleteRustDesk(id); await refresh(); }
  catch (e) { msg.error(apiErrMsg(e)); }
}

async function copy(text: string) {
  try { await navigator.clipboard.writeText(text); msg.success(t("common.copied")); }
  catch { msg.error(t("common.fail")); }
}

// ── 代理狀態 ──
function agentState(r: RustDeskServer): "no_key" | "never" | "online" | "offline" {
  if (!r.has_agent_key) return "no_key";
  if (!r.agent_last_seen_at) return "never";
  // 用後端在回應當下的判斷：以前拿瀏覽器的「現在」去算載入時的舊資料，頁面開著超過一分鐘、一重繪就變「離線」，
  // 重新整理又「已連線」（使用者 2026-10-05）；瀏覽器時鐘不準也不再影響
  if (typeof r.agent_online === "boolean") return r.agent_online ? "online" : "offline";
  const age = Date.now() - new Date(r.agent_last_seen_at).getTime();
  return age <= r.agent_poll_seconds * 6 * 1000 ? "online" : "offline";
}
const AGENT_TAG = { no_key: "warning", never: "default", online: "success", offline: "error" } as const;

// ── 立即同步 ──
async function syncNow(r: RustDeskServer) {
  try {
    const res = await syncRustDeskNow(r.id);
    if (res.agent_online) msg.success(t("rustdesk.sync_queued", { n: res.eta_seconds }));
    else msg.warning(t("rustdesk.sync_queued_offline"));
    // 代理取走旗標後讀資料庫、回報，再更新畫面
    setTimeout(() => { void refresh(); }, (res.eta_seconds + 4) * 1000);
  } catch (e) { msg.error(apiErrMsg(e)); }
}

// ── 測試（代理在 RustDesk 主機上逐項檢查，結果下一次輪詢後送回）──
const TEST_WAIT_SECONDS = 60;
const testShow = ref(false);
const testServer = ref<RustDeskServer | null>(null);
const testState = ref<RustDeskTestState | null>(null);
const testTimedOut = ref(false);
let testTimer: ReturnType<typeof setTimeout> | undefined;
const testWaiting = computed(() => !!testState.value && !testState.value.result_at && !testTimedOut.value);
const testFailed = computed(() => (testState.value?.checks ?? []).filter((c) => !c.ok).length);

async function runTest(r: RustDeskServer) {
  clearTimeout(testTimer);
  testServer.value = r;
  testState.value = null;
  testTimedOut.value = false;
  testShow.value = true;
  try {
    testState.value = await startRustDeskTest(r.id);
  } catch (e) { msg.error(apiErrMsg(e)); testShow.value = false; return; }
  const started = Date.now();
  const tick = async () => {
    if (!testShow.value || !testState.value) return;
    try {
      const st = await getRustDeskTest(r.id);
      if (st.test_id === testState.value.test_id) testState.value = st;
    } catch { /* 下一輪再問 */ }
    if (testState.value?.result_at) { void loadServers(); return; }
    if (Date.now() - started > TEST_WAIT_SECONDS * 1000) { testTimedOut.value = true; return; }
    testTimer = setTimeout(tick, 2000);
  };
  testTimer = setTimeout(tick, 1500);
}
watch(testShow, (v) => { if (!v) clearTimeout(testTimer); });

function checkDetail(c: { key: string; detail: string | null }): string {
  if (c.key === "receiver" && c.detail === "off") return t("rustdesk.receiver_off");
  if (c.key === "peer_delete" && c.detail === "read-only (delete not enabled)") return t("rustdesk.check_peer_delete_off");
  return c.detail ?? "";
}

// ── 安裝指令／金鑰 ──
const installShow = ref(false);
const installServer = ref<RustDeskServer | null>(null);
const installKey = ref<string | null>(null);
const installJustCreated = ref(false);
const installNoKey = ref(false);
const serverOrigin = window.location.origin;
// sudo 只在非 root 時加（見 utils/sudo）；帶環境變數一定要透過 env，否則 root 時 VAR=val 會被當成指令
// 這台允許「刪除舊註冊」時，安裝指令帶 JT_RD_ALLOW_DELETE=1（主機端也同意，代理才寫得了 hbbs 的資料庫）
const installAllowDelete = computed(() => !!installLive.value?.allow_peer_delete);
// 代理怎麼確認連到的是真的 jt-ipam（2026-11-04，見 useAgentTrust）。安裝程式重跑會沿用舊設定，
// 所以「用系統 CA 驗證」要明講（explicitCa），舊的指紋才會被清掉
const trust = useAgentTrust({ explicitCa: true });
const installCmd = computed(() =>
  `curl ${trust.linux.value[0]} ${serverOrigin}/api/v1/rustdesk/agent/installer.sh | ${SUDO} env `
  + `JT_IPAM_URL=${serverOrigin} JT_IPAM_AGENT_KEY=${installKey.value ?? "<key>"}${trust.linux.value[1]} `
  + `${installAllowDelete.value ? "JT_RD_ALLOW_DELETE=1 " : ""}bash`);
// 已經裝好的主機：只開啟寫入（網址、金鑰與伺服器驗證沿用主機上現有的設定；下載安裝程式那一次照樣驗證）
const allowDeleteCmd = computed(() =>
  `curl ${trust.linux.value[0]} ${serverOrigin}/api/v1/rustdesk/agent/installer.sh | ${SUDO} env JT_RD_ALLOW_DELETE=1 bash`);
const uninstallCmd = computed(() =>
  `curl ${trust.linux.value[0]} ${serverOrigin}/api/v1/rustdesk/agent/installer.sh | ${SUDO} env JT_IPAM_UNINSTALL=1 bash`);
const PATHS = [
  ["path_program", "/opt/jt-ipam-rustdesk-agent/jt_ipam_rustdesk_agent.py"],
  ["path_config", "/etc/jt-ipam-rustdesk-agent.env"],
  ["path_service", "jt-ipam-rustdesk-agent.service"],
  ["path_log", "journalctl -u jt-ipam-rustdesk-agent -f"],
] as const;
// 安裝視窗開著時，每 5 秒更新一次代理狀態：裝好之後畫面上直接看得到「已連線」
let installPoll: ReturnType<typeof setInterval> | undefined;
const installLive = computed(() => rows.value.find((r) => r.id === installServer.value?.id) ?? installServer.value);

function openInstall(r: RustDeskServer, key: string | null = null, justCreated = false) {
  installServer.value = r;
  installKey.value = key;
  installJustCreated.value = justCreated;
  installNoKey.value = false;
  installShow.value = true;
  if (!key) {
    getRustDeskAgentKey(r.id).then((k) => { installKey.value = k; })
      .catch(() => { installNoKey.value = true; });
  }
}
watch(installShow, (v) => {
  clearInterval(installPoll);
  if (v) installPoll = setInterval(() => { void loadServers().catch(() => {}); }, 5000);
});
async function rotateKey() {
  if (!installServer.value) return;
  try {
    const r = await rotateRustDeskAgentKey(installServer.value.id);
    installKey.value = r.agent_key;
    installNoKey.value = false;
    msg.success(installJustCreated.value ? t("common.ok") : t("rustdesk.rotated"));
    await loadServers();
  } catch (e) { msg.error(apiErrMsg(e)); }
}

// 頁面開著時每 15 秒更新伺服器列（代理狀態、裝置數、Key 錯誤）；分頁在背景時不打
const liveRefresh = setInterval(() => {
  if (document.visibilityState === "visible" && tab.value === "servers") void loadServers().catch(() => {});
}, 15000);
onBeforeUnmount(() => {
  clearTimeout(testTimer); clearInterval(installPoll); clearInterval(liveRefresh); clearTimeout(delTimer);
});

function iconAction(icon: any, label: string, onClick: () => void, type?: any, testid?: string) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type, "aria-label": label, "data-testid": testid,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}

function agentCell(r: RustDeskServer) {
  const st = agentState(r);
  const tip: string[] = [];
  if (r.agent_last_seen_at) tip.push(t("rustdesk.agent_last_seen", { t: fmtDateTime(r.agent_last_seen_at) }));
  if (st === "never" || st === "no_key") tip.push(t("rustdesk.agent_install_hint"));
  if (r.agent_status?.data_dir) tip.push(r.agent_status.data_dir);
  if (r.allow_peer_delete && st !== "never" && st !== "no_key") tip.push(`${t("rustdesk.agent_cap")}：${capLabel(r)}`);
  return h(NTooltip, { disabled: !tip.length }, {
    trigger: () => h(NTag, { size: "small", type: AGENT_TAG[st], bordered: false, "data-testid": "rustdesk-agent-state" },
      () => t(`rustdesk.agent_${st}`)),
    default: () => tip.map((x) => h("div", null, x)),
  });
}

/** 代理回報的寫入能力（「刪除舊註冊」）：可以寫入／唯讀（原因）／尚未回報 */
function capLabel(r: RustDeskServer): string {
  const cap = r.agent_status?.capabilities;
  if (!cap) return t("rustdesk.agent_cap_unknown");
  if (cap.delete) return t("rustdesk.agent_cap_delete");
  return cap.delete_reason ? `${t("rustdesk.agent_cap_readonly")} · ${cap.delete_reason}` : t("rustdesk.agent_cap_readonly");
}

function agentOutdated(r: RustDeskServer): boolean {
  return !!(r.agent_version && r.agent_latest_version && r.agent_version !== r.agent_latest_version);
}

// 各欄排序用的值（未定義的排最後，由 autoSort 的比較函式處理 null）
const AGENT_RANK = { online: 0, offline: 1, never: 2, no_key: 3 } as const;
function byValue<T>(get: (r: T) => string | number | null | undefined) {
  return (a: T, b: T) => {
    const av = get(a), bv = get(b);
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    return typeof av === "number" && typeof bv === "number" ? av - bv
      : String(av).localeCompare(String(bv), undefined, { numeric: true });
  };
}
const ts = (v: string | null | undefined) => (v ? Date.parse(v) || null : null);

function reportsCell(r: RustDeskServer) {
  if (!r.receive_reports) return h("span", { style: "opacity:.6" }, t("common.disabled"));
  const rcv = r.agent_status?.receiver;
  const dropped = Object.entries(r.events_dropped || {}).filter(([, n]) => n > 0);
  let state;
  if (rcv?.error) {
    state = h(NTag, { size: "tiny", type: "error", bordered: false }, () => t("rustdesk.receiver_error"));
  } else if (rcv?.listening) {
    state = h(NTag, { size: "tiny", type: "success", bordered: false }, () => t("rustdesk.receiver_on", { port: rcv.port }));
  } else {
    state = h(NTag, { size: "tiny", bordered: false }, () => t("rustdesk.receiver_wait"));
  }
  return h(NTooltip, null, {
    trigger: () => h("div", { "data-testid": "rustdesk-reports" }, [
      state,
      h("div", { style: "font-size:11.5px;opacity:.65;margin-top:2px" },
        (r.last_events_at ? fmtDateTime(r.last_events_at) : t("rustdesk.reports_none"))
        + (dropped.length ? ` · ${t("rustdesk.reports_dropped", { n: dropped.reduce((a, [, n]) => a + n, 0) })}` : "")),
    ]),
    default: () => [
      rcv?.error ? h("div", null, rcv.error) : null,
      ...dropped.map(([k, n]) => h("div", null, `${te(`rustdesk.drop_${k}`) ? t(`rustdesk.drop_${k}`) : k}：${n}`)),
      h("div", null, t("rustdesk.reports_hint", { port: r.api_port })),
    ],
  });
}

const allCols = computed<DataTableColumns<RustDeskServer>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 130, width: 140, ellipsis: { tooltip: true } },
  withExportValue({
    title: t("rustdesk.col_agent_state"), key: "agent", width: 110,
    sorter: byValue((r: RustDeskServer) => AGENT_RANK[agentState(r)]),
    render: (r: RustDeskServer) => agentCell(r),
  }, (r: RustDeskServer) => t(`rustdesk.agent_${agentState(r)}`)),
  { title: t("rustdesk.col_agent_host"), key: "agent_hostname", width: 140, ellipsis: { tooltip: true },
    render: (r) => r.agent_hostname ?? "—" },
  { title: t("rustdesk.col_agent_ip"), key: "agent_source_ip", width: 140,
    sorter: byValue((r: RustDeskServer) => r.agent_source_ip),
    render: (r) => h("span", { class: "mono" }, r.agent_source_ip ?? "—") },
  {
    title: t("rustdesk.col_agent_version"), key: "agent_version", width: 110,
    sorter: byValue((r: RustDeskServer) => r.agent_version),
    render: (r) => {
      if (!r.agent_version) return "—";
      if (!agentOutdated(r)) return r.agent_version;
      return h(NTooltip, null, {
        trigger: () => h(NTag, { size: "small", type: "warning", bordered: false }, () => r.agent_version),
        default: () => t("rustdesk.agent_outdated", { v: r.agent_latest_version }),
      });
    },
  },
  {
    title: t("certTrust.col"), key: "agent_tls", width: 130,
    sorter: byValue((r: RustDeskServer) => r.agent_tls_mode ?? ""),
    render: (r) => renderTlsState(!!r.agent_last_seen_at, r.agent_tls_mode, "rustdesk-agent-tls",
      (st) => (st === "pinned" || st === "ca" ? t(`certTrust.hint_${st}`) : t(`rustdesk.trust_fix_${st}`)), t),
  },
  {
    title: t("common.status"), key: "enabled", width: 90,
    sorter: byValue((r: RustDeskServer) => (r.enabled ? 0 : 1)),
    render: (r) => h(NTag, { type: r.enabled ? "success" : "default", size: "small" },
      () => r.enabled ? t("common.enabled") : t("common.disabled")),
  },
  { title: t("rustdesk.col_hbbs_version"), key: "server_version", width: 110,
    sorter: byValue((r: RustDeskServer) => r.server_version),
    render: (r) => r.server_version ?? "—" },
  {
    // 表格裡只預覽前 5 個字（使用者 2026-10-05）；完整的在提示裡，複製鈕複製完整公鑰
    title: t("rustdesk.public_key"), key: "public_key", width: 110,
    render: (r) => r.public_key
      ? h("span", { class: "rd-nowrap" }, [
          h(NTooltip, null, { trigger: () => h("span", { class: "rd-key", "data-testid": "rustdesk-key-preview" },
                                               `${r.public_key!.slice(0, 5)}…`),
                              default: () => r.public_key }),
          h(CopyButton, { text: r.public_key!, label: t("rustdesk.copy_key") })])
      : "—",
  },
  withExportValue({
    title: t("rustdesk.devices"), key: "devices", width: 200,
    sorter: byValue((r: RustDeskServer) => r.last_summary?.peers ?? null),
    render: (r: RustDeskServer) => {
      const sm = r.last_summary;
      if (!sm || sm.peers == null) return "—";
      return h(NSpace, { size: 4, wrap: false }, () => [
        h(NTag, { size: "tiny", bordered: false }, () => t("rustdesk.n_total", { n: sm.peers })),
        h(NTag, { size: "tiny", type: "success", bordered: false }, () => t("rustdesk.n_online", { n: sm.online ?? 0 })),
        h(NTag, { size: "tiny", type: "info", bordered: false }, () => t("rustdesk.n_matched", { n: sm.matched ?? 0 })),
        r.key_problems ? h(NTag, {
          size: "tiny", type: "error", bordered: false, style: "cursor: pointer", "data-testid": "rustdesk-key-problems",
          title: t("rustdesk.key_problems_hint"),
          onClick: () => { selected.value = r.id; keyFilter.value = true; tab.value = "devices"; },
        }, () => t("rustdesk.n_key_problems", { n: r.key_problems })) : null,
      ]);
    },
  }, (r: RustDeskServer) => r.last_summary?.peers ?? null),
  withExportValue({
    title: t("rustdesk.db"), key: "db", width: 100,
    sorter: byValue((r: RustDeskServer) => (r.file_status?.db ? (r.file_status.db.ok ? 0 : 1) : null)),
    render: (r: RustDeskServer) => {
      const st = r.file_status?.db;
      if (!st) return "—";
      return h(NTooltip, null, {
        trigger: () => h(NTag, { size: "tiny", type: st.ok ? "success" : "error", bordered: false,
                                 "data-testid": "rustdesk-db" }, () => (st.ok ? "✓" : "✗")),
        default: () => st.ok ? st.path : `${st.path}: ${st.error ?? ""}`,
      });
    },
  }, (r: RustDeskServer) => (r.file_status?.db ? (r.file_status.db.ok ? "OK" : r.file_status.db.error) : null)),
  withExportValue({ title: t("rustdesk.reports"), key: "reports", width: 190,
                    sorter: byValue((r: RustDeskServer) => (r.receive_reports ? ts(r.last_events_at) ?? 0 : null)),
                    render: (r: RustDeskServer) => reportsCell(r) },
    (r: RustDeskServer) => r.last_events_at),
  { title: t("rustdesk.last_report"), key: "last_report_at", width: 160,
    sorter: byValue((r: RustDeskServer) => ts(r.last_report_at)),
    render: (r) => fmtDateTime(r.last_report_at) },
  {
    title: t("cols.last_error"), key: "last_error", minWidth: 160, width: 180,
    ellipsis: { tooltip: true }, render: (r) => r.last_error ?? "—",
  },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 214, fixed: "right",
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openEdit(r), undefined, "rustdesk-edit"),
      iconAction(TestIcon, t("common.test"), () => runTest(r), undefined, "rustdesk-test"),
      iconAction(SyncIcon, t("rustdesk.sync_now"), () => syncNow(r), "primary", "rustdesk-sync"),
      iconAction(TerminalIcon, t("rustdesk.install"), () => openInstall(r), undefined, "rustdesk-install"),
      h(NPopconfirm, { onPositiveClick: () => del(r.id) }, {
        trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
        default: () => t("rustdesk.confirm_delete"),
      }),
    ]),
  },
]));
const cols = computed<DataTableColumns<RustDeskServer>>(() =>
  orderColumns(allCols.value.filter((c: any) => vis.value.includes(c.key))),
);
const colsWidth = computed(() => widthOf(cols.value));

/** 表格寬度＝看得到的欄位寬度總和（欄位挑選後跟著變，不留一大片空白也不擠） */
function widthOf(cols: any[]): number {
  return cols.reduce((sum, c) => sum + (Number(c.width) || Number(c.minWidth) || 120), 0);
}

// ── 裝置清單（伺服器端分頁、搜尋與排序：裝置可能上萬台，只排畫面上這一頁沒有意義）──
const selected = ref<string | null>(null);
const selectedServer = computed(() => rows.value.find((r) => r.id === selected.value) ?? null);
const auditRef = ref<InstanceType<typeof RustDeskAuditTable> | null>(null);
const peers = ref<RustDeskPeer[]>([]);
const peerTotal = ref(0);
const peerLoading = ref(false);
const q = ref("");
// never＝jt-ipam 從沒看過它上線（「刪除舊註冊」最常挑的那一群）
const onlineFilter = ref<"all" | "online" | "offline" | "never">("all");
const matchFilter = ref<string | null>(null);
// 只看 Key 設錯的裝置（伺服器列的「Key 錯誤 N」點下去就是這個篩選）
const keyFilter = ref<boolean>(false);
const page = ref(1);
const pageSize = ref(50);
const peerSortKey = ref<string | null>(null);
const peerSortOrder = ref<"ascend" | "descend" | false>(false);

const PEER_COLS = ["rustdesk_id", "online", "last_online_at", "last_heartbeat_at", "hostname", "username", "os_name",
  "client_version", "registered_ip", "address_ip", "address_hostname", "address_device_kind",
  "match_evidence", "first_registered_at"];
const PEER_DEFAULT = PEER_COLS.filter((k) => !["last_heartbeat_at", "client_version", "first_registered_at"].includes(k));
const peerPrefs = useColumnPrefs("rustdesk_peers", PEER_COLS, PEER_DEFAULT);
// 後端接受排序的欄位（其餘不給排序箭頭）
const PEER_SORTABLE = new Set(PEER_COLS.filter((k) => k !== "match_evidence"));
const peerPicker = computed(() => [
  { key: "rustdesk_id", label: t("rustdesk.id") },
  { key: "online", label: t("common.status") },
  { key: "last_online_at", label: t("rustdesk.last_online") },
  { key: "last_heartbeat_at", label: t("rustdesk.col_last_heartbeat") },
  { key: "hostname", label: t("rustdesk.col_reported_hostname") },
  { key: "username", label: t("rustdesk.col_username") },
  { key: "os_name", label: t("rustdesk.col_os") },
  { key: "client_version", label: t("rustdesk.col_client_version") },
  { key: "registered_ip", label: t("rustdesk.col_reg_report_ip") },
  { key: "address_ip", label: t("rustdesk.mapped_ip") },
  { key: "address_hostname", label: t("rustdesk.col_ip_hostname") },
  { key: "address_device_kind", label: t("cols.device_kind") },
  { key: "match_evidence", label: t("rustdesk.col_evidence") },
  { key: "first_registered_at", label: t("rustdesk.first_registered") },
]);

const serverOptions = computed(() => rows.value.map((r) => ({ label: r.name, value: r.id })));
const onlineOptions = computed(() => [
  { label: t("rustdesk.all"), value: "all" },
  { label: t("rustdesk.online"), value: "online" },
  { label: t("rustdesk.offline"), value: "offline" },
  { label: t("rustdesk.never_online"), value: "never" },
]);
const MATCH = ["matched", "conflict", "device_changed", "hostname_only", "not_seen_online", "stale", "shared", "ambiguous",
  "unmanaged", "no_ip"] as const;
const matchOptions = computed(() => MATCH.map((m) => ({ label: t(`rustdesk.match_${m}`), value: m })));

function peerParams() {
  return {
    q: q.value.trim() || undefined,
    online: onlineFilter.value === "online" ? true : onlineFilter.value === "offline" ? false : undefined,
    never_online: onlineFilter.value === "never" || undefined,
    match_status: matchFilter.value || undefined,
    key_problem: keyFilter.value || undefined,
    sort: peerSortKey.value && peerSortOrder.value ? peerSortKey.value : undefined,
    order: peerSortOrder.value === "descend" ? ("desc" as const) : ("asc" as const),
  };
}
async function loadPeers() {
  if (!selected.value) { peers.value = []; peerTotal.value = 0; return; }
  peerLoading.value = true;
  try {
    const r = await listRustDeskPeers(selected.value, { ...peerParams(), page: page.value, page_size: pageSize.value });
    peers.value = r.items;
    peerTotal.value = r.total;
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { peerLoading.value = false; }
}
// 匯出要整份（照目前的篩選與排序），不是畫面上這一頁
async function fetchAllPeers(): Promise<RustDeskPeer[]> {
  if (!selected.value) return [];
  const all: RustDeskPeer[] = [];
  for (let p = 1; p <= 200; p++) {
    const r = await listRustDeskPeers(selected.value, { ...peerParams(), page: p, page_size: 500 });
    all.push(...r.items);
    if (r.items.length < 500 || all.length >= r.total) break;
  }
  return all;
}
/** NDataTable 的 @update:sorter：排序交給後端，回到第一頁重抓 */
function onPeerSorter(st: DataTableSortState | DataTableSortState[] | null) {
  const one = Array.isArray(st) ? st[0] : st;
  peerSortKey.value = one && one.order ? String(one.columnKey) : null;
  peerSortOrder.value = one ? one.order : false;
  page.value = 1;
  void loadPeers();
}
let qTimer: ReturnType<typeof setTimeout> | undefined;
watch(q, () => { clearTimeout(qTimer); qTimer = setTimeout(() => { page.value = 1; void loadPeers(); }, 300); });
watch([selected, onlineFilter, matchFilter, keyFilter], () => { page.value = 1; void loadPeers(); });

const pagination = computed(() => ({
  page: page.value, pageSize: pageSize.value, itemCount: peerTotal.value,
  showSizePicker: true, pageSizes: [50, 100, 200],
  prefix: () => t("common.total_n", { n: peerTotal.value }),
  onUpdatePage: (p: number) => { page.value = p; void loadPeers(); },
  onUpdatePageSize: (sz: number) => { pageSize.value = sz; page.value = 1; void loadPeers(); },
}));

const matchType: Record<string, "success" | "warning" | "default" | "info" | "error"> = {
  matched: "success", conflict: "error", device_changed: "warning", hostname_only: "info", shared: "warning", ambiguous: "warning", stale: "default",
  not_seen_online: "default", unmanaged: "info", no_ip: "default",
};
const dash = (v: string | null | undefined) => v || "—";
const mono = (v: string | null | undefined) => (v ? h("span", { class: "mono" }, v) : "—");
/** 「Key 錯誤」標籤：Key 錯的客戶端照樣註冊、回報、區網直連，只有走中繼被拒（網頁連線一定走中繼） */
function keyProblemTag(kp: RustDeskKeyProblem) {
  return h(NTooltip, { style: "max-width: 360px" }, {
    trigger: () => h(NTag, { size: "small", type: "error", bordered: false,
                             "data-testid": "rustdesk-key-problem" }, () => t("rustdesk.key_problem")),
    default: () => t(kp.scope === "hbbs" ? "rustdesk.key_problem_hint_hbbs" : "rustdesk.key_problem_hint",
                     { at: fmtDateTime(kp.at), n: kp.count }),
  });
}
const peerColsAll = computed<DataTableColumns<RustDeskPeer>>(() => [
  {
    title: t("rustdesk.id"), key: "rustdesk_id", width: 160,
    render: (p) => h("span", { class: "rd-nowrap" }, [
      h("span", { class: "rd-id" }, p.rustdesk_id), h(CopyButton, { text: p.rustdesk_id, label: t("rustdesk.copy_id") })]),
  },
  withExportValue({
    // 窄欄（使用者 2026-10-05：「狀態欄位過寬」）；有 Key 錯誤時標籤放第二行，不撐寬整欄
    title: t("common.status"), key: "online", width: 120,
    // 表格裡用 render 函式畫的元素吃不到 scoped 樣式，直接寫 inline
    render: (p: RustDeskPeer) => h("div", { style: "display:flex;flex-direction:column;align-items:flex-start;gap:3px" }, [
      h(NTag, { size: "small", type: p.online ? "success" : "default", bordered: false },
        () => (p.online ? t("rustdesk.online") : t("rustdesk.offline"))),
      // 對應狀態併進同一欄（使用者 2026-10-05）
      h(NTooltip, null, {
        trigger: () => h(NTag, { size: "small", bordered: false, type: matchType[p.match_status] ?? "default",
                                 "data-testid": "rustdesk-match" }, () => t(`rustdesk.match_${p.match_status}`)),
        default: () => t(`rustdesk.match_${p.match_status}_hint`),
      }),
      p.key_problem ? keyProblemTag(p.key_problem) : null,
    ]),
  }, (p: RustDeskPeer) => [p.online ? t("rustdesk.online") : t("rustdesk.offline"), t(`rustdesk.match_${p.match_status}`),
    p.key_problem ? t("rustdesk.key_problem") : ""].filter(Boolean).join(" / ")),
  { title: t("rustdesk.last_online"), key: "last_online_at", width: 160, render: (p) => fmtDateTime(p.last_online_at) },
  { title: t("rustdesk.col_last_heartbeat"), key: "last_heartbeat_at", width: 160,
    render: (p) => fmtDateTime(p.last_heartbeat_at) },
  { title: t("rustdesk.col_reported_hostname"), key: "hostname", width: 160, ellipsis: { tooltip: true },
    render: (p) => dash(p.hostname) },
  { title: t("rustdesk.col_username"), key: "username", width: 130, ellipsis: { tooltip: true },
    render: (p) => dash(p.username) },
  // 客戶端回報的是「windows / Windows 10 Pro - 10 (19045)」：完整版本放第一行（不截斷），平台用小字放第二行
  withExportValue({
    title: t("rustdesk.col_os"), key: "os_name", width: 220,
    render: (p: RustDeskPeer) => {
      if (!p.os_name) return "—";
      const [detail, plat] = rustdeskOs(p.os_name);
      return plat
        ? h("div", { style: "line-height:1.35" }, [h("div", detail), h("div", { style: "font-size:11px;opacity:.6" }, plat)])
        : detail;
    },
  }, (p: RustDeskPeer) => p.os_name || ""),
  { title: t("rustdesk.col_client_version"), key: "client_version", width: 110, render: (p) => dash(p.client_version) },
  // 登記 IP／回報 IP 同一欄上下兩行（使用者 2026-10-05）；排序照登記 IP
  withExportValue({
    title: t("rustdesk.col_reg_report_ip"), key: "registered_ip", width: 170,
    render: (p: RustDeskPeer) => h("div", { style: "line-height:1.45" }, [
      h("div", [h("span", { style: "font-size:11px;opacity:.6;margin-right:6px" }, t("rustdesk.ip_reg_short")),
                mono(p.registered_ip)]),
      h("div", [h("span", { style: "font-size:11px;opacity:.6;margin-right:6px" }, t("rustdesk.ip_rep_short")),
                mono(p.report_ip)]),
    ]),
  }, (p: RustDeskPeer) => `${p.registered_ip || ""} / ${p.report_ip || ""}`),
  withExportValue({
    title: t("rustdesk.mapped_ip"), key: "address_ip", width: 140,
    render: (p: RustDeskPeer) => p.address_id
      ? h(NButton, { text: true, type: "primary", class: "mono",
                     onClick: () => router.push({ name: "address-detail", params: { id: p.address_id! } }) },
          () => p.address_ip)
      : "—",
  }, (p: RustDeskPeer) => p.address_ip),
  { title: t("rustdesk.col_ip_hostname"), key: "address_hostname", width: 180, ellipsis: { tooltip: true },
    render: (p) => dash(p.address_hostname) },
  // 對應 IP 的設備類型（與其他 IP 清單同一個顯示方式；伺服器端排序）
  withExportValue({
    title: t("cols.device_kind"), key: "address_device_kind", width: 150,
    render: (p: RustDeskPeer) => p.address_id
      ? renderDeviceKind({ device_kind: p.address_device_kind, device_model: p.address_device_model }, t, te)
      : "—",
  }, (p: RustDeskPeer) => deviceKindLabel(p.address_device_kind, t, te)),
  withExportValue({
    title: t("rustdesk.col_evidence"), key: "match_evidence", width: 200,
    render: (p: RustDeskPeer) => {
      const parts: any[] = [];
      if (p.match_evidence?.length) {
        parts.push(h("div", { "data-testid": "rustdesk-evidence" }, p.match_evidence.map((e) => t(`rustdesk.ev_${e}`)).join(" + ")));
      }
      if (p.candidate_address_id) {
        parts.push(h(NTooltip, { disabled: !p.candidate_hostname }, {
          trigger: () => h(NButton, { text: true, size: "tiny", type: "primary", "data-testid": "rustdesk-candidate",
                                      onClick: () => router.push({ name: "address-detail", params: { id: p.candidate_address_id! } }) },
            () => t("rustdesk.candidate", { ip: p.candidate_ip })),
          default: () => t("rustdesk.candidate_tip", { host: p.candidate_hostname ?? "" }),
        }));
      }
      return parts.length ? h("div", null, parts) : "—";
    },
  }, (p: RustDeskPeer) => [(p.match_evidence ?? []).map((e) => t(`rustdesk.ev_${e}`)).join(" + "),
                           p.candidate_ip ? t("rustdesk.candidate", { ip: p.candidate_ip }) : ""]
    .filter(Boolean).join("; ")),
  { title: t("rustdesk.first_registered"), key: "first_registered_at", width: 160,
    render: (p) => fmtDateTime(p.first_registered_at) },
]);
const peerColsShown = computed<DataTableColumns<RustDeskPeer>>(() => peerPrefs.orderColumns(peerColsAll.value
  .filter((c: any) => peerPrefs.visibleKeys.value.includes(c.key)))
  .map((c: any) => (PEER_SORTABLE.has(c.key)
    ? { ...c, sorter: true, sortOrder: peerSortKey.value === c.key ? peerSortOrder.value : false }
    : c)));
// 勾選欄只在這台允許「刪除舊註冊」時出現；上線中的不能勾（代理刪之前也會再查一次）。匯出用不含勾選欄的那份
const canSelectForDelete = computed(() => !!selectedServer.value?.allow_peer_delete);
const peerCols = computed<DataTableColumns<RustDeskPeer>>(() => (canSelectForDelete.value
  ? [{ type: "selection", disabled: (p: RustDeskPeer) => p.online, width: 40 } as any, ...peerColsShown.value]
  : peerColsShown.value));
const peerColsWidth = computed(() => widthOf(peerCols.value));

// ── 刪除舊註冊（網頁上允許＋主機端以 --allow-delete 安裝的代理；代理刪之前再查一次線上狀態，上線中的略過）──
const DELETE_MAX = 500;
const checkedIds = ref<string[]>([]);
/** 不能刪的原因（null＝可以）：按鈕反灰時放在提示裡 */
const deleteBlocker = computed<string | null>(() => {
  const s = selectedServer.value;
  if (!s || !s.allow_peer_delete) return t("rustdesk.del_off");
  if (!s.enabled) return t("rustdesk.del_disabled");
  const cap = s.agent_status?.capabilities;
  if (!cap) return t("rustdesk.del_agent_unknown");
  if (!cap.delete) return t("rustdesk.del_agent_cannot", { reason: cap.delete_reason ?? "" });
  return null;
});
const deleteTip = computed(() => deleteBlocker.value
  ?? (checkedIds.value.length ? t("rustdesk.del_tip", { n: checkedIds.value.length }) : t("rustdesk.del_pick")));
function onChecked(keys: DataTableRowKey[]) {
  const ids = keys.map(String);
  if (ids.length > DELETE_MAX) msg.warning(t("rustdesk.del_capped", { n: DELETE_MAX }));
  checkedIds.value = ids.slice(0, DELETE_MAX);
}
/** 照目前的篩選把離線的裝置全部勾起來（跨頁，最多 DELETE_MAX 個）；搭配「從未上線」篩選最常用 */
const selectingAll = ref(false);
async function selectAllMatching() {
  if (!selected.value) return;
  selectingAll.value = true;
  try {
    const ids = new Set(checkedIds.value);
    let more = false;
    for (let p = 1; p <= 20 && ids.size < DELETE_MAX; p++) {
      const r = await listRustDeskPeers(selected.value, { ...peerParams(), online: false, page: p, page_size: 500 });
      for (const x of r.items) {
        if (ids.size >= DELETE_MAX) { more = true; break; }
        ids.add(x.rustdesk_id);
      }
      if (r.items.length < 500) break;
    }
    if (more) msg.warning(t("rustdesk.del_capped", { n: DELETE_MAX }));
    checkedIds.value = [...ids];
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { selectingAll.value = false; }
}

const delShow = ref(false);
const delBusy = ref(false);
/** 這一批的結果（等代理處理時每 3 秒更新；全部有結果就停） */
const delSummary = ref<Record<RustDeskPeerDeleteStatus, number> | null>(null);
let delTimer: ReturnType<typeof setTimeout> | undefined;
const DEL_WAIT_MS = 120_000;
watch(selected, () => { checkedIds.value = []; delSummary.value = null; clearTimeout(delTimer); });

function countStatuses(rows: RustDeskPeerDelete[]): Record<RustDeskPeerDeleteStatus, number> {
  const out = { pending: 0, deleted: 0, skipped_online: 0, not_found: 0, failed: 0, cancelled: 0 };
  for (const r of rows) out[r.status] += 1;
  return out;
}
function summaryText(c: Record<RustDeskPeerDeleteStatus, number>): string {
  return t("rustdesk.del_summary", { deleted: c.deleted, skipped: c.skipped_online, not_found: c.not_found,
                                      failed: c.failed + c.cancelled })
    + (c.pending ? t("rustdesk.del_summary_pending", { n: c.pending }) : "");
}

async function confirmDelete() {
  const sid = selected.value;
  if (!sid || !checkedIds.value.length) return;
  const ids = [...checkedIds.value];
  delBusy.value = true;
  try {
    const r = await requestRustDeskPeerDelete(sid, ids);
    delShow.value = false;
    checkedIds.value = [];
    const n = r.queued + r.already_pending;
    if (r.agent_online) msg.info(t("rustdesk.del_queued", { n, s: r.eta_seconds }));
    else msg.warning(t("rustdesk.del_queued_offline", { n }));
    watchDeleteResults(sid, r.requested_at, new Set(ids));
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { delBusy.value = false; }
}

function watchDeleteResults(sid: string, since: string, ids: Set<string>) {
  clearTimeout(delTimer);
  const started = Date.now();
  const tick = async () => {
    if (selected.value !== sid) return;
    try {
      const r = await listRustDeskPeerDeletes(sid, { since, page_size: 500 });
      const c = countStatuses(r.items.filter((x) => ids.has(x.rustdesk_id)));
      delSummary.value = c;
      if (!c.pending) {
        const text = summaryText(c);
        if (c.failed || c.cancelled) msg.warning(text); else msg.success(text);
        await refresh();
        return;
      }
    } catch { /* 下一輪再問 */ }
    if (Date.now() - started > DEL_WAIT_MS) { msg.warning(t("rustdesk.del_slow")); return; }
    delTimer = setTimeout(tick, 3000);
  };
  delTimer = setTimeout(tick, 2000);
}

// 刪除紀錄（最近的請求與結果，新的在前；伺服器端分頁）
const logShow = ref(false);
const logRows = ref<RustDeskPeerDelete[]>([]);
const logTotal = ref(0);
const logPage = ref(1);
const logLoading = ref(false);
const DEL_TAG: Record<RustDeskPeerDeleteStatus, "default" | "success" | "warning" | "error" | "info"> = {
  pending: "info", deleted: "success", skipped_online: "warning", not_found: "default", failed: "error",
  cancelled: "default",
};
async function loadDeleteLog() {
  if (!selected.value) return;
  logLoading.value = true;
  try {
    const r = await listRustDeskPeerDeletes(selected.value, { page: logPage.value, page_size: 50 });
    logRows.value = r.items;
    logTotal.value = r.total;
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { logLoading.value = false; }
}
function openDeleteLog() { logPage.value = 1; logShow.value = true; void loadDeleteLog(); }
const logPagination = computed(() => ({
  page: logPage.value, pageSize: 50, itemCount: logTotal.value,
  prefix: () => t("common.total_n", { n: logTotal.value }),
  onUpdatePage: (p: number) => { logPage.value = p; void loadDeleteLog(); },
}));
const logCols = computed<DataTableColumns<RustDeskPeerDelete>>(() => [
  { title: t("rustdesk.del_requested_at"), key: "requested_at", width: 160, render: (r) => fmtDateTime(r.requested_at) },
  { title: t("rustdesk.id"), key: "rustdesk_id", width: 130, render: (r) => h("span", { class: "rd-id" }, r.rustdesk_id) },
  { title: t("rustdesk.del_result"), key: "status", width: 130,
    render: (r) => h(NTag, { size: "small", bordered: false, type: DEL_TAG[r.status] ?? "default" },
      () => t(`rustdesk.del_status_${r.status}`)) },
  { title: t("rustdesk.del_detail"), key: "detail", minWidth: 160, ellipsis: { tooltip: true }, render: (r) => r.detail || "—" },
  { title: t("rustdesk.del_requested_by"), key: "requested_by_name", width: 120, render: (r) => r.requested_by_name || "—" },
  { title: t("rustdesk.del_finished_at"), key: "finished_at", width: 160, render: (r) => fmtDateTime(r.finished_at) },
]);

const devicesTabLabel = computed(() => {
  const n = selectedServer.value?.last_summary?.peers;
  return n == null ? t("rustdesk.devices") : `${t("rustdesk.devices")} (${n})`;
});

onMounted(() => { void refresh(); void trust.load(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><RustDeskIcon /></n-icon>
        <span>{{ t("rustdesk.page_title") }}</span>
      </n-space>
    </template>

    <n-tabs v-model:value="tab" type="line" data-testid="rustdesk-tabs">
      <n-tab-pane name="servers">
        <template #tab>
          <span class="rd-tab"><n-icon :size="16"><RustDeskIcon /></n-icon>{{ t("rustdesk.title") }}</span>
        </template>
        <n-alert type="info" :bordered="false" style="margin-bottom: 12px">
          <div>{{ t("rustdesk.intro") }}</div>
          <ol class="rd-steps">
            <li>{{ t("rustdesk.step_create") }}</li>
            <li>{{ t("rustdesk.step_install") }} <code>/var/lib/rustdesk-server</code></li>
            <li>{{ t("rustdesk.step_test") }}</li>
            <li>{{ t("rustdesk.step_reports", { port: 21114 }) }}</li>
          </ol>
          <div class="rd-note">{{ t("rustdesk.privacy") }}</div>
        </n-alert>

        <n-space style="margin-bottom: 12px">
          <n-button @click="refresh" :loading="loading">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>
            {{ t("common.refresh") }}
          </n-button>
          <n-button type="primary" data-testid="rustdesk-create" @click="openCreate">
            <template #icon><n-icon><PlusIcon /></n-icon></template>
            {{ t("common.create") }}
          </n-button>
          <ColumnPicker :all="picker" :visible="vis" @update:visible="setVis" @reset="resetVis"
                        :order="order" @update:order="setOrder" />
          <ExportButton :columns="cols" :rows="rows" filename="rustdesk-servers" :title="t('rustdesk.title')" />
        </n-space>

        <n-data-table :columns="cols" :data="rows" :loading="loading" :bordered="false" :scroll-x="colsWidth"
                      data-testid="rustdesk-servers" />
      </n-tab-pane>

      <n-tab-pane name="devices">
        <template #tab>
          <span class="rd-tab"><n-icon :size="16"><DevicesIcon /></n-icon>{{ devicesTabLabel }}</span>
        </template>
        <div data-testid="rustdesk-peers">
          <!-- 每個子元件都要有 key：NSpace 依位置把子元件包進外框，伺服器選單（v-if）等清單載入後才出現時，
               沒有 key 的同型元件會被當成同一個沿用，只更新動態的屬性 —— 上線篩選曾經因此拿著對應狀態的
               事件處理，選「離線」卻改到對應狀態（兩台以上伺服器時）。 -->
          <n-space style="margin-bottom: 10px" :wrap="true" align="center">
            <n-select v-if="rows.length > 1" key="server" v-model:value="selected" :options="serverOptions"
                      style="width: 200px" :placeholder="t('rustdesk.server_pick')" data-testid="rustdesk-peers-server" />
            <n-input key="q" v-model:value="q" clearable :placeholder="t('rustdesk.search_ph')" style="width: 240px"
                     data-testid="rustdesk-search" />
            <n-select key="online" v-model:value="onlineFilter" :options="onlineOptions" style="width: 150px"
                      data-testid="rustdesk-online-filter" />
            <n-select key="match" v-model:value="matchFilter" :options="matchOptions" clearable style="width: 180px"
                      :placeholder="t('rustdesk.match')" data-testid="rustdesk-match-filter" />
            <n-checkbox key="keyp" v-model:checked="keyFilter" data-testid="rustdesk-key-filter">{{ t("rustdesk.key_problem_only") }}</n-checkbox>
            <ColumnPicker key="cols" :all="peerPicker" :visible="peerPrefs.visibleKeys.value"
                          @update:visible="peerPrefs.setVisible" @reset="peerPrefs.reset"
                          :order="peerPrefs.order.value" @update:order="peerPrefs.setOrder" />
            <ExportButton key="export" :columns="peerColsShown" :rows="peers" :fetch-all="fetchAllPeers"
                          filename="rustdesk-devices" :title="t('rustdesk.devices')" />
            <!-- 刪除舊註冊：不能刪時按鈕反灰，提示說出原因（網頁沒開／代理沒有寫入權限…） -->
            <n-tooltip key="del" style="max-width: 420px">
              <template #trigger>
                <span>
                  <n-button type="error" secondary :disabled="!!deleteBlocker || !checkedIds.length"
                            data-testid="rustdesk-del-btn" @click="delShow = true">
                    <template #icon><n-icon><DeleteIcon /></n-icon></template>
                    {{ t("rustdesk.del_btn") }}{{ checkedIds.length ? ` (${checkedIds.length})` : "" }}
                  </n-button>
                </span>
              </template>
              {{ deleteTip }}
            </n-tooltip>
            <template v-if="canSelectForDelete">
              <n-button key="del-all" :loading="selectingAll" data-testid="rustdesk-del-select-all"
                        @click="selectAllMatching">
                <template #icon><n-icon><SelectAllIcon /></n-icon></template>{{ t("rustdesk.del_select_all") }}
              </n-button>
              <n-button v-if="checkedIds.length" key="del-clear" data-testid="rustdesk-del-clear"
                        @click="checkedIds = []">
                <template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("rustdesk.del_clear") }}
              </n-button>
              <n-button key="del-log" data-testid="rustdesk-del-log-btn" @click="openDeleteLog">
                <template #icon><n-icon><ListIcon /></n-icon></template>{{ t("rustdesk.del_log") }}
              </n-button>
            </template>
          </n-space>
          <n-alert v-if="delSummary" :type="delSummary.pending ? 'info' : (delSummary.failed || delSummary.cancelled) ? 'warning' : 'success'"
                   :bordered="false" closable style="margin-bottom: 10px" data-testid="rustdesk-del-summary"
                   @close="delSummary = null">
            <n-space align="center" :size="8">
              <n-spin v-if="delSummary.pending" key="spin" :size="14" />
              <span key="text">{{ summaryText(delSummary) }}</span>
              <n-button key="log" text type="primary" size="small" @click="openDeleteLog">{{ t("rustdesk.del_log") }}</n-button>
            </n-space>
          </n-alert>
          <n-text v-if="!peerTotal && !peerLoading" depth="3" style="font-size: 13px">{{ t("rustdesk.no_devices") }}</n-text>
          <n-data-table v-else :columns="peerCols" :data="peers" :loading="peerLoading" :bordered="false"
                        :scroll-x="peerColsWidth" remote :pagination="pagination"
                        :row-key="(p: RustDeskPeer) => p.rustdesk_id"
                        :checked-row-keys="checkedIds" @update:checked-row-keys="onChecked"
                        data-testid="rustdesk-peers-table" @update:sorter="onPeerSorter" />
        </div>
      </n-tab-pane>

      <n-tab-pane name="audit">
        <template #tab>
          <span class="rd-tab"><n-icon :size="16"><AuditIcon /></n-icon>{{ t("rustdesk.audit") }}</span>
        </template>
        <n-space v-if="rows.length > 1" style="margin-bottom: 10px">
          <n-select v-model:value="selected" :options="serverOptions" style="width: 200px"
                    :placeholder="t('rustdesk.server_pick')" />
        </n-space>
        <RustDeskAuditTable ref="auditRef" :server-id="selected" />
      </n-tab-pane>
    </n-tabs>

    <!-- 刪除舊註冊：確認 -->
    <n-modal v-model:show="delShow" preset="card" style="width: min(560px, 100%)" :title="t('rustdesk.del_title')">
      <div data-testid="rustdesk-del-confirm">
        <div style="font-weight: 600; margin-bottom: 8px">
          {{ t("rustdesk.del_confirm", { n: checkedIds.length, server: selectedServer?.name ?? "" }) }}
        </div>
        <ul class="rd-del-notes">
          <li>{{ t("rustdesk.del_note_online") }}</li>
          <li>{{ t("rustdesk.del_note_return") }}</li>
          <li>{{ t("rustdesk.del_note_memory") }}</li>
        </ul>
      </div>
      <template #footer>
        <n-space justify="end">
          <n-button @click="delShow = false">
            <template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}
          </n-button>
          <n-button type="error" :loading="delBusy" data-testid="rustdesk-del-confirm-btn" @click="confirmDelete">
            <template #icon><n-icon><DeleteIcon /></n-icon></template>
            {{ t("rustdesk.del_confirm_btn", { n: checkedIds.length }) }}
          </n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 刪除舊註冊：紀錄 -->
    <n-modal v-model:show="logShow" preset="card" style="width: min(900px, 100%)"
             :title="`${t('rustdesk.del_log_title')} — ${selectedServer?.name ?? ''}`">
      <div data-testid="rustdesk-del-log">
        <n-text v-if="!logTotal && !logLoading" depth="3" style="font-size: 13px">{{ t("rustdesk.del_log_empty") }}</n-text>
        <n-data-table v-else :columns="logCols" :data="logRows" :loading="logLoading" :bordered="false" size="small"
                      remote :pagination="logPagination" :scroll-x="860" :row-key="(r: RustDeskPeerDelete) => r.id" />
      </div>
      <template #footer>
        <n-space justify="end">
          <n-button @click="void loadDeleteLog()">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}
          </n-button>
          <n-button @click="logShow = false">
            <template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.close") }}
          </n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 新增／編輯 -->
    <n-modal v-model:show="show" preset="card"
             :title="editing ? t('common.edit') : `${t('common.create')} — ${t('rustdesk.title')}`"
             style="width: min(580px, 100%)">
      <n-form>
        <n-form-item :label="t('common.name')"><n-input v-model:value="form.name" data-testid="rustdesk-name" /></n-form-item>
        <n-form-item :label="t('rustdesk.client_address')">
          <div style="width: 100%">
            <n-input v-model:value="form.client_address" placeholder="rd.example.com"
                     data-testid="rustdesk-client-address" />
            <div class="form-hint">{{ t("rustdesk.client_address_hint") }}</div>
          </div>
        </n-form-item>
        <n-form-item :label="t('rustdesk.receive_reports')">
          <div style="width: 100%">
            <n-space align="center" :size="12">
              <n-switch v-model:value="form.receive_reports" data-testid="rustdesk-receive-reports" />
              <span style="font-size: 12px; opacity: .7">{{ t("rustdesk.api_port") }}</span>
              <n-input-number v-model:value="form.api_port" :min="1" :max="65535" size="small" style="width: 110px"
                              :disabled="!form.receive_reports" />
            </n-space>
            <div class="form-hint">{{ t("rustdesk.receive_reports_hint") }}</div>
          </div>
        </n-form-item>
        <!-- 相容 RustDesk 的網頁連線：後端連 hbbs 會合、連 hbbr 中繼，只轉送密文（加解密與登入都在瀏覽器） -->
        <n-form-item :label="t('rustdesk.web_enabled')">
          <div style="width: 100%">
            <n-switch v-model:value="form.web_enabled" data-testid="rustdesk-web-enabled" />
            <div class="form-hint">{{ t("rustdesk.web_enabled_hint") }}</div>
          </div>
        </n-form-item>
        <template v-if="form.web_enabled">
          <n-form-item :label="t('rustdesk.hbbs_host')">
            <div style="width: 100%">
              <n-input v-model:value="form.hbbs_host" data-testid="rustdesk-hbbs-host"
                       :placeholder="editing?.agent_source_ip || 'rd.example.com'" />
              <div class="form-hint">{{ t("rustdesk.hbbs_host_hint") }}</div>
            </div>
          </n-form-item>
          <n-form-item :label="t('rustdesk.relay_host')">
            <div style="width: 100%">
              <n-input v-model:value="form.relay_host" data-testid="rustdesk-relay-host"
                       :placeholder="t('rustdesk.relay_host_ph')" />
              <div class="form-hint">{{ t("rustdesk.relay_host_hint") }}</div>
            </div>
          </n-form-item>
          <n-form-item :label="t('rustdesk.transport')">
            <div style="width: 100%">
              <n-select v-model:value="form.transport" :options="transportOptions" style="max-width: 280px"
                        data-testid="rustdesk-transport" />
              <div class="form-hint">{{ t("rustdesk.transport_hint") }}</div>
            </div>
          </n-form-item>
          <!-- 網頁檔案傳輸（規格附錄 J.6）：另一條連線，沿用網頁連線的中繼；預設關 -->
          <n-form-item :label="t('rustdesk.web_file_transfer')">
            <div style="width: 100%">
              <n-switch v-model:value="form.web_file_transfer" data-testid="rustdesk-web-file-transfer" />
              <div class="form-hint">{{ t("rustdesk.web_file_transfer_hint") }}</div>
            </div>
          </n-form-item>
          <n-form-item v-if="form.web_file_transfer" :label="t('rustdesk.web_file_max_file_mb')">
            <div style="width: 100%">
              <n-space align="center" :size="12">
                <n-input-number v-model:value="form.web_file_max_file_mb" :min="1" :max="1048576" size="small"
                                style="width: 140px" data-testid="rustdesk-web-file-max-file" />
                <span style="font-size: 12px; opacity: .7">{{ t("rustdesk.web_file_max_total_mb") }}</span>
                <n-input-number v-model:value="form.web_file_max_total_mb" :min="1" :max="1048576" size="small"
                                style="width: 140px" data-testid="rustdesk-web-file-max-total" />
              </n-space>
              <div class="form-hint">{{ t("rustdesk.web_file_limits_hint") }}</div>
            </div>
          </n-form-item>
        </template>
        <!-- 刪除舊註冊：預設關；RustDesk 主機上也要以 --allow-delete 安裝代理（兩邊都同意才會動到 RustDesk 的資料） -->
        <n-form-item :label="t('rustdesk.allow_peer_delete')">
          <div style="width: 100%">
            <n-switch v-model:value="form.allow_peer_delete" data-testid="rustdesk-allow-delete" />
            <div class="form-hint">{{ t("rustdesk.allow_peer_delete_hint") }}</div>
          </div>
        </n-form-item>
        <n-form-item :label="t('rustdesk.interval')">
          <n-input-number v-model:value="form.report_interval_seconds" :min="60" :max="86400" />
        </n-form-item>
        <n-form-item :label="t('common.enable')">
          <n-switch v-model:value="form.enabled" />
        </n-form-item>
        <n-form-item :label="t('common.description')">
          <n-input v-model:value="form.description" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="show = false">
          <template #icon><n-icon><CancelIcon /></n-icon></template>
          {{ t("common.cancel") }}
        </n-button>
        <n-button type="primary" data-testid="rustdesk-save" @click="submit">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </n-space>
    </n-modal>

    <!-- 安裝指令／金鑰 -->
    <n-modal v-model:show="installShow" preset="card" style="width: min(720px, 100%)"
             :title="`${t('rustdesk.install_title')} — ${installServer?.name ?? ''}`">
      <div data-testid="rustdesk-install-modal">
        <n-alert v-if="installJustCreated" type="success" :bordered="false" style="margin-bottom: 12px">
          {{ t("rustdesk.install_created") }}
        </n-alert>
        <n-alert v-if="installNoKey" type="warning" :bordered="false" style="margin-bottom: 12px">
          {{ t("rustdesk.no_key") }}
          <div style="margin-top: 8px">
            <n-button size="small" type="primary" @click="rotateKey">{{ t("rustdesk.gen_key") }}</n-button>
          </div>
        </n-alert>
        <template v-if="installKey">
          <AgentTrustPicker :trust="trust" testid="rustdesk-trust" />
          <div class="rd-label">{{ t("rustdesk.install_where") }}</div>
          <n-space align="center" :wrap="false" :size="8">
            <code class="rd-code" data-testid="rustdesk-install-cmd">{{ installCmd }}</code>
            <n-button size="small" secondary @click="copy(installCmd)">
              <template #icon><n-icon :component="CopyIcon" /></template>{{ t("common.copy") }}
            </n-button>
          </n-space>
          <div class="form-hint" style="margin-top: 6px">{{ t("rustdesk.install_dir_hint") }}</div>
          <!-- 刪除舊註冊：主機端也要同意（JT_RD_ALLOW_DELETE=1／--allow-delete），說明為什麼指令多了這一段 -->
          <n-alert v-if="installAllowDelete" type="warning" :bordered="false" style="margin-top: 10px"
                   data-testid="rustdesk-install-allow-delete">
            <div>{{ t("rustdesk.install_allow_delete") }}</div>
            <n-space align="center" :wrap="false" :size="8" style="margin-top: 6px">
              <code class="rd-code">{{ allowDeleteCmd }}</code>
              <n-button size="small" secondary @click="copy(allowDeleteCmd)">
                <template #icon><n-icon :component="CopyIcon" /></template>{{ t("common.copy") }}
              </n-button>
            </n-space>
          </n-alert>
          <div v-else class="form-hint" style="margin-top: 4px">{{ t("rustdesk.install_read_only_hint") }}</div>

          <div class="rd-label" style="margin-top: 14px">{{ t("rustdesk.agent_col") }}</div>
          <div v-if="installLive" data-testid="rustdesk-install-state">
            <n-tag size="small" :bordered="false" :type="AGENT_TAG[agentState(installLive)]">
              {{ t(`rustdesk.agent_${agentState(installLive)}`) }}
            </n-tag>
            <span v-if="installLive.agent_last_seen_at" style="font-size: 12px; opacity: .7; margin-left: 8px">
              {{ t("rustdesk.agent_last_seen", { t: fmtDateTime(installLive.agent_last_seen_at) }) }}
              <template v-if="installLive.agent_hostname"> · {{ installLive.agent_hostname }}</template>
              <template v-if="installLive.agent_source_ip"> · {{ installLive.agent_source_ip }}</template>
            </span>
            <n-spin v-else :size="12" style="margin-left: 8px" />
            <div v-if="installLive.allow_peer_delete && installLive.agent_last_seen_at" class="form-hint"
                 data-testid="rustdesk-install-cap">
              {{ t("rustdesk.agent_cap") }}：{{ capLabel(installLive) }}
            </div>
          </div>

          <div class="rd-label" style="margin-top: 14px">{{ t("rustdesk.install_paths") }}</div>
          <table class="rd-paths">
            <tr v-for="[k, v] in PATHS" :key="k"><td>{{ t(`rustdesk.${k}`) }}</td><td><code>{{ v }}</code></td></tr>
          </table>

          <div class="rd-label" style="margin-top: 14px">{{ t("rustdesk.agent_key") }}</div>
          <n-space align="center" :wrap="false" :size="8">
            <code class="rd-code">{{ installKey }}</code>
            <n-button size="small" secondary @click="copy(installKey!)">
              <template #icon><n-icon :component="CopyIcon" /></template>{{ t("common.copy") }}
            </n-button>
            <n-popconfirm @positive-click="rotateKey">
              <template #trigger>
                <n-button size="small" tertiary type="warning" data-testid="rustdesk-rotate">{{ t("rustdesk.rotate_key") }}</n-button>
              </template>
              {{ t("rustdesk.rotate_confirm") }}
            </n-popconfirm>
          </n-space>
          <div class="form-hint" style="margin-top: 6px">{{ t("rustdesk.key_warn") }}</div>

          <div class="rd-label" style="margin-top: 14px">{{ t("rustdesk.uninstall") }}</div>
          <n-space align="center" :wrap="false" :size="8">
            <code class="rd-code">{{ uninstallCmd }}</code>
            <n-button size="small" secondary @click="copy(uninstallCmd)">
              <template #icon><n-icon :component="CopyIcon" /></template>{{ t("common.copy") }}
            </n-button>
          </n-space>
        </template>
      </div>
      <template #footer>
        <n-space justify="end">
          <n-button @click="installShow = false">
            <template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.close") }}
          </n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 測試 -->
    <n-modal v-model:show="testShow" preset="card" style="width: min(640px, 100%)"
             :title="`${t('rustdesk.test_title')} — ${testServer?.name ?? ''}`">
      <div data-testid="rustdesk-test-result">
        <template v-if="testWaiting">
          <n-space align="center" :size="10">
            <n-spin :size="16" />
            <span>{{ t("rustdesk.test_waiting", { n: testServer?.agent_poll_seconds ?? 10 }) }}</span>
          </n-space>
          <n-alert v-if="testState && !testState.agent_last_seen_at" type="warning" :bordered="false" style="margin-top: 12px">
            {{ t("rustdesk.test_never") }}
          </n-alert>
        </template>
        <n-alert v-else-if="testTimedOut" type="error" :bordered="false">
          {{ t("rustdesk.test_timeout", { n: TEST_WAIT_SECONDS }) }}
          <div v-if="testState?.agent_last_seen_at">
            {{ t("rustdesk.test_last_seen", { t: fmtDateTime(testState.agent_last_seen_at) }) }}
          </div>
          <div v-else>{{ t("rustdesk.test_never") }}</div>
        </n-alert>
        <template v-else-if="testState?.result_at">
          <n-alert :type="testFailed ? 'warning' : 'success'" :bordered="false" style="margin-bottom: 10px">
            {{ testFailed ? t("rustdesk.test_failed", { n: testFailed }) : t("rustdesk.test_passed") }}
            <span style="opacity: .7; margin-left: 6px">{{ fmtDateTime(testState.result_at) }}</span>
          </n-alert>
          <table class="rd-checks">
            <tr v-for="c in testState.checks" :key="c.key" :data-testid="`rustdesk-check-${c.key}`">
              <td><n-tag size="tiny" :type="c.ok ? 'success' : 'error'" :bordered="false">{{ c.ok ? "✓" : "✗" }}</n-tag></td>
              <td>{{ te(`rustdesk.check_${c.key}`) ? t(`rustdesk.check_${c.key}`) : c.key }}</td>
              <td class="rd-check-detail">{{ checkDetail(c) }}</td>
            </tr>
          </table>
        </template>
      </div>
      <template #footer>
        <n-space justify="end">
          <n-button v-if="testServer && !testWaiting" @click="runTest(testServer)">
            <template #icon><n-icon><TestIcon /></n-icon></template>{{ t("rustdesk.test_again") }}
          </n-button>
          <n-button @click="testShow = false">
            <template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.close") }}
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </n-card>
</template>

<style scoped>
.form-hint { font-size: 11.5px; opacity: .7; margin-top: 4px; line-height: 1.5; }
.rd-tab { display: inline-flex; align-items: center; gap: 6px; }
.rd-steps { margin: 6px 0 4px; padding-left: 20px; line-height: 1.8; }
.rd-steps code { font-size: 12px; }
.rd-note { font-size: 12px; opacity: .8; }
.rd-key { font-family: ui-monospace, monospace; font-size: 11.5px; opacity: .75; }
.rd-id { font-family: ui-monospace, monospace; }
.rd-nowrap { display: inline-flex; align-items: center; white-space: nowrap; }
.mono { font-family: ui-monospace, monospace; }
.rd-label { font-weight: 600; margin-bottom: 6px; }
.rd-code { flex: 1; display: block; word-break: break-all; font-size: 12px; padding: 6px 8px; border-radius: 4px;
           background: var(--n-color-embedded, rgba(127, 127, 127, .1)); }
.rd-paths td, .rd-checks td { padding: 3px 10px 3px 0; vertical-align: top; font-size: 13px; }
.rd-paths code { font-size: 12px; }
.rd-check-detail { font-family: ui-monospace, monospace; font-size: 12px; opacity: .8; word-break: break-all; }
.rd-del-notes { margin: 0; padding-left: 20px; line-height: 1.7; font-size: 13px; }
</style>
