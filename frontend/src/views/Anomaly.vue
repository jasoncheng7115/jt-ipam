<script setup lang="ts">
import { osFamilyLabel, useScanProbes } from "@/api/scanProbes";
import { computed, onMounted, ref, h, watch } from "vue";
import LiveStatusDot from "@/components/LiveStatusDot.vue";
import { classifyAddressLiveness, type LivenessKind } from "@/composables/useLivenessSettings";
import { fmtDateTime } from "@/utils/datetime";
import { useI18n } from "vue-i18n";
import { RouterLink, useRoute, useRouter } from "vue-router";
import { useEntityLinks } from "@/composables/useEntityLinks";
import {
  NCard, NSpace, NIcon, NButton, NAlert, NGrid, NGi, NDataTable, NEmpty, NInput,
  NTabs, NTabPane, NModal, NSelect, NSwitch, NInputNumber, NTimePicker, NCollapse, NCollapseItem, NTag,
  useMessage, type DataTableColumns,
} from "naive-ui";
import {
  getAnomalySchedule, ignoreAnomalyForIp, listIgnorableCategories,
  runAnomalyScan, updateAnomalySchedule, getLastAnomalyReport,
  type AnomalyReport, type AnomalySchedule,
} from "@/api/phase3";
import { AiAuditIcon, AnomalyIcon, DownloadIcon, EyeIcon, IdentifyIcon, InfoIcon, PendingIcon, SettingsIcon, TestIcon, renderIcon } from "@/icons";
import { useAuthStore } from "@/stores/auth";
import { renderMarkdown } from "@/utils/markdown";
import { downloadTextFile } from "@/utils/investigateReport";
import { listSubnets, setAnomalyScope } from "@/api/subnets";
import { apiClient, apiErrMsg } from "@/api/client";
import { autoSort } from "@/composables/useTableSort";
import { withExportValue } from "@/utils/tableExport";
import { deviceKindColumn, deviceKindLabel } from "@/utils/deviceKindCell";
import type { Subnet } from "@/types";
import { useTablePagination } from "@/composables/useTablePagination";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import ColumnPicker from "@/components/ColumnPicker.vue";

const { t, te, locale } = useI18n();
// OS 家族的顯示名稱（類型或 OS 突變那一欄；與 IP 清單同一份對照）
const { catalog: probeCatalog } = useScanProbes();
const msg = useMessage();
const pg = useTablePagination();
const loading = ref(false);
const report = ref<AnomalyReport | null>(null);
// 未授權 IP 超過清單上限：只列出最近看到的那些，要講出來（以前靜靜切掉）
const unauthTruncated = computed(() =>
  (report.value?.unauthorized_total ?? 0) > (report.value?.unauthorized_ips.length ?? 0));
const lastRunAt = ref<string | null>(null);
/** 上次結果是排程跑的還是手動按的（進頁面載入上次結果時顯示） */
const lastTrigger = ref<"manual" | "schedule" | null>(null);

// 偵測範圍（寫的是 subnets.anomaly_enabled，跟子網路編輯頁同一個欄位）
const scopeShow = ref(false);
const scopeSaving = ref(false);
const scopeIds = ref<string[]>([]);
const subnets = ref<Subnet[]>([]);
const subnetsLoading = ref(false);
const subnetOptions = computed(() => subnets.value.map((s) => ({
  label: s.description ? `${s.cidr} — ${s.description}` : s.cidr,
  value: s.id,
})));

async function loadSubnets() {
  subnetsLoading.value = true;
  try {
    const r = await listSubnets({ pageSize: 500 });
    subnets.value = r.items;
    scopeIds.value = r.items.filter((s) => s.anomaly_enabled).map((s) => s.id);
  } catch { /* 沒權限就留空，不擋整頁 */ } finally { subnetsLoading.value = false; }
}

function openScope() {
  scopeShow.value = true;
  void loadSubnets();
}

// ── 排程 ─────────────────────────────────────────────────────────────────
// 偵測邏輯早就寫好了，但只能靠人按「執行掃描」—— IP 衝突、非法 DHCP 不會挑上班時間發生。
// 形狀刻意與 AI 巡檢排程一致（同一份 due() 判斷），另外多一個「每隔 N 分鐘」：
// 那些是營運監控，等到隔天太慢。
const schedShow = ref(false);
const schedSaving = ref(false);
const sched = ref<AnomalySchedule | null>(null);

const freqOptions = computed(() => [
  { label: t("anomaly.sched_freq_interval"), value: "interval" },
  { label: t("anomaly.sched_freq_daily"), value: "daily" },
  { label: t("anomaly.sched_freq_weekly"), value: "weekly" },
  { label: t("anomaly.sched_freq_monthly"), value: "monthly" },
]);
// 星期的文案沿用巡檢排程那一份（`llm_settings.weekday_N`）—— 另造一套鍵值
// 只會多出一份要維護的翻譯，而且漏翻時畫面上會直接露出鍵名。
const weekdayOptions = computed(() => [1, 2, 3, 4, 5, 6, 7].map((d) => ({
  label: t(`llm_settings.weekday_${d}`), value: d,
})));

function hhmmToMs(hhmm: string): number {
  const [h, m] = hhmm.split(":").map((x) => Number(x) || 0);
  const d = new Date();
  d.setHours(h, m, 0, 0);
  return d.getTime();
}

async function openSched() {
  try {
    sched.value = await getAnomalySchedule();
    schedShow.value = true;
  } catch (e) { msg.error(apiErrMsg(e)); }
}

async function patchSched(patch: Partial<AnomalySchedule>) {
  if (!sched.value) return;
  schedSaving.value = true;
  try {
    sched.value = await updateAnomalySchedule(patch);
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { schedSaving.value = false; }
}

function setSchedTime(index: number, formatted: string | null) {
  if (!formatted || !sched.value) return;
  const next = [...sched.value.times];
  next[index] = formatted;
  void patchSched({ times: next });
}

function removeSchedTime(index: number) {
  if (!sched.value) return;
  const next = sched.value.times.filter((_, i) => i !== index);
  // 一個都不留＝排程開著卻永遠不觸發。後端也會擋，這裡先不讓它發生
  if (next.length) void patchSched({ times: next });
}

async function saveScope() {
  scopeSaving.value = true;
  try {
    await setAnomalyScope(scopeIds.value);
    msg.success(t("common.saved"));
    scopeShow.value = false;
  } catch (e) { msg.error(apiErrMsg(e)); await loadSubnets(); }
  finally { scopeSaving.value = false; }
}
// 可以逐 IP 忽略的類別。清單由後端決定（`/anomalies/ignorable`），這裡的預設值只是
// 在還沒載回來之前不要讓按鈕閃現；**不要**在前端另外寫死一份 —— 兩份清單遲早會不一致。
const IGNORABLE = ref<string[]>([]);
const ignoreBusy = ref<Set<string>>(new Set());

async function loadIgnorable() {
  try { IGNORABLE.value = (await listIgnorableCategories()).categories; } catch { /* 靜默 */ }
}

async function doIgnore(ipId: string, category: string) {
  ignoreBusy.value = new Set([...ignoreBusy.value, ipId]);
  try {
    await ignoreAnomalyForIp(ipId, category);
    msg.success(t("anomaly.ignore_done"));
    await run();                      // 重跑一次，被忽略的那列就會消失
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally {
    const next = new Set(ignoreBusy.value); next.delete(ipId); ignoreBusy.value = next;
  }
}

const CATEGORY_KEYS = [
  "ip_conflicts", "arp_flux", "l2_subnet_bleed", "mac_drifts", "ghost_ips", "unauthorized_ips", "rogue_dhcp",
  "external_exposure", "dangling_dns", "dns_compare_mismatch", "duplicate_ip_records", "suspicious_changes",
  "fw_rule_rot",
  "arp_only_liveness",
  "stale_device_links",
  "mac_flapping",
  "identity_changes",
];
const route = useRoute();
const router = useRouter();
const links = useEntityLinks(router);
// 通知點進來要落在對應的頁籤（?tab=fw_rule_rot），不是丟到第一個分類讓人自己找
const activeTab = ref(
  CATEGORY_KEYS.includes(String(route.query.tab)) ? String(route.query.tab) : "ip_conflicts");

type CatKey = "ip_conflicts" | "arp_flux" | "l2_subnet_bleed" | "mac_drifts" | "ghost_ips" | "unauthorized_ips"
  | "rogue_dhcp" | "external_exposure" | "dangling_dns" | "dns_compare_mismatch" | "duplicate_ip_records"
  | "suspicious_changes"
  | "fw_rule_rot"
  | "arp_only_liveness"
  | "stale_device_links"
  | "mac_flapping"
  | "identity_changes";
const CATEGORIES: { key: CatKey; label: () => string }[] = [
  { key: "ip_conflicts", label: () => t("anomaly.ip_conflicts") },
  // 2026-10-09：同一台主機多張網卡（從 IP 衝突分出來）、兩個子網段混在同一個二層
  { key: "arp_flux", label: () => t("anomaly.arp_flux") },
  { key: "l2_subnet_bleed", label: () => t("anomaly.l2_bleed") },
  { key: "mac_drifts", label: () => t("anomaly.mac_drifts") },
  { key: "ghost_ips", label: () => t("anomaly.ghost_ips") },
  { key: "unauthorized_ips", label: () => t("anomaly.unauthorized") },
  { key: "rogue_dhcp", label: () => t("anomaly.rogue_dhcp") },
  { key: "external_exposure", label: () => t("anomaly.exposure") },
  { key: "dangling_dns", label: () => t("anomaly.dangling_dns") },
  // 2026-10-10：DNS 比對群組各台不一樣（持續超過寬限時間的才列）
  { key: "dns_compare_mismatch", label: () => t("anomaly.dns_compare_mismatch") },
  { key: "duplicate_ip_records", label: () => t("anomaly.dup_ip") },
  { key: "suspicious_changes", label: () => t("anomaly.changes") },
  { key: "fw_rule_rot", label: () => t("anomaly.fw_rot") },
  { key: "arp_only_liveness", label: () => t("anomaly.arp_only") },
  { key: "stale_device_links", label: () => t("anomaly.stale_link") },
  { key: "mac_flapping", label: () => t("anomaly.mac_flapping") },
  { key: "identity_changes", label: () => t("anomaly.identity_changes") },
];

const rogueTitle = computed(() =>
  t("anomaly.rogue_dhcp") + `（${report.value?.rogue_dhcp?.length ?? 0}）`);

// 首屏四張統計卡；數字 > 0 用警示色，一眼看得出哪一類有事
const statCards = computed(() => {
  const r = report.value;
  if (!r) return [];
  return [
    { key: "ip_conflicts", label: t("anomaly.ip_conflicts"), value: r.ip_conflicts.length },
    { key: "mac_drifts", label: t("anomaly.mac_drifts"), value: r.mac_drifts.length },
    { key: "ghost_ips", label: t("anomaly.ghost_ips"), value: r.ghost_ips.length },
    { key: "unauthorized_ips", label: t("anomaly.unauthorized"),
      value: r.unauthorized_ips.length },
  ];
});

const anyFindings = computed(() => {
  const r = report.value;
  return !!r && (r.ip_conflicts.length + r.mac_drifts.length + r.ghost_ips.length
    + r.unauthorized_ips.length + (r.rogue_dhcp?.length ?? 0)
    + (r.external_exposure?.length ?? 0) + (r.dangling_dns?.length ?? 0)
    + (r.dns_compare_mismatch?.length ?? 0)
    + (r.duplicate_ip_records?.length ?? 0) + (r.suspicious_changes?.length ?? 0)
    + (r.fw_rule_rot?.length ?? 0)
    + (r.arp_only_liveness?.length ?? 0)
    + (r.stale_device_links?.length ?? 0)
    + (r.mac_flapping?.length ?? 0)
    + (r.identity_changes?.length ?? 0)
    + (r.arp_flux?.length ?? 0) + (r.l2_subnet_bleed?.length ?? 0)
    + (r.mac_drift_reference?.length ?? 0)) > 0;
});
// MAC 漂移的參考項目（虛擬機遷移、隨機 MAC 漫遊、上行路徑變更）：同一個頁籤下方收合顯示，不通知
const driftRefs = computed(() => report.value?.mac_drift_reference ?? []);
const DRIFT_REF_KEYS = ["category", "mac", "ips", "device_name", "from_port", "to_port", "moved_at"];
const driftRefCols = computed<DataTableColumns<any>>(() => autoSort(DRIFT_REF_KEYS.map((k) => ({
  title: colLabel(k), key: k,
  ...(k === "ips" ? { minWidth: 200 } : k === "moved_at" ? { minWidth: 160 }
    : { width: ({ category: 190, mac: 145, device_name: 120 } as Record<string, number>)[k] ?? 105 }),
  render: (r: any) => (k === "category"
    ? h(NTag, { size: "small", bordered: false, type: r.category === "vm_migration" ? "info" : "default" },
        { default: () => pretty(k, r[k]) })
    : renderVal(k, r[k], r, "mac_drifts")),
}))));
function catRows(key: CatKey): Record<string, any>[] {
  return ((report.value?.[key] as Record<string, any>[]) ?? []).map(localizeRow);
}

// 篩選（IP／主機名稱／MAC／說明…）：所有分類共用同一個關鍵字，切頁籤不用重打，
// 頁籤上的數字也跟著變成「符合／全部」—— 一眼看得出某個 IP 出現在哪幾類異常裡（使用者要求）。
// 比對「畫面上看到的字」（pretty 之後，例如翻譯過的類型），巢狀的清單再多比一次原始值。
const filterQ = ref(typeof route.query.q === "string" ? route.query.q : "");
// 頁籤與篩選寫進網址：從「探測」按返回時回到同一個頁籤、同一個篩選（以前會跳回第一個頁籤）
watch([activeTab, filterQ], ([tab, q]) => {
  const query: Record<string, string> = { ...route.query as Record<string, string>, tab };
  if (q.trim()) query.q = q.trim(); else delete query.q;
  if (query.tab !== route.query.tab || query.q !== route.query.q) void router.replace({ query });
});
function rowMatches(key: CatKey, row: Record<string, any>, q: string): boolean {
  for (const k of CAT_KEYS[key]) {
    if (k === "live") continue;
    if (k === "device_kind") {
      if (deviceKindLabel(row[k], t, te).toLowerCase().includes(q)) return true;
      continue;
    }
    const v = row[k];
    if (v == null || v === "") continue;
    if (pretty(k, v).toLowerCase().includes(q)) return true;
    if (typeof v === "object" && JSON.stringify(v).toLowerCase().includes(q)) return true;
  }
  return false;
}
function shownRows(key: CatKey): Record<string, any>[] {
  const rows = catRows(key);
  const q = filterQ.value.trim().toLowerCase();
  return q ? rows.filter((r) => rowMatches(key, r, q)) : rows;
}
function tabLabel(c: { key: CatKey; label: () => string }): string {
  const total = catRows(c.key).length;
  return filterQ.value.trim()
    ? `${c.label()} (${shownRows(c.key).length}/${total})`
    : `${c.label()} (${total})`;
}

// 後端送來的說明文字：`detail` 是中文原字串（匯出與 AI 判讀走那一份，那兩條路沒有
// 瀏覽器的語言可問），`detail_key`（＋選用的 `detail_params`）才是畫面上顯示的。
// 一律以欄位名為準，所以後端之後對別的欄位比照辦理時，這裡不用改。
function localizeRow(row: Record<string, any>): Record<string, any> {
  let out = row;
  for (const [k, v] of Object.entries(row)) {
    if (!k.endsWith("_key") || typeof v !== "string" || !te(v)) continue;
    const field = k.slice(0, -4);
    if (out === row) out = { ...row };
    out[field] = t(v, (row[`${field}_params`] || {}) as Record<string, unknown>);
  }
  return out;
}

// 欄位標題。技術縮寫（MAC／IP）三種語言都一樣，所以不進語言檔；其餘查
// `anomaly.col.*`，查不到就退回欄位原名（後端新增欄位時不會變成空白）。
const RAW_COL: Record<string, string> = { mac: "MAC", macs: "MAC", ip: "IP" };
// 上線狀態：與 IP 清單同一顆燈、同一套規則。排序：上線 → 近期出現 → 離線 → 未知 → 沒有依據
const LIVE_RANK: Record<LivenessKind, number> = { online: 0, stale: 1, offline: 2, unknown: 3 };
function liveRank(live: any): number {
  return live ? LIVE_RANK[classifyAddressLiveness(live)] : 4;
}
function liveText(live: any): string {
  return live ? t(`visualisation.${classifyAddressLiveness(live)}`) : "—";
}
function liveDot(live: any) {
  return live ? h(LiveStatusDot, { address: live }) : h("span", { style: "opacity:.5" }, "—");
}
function colLabel(k: string): string {
  if (RAW_COL[k]) return RAW_COL[k];
  if (k === "device_kind") return t("cols.device_kind");
  const key = `anomaly.col.${k}`;
  return te(key) ? t(key) : k;
}
// 各類別的欄位（順序）＋預設隱藏（ip_address_id 是內部 UUID，預設不顯示，可在「欄位」勾選）
const CAT_KEYS: Record<CatKey, string[]> = {
  // 依據：ARP（1 小時內多個 MAC）或 MAC 來回切換（24 小時內，issue #41）
  // 可信度：至少兩台機器各有兩個以上來源看到才算高（2026-10-09）
  ip_conflicts: ["ip", "live", "device_kind", "evidence", "confidence", "changes", "macs"],
  // 同一台主機多張網卡：哪台、每個 MAC 是哪張網卡、建議的 sysctl
  arp_flux: ["ip", "live", "host", "evidence", "macs", "fix"],
  // 兩個子網段混在同一個二層：哪兩段、證據數量與例子
  l2_subnet_bleed: ["subnets", "evidence", "arp_count", "fdb_count", "arp_examples", "fdb_examples"],
  // 同一台交換器上換了埠：從哪個埠換到哪個埠、什麼時候（出現位置是明細，預設收起）
  mac_drifts: ["mac", "ips", "device_name", "from_port", "to_port", "moved_at", "locations"],
  ghost_ips: ["ip", "live", "hostname", "device_kind", "last_seen_scanner", "last_seen_librenms", "ip_address_id"],
  // ARP 看到的 MAC（廠商、隨機 MAC、誰看到的）：只有一個位址看不出是誰
  unauthorized_ips: ["ip", "live", "last_seen_at", "macs"],
  rogue_dhcp: ["server_ip", "live", "subnet_cidr", "mac", "vendor", "offered_ip", "router",
               "first_seen_at", "last_seen_at"],
  external_exposure: ["kind", "ip", "live", "hostname", "device_kind", "ports", "subnet", "monitored",
                      "effective_status", "names", "owner", "rules", "ip_address_id"],
  // 同一個比對群組裡相同的紀錄合成一筆：server 會列出哪幾台都有，compare_group 是哪一組
  dangling_dns: ["name", "value", "live", "type", "zone", "server", "compare_group"],
  // 哪一組、哪個 zone 的哪筆紀錄，哪幾台有、哪幾台沒有、從什麼時候開始
  dns_compare_mismatch: ["group", "zone", "name", "type", "value", "present_on", "missing_on", "first_seen_at"],
  duplicate_ip_records: ["ip", "live", "records"],
  suspicious_changes: ["kind", "actor", "actor_ip", "object_type", "action",
                       "count", "first_at", "last_at"],
  // 防火牆：同名規則（Anti-Lockout 這類）會來自好幾台，要看得出是哪一台
  fw_rule_rot: ["kind", "firewall", "name", "source", "interface", "port", "descr", "detail"],
  arp_only_liveness: ["ip", "live", "hostname", "device_kind", "mac", "last_seen_arp", "ip_address_id"],
  // 頻繁換 MAC：先看是哪個 IP、換過幾個、時間跨度，再看 MAC 清單。
  // randomized 要露出來 —— 那一欄是「這些看起來是隱私隨機化位址」，
  // 使用者據此判斷要不要把這個 IP 加進忽略清單。
  mac_flapping: ["ip", "live", "hostname", "device_kind", "mac_count", "randomized", "days", "macs", "ip_id"],
  stale_device_links: ["ip", "live", "hostname", "device_kind", "mac", "device", "linked_at", "mac_changed_at",
                       "ip_address_id"],
  // 類型或 OS 突變：哪個 IP、從什麼變成什麼，再看現在判讀出的型號與 OS
  identity_changes: ["ip", "live", "hostname", "device_kind", "shifts", "device_model", "os_guess", "last_at",
                     "ip_id"],
};
// 設備類型（IP 記錄上掃描代理判讀出的類型）：各頁都可以在「欄位」勾選；只有「類型或 OS 突變」預設顯示
const CAT_HIDDEN: Partial<Record<CatKey, string[]>> = {
  ip_conflicts: ["device_kind"],
  // 欄位多、MAC 欄又寬：次要的預設收起，讓「建議修法」「例子」不必橫向捲動就看得到
  arp_flux: ["evidence"],
  l2_subnet_bleed: ["arp_count", "fdb_count"],
  mac_drifts: ["locations"],
  mac_flapping: ["ip_id", "days", "device_kind"],
  identity_changes: ["ip_id"],
  ghost_ips: ["ip_address_id", "device_kind"],
  arp_only_liveness: ["device_kind"],
  stale_device_links: ["device_kind"],
  // owner 實務上幾乎沒人填、rules 是原始規則明細、ip_address_id 是內部 UUID：
  // 預設不顯示，需要的人可在「欄位」自行勾選
  external_exposure: ["ip_address_id", "owner", "rules", "device_kind"],
  // 規則描述多半就是名稱（同步時沒有名稱就拿描述當），預設不重複顯示
  fw_rule_rot: ["descr"],
};

// 每個類別一份欄位顯示偏好
const prefs = {} as Record<CatKey, ReturnType<typeof useColumnPrefs>>;
for (const c of CATEGORIES) {
  const keys = CAT_KEYS[c.key];
  const hidden = CAT_HIDDEN[c.key] ?? [];
  prefs[c.key] = useColumnPrefs(`anomaly_${c.key}`, keys, keys.filter((k) => !hidden.includes(k)));
}
function pickerItems(key: CatKey) {
  return CAT_KEYS[key].map((k) => ({ key: k, label: colLabel(k) }));
}

function pretty(k: string, val: any): string {
  if (val == null || val === "") return "";
  // kind 是分類代碼（exposed_unmonitored…），要翻成看得懂的字，不能把 enum 直接印給人看
  // kind 橫跨三類（對外曝險 exp_*、可疑變更 chg_*、防火牆規則劣化 rotk_*），找得到才翻，找不到就原樣顯示
  if (k === "kind") {
    for (const p of ["anomaly.exp_", "anomaly.chg_", "anomaly.rotk_"]) {
      const key = `${p}${val}`;
      if (te(key)) return t(key);
    }
    return String(val);
  }
  if (k === "monitored" || k === "randomized") return val ? t("common.yes") : t("common.no");
  if (k === "category") return te(`anomaly.drift_cat.${val}`) ? t(`anomaly.drift_cat.${val}`) : String(val);
  if (k === "evidence" && Array.isArray(val)) {
    return val.map((x) => (te(`anomaly.evidence_${x}`) ? t(`anomaly.evidence_${x}`) : String(x)))
      .join("、");
  }
  if (Array.isArray(val)) {
    return val.map((x) => (typeof x === "object" && x !== null ? objLine(x) : String(x)))
      .join("、");
  }
  if (k.includes("device_id")) return String(val).slice(0, 8);
  if (k.includes("last_seen") || k.includes("_at") || k.includes("time")) return fmtDateTime(String(val));   // 轉本地時區
  return String(val);
}
function objLine(o: Record<string, any>): string {
  return Object.entries(o)
    .filter(([, v]) => v != null && v !== "")
    .map(([k, v]) => `${colLabel(k)}：${pretty(k, v)}`)
    .join("　·　");
}
function cell(label: string, val: string) {
  return h("span", { style: "white-space:nowrap;overflow:hidden;text-overflow:ellipsis" }, [
    h("span", { style: "opacity:.55;margin-right:4px" }, label),
    h("span", val || "—"),
  ]);
}
function renderLocation(o: Record<string, any>) {
  const dev = o.device_name || (o.device_id ? String(o.device_id).slice(0, 8) : "—");
  return h("div", {
    style: "display:grid;grid-template-columns:minmax(0,1fr) 110px 132px;gap:14px;font-size:12.5px;align-items:baseline",
  }, [
    cell(colLabel("device_id"), dev),
    cell(colLabel("port"), o.port ?? "—"),
    cell(colLabel("last_seen_at"), pretty("last_seen_at", o.last_seen_at)),
  ]);
}
const SEEN_VENDOR: Record<string, string> = {
  opnsense: "OPNsense", pfsense: "pfSense", fortigate: "FortiGate", paloalto: "Palo Alto", checkpoint: "Check Point",
  mikrotik: "MikroTik", librenms: "LibreNMS", adguard: "AdGuard", proxmox: "Proxmox",
  windows_dhcp: "Windows DHCP", kea_dhcp: "Kea DHCP", isc_dhcp: "ISC DHCP", technitium: "Technitium DHCP",
};
/** 誰看到這個 MAC：`scanner` → 掃描代理、`arp:opnsense` → ARP 表（OPNsense） */
function seenBy(src: string): string {
  const [kind, vendor] = src.includes(":") ? src.split(":", 2) : ["", src];
  if (kind === "arp") return t("anomaly.seen_arp_vendor", { vendor: SEEN_VENDOR[vendor] ?? vendor });
  if (vendor === "scanner") return t("anomaly.seen_scanner");
  if (vendor === "manual") return t("anomaly.seen_manual");
  return SEEN_VENDOR[vendor] ?? vendor;
}
function renderMac(o: Record<string, any>) {
  // 本地管理位址（虛擬機／容器／手機 MAC 隨機化）沒有 OUI 登記，查不到廠商是正常的。
  // 標出來才看得懂：同一 IP 上「真實 MAC + 隨機 MAC」多半是同一台裝置，不是兩台在搶。
  // 疑似讀壞／過期快取（2026-10-09）：照樣列出、不算一台機器。標在廠商的位置（讀壞的本來就查不到廠商）
  const tag = o.suspect
    ? h("span", { class: "anm-mac-tag anm-mac-tag--suspect", title: t(`anomaly.suspect_${o.suspect}_hint`) },
        t(`anomaly.suspect_${o.suspect}`))
    : o.local
      ? h("span", { class: "anm-mac-tag anm-mac-tag--local", title: t("anomaly.mac_local") }, t("anomaly.mac_local"))
      : (o.vendor ? h("span", { class: "anm-mac-tag", title: String(o.vendor) }, String(o.vendor)) : h("span"));
  // 誰看到的（掃描代理／防火牆 ARP 表／LibreNMS）：兩個 MAC 各是誰回報的，判斷真假時很關鍵。
  // 幾個來源＋滑過看是哪幾台設備的 ARP 表（哪個介面、最後一次）——「只有一台看過」一眼就看得出來
  const reporters: any[] = Array.isArray(o.reporters) ? o.reporters : [];
  const parts: string[] = [];
  if (Array.isArray(o.nic) && o.nic.length) parts.push(`${t("anomaly.nic")}：${o.nic.join("、")}`);
  if (Array.isArray(o.sources) && o.sources.length) parts.push(o.sources.map((x: string) => seenBy(String(x))).join("、"));
  if (reporters.length) parts.push(t("anomaly.reporter_count", { n: o.reporter_count ?? reporters.length }));
  const detail = reporters.map((r) => [r.device || seenBy(String(r.source)), r.interface,
                                       r.last_seen_at ? fmtDateTime(String(r.last_seen_at)) : null]
    .filter(Boolean).join(" · ")).join("\n");
  const seen = parts.length
    ? h("span", { class: "anm-mac-seen", title: detail || colLabel("sources"),
                  "data-testid": reporters.length ? "anm-mac-reporters" : undefined }, parts.join(" · "))
    : h("span");
  // 每一列同一組欄寬（MAC｜廠商｜誰看到的｜時間），多個 MAC 疊起來時上下對齊；MAC 與廠商不折行
  return h("div", { class: "anm-mac-row" }, [
    // 點 MAC 看它的完整歷程（用過哪些 IP、出現在哪個交換器埠）
    o.mac ? h(RouterLink, { to: { name: "mac-history", params: { mac: o.mac } },
                           style: "font-family:var(--jt-mono,monospace);color:var(--primary-color,#18a058);text-decoration:none" },
                     () => o.mac)
      : h("span", null, "—"),
    tag,
    seen,
    h("span", { class: "anm-mac-time" }, pretty("last_seen_at", o.last_seen_at)),
  ]);
}
// 表格裡的 IP 要能點進 IP 詳細資料（回報：看到可疑 IP 卻只能自己複製去搜）。
// 有 ip_address_id 就直接開那一筆；只有文字就帶去 /addresses?q=<ip> 搜尋。
function renderIp(row: any, ipText: string) {
  return row?.ip_address_id
    ? links.ipById(row.ip_address_id, ipText)
    : links.ipByText(ipText);
}
function renderVal(k: string, v: any, row?: any, cat?: CatKey) {
  if (k === "live") return liveDot(v);
  if (v == null || v === "") return "—";
  if ((k === "ip" || k === "server_ip" || k === "offered_ip") && typeof v === "string") {
    return renderIp(row, v);
  }
  if (cat === "l2_subnet_bleed" && k === "evidence" && Array.isArray(v)) {
    return h("div", { style: "display:flex;flex-direction:column;gap:2px;font-size:12.5px" },
      v.map((x: string) => h("div", null, te(`anomaly.evidence_${x}`) ? t(`anomaly.evidence_${x}`) : x)));
  }
  // 依據是代碼清單（arp / mac_flip），要翻成字 —— 走下面通用的陣列處理會把代碼原樣印出來
  if (k === "evidence") return pretty(k, v);
  if (k === "confidence") {
    return h(NTag, { size: "small", bordered: false, type: v === "high" ? "error" : "default",
                     title: t("anomaly.confidence_hint") },
             { default: () => t(`anomaly.confidence_${v}`) });
  }
  // ARP flux 的建議修法：一行一個 sysctl，等寬字
  if (k === "fix" && Array.isArray(v)) {
    return h("div", { style: "display:flex;flex-direction:column;gap:2px;font-size:12px" },
      v.map((line: string) => h("code", { style: "white-space:nowrap" }, line)));
  }
  if (k === "subnets" && Array.isArray(v)) {
    return h("div", { style: "display:flex;flex-direction:column;gap:2px;font-size:12.5px;white-space:nowrap" },
      v.map((c: string, i: number) => h("div", null, i ? `↔ ${c}` : c)));
  }
  if (k === "arp_examples" && Array.isArray(v)) {
    return h("div", { style: "display:flex;flex-direction:column;gap:2px;font-size:12.5px" },
      v.map((x: any) => h("div", { style: "white-space:normal" }, t("anomaly.bleed_arp_line", {
        ip: x.ip, subnet: x.subnet ?? "—", mac: x.mac, owner: x.mac_owner_ip, owner_subnet: x.mac_owner_subnet ?? "—" }))));
  }
  if (k === "fdb_examples" && Array.isArray(v)) {
    return h("div", { style: "display:flex;flex-direction:column;gap:2px;font-size:12.5px" },
      v.map((x: any) => h("div", { style: "white-space:normal" }, t("anomaly.bleed_fdb_line", {
        switch: x.switch, vlan: x.vlan,
        detail: Object.entries(x.macs ?? {}).map(([cidr, list]) => `${cidr}：${(list as any[])
          .map((m) => (m.port ? `${m.mac}（${m.port}）` : m.mac)).join("、")}`).join("；") }))));
  }
  // MAC 歷程（只有「頻繁更換 MAC」這一類）：一個 MAC 一行、**不折行**。MAC 字串被折成
  // 「0a:1b:2 / c:00:00 / :01」三行的話，這一欄就完全讀不出先後順序。
  // ⚠️ 只限這一類：IP 衝突的 MAC 要走 renderMac（廠商、本地管理標記、誰看到的）——
  // 以前這裡不分類別，IP 衝突的那些標記從這段加進來之後就再也沒顯示過（issue #41 時發現）。
  if (cat === "mac_flapping" && k === "macs" && Array.isArray(v) && v.length
      && typeof v[0] === "object") {
    return h("div", { style: "display:flex;flex-direction:column;gap:2px;font-size:12.5px" },
      v.map((it: any) => h("div", { style: "white-space:nowrap" }, [
        h("span", { style: "font-family:var(--mono,monospace)" }, String(it.mac ?? "")),
        // 疑似讀壞／過期快取：列出來但沒算進「換過幾個 MAC」
        it.suspect
          ? h("span", { class: "anm-mac-tag anm-mac-tag--suspect", style: "margin-left:8px",
                        title: t(`anomaly.suspect_${it.suspect}_hint`) }, t(`anomaly.suspect_${it.suspect}`))
          : null,
        it.last_seen_at
          ? h("span", { style: "opacity:.65;margin-left:8px" }, fmtDateTime(String(it.last_seen_at)))
          : null,
      ])));
  }
  // 類型或 OS 突變：「設備類型：印表機 → Windows 主機」一項一行
  if (cat === "identity_changes" && k === "shifts" && Array.isArray(v)) {
    return h("div", { style: "display:flex;flex-direction:column;gap:2px;font-size:12.5px" },
      v.map((c: any) => h("div", { style: "white-space:nowrap" }, identityChangeText(c))));
  }
  if (k === "ips" && Array.isArray(v)) {
    if (!v.length) return h("span", { style: "opacity:.5" }, "—");
    return h("div", { style: "display:flex;flex-direction:column;gap:2px;font-size:12.5px" },
      v.map((it: any) => h("div", { style: "display:flex;align-items:center;gap:6px" }, [
        // 一列有好幾個 IP（MAC 變動）：每個 IP 前面一顆上線燈
        row?.live_ips ? liveDot(row.live_ips[it.ip]) : null,
        it.ip_address_id ? links.ipById(it.ip_address_id, it.ip) : links.ipByText(it.ip),
        it.hostname ? h("span", { style: "opacity:.7" }, `（${it.hostname}）`) : null,
      ])));
  }
  if (k === "macs" && Array.isArray(v)) {
    return h("div", { style: "display:flex;flex-direction:column;gap:3px" }, v.map(renderMac));
  }
  if (Array.isArray(v)) {
    return h("div", { style: "display:flex;flex-direction:column;gap:3px" },
      v.map((it: any) => k === "locations"
        ? renderLocation(it)
        : h("div", { style: "font-size:12.5px" }, it && typeof it === "object" ? objLine(it) : String(it))));
  }
  if (typeof v === "object") return objLine(v);
  return pretty(k, v);
}
function identityValue(field: string, v: any): string {
  if (v == null || v === "") return "—";
  if (field === "device_kind") {
    const key = `identify.type.${v}`;
    return te(key) ? t(key) : String(v);
  }
  return osFamilyLabel(probeCatalog.value.os_families, String(v), locale.value) || String(v);
}
function identityChangeText(c: any): string {
  const label = c.field === "device_kind" ? t("anomaly.identity_kind") : t("anomaly.identity_os");
  const times = c.times > 1 ? t("anomaly.identity_times", { n: c.times }) : "";
  return `${label}：${identityValue(c.field, c.old)} → ${identityValue(c.field, c.new)}${times}`;
}
// ── 欄寬依內容 ────────────────────────────────────────────────────────────
// 使用者回饋：值都很短的欄（狀況、來源、介面、埠…）不必跟長欄平分寬度，省下來的留給最後一欄
// （通常是「說明」，原本被截成「埠轉發的目標位址不在 IPA…」）。所以一般欄依內容量出寬度，
// 最後一個資料欄不給寬度、吃掉剩下的空間；欄寬仍可拖拉調整（全站表格都可以）。
let measureCtx: CanvasRenderingContext2D | null = null;
function textWidth(s: string): number {
  if (!measureCtx) {
    measureCtx = document.createElement("canvas").getContext("2d");
    if (measureCtx) measureCtx.font = `14px ${getComputedStyle(document.body).fontFamily}`;
  }
  return measureCtx ? measureCtx.measureText(s).width : s.length * 8;
}
// 這幾欄一格裡放好幾行物件（MAC＋廠商＋誰看到的、出現位置…），維持原本的寬度設定
const MULTI_LINE_KEYS = new Set(["locations", "macs", "ips", "fix", "arp_examples", "fdb_examples"]);
const COL_PAD = 26;          // 儲存格左右內距
const SORT_ICON = 30;        // 標頭的排序圖示
const COL_MIN = 64;
const COL_MAX = 360;
/** 一格實際顯示的文字（陣列一個元素一行，取最長那行） */
function cellLines(k: string, v: any): string[] {
  if (v == null || v === "") return ["—"];
  if (Array.isArray(v) && k !== "evidence") {
    return v.map((it: any) => (it && typeof it === "object" ? objLine(it) : String(it)));
  }
  return [pretty(k, v)];
}
function autoWidth(key: CatKey, k: string): number {
  let w = textWidth(colLabel(k)) + SORT_ICON;
  for (const row of catRows(key).slice(0, 300)) {
    for (const line of cellLines(k, row[k])) w = Math.max(w, textWidth(line));
  }
  return Math.round(Math.min(COL_MAX, Math.max(COL_MIN, w + COL_PAD)));
}

// ── 探測 ─────────────────────────────────────────────────────────────────
// 使用者要求：清單的操作欄也要有 IP 詳細頁那顆「探測」，同一個功能（只有管理員）。
// 每一列是單一主機的類別才有；IPAM 有記錄就開那筆記錄的探測頁，沒有（未授權 IP）就以位址探測。
const IDENTIFY_FIELD: Partial<Record<CatKey, string>> = {
  ip_conflicts: "ip", arp_flux: "ip", ghost_ips: "ip", unauthorized_ips: "ip", rogue_dhcp: "server_ip",
  external_exposure: "ip", duplicate_ip_records: "ip", arp_only_liveness: "ip",
  mac_flapping: "ip", stale_device_links: "ip", identity_changes: "ip",
};
const auth = useAuthStore();
function openIdentify(r: any, key: CatKey) {
  const ip = String(r[IDENTIFY_FIELD[key] ?? "ip"] ?? "");
  const id = r.ip_address_id ?? r.ip_id;
  if (id) void router.push({ name: "address-identify", params: { id: String(id) } });
  else if (ip) void router.push({ name: "ip-identify", params: { ip } });
}
/** 小按鈕的寬度（依目前語言的文字量，含圖示與內距） */
function btnWidth(label: string, icon = true): number {
  return Math.ceil(textWidth(label) * (12 / 14)) + 20 + (icon ? 20 : 0);
}

// 依該類別的可見欄位（已套欄位偏好與使用者拖拉的順序）組欄位
function catCols(key: CatKey): DataTableColumns<any> {
  const visible = prefs[key].visibleKeys.value;
  const keys = prefs[key].orderKeys(CAT_KEYS[key].filter((k) => visible.includes(k)));
  const flexKey = [...keys].reverse().find((k) => !MULTI_LINE_KEYS.has(k));
  const lastKey = keys[keys.length - 1];
  // autoSort：與全站表格一致，替沒有自訂 sorter 的欄位補上預設排序。
  // 這幾張表原本整排標頭都不能排 —— 十幾筆 MAC 變動想按時間或按網段看都做不到。
  const cols = autoSort(keys.map((k) => {
    // MAC 清單一列要放好幾個「MAC＋時間」，窄欄會擠成一團看不出先後
    const wide = k === "locations" || k === "macs" || k === "arp_examples" || k === "fdb_examples";
    // 子網段混用的「子網段」「依據」一項一行（兩段 CIDR、兩種證據擺一行會被截斷）
    const multi = MULTI_LINE_KEYS.has(k) || (key === "l2_subnet_bleed" && (k === "subnets" || k === "evidence"));
    const sizing = multi
      ? { minWidth: wide ? 420 : k === "fix" ? 300 : (key === "l2_subnet_bleed" && k === "evidence") ? 270 : 220,
          // MAC 歷程不折行 —— 沒有明確寬度時會蓋到「操作」欄的按鈕上。
          // IP 衝突每個 MAC 還帶廠商與「誰看到的」，要寬一些
          ...(k === "macs" ? { width: key === "ip_conflicts" || key === "arp_flux" ? 600 : 340 } : {}) }
      // 最後一欄吃掉剩下的寬度（只有它是最後一欄時；最後是多行欄的話就照內容寬度）
      : k === flexKey && k === lastKey
        ? { minWidth: Math.max(160, autoWidth(key, k)) }
        : { width: autoWidth(key, k) };
    if (k === "device_kind") return deviceKindColumn(t, te);
    if (k === "live") {
      return withExportValue({
        title: colLabel(k), key: k, width: Math.max(64, Math.round(textWidth(colLabel(k)) + SORT_ICON + COL_PAD)),
        titleAlign: "center", align: "center",
        sorter: (a: any, b: any) => liveRank(a.live) - liveRank(b.live),
        render: (r: any) => liveDot(r.live),
      }, (r: any) => liveText(r.live));
    }
    // 最後一欄（吃剩下寬度的那欄，通常是說明）放不下就換行，不截斷 —— 要看得到整句
    const wrapLast = k === flexKey && k === lastKey;
    return {
      title: colLabel(k),
      key: k,
      ...sizing,
      ellipsis: wide || k === "ips" || wrapLast ? false : { tooltip: true },
      render: (r: any) => renderVal(k, r[k], r, key),
    };
  }));

  // 操作欄（一欄，按鈕並排）：探測、忽略這個 IP、AI 判讀。
  // 欄位標題用「操作」——與按鈕同名看起來像重複貼兩次（使用者回饋，與規則異動頁同一批）。
  const canIdentify = !!auth.me?.is_admin && !!IDENTIFY_FIELD[key];
  const canIgnore = IGNORABLE.value.includes(key);
  // 未授權 IP：加「AI 判讀」—— 把「有一個不明 IP」變成「看起來是什麼、下一步查哪」。
  const canTriage = key === "unauthorized_ips";
  if (canIdentify || canIgnore || canTriage) {
    let w = 24;
    if (canIdentify) w += btnWidth(t("identify.title")) + 6;
    if (canIgnore) w += btnWidth(t("anomaly.ignore_btn"), false) + 6;
    if (canTriage) w += btnWidth(t("anomaly.triage_btn")) + btnWidth(t("fw_changes.ai_view")) + 12;
    cols.push({
      title: t("common.actions"), key: "_actions", width: Math.max(90, w), className: "col-actions",
      render: (r: any) => h("span",
        { style: "display:inline-flex;align-items:center;gap:6px;flex-wrap:wrap" }, [
          canIdentify && r[IDENTIFY_FIELD[key] ?? "ip"]
            // title：視窗窄時操作欄只剩圖示（全站 col-actions 規則），滑過要看得出是哪一顆
            ? h(NButton, { size: "tiny", secondary: true, "data-testid": "anomaly-identify",
                           title: t("identify.title"), onClick: () => openIdentify(r, key) },
                { icon: renderIcon(IdentifyIcon, 15), default: () => t("identify.title") })
            : null,
          // 可以逐 IP 忽略的類別：給一顆「忽略這個 IP」。
          // 沒有這個機制的話，開了隱私隨機化的裝置（Windows 11／macOS／iOS／Android，每次
          // 連線都換 MAC）會把整頁洗掉，使用者只能把整條規則關掉 —— 連真正的 IP 搶用也一起看不到。
          canIgnore && r.ip_id
            ? h(NButton, {
                size: "tiny", secondary: true, loading: ignoreBusy.value.has(r.ip_id),
                disabled: ignoreBusy.value.has(r.ip_id), title: t("anomaly.ignore_btn"),
                onClick: () => doIgnore(r.ip_id, key),
              }, { default: () => t("anomaly.ignore_btn") })
            : null,
          canTriage
            ? h(NButton, {
                size: "tiny", secondary: true, loading: triageBusy.value.has(r.ip),
                disabled: triageBusy.value.has(r.ip), title: t("anomaly.triage_btn"),
                onClick: () => doTriage(r.ip),
              }, { icon: renderIcon(AiAuditIcon, 15), default: () => t("anomaly.triage_btn") })
            : null,
          canTriage && triageResults.value[r.ip]
            ? h(NButton, { size: "tiny", type: "primary", secondary: true, title: t("fw_changes.ai_view"),
                           onClick: () => { triageShow.value = r.ip; } },
                { icon: renderIcon(EyeIcon, 15), default: () => t("fw_changes.ai_view") })
            : null,
        ]),
    } as any);
  }
  return cols;
}
/** 表格總寬：欄位加總，超過畫面時左右捲動（原本寫死 600，欄位一多就擠在一起） */
function catScrollX(key: CatKey): number {
  return catCols(key).reduce((sum, c: any) => sum + (Number(c.width) || Number(c.minWidth) || 120), 0);
}

// AI 判讀：LLM 要跑幾十秒 —— 背景執行，完成後該列長出「檢視結果」，
// 結果留在頁面可重看（與防火牆規則異動的 AI 解讀同一套體驗）。
interface TriageResult { ip: string; card: string; disclaimer: string; model?: string }
const triageBusy = ref<Set<string>>(new Set());
const triageResults = ref<Record<string, TriageResult>>({});
const triageShow = ref<string | null>(null);
async function doTriage(ip: string) {
  triageBusy.value.add(ip); triageBusy.value = new Set(triageBusy.value);
  try {
    const { data } = await apiClient.post("/api/v1/anomalies/triage", { ip });
    triageResults.value = { ...triageResults.value, [ip]: data };
    msg.success(t("fw_changes.ai_done"));
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.server"));
  } finally { triageBusy.value.delete(ip); triageBusy.value = new Set(triageBusy.value); }
}

/** 下載判讀報告：.md 保留原始 markdown；.txt 去標記純文字（與規則異動頁一致）。 */
function downloadTriage(fmt: "md" | "txt") {
  const ip = triageShow.value;
  const res = ip ? triageResults.value[ip] : null;
  if (!ip || !res) return;
  const header = [
    `# ${t("anomaly.triage_title", { ip })}`,
    "",
    `- ${t("fw_changes.ai_model")}：${res.model ?? "—"}`,
    "",
    `> ${res.disclaimer}`,
    "",
  ].join("\n");
  const body = header + res.card + "\n";
  const text = fmt === "md" ? body : body
    .replace(/\*\*([^*]+)\*\*/g, "$1").replace(/`([^`]+)`/g, "$1")
    .replace(/^#{1,6}\s+/gm, "").replace(/^>\s?/gm, "");
  downloadTextFile(text, `ip-triage-${ip}.${fmt}`, fmt);
}

// 進頁面載入上次結果時不要動到「執行偵測」的載入狀態：按鈕載入中會吃掉點擊，使用者一進來就按
// 會沒反應（e2e 抓到）。手動按過就以手動結果為準，晚到的「上次結果」不蓋掉它
let ranThisVisit = false;
const lastLoading = ref(false);
async function run() {
  ranThisVisit = true;
  loading.value = true;
  try {
    report.value = await runAnomalyScan();
    lastRunAt.value = fmtDateTime(new Date());
    lastTrigger.value = "manual";
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.server"));
  } finally {
    loading.value = false;
  }
}
/** 進頁面先拿上次的結果（手動或排程）—— 以前每次進來都是空的，點去探測再返回也得重跑 */
async function loadLast() {
  lastLoading.value = true;
  try {
    const last = await getLastAnomalyReport();
    if (last.report && !report.value && !ranThisVisit) {
      report.value = last.report;
      lastRunAt.value = last.at ? fmtDateTime(last.at) : null;
      lastTrigger.value = last.trigger;
    }
  } catch { /* 拿不到就照舊：按「執行偵測」 */ }
  finally { lastLoading.value = false; }
}
onMounted(() => { void loadIgnorable(); void loadLast(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><AnomalyIcon /></n-icon>
        <span>{{ t("anomaly.title") }}</span>
      </n-space>
    </template>
    <n-space align="center" style="margin-bottom: 12px" :wrap-item="false">
      <n-button type="primary" :loading="loading" @click="run">
        <template #icon><n-icon><TestIcon /></n-icon></template>
        {{ t("anomaly.run_scan") }}
      </n-button>
      <span v-if="lastRunAt" style="opacity: 0.7; font-size: 13px">
        {{ t("anomaly.last_run") }}: {{ lastRunAt }}<template v-if="lastTrigger === 'schedule'">（{{ t("anomaly.by_schedule") }}）</template>
      </span>
      <n-button size="small" quaternary @click="openScope">
        <template #icon><n-icon><SettingsIcon /></n-icon></template>
        {{ t("anomaly.scope_btn") }}
      </n-button>
      <n-button size="small" quaternary @click="openSched">
        <template #icon><n-icon><PendingIcon /></n-icon></template>
        {{ t("anomaly.sched_btn") }}
      </n-button>
    </n-space>

    <!-- 偵測範圍：訪客／實驗網段本來就會一堆異常，留著只會把真正該處理的埋掉 -->
    <n-modal v-model:show="scopeShow" preset="card" style="max-width: 620px"
             :title="t('anomaly.scope_title')">
      <n-alert type="info" :bordered="false" style="margin-bottom:12px">
        {{ t("anomaly.scope_hint") }}
      </n-alert>
      <n-select v-model:value="scopeIds" multiple filterable clearable
                :options="subnetOptions" :loading="subnetsLoading"
                :placeholder="t('anomaly.scope_none')" />
      <template #footer>
        <n-space justify="end">
          <n-button @click="scopeShow = false">{{ t("common.cancel") }}</n-button>
          <n-button type="primary" :loading="scopeSaving" @click="saveScope">
            {{ t("common.save") }}
          </n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 排程：跑的是同一支偵測，差別在通知只發「與上次相比是新的」 -->
    <n-modal v-model:show="schedShow" preset="card" style="max-width: 640px"
             :title="t('anomaly.sched_title')">
      <n-alert type="info" :bordered="false" style="margin-bottom:12px">
        {{ t("anomaly.sched_hint") }}
      </n-alert>
      <n-space v-if="sched" vertical :size="14">
        <div>
          <n-switch :value="sched.schedule_enabled" :loading="schedSaving"
                    @update:value="(v: boolean) => patchSched({ schedule_enabled: v })" />
          <span style="margin-left:10px">{{ t("anomaly.sched_enabled") }}</span>
          <p class="sched-hint">{{ t("anomaly.sched_enabled_hint") }}</p>
        </div>
        <div>
          <label>{{ t("anomaly.sched_freq") }}</label>
          <n-space align="center" :size="12" :wrap="true">
            <n-select :value="sched.frequency" :options="freqOptions" style="width: 170px"
                      :disabled="!sched.schedule_enabled"
                      @update:value="(v: AnomalySchedule['frequency']) => patchSched({ frequency: v })" />
            <n-input-number v-if="sched.frequency === 'interval'" :value="sched.interval_minutes"
                            :min="5" :max="1440" :step="5" style="width: 150px"
                            :disabled="!sched.schedule_enabled"
                            @update:value="(v: number | null) => v && patchSched({ interval_minutes: v })" />
            <n-select v-if="sched.frequency === 'weekly'" :value="sched.weekdays" multiple
                      :options="weekdayOptions" style="min-width: 260px"
                      :disabled="!sched.schedule_enabled"
                      :placeholder="t('anomaly.sched_weekdays_ph')"
                      @update:value="(v: number[]) => v.length && patchSched({ weekdays: v })" />
            <n-input-number v-if="sched.frequency === 'monthly'" :value="sched.month_day"
                            :min="1" :max="31" style="width: 130px"
                            :disabled="!sched.schedule_enabled"
                            @update:value="(v: number | null) => v && patchSched({ month_day: v })" />
          </n-space>
          <p v-if="sched.frequency === 'interval'" class="sched-hint">
            {{ t("anomaly.sched_interval_hint") }}
          </p>
          <p v-if="sched.frequency === 'monthly'" class="sched-hint">
            {{ t("anomaly.sched_month_day_hint") }}
          </p>
        </div>
        <div v-if="sched.frequency !== 'interval'">
          <label>{{ t("anomaly.sched_times") }}</label>
          <div class="sched-times">
            <div v-for="(tm, i) in sched.times" :key="i" class="sched-time-row">
              <n-time-picker :value="hhmmToMs(tm)" format="HH:mm" style="width: 130px"
                             :disabled="!sched.schedule_enabled"
                             @update:formatted-value="(v: string | null) => setSchedTime(i, v)" />
              <n-button quaternary size="small"
                        :disabled="!sched.schedule_enabled || sched.times.length <= 1"
                        @click="removeSchedTime(i)">✕</n-button>
            </div>
            <n-button size="small" quaternary :disabled="!sched.schedule_enabled"
                      @click="patchSched({ times: [...sched.times, '12:00'] })">
              + {{ t("anomaly.sched_add_time") }}
            </n-button>
          </div>
          <p class="sched-hint">{{ t("anomaly.sched_times_hint") }}</p>
        </div>
        <div style="opacity:.75; font-size:13px">
          {{ t("anomaly.sched_last_run") }}:
          {{ sched.last_run_at ? fmtDateTime(sched.last_run_at) : t("anomaly.sched_never") }}
        </div>
      </n-space>
      <template #footer>
        <n-space justify="end">
          <n-button @click="schedShow = false">{{ t("common.close") }}</n-button>
        </n-space>
      </template>
    </n-modal>

    <n-alert v-if="!report" type="info">
      <template #icon><n-icon><InfoIcon /></n-icon></template>
      {{ t("anomaly.help") }}
    </n-alert>

    <template v-if="report">
      <!-- 非法 DHCP 一出現幾乎必定有事：它會把錯的位址跟閘道發給整個網段的機器。
           混在分頁裡跟其它異常一樣大小的話，看到的人不會知道這條要優先處理。 -->
      <n-alert v-if="report.rogue_dhcp?.length" type="error" :show-icon="true"
               style="margin-bottom: 16px" :title="rogueTitle">
        {{ t("anomaly.rogue_dhcp_warn") }}
        <div class="rogue-list">
          <span v-for="r in report.rogue_dhcp.slice(0, 8)" :key="r.server_ip" class="rogue-chip">
            {{ r.server_ip }}<template v-if="r.subnet_cidr"> · {{ r.subnet_cidr }}</template>
            <template v-if="r.vendor"> · {{ r.vendor }}</template>
          </span>
        </div>
      </n-alert>

      <!-- 統計卡：有框有底色才看得出是一組數字，且點下去直接切到那一類（回報） -->
      <n-grid :cols="4" x-gap="12" y-gap="12" style="margin-bottom: 16px">
        <n-gi v-for="s in statCards" :key="s.key">
          <div class="anom-stat" :class="{ 'anom-stat--hit': s.value > 0 }"
               role="button" tabindex="0"
               @click="activeTab = s.key" @keyup.enter="activeTab = s.key">
            <div class="anom-stat__label">{{ s.label }}</div>
            <div class="anom-stat__value">{{ s.value }}</div>
          </div>
        </n-gi>
      </n-grid>
      <n-empty v-if="!anyFindings" :description="t('anomaly.none_found')" style="margin: 24px 0" />

      <n-tabs v-else v-model:value="activeTab" type="line" animated>
        <n-tab-pane v-for="c in CATEGORIES" :key="c.key" :name="c.key"
                    :tab="tabLabel(c)">
          <!-- 每個類別先講清楚「這是什麼、為什麼會出現」，否則一長串 IP 沒人看得懂 -->
          <n-alert type="default" :bordered="false" :show-icon="false" class="cat-note">
            {{ t(`anomaly.explain_${c.key}`) }}
          </n-alert>
          <n-alert v-if="c.key === 'unauthorized_ips' && unauthTruncated" type="warning" :bordered="false"
                   :show-icon="false" class="cat-note" data-testid="unauth-truncated">
            {{ t("anomaly.unauthorized_truncated", { shown: report?.unauthorized_ips.length ?? 0,
                                                     total: report?.unauthorized_total ?? 0 }) }}
          </n-alert>
          <template v-if="catRows(c.key).length">
            <div class="cat-toolbar">
              <n-input v-model:value="filterQ" clearable size="small" class="cat-filter"
                       :placeholder="t('anomaly.filter_ph')" />
              <ColumnPicker :all="pickerItems(c.key)" :visible="prefs[c.key].visibleKeys.value"
                            @update:visible="prefs[c.key].setVisible" @reset="prefs[c.key].reset"
                            :order="prefs[c.key].order.value" @update:order="prefs[c.key].setOrder" />
            </div>
            <n-data-table :columns="catCols(c.key)" :data="shownRows(c.key)"
                          :bordered="false" size="small" :scroll-x="catScrollX(c.key)" :pagination="pg" />
          </template>
          <n-empty v-else :description="t('anomaly.none_found')" style="margin: 16px 0" />
          <n-collapse v-if="c.key === 'mac_drifts' && driftRefs.length" style="margin-top: 12px"
                      data-testid="drift-reference">
            <n-collapse-item :title="t('anomaly.drift_reference', { n: driftRefs.length })" name="ref">
              <div class="drift-ref-hint">{{ t("anomaly.drift_reference_hint") }}</div>
              <n-data-table :columns="driftRefCols" :data="driftRefs" :bordered="false" size="small"
                            :scroll-x="1030" :pagination="pg" />
            </n-collapse-item>
          </n-collapse>
        </n-tab-pane>
      </n-tabs>
    </template>
  </n-card>

  <!-- AI 鑑識卡：走全站共用 markdown 渲染器（先跳脫再產標籤，無注入面）——
       原本 pre-wrap 純文字會把 **粗體** 的星號原樣露出（使用者截圖）；＋模型標示＋下載 -->
  <n-modal :show="!!triageShow" preset="card" style="width: 560px; max-width: 94vw"
           :title="t('anomaly.triage_title', { ip: triageShow ?? '' })"
           @update:show="(v: boolean) => { if (!v) triageShow = null; }">
    <n-alert type="warning" :bordered="false" style="margin-bottom: 10px">
      {{ triageShow ? triageResults[triageShow]?.disclaimer : "" }}
    </n-alert>
    <!-- eslint-disable-next-line vue/no-v-html -->
    <div class="triage-body" v-html="renderMarkdown(triageShow ? triageResults[triageShow]?.card ?? '' : '')" />
    <template #footer>
      <div class="triage-foot">
        <span class="triage-model">{{ t("fw_changes.ai_model") }}：{{
          (triageShow ? triageResults[triageShow]?.model : "") || "—" }}</span>
        <n-space :size="8">
          <n-button size="small" secondary @click="downloadTriage('md')">
            <template #icon><n-icon><DownloadIcon /></n-icon></template>
            {{ t("fw_changes.ai_dl_md") }}
          </n-button>
          <n-button size="small" secondary @click="downloadTriage('txt')">
            <template #icon><n-icon><DownloadIcon /></n-icon></template>
            {{ t("fw_changes.ai_dl_txt") }}
          </n-button>
        </n-space>
      </div>
    </template>
  </n-modal>
</template>

<!-- 表格儲存格是 NDataTable 用 render 函式畫的，scoped 樣式套不到：IP 衝突的 MAC 列樣式放這裡（以前寫在 scoped 裡，
     廠商標籤沒有底色也不會不折行，MAC 與廠商被折成兩三行、各列對不齊） -->
<style>
.anm-mac-row {
  display: grid; grid-template-columns: 17.5ch 8.5em minmax(0, 1fr) auto;
  column-gap: 8px; align-items: baseline; font-size: 12.5px;
}
.anm-mac-row > a, .anm-mac-row > span { min-width: 0; }
.anm-mac-row > a { white-space: nowrap; }
.anm-mac-tag {
  font-size: 11px; padding: 0 6px; border-radius: 3px; white-space: nowrap; justify-self: start; max-width: 100%;
  overflow: hidden; text-overflow: ellipsis;
  background: var(--n-color-embedded, rgba(128, 128, 128, .12));
  color: var(--n-text-color-disabled, inherit);
}
/* 本地管理／隨機位址：標成警示色，因為它是「多半不是真衝突」的主要線索 */
.anm-mac-tag--local { background: rgba(240, 160, 32, .16); color: #b26a00; }
/* 疑似讀壞／過期快取：灰底刪除線語氣，表示「列出來但不算數」 */
.anm-mac-tag--suspect { background: rgba(128, 128, 128, .18); color: inherit; opacity: .8; font-style: italic; }
.anm-mac-seen { font-size: 11.5px; opacity: .6; }
.anm-mac-time { opacity: .55; white-space: nowrap; }
</style>

<style scoped>
/* 排程視窗：提示文字要小一號、灰一點，否則整個視窗看起來都是同等重要的字 */
.sched-hint { margin: 6px 0 0; font-size: 12px; opacity: .7; line-height: 1.5; }
.sched-times { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.sched-time-row { display: flex; align-items: center; gap: 2px; }

/* 統計卡：外框 + 底色，數字有值時轉為警示色（原本是裸數字，看起來像沒對齊的散字） */
.anom-stat {
  border: 1px solid var(--n-border-color, rgba(128, 128, 128, 0.28));
  border-radius: 10px;
  padding: 10px 14px;
  background: rgba(127, 127, 127, 0.04);
  cursor: pointer;
  transition: border-color .15s ease, background .15s ease, transform .1s ease;
}
.anom-stat:hover { border-color: #18a058; transform: translateY(-1px); }
.anom-stat__label { font-size: 12.5px; opacity: .7; }
.anom-stat__value { font-size: 26px; font-weight: 600; line-height: 1.25; }
.anom-stat--hit {
  border-color: rgba(240, 160, 32, .55);
  background: rgba(240, 160, 32, .09);
}
.anom-stat--hit .anom-stat__value { color: #d97706; }

.triage-body { font-size: 13px; line-height: 1.85; }
.triage-body :deep(code) { background: rgba(128, 128, 128, .14); border-radius: 4px;
  padding: 1px 5px; font-size: 12px; }
.triage-body :deep(p) { margin: 6px 0; }
.triage-body :deep(ul), .triage-body :deep(ol) { margin: 4px 0; padding-left: 20px; }
.triage-foot { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.triage-model { font-size: 12px; opacity: .65; }
.cat-note { margin: 4px 0 14px; font-size: 12.5px; line-height: 1.7; }
.cat-toolbar {
  display: flex; justify-content: space-between; align-items: center;
  gap: 8px; flex-wrap: wrap; margin-bottom: 8px;
}
.cat-filter { width: 320px; max-width: 100%; }
.rogue-list { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 6px; }
.rogue-chip {
  font-family: var(--font-mono, monospace); font-size: 12px;
  padding: 2px 8px; border-radius: 4px; background: rgba(208, 48, 80, .12);
}
.drift-ref-hint { font-size: 12.5px; opacity: .7; margin-bottom: 8px; }
</style>
