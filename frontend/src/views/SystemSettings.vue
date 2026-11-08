<script setup lang="ts">
import { getImpactSettings, putImpactSettings } from "@/api/changeImpact";
import { Maintenance } from "@/api/maintenance";
import { useChangeImpact } from "@/composables/useChangeImpact";
/**
 * 系統設定（僅管理員）— 全域、所有使用者共用的設定，獨立於「個人設定」。
 * 含：地圖供應商、機櫃名稱對齊、上線判定閾值、GeoIP(MaxMind) 本地資料庫與排程。
 */
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NSpace, NIcon, NSelect, NInput, NInputNumber, NSwitch, NCheckbox, NCheckboxGroup,
  NButton, NPopconfirm, NTag, NAlert, useMessage,
} from "naive-ui";
const origin = window.location.origin;
import { AdminIcon, SaveIcon, RefreshIcon, RequestsIcon, WarnIcon, DeleteIcon, OkIcon, CopyIcon, EyeIcon, PlayIcon } from "@/icons";
import { getRackEmbedConfig, revealRackEmbedToken, setRackEmbedConfig, type RackEmbedConfig } from "@/api/racks";
import { getBackupEncryption, removeBackupEncryption, setBackupEncryption, type BackupEncryption } from "@/api/system";
import { fmtDate } from "@/utils/datetime";
import { getLdap, putLdap, testLdap, testLdapAuth, type LdapConfig,
  getAuditForward, putAuditForward, testAuditForward, type AuditForward,
  getOidcConfig, putOidcConfig, testOidc, type OidcConfig,
  getSamlConfig, putSamlConfig, testSaml, type SamlConfig,
  getConsoleSecurity, setConsoleSecurity, type RdpEngine, type ConsoleEngine,
  type ConsoleSecurity, type ConsoleSecurityPatch, requestSftpProbeTicket,
  getUiDisplay, setUiDisplay,
  getDevicePortFilter, setDevicePortFilter } from "@/api/system";
import { listGroups } from "@/api/admin";
import { useRouter } from "vue-router";
import { getAutolink, putAutolink, previewAutolink,
  type AutolinkConfig, type AutolinkPreview } from "@/api/system";
import { listSubnets } from "@/api/subnets";
import { fmtDateTime, fmtRelative } from "@/utils/datetime";
import { evidenceVendorLabel } from "@/utils/investigateSections";
import { apiErrMsg } from "@/api/client";
import GlobalSearchSettings from "@/components/GlobalSearchSettings.vue";
import DnsResolversSettings from "@/components/DnsResolversSettings.vue";
import { runSftpProbe, fmtRate, roughDuration, type ProbeResult } from "@/utils/sftpProbe";
import {
  getMapProvider, setMapProvider, getRackNameAlign, setRackNameAlign,
  getOnlineGrace, setOnlineGrace,
  getIpCooldown, setIpCooldown,
  getCertExpiryDays, setCertExpiryDays, getAgentAutoPin, setAgentAutoPin,
  getGeoipConfig, setGeoipConfig, updateGeoipDbNow,
  type GeoIPConfig, type RackNameAlign, type LivenessSource,
} from "@/api/basic";

const { t } = useI18n();
const router = useRouter();
const msg = useMessage();

// 地圖供應商
// 連線管理資安：RDP 控制端貼上文字到被控端（預設關閉）＋ RDP 連線引擎
// 兩個欄位共用同一個端點，所以每次都要把另一個一起送回去，否則會把它蓋成預設值
const rdpClipPaste = ref(false);
const consoleRelay = ref(false);
// RDP／VNC 的預設是 guacd（2026-09-27）；載入前先顯示預設值
const rdpEngine = ref<RdpEngine>("guacd");
// 這台機器能不能用 FreeRDP（後端算好的事實）。缺套件時要把安裝指令原樣顯示出來，
// 讓管理者可以直接複製 —— 不要只說「不可用」。
const freerdpOk = ref(true);
const freerdpMissing = ref<string[]>([]);
/** aardwolf（選用的備用引擎：RDP 的 aardwolf、VNC 的內建）有沒有裝起來；裝不起來多半是 Python 太新（issue #39／#42） */
const aardwolfOk = ref(true);
const pythonVer = ref("");
const freerdpCmd = ref("");
const rdpEngineOpts = computed(() => [
  { label: t("settings.system.engine_guacd_default"), value: "guacd" },
  { label: t("settings.system.rdp_engine_aardwolf"), value: "aardwolf" },
  { label: t("settings.system.rdp_engine_freerdp"), value: "freerdp" },
]);
// VNC 預設 guacd、SSH 預設內建：「（預設）」標在各自的預設值上，所以兩份選項分開
const vncEngine = ref<ConsoleEngine>("guacd");
const sshEngine = ref<ConsoleEngine>("builtin");
const vncEngineOpts = computed(() => [
  { label: t("settings.system.engine_guacd_default"), value: "guacd" },
  { label: t("settings.system.engine_builtin_plain"), value: "builtin" },
]);
const sshEngineOpts = computed(() => [
  { label: t("settings.system.engine_builtin"), value: "builtin" },
  { label: t("settings.system.engine_guacd"), value: "guacd" },
]);
// guacd 服務的狀態（後端實際連一次問出來的）：選了 guacd 卻沒在跑，要用警示色講清楚
const guacdOk = ref(false);
// 載入完成前不畫 guacd 狀態：guacd 是 RDP／VNC 的預設，未載入的初始值會先閃一個紅色「連不到 guacd（）」
const consoleLoaded = ref(false);
const guacdProtocols = ref<Record<string, boolean>>({});
const guacdAddress = ref("");
const guacdError = ref("");
const guacdCmd = ref("");
const guacdUsedBy = computed(() => [
  rdpEngine.value === "guacd" ? "rdp" : "", vncEngine.value === "guacd" ? "vnc" : "",
  sshEngine.value === "guacd" ? "ssh" : "",
].filter(Boolean));
const guacdMissingFor = computed(() => guacdUsedBy.value.filter((p) => !guacdProtocols.value[p]));
// SFTP 單檔上限（MB）與傳輸路徑檢查。上限放大之後，瀏覽器 → 前端反向代理 → IPAM 的 nginx
// 任何一層吃不下都會讓傳輸失敗，而那些設定讀不到 —— 所以由瀏覽器實際送一次（utils/sftpProbe）。
const SFTP_MAX_DEFAULT = 100;
const sftpMaxMb = ref(SFTP_MAX_DEFAULT);
// 載入完成前留空：否則畫面先顯示預設的 100，這時輸入的值會被晚到的載入結果蓋掉
const sftpMaxInput = ref<number | null>(null);
const probe = ref<{ running: boolean; stage?: "up" | "down"; pct?: number; result?: ProbeResult } | null>(null);
async function checkSftpPath() {
  if (probe.value?.running) return;
  probe.value = { running: true };
  try {
    const tk = await requestSftpProbeTicket();
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const result = await runSftpProbe({
      url: `${proto}//${window.location.host}${tk.ws_path}?ticket=${encodeURIComponent(tk.ticket)}`,
      upBytes: tk.up_bytes, downBytes: tk.down_bytes,
      onProgress: (stage, done, total) => {
        if (probe.value) probe.value = { running: true, stage, pct: Math.round((done / total) * 100) };
      },
    });
    probe.value = { running: false, result };
  } catch (e) {
    probe.value = { running: false, result: { ok: false, problem: "error", detail: apiErrMsg(e) } };
  }
}
/** 檢查結果要講的話（成功也講：速度與「傳一個上限大小的檔案要多久」） */
const probeText = computed(() => {
  const r = probe.value?.result;
  if (!r) return "";
  const max = sftpMaxMb.value >= 1024 ? `${(sftpMaxMb.value / 1024).toFixed(1)} GB` : `${sftpMaxMb.value} MB`;
  const bytes = sftpMaxMb.value * 1024 * 1024;
  if (r.ok) {
    return t("settings.system.sftp_probe_ok", {
      up: fmtRate(r.upBps!), down: fmtRate(r.downBps!), max,
      upTime: roughDuration(bytes / r.upBps!, t), downTime: roughDuration(bytes / r.downBps!, t),
    });
  }
  const mb = (n?: number) => `${((n ?? 0) / 1024 / 1024).toFixed(1)} MB`;
  return t(`settings.system.sftp_probe_${r.problem}`, {
    code: r.closeCode ?? "-", done: mb(r.done), total: mb(r.total), sec: r.stallSec ?? 0,
    reason: r.detail ?? "",
  });
});
/** 成功但慢到傳一個上限大小的檔案要超過一小時：用警示色，提醒這個上限在這條路上不實際 */
const probeSlow = computed(() => {
  const r = probe.value?.result;
  return !!r?.ok && (sftpMaxMb.value * 1024 * 1024) / Math.min(r.upBps!, r.downBps!) > 3600;
});
function commitSftpMax() {
  if (sftpMaxInput.value === null) return;
  const v = Math.round(Number(sftpMaxInput.value));
  if (v === sftpMaxMb.value) return;
  // 範圍自己檢查、不讓輸入框自動夾到邊界：多打幾個 0 被默默存成 100 GB 比擋下來更糟
  if (!Number.isFinite(v) || v < 1 || v > 102400) {
    msg.warning(t("settings.system.sftp_max_mb_range"));
    sftpMaxInput.value = sftpMaxMb.value;
    return;
  }
  void saveConsole({ sftp_max_file_mb: v }).then(() => {
    sftpMaxInput.value = sftpMaxMb.value;     // 存檔失敗時回到原本的值
    // 改了上限就自動檢查一次路徑（使用者要求：前面的吃不下要自動檢查出來並提示）
    if (sftpMaxMb.value === v && v > SFTP_MAX_DEFAULT) void checkSftpPath();
  });
}
function applyConsole(c: ConsoleSecurity) {
  rdpClipPaste.value = c.rdp_clipboard_paste;
  consoleRelay.value = !!c.console_relay;
  sftpMaxMb.value = c.sftp_max_file_mb ?? SFTP_MAX_DEFAULT;
  sftpMaxInput.value = sftpMaxMb.value;
  rdpEngine.value = c.rdp_engine;
  vncEngine.value = c.vnc_engine ?? "guacd";
  sshEngine.value = c.ssh_engine ?? "builtin";
  freerdpOk.value = c.freerdp_available ?? true;
  freerdpMissing.value = c.freerdp_missing ?? [];
  aardwolfOk.value = c.aardwolf_available ?? true;
  pythonVer.value = c.python_version ?? "";
  freerdpCmd.value = c.freerdp_install_cmd ?? "";
  guacdOk.value = !!c.guacd_available;
  consoleLoaded.value = true;
  guacdProtocols.value = c.guacd_protocols ?? {};
  guacdAddress.value = c.guacd_address ?? "";
  guacdError.value = c.guacd_error ?? "";
  guacdCmd.value = c.guacd_install_cmd ?? "";
}
// 同一個端點管好幾個欄位：每次都把全部送回去，否則沒送的會被蓋成預設值
async function saveConsole(patch: Partial<ConsoleSecurityPatch>) {
  const prev = { clip: rdpClipPaste.value, rdp: rdpEngine.value, vnc: vncEngine.value, ssh: sshEngine.value };
  try {
    const c = await setConsoleSecurity({
      rdp_clipboard_paste: rdpClipPaste.value, rdp_engine: rdpEngine.value,
      vnc_engine: vncEngine.value, ssh_engine: sshEngine.value,
      sftp_max_file_mb: sftpMaxMb.value, ...patch,
    });
    applyConsole(c);
    msg.success(t("common.ok"));
  } catch {
    rdpClipPaste.value = prev.clip; rdpEngine.value = prev.rdp;
    vncEngine.value = prev.vnc; sshEngine.value = prev.ssh;
    msg.error(t("errors.network"));
  }
}
// 變更影響預演的設定（後端 /change-impact/settings；管理員才拿得到 config）
const impactCfg = ref<Record<string, any>>({ enabled: false, ai_enabled: true, allow_self_review: false,
                                             run_valid_hours: 24, retention_days: 180 });
const impactShared = useChangeImpact();
async function loadImpact() {
  try { const s = await getImpactSettings(); if (s.config) impactCfg.value = s.config; } catch { /* 舊後端沒有這支 */ }
}
async function saveImpact(patch: Record<string, unknown>) {
  const prev = { ...impactCfg.value };
  impactCfg.value = { ...impactCfg.value, ...patch };
  try {
    const s = await putImpactSettings(patch);
    if (s.config) impactCfg.value = s.config;
    void impactShared.load(true);          // 選單與入口跟著出現或消失
    msg.success(t("common.ok"));
  } catch { impactCfg.value = prev; msg.error(t("errors.network")); }
}
// 維護排程（2026-11-01）：一次維護最長幾小時、範圍超過幾個 IP 要再確認
const maintCfg = ref({ max_hours: 168, large_threshold: 500 });
async function saveMaint(patch: Partial<{ max_hours: number; large_threshold: number }>) {
  const prev = { ...maintCfg.value };
  maintCfg.value = { ...maintCfg.value, ...patch };
  try { maintCfg.value = await Maintenance.saveSettings(maintCfg.value); msg.success(t("common.ok")); }
  catch { maintCfg.value = prev; msg.error(t("errors.network")); }
}
function changeRdpClipPaste(v: boolean) { rdpClipPaste.value = v; void saveConsole({ rdp_clipboard_paste: v }); }
// 主控台中繼：只送這一個欄位（後端「沒帶＝維持原值」），失敗時退回原值
async function changeConsoleRelay(v: boolean) {
  const prev = consoleRelay.value;
  consoleRelay.value = v;
  try { applyConsole(await setConsoleSecurity({ rdp_clipboard_paste: rdpClipPaste.value, rdp_engine: rdpEngine.value,
                                                 console_relay: v })); msg.success(t("common.ok")); }
  catch { consoleRelay.value = prev; msg.error(t("errors.network")); }
}
function changeRdpEngine(v: RdpEngine) { rdpEngine.value = v; void saveConsole({ rdp_engine: v }); }
function changeVncEngine(v: ConsoleEngine) { vncEngine.value = v; void saveConsole({ vnc_engine: v }); }
function changeSshEngine(v: ConsoleEngine) { sshEngine.value = v; void saveConsole({ ssh_engine: v }); }

// 異動記錄淡化天數（超過 N 天的項目以淡色顯示；0 = 不淡化）
const changeLogDimDays = ref(30);
async function changeDimDays(v: number | null) {
  const days = Math.max(0, Math.min(3650, Math.round(v ?? 0)));
  changeLogDimDays.value = days;
  try { await setUiDisplay({ change_log_dim_days: days }); msg.success(t("common.ok")); }
  catch (e) { msg.error(apiErrMsg(e)); }
}

// 裝置連接埠匯入：偽介面過濾（總開關 + 名稱樣式清單，一行一個正則）
const portFilterOn = ref(true);
const portFilterText = ref("");
async function savePortFilter() {
  const patterns = portFilterText.value.split("\n").map((s) => s.trim()).filter(Boolean);
  try {
    const saved = await setDevicePortFilter({ filter_pseudo: portFilterOn.value, ignore_patterns: patterns });
    portFilterOn.value = saved.filter_pseudo;
    portFilterText.value = saved.ignore_patterns.join("\n");
    msg.success(t("common.ok"));
  } catch (e) { msg.error(apiErrMsg(e)); }
}

const mapProvider = ref<"builtin" | "osm" | "google">("builtin");
const mapProviderOpts = computed(() => [
  { label: t("settings.system.map_builtin"), value: "builtin" },
  { label: "OpenStreetMap", value: "osm" },
  { label: "Google Maps", value: "google" },
]);
async function changeMapProvider(p: "builtin" | "osm" | "google") {
  mapProvider.value = p;
  try { await setMapProvider(p); msg.success(t("common.ok")); } catch (e) { msg.error(apiErrMsg(e)); }
}

// 機櫃裝置名稱對齊
const rackAlign = ref<RackNameAlign>("left");
const rackAlignOpts = computed(() => [
  { label: t("settings.system.align_left"), value: "left" },
  { label: t("settings.system.align_center"), value: "center" },
  { label: t("settings.system.align_right"), value: "right" },
]);
async function changeRackAlign(a: RackNameAlign) {
  rackAlign.value = a;
  try { await setRackNameAlign(a); msg.success(t("common.ok")); } catch (e) { msg.error(apiErrMsg(e)); }
}

// 上線判定：閾值（分鐘）＋採信哪些證據
const grace = ref(30);
// LibreNMS 的 ARP 預設不勾 —— 它沒有時間概念，來源設備的快取不老化就會讓關機的
// 機器一直顯示上線。候選清單由後端給（只列這個站台真的有的整合）。
const livenessSrc = ref<string[]>(["scanner", "librenms"]);
const livenessAvail = ref<LivenessSource[]>([]);

const VENDOR_LABEL: Record<string, string> = {
  opnsense: "OPNsense", pfsense: "pfSense",
  fortigate: "FortiGate", paloalto: "Palo Alto", checkpoint: "Check Point", mikrotik: "MikroTik",
  librenms: "LibreNMS",
};

/** `arp:opnsense` → 「ARP 表（OPNsense）」；沒有廠牌後綴的用既有翻譯。 */
function srcLabel(key: string): string {
  if (!key.includes(":")) return t(`system_settings.src_${key}`);
  const [kind, vendor] = key.split(":", 2);
  return `${t(`system_settings.src_kind_${kind}`)}（${VENDOR_LABEL[vendor] ?? evidenceVendorLabel(vendor, t)}）`;
}

/** 分組顯示時，種類已經在列首寫過了 → 每一格只寫廠牌名。 */
function vendorLabel(key: string): string {
  // 舊的籠統 "arp" 就是 LibreNMS 的 ARP（同一件事的舊鍵）→ 併進 ARP 那一列，
  // 放在「探測／監控」會既不同類、又因為字長而把那一列撐出換行。
  if (key === "arp") return "LibreNMS";
  const vendor = key.split(":", 2)[1] ?? key;
  return VENDOR_LABEL[vendor] ?? evidenceVendorLabel(vendor, t);
}

/** 這個來源歸在哪一組。 */
function srcKind(key: string): string {
  if (key === "arp") return "arp";
  return key.includes(":") ? key.split(":", 1)[0] : "base";
}

//: 分組的順序＝可信度由高到低（探測 → 防火牆的 ARP／VPN → 不會過期的租約與 ARP 記錄）
// iface＝設備自己的介面位址（會過期）：以前漏在這張表外，介面來源整組沒有出現在設定頁
const SRC_KINDS = ["base", "arp", "vpn", "iface", "lease"] as const;

const livenessGroups = computed(() =>
  SRC_KINDS.map((kind) => ({
    kind,
    title: kind === "base"
      ? t("system_settings.src_kind_base")
      : t(`system_settings.src_kind_${kind}`),
    // 會過期的排前面：那些才是預設採信的，不建議的（不會過期）擺最後
    items: livenessAvail.value.filter((s) => srcKind(s.key) === kind)
      .slice().sort((a, b) => Number(b.aging) - Number(a.aging)),
  })).filter((g) => g.items.length));
// IP 生命週期：釋放後的冷卻天數
const cooldownDays = ref(30);
// 憑證到期通知的全域預設。每張憑證可各自覆寫（憑證頁的鈴鐺按鈕）——
// 這裡只是「沒特別設定時用哪個」。
const certExpiryDays = ref(21);
async function changeCertExpiry(n: number | null) {
  if (n == null) return;
  certExpiryDays.value = n;
  try { await setCertExpiryDays(n); msg.success(t("common.ok")); } catch (e) { msg.error(apiErrMsg(e)); }
}
// 代理自動改成驗證 jt-ipam（2026-11-07）：還沒驗證的代理更新後自己換，不必逐台重跑安裝指令
const agentAutoPin = ref(true);
async function changeAgentAutoPin(v: boolean) {
  const old = agentAutoPin.value;
  agentAutoPin.value = v;
  try { await setAgentAutoPin(v); msg.success(t("common.ok")); }
  catch (e) { agentAutoPin.value = old; msg.error(apiErrMsg(e)); }
}
async function changeCooldown(v: number | null) {
  const n = v ?? 0;
  cooldownDays.value = n;
  try { await setIpCooldown(n); msg.success(t("common.ok")); } catch (e) { msg.error(apiErrMsg(e)); }
}

async function changeSources(v: (string | number)[]) {
  const list = v.map(String);
  livenessSrc.value = list;
  try { await setOnlineGrace(grace.value, list); msg.success(t("common.ok")); }
  catch (e) { msg.error(apiErrMsg(e)); }
}
async function changeGrace(v: number | null) {
  const n = v ?? 30;
  grace.value = n;
  try { await setOnlineGrace(n, livenessSrc.value); msg.success(t("common.ok")); }
  catch (e) { msg.error(apiErrMsg(e)); }
}

// GeoIP
// 機櫃圖對外嵌入：這裡只管系統層的總開關與權杖，要公開哪一櫃在機櫃頁逐櫃決定
const rackEmbed = ref<RackEmbedConfig | null>(null);
const showRackToken = ref(false);
// 權杖不再隨設定回傳：按「顯示」或「複製」才向後端取（後端每次都留稽核）
const rackEmbedToken = ref("");
const rackTokenDays = ref(365);
const tokenDayOptions = computed(() => [30, 90, 180, 365].map((d) => ({ label: t("llm_settings.mcp_key_days_n", { n: d }), value: d })));
const rackTokenExpired = computed(() => {
  const s = rackEmbed.value?.token_expires_at;
  return !!s && new Date(s).getTime() <= Date.now();
});
async function loadRackEmbed() {
  try { rackEmbed.value = await getRackEmbedConfig(); } catch { rackEmbed.value = null; }
}
async function ensureRackToken(): Promise<string> {
  if (!rackEmbedToken.value && rackEmbed.value?.token_set) rackEmbedToken.value = await revealRackEmbedToken();
  return rackEmbedToken.value;
}
async function toggleRackToken() {
  if (!showRackToken.value) {
    try { await ensureRackToken(); } catch { msg.error(t("errors.server")); return; }
  }
  showRackToken.value = !showRackToken.value;
}
async function changeRackEmbed(v: boolean) {
  try {
    rackEmbed.value = await setRackEmbedConfig(v);
    msg.success(t("common.saved"));
  } catch { msg.error(t("errors.server")); }
}
async function regenRackToken() {
  try {
    rackEmbed.value = await setRackEmbedConfig(rackEmbed.value?.enabled ?? true, true, rackTokenDays.value);
    rackEmbedToken.value = "";
    showRackToken.value = false;
    // 舊網址立刻失效，這件事一定要講出來，否則別人的儀表板會突然破圖而找不到原因
    msg.success(t("system_settings.rack_embed_regenerated"));
  } catch { msg.error(t("errors.server")); }
}
async function copyRackToken() {
  try {
    const tok = await ensureRackToken();
    if (!tok) return;
    await navigator.clipboard.writeText(tok);
    msg.success(t("common.copied"));
  } catch { msg.error(t("errors.server")); }
}

// 每日備份加密：沒設定時備份檔裡同時有資料庫與解密金鑰（backend.env）
const backupEnc = ref<BackupEncryption | null>(null);
const backupPw = ref("");
const backupPw2 = ref("");
const backupEncSaving = ref(false);
async function loadBackupEnc() {
  try { backupEnc.value = await getBackupEncryption(); } catch { backupEnc.value = null; }
}
async function saveBackupEnc() {
  if (backupPw.value.length < 12) { msg.warning(t("system_settings.backup_enc_too_short")); return; }
  if (backupPw.value !== backupPw2.value) { msg.warning(t("users.error_password_mismatch")); return; }
  backupEncSaving.value = true;
  try {
    backupEnc.value = await setBackupEncryption(backupPw.value);
    backupPw.value = ""; backupPw2.value = "";
    msg.success(t("common.saved"));
  } catch (e) { msg.error(apiErrMsg(e)); } finally { backupEncSaving.value = false; }
}
async function clearBackupEnc() {
  try { await removeBackupEncryption(); await loadBackupEnc(); msg.success(t("common.saved")); }
  catch (e) { msg.error(apiErrMsg(e)); }
}

const geoip = ref<GeoIPConfig | null>(null);
const geoipAccount = ref("");
const geoipKey = ref("");
const geoipSaving = ref(false);
const geoipUpdating = ref(false);
const geoipEditionOpts = computed(() => (geoip.value?.all_editions ?? []).map((e) => ({ label: e, value: e })));
const geoipFreqOpts = computed(() => (geoip.value?.frequencies ?? []).map((f) => ({ label: t(`settings.system.freq_${f.replace("-", "_")}`), value: f })));
async function loadGeoip() {
  try { geoip.value = await getGeoipConfig(); geoipAccount.value = geoip.value.account_id ?? ""; } catch { /* ignore */ }
}
async function saveGeoip() {
  if (!geoip.value) return;
  geoipSaving.value = true;
  try {
    geoip.value = await setGeoipConfig({
      account_id: geoipAccount.value.trim() || null,
      license_key: geoipKey.value.trim() || null,
      editions: geoip.value.editions,
      auto_update: geoip.value.auto_update,
      frequency: geoip.value.frequency,
    });
    geoipKey.value = "";
    msg.success(t("common.saved"));
  } catch (e) { msg.error(apiErrMsg(e)); } finally { geoipSaving.value = false; }
}
async function updateGeoipNow() {
  geoipUpdating.value = true;
  try {
    const r = await updateGeoipDbNow();
    geoip.value = r.config;
    if (r.result?.error === "not_configured") msg.warning(t("settings.system.geoip_need_creds"));
    else msg.success(t("settings.system.geoip_updated"));
  } catch (e) { msg.error(apiErrMsg(e)); } finally { geoipUpdating.value = false; }
}
function fmtBytes(n: number | null): string {
  if (!n) return "—";
  return n > 1e6 ? (n / 1e6).toFixed(1) + " MB" : (n / 1e3).toFixed(0) + " KB";
}

// 外部認證 / LDAP（AD）
const ldap = ref<LdapConfig>({
  enabled: false, server: null, port: 389, use_ssl: false, use_starttls: true,
  bind_dn: null, password_set: false, search_base: null,
  user_filter: "(sAMAccountName={username})", attr_email: "mail",
  attr_display_name: "displayName", attr_member_of: "memberOf", admin_groups: [],
  default_group_id: null, ca_cert_pem: null, allow_weak_tls: false,
});
const ldapGroups = ref<{ label: string; value: string }[]>([]);
const ldapGroupOpts = computed(() => [{ label: t("settings.system.ldap_no_default_role"), value: "" }, ...ldapGroups.value]);
const ldapDefaultGroup = computed<string>({
  get: () => ldap.value.default_group_id ?? "",
  set: (v) => { ldap.value.default_group_id = v || null; },
});
async function loadLdapGroups() {
  try { const r = await listGroups(200, 0); ldapGroups.value = r.items.map((g) => ({ label: g.name, value: g.id })); } catch { /* ignore */ }
}
const ldapPw = ref("");           // 留空＝不變更；輸入＝更新
const ldapSaving = ref(false);
const ldapTesting = ref(false);
const ldapTlsOpts = computed(() => [
  { label: "StartTLS (389)", value: "starttls" },
  { label: "LDAPS (636)", value: "ssl" },
  { label: t("settings.system.ldap_tls_none"), value: "none" },
]);
const ldapTlsMode = computed<"starttls" | "ssl" | "none">({
  get: () => ldap.value.use_ssl ? "ssl" : ldap.value.use_starttls ? "starttls" : "none",
  set: (m) => { ldap.value.use_ssl = m === "ssl"; ldap.value.use_starttls = m === "starttls"; },
});
const ldapCaPem = computed<string>({
  get: () => ldap.value.ca_cert_pem ?? "",
  set: (v) => { ldap.value.ca_cert_pem = v; },
});
const ldapGroupsText = computed<string>({
  get: () => ldap.value.admin_groups.join("\n"),
  set: (v) => { ldap.value.admin_groups = v.split("\n").map((s) => s.trim()).filter(Boolean); },
});
async function loadLdap() { try { ldap.value = await getLdap(); ldapPw.value = ""; } catch { /* ignore */ } }
async function saveLdap() {
  ldapSaving.value = true;
  try {
    const { password_set: _ps, ca_cert_subjects: _cs, ...rest } = ldap.value;
    ldap.value = await putLdap({ ...rest, bind_password: ldapPw.value ? ldapPw.value : null });
    ldapPw.value = "";
    msg.success(t("common.saved"));
  } catch (e) { msg.error(apiErrMsg(e)); } finally { ldapSaving.value = false; }
}
async function clearLdapPw() {
  ldapSaving.value = true;
  try {
    const { password_set: _ps, ca_cert_subjects: _cs, ...rest } = ldap.value;
    ldap.value = await putLdap({ ...rest, bind_password: "" });
    ldapPw.value = "";
    msg.success(t("common.ok"));
  } catch (e) { msg.error(apiErrMsg(e)); } finally { ldapSaving.value = false; }
}
async function doTestLdap() {
  ldapTesting.value = true;
  try {
    const r = await testLdap();
    msg.success(`${t("settings.system.ldap_test_ok")} — ${r.who_am_i || r.server}`);
    // 連得上不代表登入會過：搜尋起點讀不到、AD 卻用 OpenLDAP 的 (uid=…) 都在這裡先講（2026-10-21）
    for (const w of r.warnings ?? []) msg.warning(t(`errors.${w.code}`, w.params), { duration: 15000, closable: true });
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { ldapTesting.value = false; }
}

// ── OIDC SSO ──
const oidc = ref<OidcConfig>({
  enabled: false, issuer: null, client_id: null, client_secret_set: false,
  redirect_uri: null, scope: "openid profile email",
  groups_claim: "groups", username_claim: "preferred_username",
  admin_groups: [], default_group_id: null,
});
const oidcSecret = ref("");        // 留空＝不變更；輸入＝更新
const oidcSaving = ref(false);
const oidcTesting = ref(false);
const oidcAdminGroupsText = computed<string>({
  get: () => (oidc.value.admin_groups || []).join(", "),
  set: (v) => { oidc.value.admin_groups = v.split(",").map((x) => x.trim()).filter(Boolean); },
});
async function loadOidc() { try { oidc.value = await getOidcConfig(); oidcSecret.value = ""; } catch { /* ignore */ } }
async function saveOidc() {
  oidcSaving.value = true;
  try {
    const { client_secret_set: _s, ...rest } = oidc.value;
    const patch: any = { ...rest };
    if (oidcSecret.value) patch.client_secret = oidcSecret.value;  // 只在輸入時更新
    oidc.value = await putOidcConfig(patch);
    oidcSecret.value = "";
    msg.success(t("common.saved"));
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.network")); }
  finally { oidcSaving.value = false; }
}
async function doTestOidc() {
  oidcTesting.value = true;
  try {
    const r = await testOidc();   // 成功會回 discovery 資訊；失敗會丟錯
    msg.success(`${t("settings.system.oidc_test_ok")} — ${r.issuer || ""}`);
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("settings.system.oidc_test_fail")); }
  finally { oidcTesting.value = false; }
}

// ── SAML SSO ──
const saml = ref<SamlConfig>({
  enabled: false, idp_metadata_url: null, idp_metadata_xml: null,
  sp_entity_id: null, sp_acs_url: null, sp_sls_url: null, sp_x509_cert: null,
  sp_private_key_set: false, want_assertions_signed: true, want_assertions_encrypted: false,
  want_name_id_encrypted: false, authn_requests_signed: false,
  attr_username: "uid", attr_email: "email", attr_displayname: "displayName",
  attr_groups: "groups", admin_groups: [], default_group_id: null,
});
const samlKey = ref("");           // SP 私鑰，留空＝不變更
const samlSaving = ref(false);
const samlTesting = ref(false);
const samlAdminGroupsText = computed<string>({
  get: () => (saml.value.admin_groups || []).join(", "),
  set: (v) => { saml.value.admin_groups = v.split(",").map((x) => x.trim()).filter(Boolean); },
});
async function loadSaml() { try { saml.value = await getSamlConfig(); samlKey.value = ""; } catch { /* ignore */ } }
async function saveSaml() {
  samlSaving.value = true;
  try {
    const { sp_private_key_set: _s, ...rest } = saml.value;
    const patch: any = { ...rest };
    if (samlKey.value) patch.sp_private_key = samlKey.value;
    saml.value = await putSamlConfig(patch);
    samlKey.value = "";
    msg.success(t("common.saved"));
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.network")); }
  finally { samlSaving.value = false; }
}
async function doTestSaml() {
  samlTesting.value = true;
  try {
    const r = await testSaml();
    msg.success(`${t("settings.system.saml_test_ok")} — ${r.entity_id || ""}`);
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("settings.system.saml_test_fail")); }
  finally { samlTesting.value = false; }
}
// 用真實帳密測試完整驗證流程
const ldapTestUser = ref("");
const ldapTestPw = ref("");
const ldapAuthTesting = ref(false);
async function doTestLdapAuth() {
  if (!ldapTestUser.value || !ldapTestPw.value) { msg.warning(t("settings.system.ldap_authtest_need")); return; }
  ldapAuthTesting.value = true;
  try {
    const r = await testLdapAuth(ldapTestUser.value, ldapTestPw.value);
    msg.success(`✓ ${r.dn}${r.is_admin ? ` · ${t("users.is_admin")}` : ""}${r.display_name ? " · " + r.display_name : ""}`, { duration: 8000 });
    ldapTestPw.value = "";
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { ldapAuthTesting.value = false; }
}

// 稽核轉送到 Graylog
const af = ref<AuditForward>({ enabled: false, host: null, port: 12201, protocol: "udp", fmt: "gelf" });
const afSaving = ref(false);
const afTesting = ref(false);
const afProtoOpts = [{ label: "UDP", value: "udp" }, { label: "TCP", value: "tcp" }];
const afFmtOpts = [{ label: "GELF", value: "gelf" }, { label: "Syslog (RFC5424)", value: "syslog" }, { label: "CEF", value: "cef" }];
async function loadAf() { try { af.value = await getAuditForward(); } catch { /* ignore */ } }
async function saveAf() {
  afSaving.value = true;
  try { af.value = await putAuditForward(af.value); msg.success(t("common.saved")); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.network")); } finally { afSaving.value = false; }
}
async function doTestAf() {
  if (!af.value.host) { msg.warning(t("settings.system.af_need_host")); return; }
  afTesting.value = true;
  try { const r = await testAuditForward(af.value); msg.success(`${t("settings.system.af_test_ok")} — ${r.sent_to}`); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("settings.system.af_test_fail")); } finally { afTesting.value = false; }
}

onMounted(() => {
  void loadImpact();
  Maintenance.settings().then((v) => { maintCfg.value = v; }).catch(() => {});
  void loadRackEmbed();
  void loadBackupEnc();
  getUiDisplay().then((d) => { changeLogDimDays.value = d.change_log_dim_days; }).catch(() => {});
  getDevicePortFilter().then((d) => {
    portFilterOn.value = d.filter_pseudo;
    portFilterText.value = d.ignore_patterns.join("\n");
  }).catch(() => {});
  getConsoleSecurity().then((c) => {
    applyConsole(c);
    // 上限放大過就每次開頁面都檢查一次：路徑可能在設定之後才變（換了代理、加了 WAF）
    if (sftpMaxMb.value > SFTP_MAX_DEFAULT) void checkSftpPath();
  }).catch(() => {});
  getMapProvider().then((p) => { mapProvider.value = p; }).catch(() => {});
  getRackNameAlign().then((a) => { rackAlign.value = a; }).catch(() => {});
  getOnlineGrace().then((c) => {
    grace.value = c.minutes; livenessSrc.value = c.sources; livenessAvail.value = c.available;
  })
    .catch(() => {});
  getIpCooldown().then((d) => { cooldownDays.value = d; }).catch(() => {});
  getCertExpiryDays().then((d) => { certExpiryDays.value = d; }).catch(() => {});
  getAgentAutoPin().then((v) => { agentAutoPin.value = v; }).catch(() => {});
  void loadGeoip();
  void loadLdap();
  void loadLdapGroups();
  void loadOidc();
  void loadSaml();
  void loadAf();
  void loadAutolink();
});

// ── 依網卡 MAC 自動掛裝置（預設關閉）
const autolink = ref<AutolinkConfig>({ enabled: false, scope_subnet_ids: null });
const autolinkPreview = ref<AutolinkPreview | null>(null);
const autolinkBusy = ref(false);
const subnetOptions = ref<{ label: string; value: string }[]>([]);

async function loadAutolink() {
  try {
    autolink.value = await getAutolink();
    const subs = await listSubnets({ pageSize: 500 });
    const rows = Array.isArray(subs) ? subs : (subs as any).items ?? [];
    subnetOptions.value = rows.map((x: any) => ({ label: x.cidr, value: x.id }));
  } catch { /* 沒權限或尚未設定：維持預設關閉 */ }
}

async function saveAutolink(patch: Partial<AutolinkConfig>) {
  try {
    autolink.value = await putAutolink(patch);
    msg.success(t("common.saved"));
  } catch (e) { msg.error(apiErrMsg(e)); }
}

async function doPreviewAutolink() {
  autolinkBusy.value = true;
  try { autolinkPreview.value = await previewAutolink(); }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { autolinkBusy.value = false; }
}
</script>

<template>
  <div class="ss-page">
    <div class="ss-title">
      <n-icon :size="22"><AdminIcon /></n-icon>
      <span>{{ t("system_settings.title") }}</span>
    </div>
    <div class="ss-wrap">
      <!-- 資安：連線管理 -->
      <n-card class="ss-group" size="small">
        <template #header><span class="ss-h">{{ t("system_settings.grp_security") }}</span></template>
        <!-- 一列一個設定：左邊名稱與說明、右邊控制項。以前三欄格子裡說明有長有短，
             整區高低不齊、控制項東一個西一個（使用者回報排列不好看） -->
        <div class="ss-rows">
          <div class="ss-r fld" data-testid="rdp-engine-row">
            <div class="ss-r__text">
              <label>{{ t("settings.system.rdp_engine") }}</label>
              <div class="hint">{{ t("settings.system.rdp_engine_hint") }}</div>
            </div>
            <div class="ss-r__ctl">
              <n-select :value="rdpEngine" :options="rdpEngineOpts" @update:value="changeRdpEngine" />
            </div>
            <!-- 一律建議 guacd（使用者 2026-10-13）：以前不管選哪個引擎，這台沒裝 FreeRDP 就跳黃色警告＋安裝指令，
                 客戶以為要裝 FreeRDP 就切了過去。現在缺套件只在真的選了那個引擎時講，而且先建議改回 guacd；
                 選了備用引擎（FreeRDP／aardwolf）就提示改回 guacd，按一下就改。aardwolf 會逐步淘汰 -->
            <n-alert v-if="!aardwolfOk && (rdpEngine === 'aardwolf' || vncEngine === 'builtin')" type="error"
                     :bordered="false" class="ss-r__full">
              {{ t("settings.system.rdp_engine_no_aardwolf", { python: pythonVer }) }}
            </n-alert>
            <n-alert v-if="rdpEngine === 'freerdp' && !freerdpOk" type="error" :bordered="false" class="ss-r__full"
                     data-testid="rdp-freerdp-missing">
              <div>{{ t("settings.system.rdp_engine_missing", { pkgs: freerdpMissing.join("、") }) }}</div>
              <div v-if="freerdpCmd" class="hint" style="margin-top:6px">{{ t("settings.system.freerdp_install_note") }}</div>
              <code v-if="freerdpCmd" class="rdp-install-cmd">{{ freerdpCmd }}</code>
            </n-alert>
            <n-alert v-if="consoleLoaded && rdpEngine !== 'guacd'" type="warning" :bordered="false" class="ss-r__full"
                     data-testid="rdp-engine-recommend">
              <div class="engine-rec">
                <!-- aardwolf 多一句「會逐步淘汰」：用完整的另一句，不靠拼接（中文句號後不該有空白、英文又需要） -->
                <span>{{ t(rdpEngine === "aardwolf" ? "settings.system.engine_recommend_retiring" : "settings.system.engine_recommend",
                           { engine: rdpEngine === "freerdp" ? "FreeRDP" : "aardwolf" }) }}</span>
                <n-button size="small" type="primary" @click="changeRdpEngine('guacd')">
                  <template #icon><n-icon :component="OkIcon" /></template>{{ t("settings.system.use_guacd") }}
                </n-button>
              </div>
            </n-alert>
          </div>
          <div class="ss-r fld">
            <div class="ss-r__text">
              <label>{{ t("settings.system.vnc_engine") }}</label>
              <div class="hint">{{ t("settings.system.vnc_engine_hint") }}</div>
            </div>
            <div class="ss-r__ctl">
              <n-select :value="vncEngine" :options="vncEngineOpts" @update:value="changeVncEngine" />
            </div>
            <n-alert v-if="consoleLoaded && vncEngine !== 'guacd'" type="warning" :bordered="false" class="ss-r__full"
                     data-testid="vnc-engine-recommend">
              <div class="engine-rec">
                <span>{{ t("settings.system.engine_recommend_retiring", { engine: "aardwolf" }) }}</span>
                <n-button size="small" type="primary" @click="changeVncEngine('guacd')">
                  <template #icon><n-icon :component="OkIcon" /></template>{{ t("settings.system.use_guacd") }}
                </n-button>
              </div>
            </n-alert>
          </div>
          <div class="ss-r fld">
            <div class="ss-r__text">
              <label>{{ t("settings.system.ssh_engine") }}</label>
              <div class="hint">{{ t("settings.system.ssh_engine_hint") }}</div>
            </div>
            <div class="ss-r__ctl">
              <n-select :value="sshEngine" :options="sshEngineOpts" @update:value="changeSshEngine" />
            </div>
          </div>
          <!-- guacd 的實際狀態：選了卻不能用要講清楚原因與怎麼裝；能用就列出支援哪些協定 -->
          <div class="ss-r fld guacd-status">
            <div class="ss-r__text">
              <label>{{ t("settings.system.guacd_status") }}</label>
              <div class="hint">{{ t("settings.system.guacd_hint") }}</div>
            </div>
            <div class="ss-r__ctl">
              <span v-if="!consoleLoaded" class="hint">…</span>
              <n-space v-else-if="guacdOk" :size="6" align="center" :wrap="true">
                <n-tag v-for="p in ['rdp', 'vnc', 'ssh']" :key="p" size="small" :bordered="false"
                       :type="guacdProtocols[p] ? 'success' : 'default'">
                  {{ p.toUpperCase() }} {{ guacdProtocols[p] ? "✓" : "—" }}
                </n-tag>
                <span class="hint" style="margin:0">{{ guacdAddress }}</span>
              </n-space>
            </div>
            <n-alert v-if="consoleLoaded && !guacdOk" type="error" :bordered="false" class="ss-r__full">
              <div>{{ t("settings.system.guacd_down", { address: guacdAddress, reason: guacdError }) }}</div>
              <code v-if="guacdCmd" class="rdp-install-cmd">{{ guacdCmd }}</code>
            </n-alert>
            <n-alert v-else-if="consoleLoaded && guacdMissingFor.length" type="error" :bordered="false"
                     class="ss-r__full">
              {{ t("settings.system.guacd_protocol_missing", { protocols: guacdMissingFor.join("、").toUpperCase() }) }}
            </n-alert>
          </div>
          <div class="ss-r fld sftp-max">
            <div class="ss-r__text">
              <label>{{ t("settings.system.sftp_max_mb") }}</label>
              <div class="hint">{{ t("settings.system.sftp_max_mb_hint") }}</div>
            </div>
            <div class="ss-r__ctl">
              <n-space :size="8" align="center" :wrap="false">
                <!-- Enter 由外層的 div 接：n-input-number 不會把 keyup 轉出來。
                     不給 min／max：那會讓元件把超出範圍的值自動夾到邊界後存下去 -->
                <div @keyup.enter="commitSftpMax">
                  <n-input-number v-model:value="sftpMaxInput" :step="100" :show-button="false"
                                  style="width:150px" @blur="commitSftpMax">
                    <template #suffix>MB</template>
                  </n-input-number>
                </div>
                <n-button size="small" :loading="probe?.running" @click="checkSftpPath"><template #icon><n-icon><PlayIcon /></n-icon></template>
                  {{ t("settings.system.sftp_probe_run") }}
                </n-button>
              </n-space>
            </div>
            <div v-if="probe?.running" class="hint sftp-probe-running ss-r__full">
              {{ t("settings.system.sftp_probe_running", {
                stage: probe.stage === "down" ? t("settings.system.sftp_probe_down") : t("settings.system.sftp_probe_up"),
                pct: probe.pct ?? 0 }) }}
            </div>
            <n-alert v-else-if="probe?.result" class="sftp-probe-result ss-r__full" :bordered="false"
                     :type="probe.result.ok ? (probeSlow ? 'warning' : 'success') : 'error'">
              <div>{{ probeText }}</div>
              <div class="hint" style="margin-top:4px">{{ t("settings.system.sftp_probe_note") }}</div>
            </n-alert>
          </div>
          <div class="ss-r fld">
            <div class="ss-r__text">
              <label>{{ t("settings.system.rdp_clip_paste") }}</label>
              <div class="hint">{{ t("settings.system.rdp_clip_paste_hint") }}</div>
            </div>
            <div class="ss-r__ctl">
              <n-switch :value="rdpClipPaste" @update:value="changeRdpClipPaste" />
            </div>
          </div>
          <div class="ss-r fld">
            <div class="ss-r__text">
              <label>{{ t("relay.system_switch") }}</label>
              <div class="hint">{{ t("relay.system_switch_hint") }}</div>
            </div>
            <div class="ss-r__ctl">
              <n-switch :value="consoleRelay" data-testid="console-relay-switch" @update:value="changeConsoleRelay" />
            </div>
          </div>
        </div>
      </n-card>

      <!-- 變更影響預演（預設關閉；開了之後選單、IP 頁、裝置頁才有入口） -->
      <n-card class="ss-group" size="small" data-testid="ss-change-impact">
        <template #header><span class="ss-h">{{ t("change_impact.title") }}</span></template>
        <!-- 一列一個設定（同資安卡片）：以前放在兩欄格子裡，說明被擠成一行四五個字、右欄對不齊、整張卡大片空白 -->
        <div class="ss-rows">
          <div class="ss-r fld">
            <div class="ss-r__text">
              <label>{{ t("change_impact.set_enabled") }}</label>
              <div class="hint">{{ t("change_impact.set_enabled_hint") }}</div>
            </div>
            <div class="ss-r__ctl">
              <n-switch :value="impactCfg.enabled" data-testid="cip-enabled-switch"
                        @update:value="(v: boolean) => saveImpact({ enabled: v })" />
            </div>
          </div>
          <div class="ss-r fld">
            <div class="ss-r__text">
              <label>{{ t("change_impact.set_ai") }}</label>
              <div class="hint">{{ t("change_impact.set_ai_hint") }}</div>
            </div>
            <div class="ss-r__ctl">
              <n-switch :value="impactCfg.ai_enabled" @update:value="(v: boolean) => saveImpact({ ai_enabled: v })" />
            </div>
          </div>
          <!-- 誰能覆核、會簽或依序多關卡、是否允許自己覆核：都在「申請審核設定」（使用者 2026-10-08） -->
          <div class="ss-r fld" data-testid="cip-reviewers">
            <div class="ss-r__text">
              <label>{{ t("change_impact.set_reviewers") }}</label>
              <div class="hint">{{ t("change_impact.set_reviewers_moved") }}</div>
            </div>
            <div class="ss-r__ctl">
              <n-button size="small" @click="router.push({ name: 'approval_settings', query: { tab: 'change_impact' } })">
                <template #icon><n-icon><RequestsIcon /></n-icon></template>{{ t("change_impact.set_reviewers_go") }}
              </n-button>
            </div>
          </div>
          <div class="ss-r fld">
            <div class="ss-r__text">
              <label>{{ t("change_impact.set_valid_hours") }}</label>
              <div class="hint">{{ t("change_impact.set_valid_hours_hint") }}</div>
            </div>
            <div class="ss-r__ctl">
              <n-input-number :value="impactCfg.run_valid_hours" :min="1" :max="168" style="width: 160px"
                              @update:value="(v: number | null) => v && saveImpact({ run_valid_hours: v })" />
            </div>
          </div>
          <div class="ss-r fld">
            <div class="ss-r__text">
              <label>{{ t("change_impact.set_retention") }}</label>
              <div class="hint">{{ t("change_impact.set_retention_hint") }}</div>
            </div>
            <div class="ss-r__ctl">
              <n-input-number :value="impactCfg.retention_days" :min="7" :max="3650" style="width: 160px"
                              @update:value="(v: number | null) => v && saveImpact({ retention_days: v })" />
            </div>
          </div>
        </div>
      </n-card>

      <!-- 維護排程：維護期間範圍內不告警 -->
      <n-card class="ss-group" size="small" data-testid="ss-maintenance">
        <template #header><span class="ss-h">{{ t("maint.page_title") }}</span></template>
        <div class="ss-rows">
          <div class="ss-r fld">
            <div class="ss-r__text">
              <label>{{ t("maint.set_max_hours") }}</label>
              <div class="hint">{{ t("maint.set_max_hours_hint") }}</div>
            </div>
            <div class="ss-r__ctl">
              <n-input-number :value="maintCfg.max_hours" :min="1" :max="2160" style="width: 140px"
                              @update:value="(v: number | null) => v && saveMaint({ max_hours: v })" />
            </div>
          </div>
          <div class="ss-r fld">
            <div class="ss-r__text">
              <label>{{ t("maint.set_large") }}</label>
              <div class="hint">{{ t("maint.set_large_hint") }}</div>
            </div>
            <div class="ss-r__ctl">
              <n-input-number :value="maintCfg.large_threshold" :min="10" :max="1000000" style="width: 140px"
                              @update:value="(v: number | null) => v && saveMaint({ large_threshold: v })" />
            </div>
          </div>
        </div>
      </n-card>

      <!-- 顯示與地圖 -->
      <n-card class="ss-group" size="small">
        <template #header><span class="ss-h">{{ t("system_settings.grp_display") }}</span></template>
        <div class="ss-grid">
          <div class="fld">
            <label>{{ t("settings.system.map_provider") }}</label>
            <n-select :value="mapProvider" :options="mapProviderOpts" @update:value="changeMapProvider" />
            <div class="hint">{{ t("settings.system.map_provider_hint") }}</div>
          </div>
          <div class="fld">
            <label>{{ t("settings.system.rack_name_align") }}</label>
            <n-select :value="rackAlign" :options="rackAlignOpts" @update:value="changeRackAlign" />
            <div class="hint">{{ t("settings.system.rack_name_align_hint") }}</div>
          </div>
          <div class="fld">
            <label>{{ t("settings.system.change_log_dim_days") }}</label>
            <n-input-number :value="changeLogDimDays" :min="0" :max="3650" :step="1"
                            @update:value="changeDimDays" style="width: 160px" />
            <div class="hint">{{ t("settings.system.change_log_dim_days_hint") }}</div>
          </div>
        </div>
      </n-card>

      <!-- 全站搜尋：隨打即找、搜哪些類型與欄位、結果的排列順序（2026-10-13） -->
      <n-card class="ss-group" size="small" data-testid="ss-global-search">
        <template #header><span class="ss-h">{{ t("search_settings.title") }}</span></template>
        <GlobalSearchSettings />
      </n-card>

      <!-- 裝置連接埠匯入：偽介面過濾 -->
      <n-card class="ss-group" size="small">
        <template #header><span class="ss-h">{{ t("system_settings.grp_device_ports") }}</span></template>
        <div class="ss-grid">
          <div class="fld">
            <label>{{ t("settings.system.port_filter_pseudo") }}</label>
            <n-switch v-model:value="portFilterOn" />
            <div class="hint">{{ t("settings.system.port_filter_pseudo_hint") }}</div>
          </div>
          <div class="fld" style="grid-column: 1 / -1">
            <label>{{ t("settings.system.port_ignore_patterns") }}</label>
            <n-input v-model:value="portFilterText" type="textarea" :disabled="!portFilterOn"
                     :autosize="{ minRows: 4, maxRows: 12 }"
                     placeholder="^ethernet_\d+$" style="font-family: monospace" />
            <div class="hint">{{ t("settings.system.port_ignore_patterns_hint") }}</div>
          </div>
          <div class="fld">
            <n-button type="primary" size="small" @click="savePortFilter">
              <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
            </n-button>
          </div>
        </div>
      </n-card>

      <!-- 上線判定 -->
      <n-card class="ss-group" size="small">
        <template #header><span class="ss-h">{{ t("system_settings.grp_liveness") }}</span></template>
        <div class="fld fld-narrow-input">
          <label>{{ t("settings.prefs.online_grace_minutes") }}</label>
          <n-input-number :value="grace" :min="1" :max="43200" style="width: 100%" @update:value="changeGrace" />
          <div class="hint">{{ t("settings.prefs.online_grace_minutes_hint") }}</div>
        </div>
        <div class="fld" style="margin-top: 12px">
          <label>{{ t("system_settings.liveness_sources") }}</label>
          <!-- 依「證據種類」分組：同一種類的來源長度相近，網格才對得齊；
               混在一起自由換行會因為字數不一而每列參差（使用者回報）。 -->
          <n-checkbox-group :value="livenessSrc" @update:value="changeSources">
            <div v-for="g in livenessGroups" :key="g.kind" class="ss-src-row">
              <span class="ss-src-kind">{{ g.title }}</span>
              <div class="ss-src-grid">
                <n-checkbox v-for="s in g.items" :key="s.key" :value="s.key" class="ss-src-item">
                  <span class="ss-src-name">{{ g.kind === "base" ? srcLabel(s.key) : vendorLabel(s.key) }}</span>
                  <!-- 不會過期的證據勾了等於「看過一次就永遠上線」→ 講明白，不要只靠說明文字 -->
                  <n-tag v-if="!s.aging" size="tiny" :bordered="false" type="warning"
                         class="ss-src-tag">
                    {{ t("system_settings.src_no_aging") }}
                  </n-tag>
                </n-checkbox>
              </div>
            </div>
          </n-checkbox-group>
          <div class="ss-warn">
            <n-icon :size="15" :component="WarnIcon" />
            <span>{{ t("system_settings.liveness_no_aging_warn") }}</span>
          </div>
          <div class="hint">{{ t("system_settings.liveness_sources_hint") }}</div>
        </div>
      </n-card>

      <!-- IP 生命週期 -->
      <n-card class="ss-group" size="small">
        <template #header><span class="ss-h">{{ t("system_settings.grp_lifecycle") }}</span></template>
        <div class="fld fld-narrow-input">
          <label>{{ t("system_settings.cooldown_days") }}</label>
          <n-input-number :value="cooldownDays" :min="0" :max="3650" style="width: 100%"
                          @update:value="changeCooldown" />
          <div class="hint">{{ t("system_settings.cooldown_days_hint") }}</div>
        </div>
      </n-card>

      <!-- 憑證到期通知 -->
      <n-card class="ss-group" size="small">
        <template #header><span class="ss-h">{{ t("system_settings.grp_cert_alert") }}</span></template>
        <div class="fld fld-narrow-input">
          <label>{{ t("system_settings.cert_expiry_days") }}</label>
          <n-input-number :value="certExpiryDays" :min="1" :max="365" style="width: 100%"
                          @update:value="changeCertExpiry" />
          <div class="hint">{{ t("system_settings.cert_expiry_days_hint") }}</div>
        </div>
      </n-card>

      <!-- 代理驗證 jt-ipam -->
      <n-card class="ss-group" size="small" data-testid="ss-agent-trust">
        <template #header><span class="ss-h">{{ t("system_settings.grp_agent_trust") }}</span></template>
        <div class="ss-rows">
          <div class="ss-r fld">
            <div class="ss-r__text">
              <label>{{ t("system_settings.agent_auto_pin") }}</label>
              <div class="hint">{{ t("system_settings.agent_auto_pin_hint") }}</div>
            </div>
            <div class="ss-r__ctl">
              <n-switch :value="agentAutoPin" data-testid="ss-agent-auto-pin" @update:value="changeAgentAutoPin" />
            </div>
          </div>
        </div>
      </n-card>

      <!-- 機櫃圖對外嵌入 -->
      <n-card class="ss-group" size="small">
        <template #header><span class="ss-h">{{ t("system_settings.grp_rack_embed") }}</span></template>
        <div class="ss-grid">
          <div class="fld">
            <label>{{ t("system_settings.rack_embed_enabled") }}</label>
            <n-switch :value="rackEmbed?.enabled ?? false" @update:value="changeRackEmbed" />
            <div class="hint">{{ t("system_settings.rack_embed_hint") }}</div>
          </div>
          <div v-if="rackEmbed?.enabled" class="fld">
            <label>{{ t("system_settings.rack_embed_token") }}</label>
            <n-input :value="showRackToken ? rackEmbedToken : '••••••••••••••••'" readonly style="width: 100%" />
            <n-space size="small" align="center" style="margin-top: 6px">
              <n-tag v-if="rackEmbed?.token_expires_at" size="small" :bordered="false"
                     :type="rackTokenExpired ? 'error' : 'default'" data-testid="rack-token-expiry">
                {{ rackTokenExpired ? t("llm_settings.mcp_key_expired", { date: fmtDate(rackEmbed.token_expires_at) })
                                    : t("llm_settings.mcp_key_expires", { date: fmtDate(rackEmbed.token_expires_at) }) }}
              </n-tag>
              <n-button size="small" @click="toggleRackToken"><template #icon><n-icon><EyeIcon /></n-icon></template>
                {{ showRackToken ? t("common.hide") : t("common.show") }}
              </n-button>
              <n-button size="small" @click="copyRackToken"><template #icon><n-icon><CopyIcon /></n-icon></template>{{ t("common.copy") }}</n-button>
              <n-select v-model:value="rackTokenDays" :options="tokenDayOptions" size="small" style="width: 110px"
                        :aria-label="t('llm_settings.mcp_key_days')" />
              <n-popconfirm @positive-click="regenRackToken">
                <template #trigger>
                  <n-button size="small" type="warning" ghost><template #icon><n-icon><RefreshIcon /></n-icon></template>
                    {{ t("system_settings.rack_embed_regen") }}
                  </n-button>
                </template>
                {{ t("system_settings.rack_embed_regen_confirm") }}
              </n-popconfirm>
            </n-space>
            <div class="hint">{{ t("system_settings.rack_embed_token_hint") }}</div>
          </div>
        </div>
      </n-card>

      <!-- 每日備份加密 -->
      <n-card class="ss-group" size="small" data-testid="backup-encryption">
        <template #header><span class="ss-h">{{ t("system_settings.grp_backup_enc") }}</span></template>
        <div class="ss-grid">
          <div class="fld">
            <label>{{ t("system_settings.backup_enc_status") }}</label>
            <n-tag :type="backupEnc?.enabled ? 'success' : 'warning'" size="small" :bordered="false">
              {{ backupEnc?.enabled ? t("system_settings.backup_enc_on", { at: fmtDate(backupEnc.set_at) })
                                    : t("system_settings.backup_enc_off") }}
            </n-tag>
            <div class="hint">{{ t("system_settings.backup_enc_hint") }}</div>
          </div>
          <div class="fld">
            <label>{{ backupEnc?.enabled ? t("system_settings.backup_enc_change") : t("system_settings.backup_enc_set") }}</label>
            <n-input v-model:value="backupPw" type="password" show-password-on="click"
                     :placeholder="t('system_settings.backup_enc_ph')" data-testid="backup-enc-pw" />
            <n-input v-model:value="backupPw2" type="password" show-password-on="click" style="margin-top: 6px"
                     :placeholder="t('users.password_confirm')" data-testid="backup-enc-pw2" />
            <n-space size="small" style="margin-top: 6px">
              <n-button size="small" type="primary" :loading="backupEncSaving" data-testid="backup-enc-save"
                        @click="saveBackupEnc">
                <template #icon><n-icon :component="SaveIcon" /></template>{{ t("common.save") }}
              </n-button>
              <n-popconfirm v-if="backupEnc?.enabled" @positive-click="clearBackupEnc">
                <template #trigger>
                  <n-button size="small" type="error" ghost>
                    <template #icon><n-icon :component="DeleteIcon" /></template>{{ t("system_settings.backup_enc_remove") }}
                  </n-button>
                </template>
                {{ t("system_settings.backup_enc_remove_confirm") }}
              </n-popconfirm>
            </n-space>
            <div class="hint">{{ t("system_settings.backup_enc_keep_hint") }}</div>
          </div>
        </div>
      </n-card>

      <!-- GeoIP -->
      <n-card v-if="geoip" class="ss-group" size="small">
        <template #header><span class="ss-h">{{ t("settings.system.geoip") }}</span></template>
        <div class="ss-grid">
          <div class="fld">
            <label>{{ t("settings.system.geoip_account") }}</label>
            <n-input v-model:value="geoipAccount" :placeholder="t('settings.system.geoip_account')" />
          </div>
          <div class="fld">
            <label>License Key</label>
            <n-input v-model:value="geoipKey" type="password" show-password-on="click"
                     :placeholder="geoip.has_key ? t('settings.system.geoip_key_set') : t('settings.system.geoip_key')" />
          </div>
        </div>
        <div class="fld" style="margin-top: 14px">
          <label>{{ t("settings.system.geoip_editions") }}</label>
          <n-select v-model:value="geoip.editions" :options="geoipEditionOpts" multiple />
          <div class="hint" style="line-height:1.5">{{ t("settings.system.geoip_asn_note") }}</div>
        </div>
        <div class="ss-row">
          <div style="display:flex; align-items:center; gap:8px">
            <n-switch v-model:value="geoip.auto_update" />
            <span style="font-size:13px">{{ t("settings.system.geoip_auto") }}</span>
          </div>
          <n-select v-model:value="geoip.frequency" :options="geoipFreqOpts" :disabled="!geoip.auto_update"
                    style="width: 200px" />
          <div style="flex:1"></div>
          <n-button size="small" :loading="geoipSaving" @click="saveGeoip">
            <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
          </n-button>
          <n-button size="small" type="primary" :loading="geoipUpdating" @click="updateGeoipNow">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("settings.system.geoip_update_now") }}
          </n-button>
        </div>
        <div class="ss-status">
          <div v-for="d in geoip.dbs" :key="d.edition" class="db-row">
            <n-tag size="tiny" :type="d.present ? 'success' : 'default'">{{ d.edition }}</n-tag>
            <span v-if="d.present" style="opacity:.7">{{ fmtBytes(d.size) }} · {{ fmtRelative(d.built_at) }}</span>
            <span v-else style="opacity:.5">{{ t("settings.system.geoip_db_missing") }}</span>
          </div>
          <div v-if="geoip.last_update_at" style="opacity:.6; margin-top:4px">
            {{ t("settings.system.geoip_last_update") }}: {{ fmtDateTime(geoip.last_update_at) }}
          </div>
          <div v-if="geoip.last_error" style="color:var(--err-color,#e88080); margin-top:4px">{{ geoip.last_error }}</div>
        </div>
        <div class="hint" style="line-height:1.6; margin-top:10px">
          {{ t("settings.system.geoip_hint") }}<br>
          {{ t("settings.system.geoip_freq_advice") }}
        </div>
      </n-card>

      <!-- 外部 DNS 解析器（對外開放服務的 FQDN 從外面看；2026-10-19） -->
      <n-card class="ss-group" size="small" data-testid="ss-dns-resolvers">
        <template #header><span class="ss-h">{{ t("dns_resolvers.title") }}</span></template>
        <DnsResolversSettings />
      </n-card>

      <!-- 外部認證 / LDAP（AD） -->
      <n-card class="ss-group" size="small">
        <template #header><span class="ss-h">{{ t("settings.system.ldap_title") }}</span></template>
        <div class="fld">
          <n-space align="center">
            <n-switch v-model:value="ldap.enabled" />
            <span style="font-size:13px">{{ t("settings.system.ldap_enable") }}</span>
          </n-space>
        </div>
        <div class="fld-grid" style="grid-template-columns:1fr 140px">
          <div class="fld">
            <label>{{ t("settings.system.ldap_server") }}</label>
            <n-input v-model:value="ldap.server" placeholder="dc01.example.com" />
          </div>
          <div class="fld">
            <label>{{ t("settings.system.ldap_port") }}</label>
            <n-input-number v-model:value="ldap.port" :min="1" :max="65535" style="width:100%" />
          </div>
        </div>
        <div class="fld">
          <label>{{ t("settings.system.ldap_tls") }}</label>
          <n-select v-model:value="ldapTlsMode" :options="ldapTlsOpts" />
        </div>
        <!-- 加密時才用得到：企業內部 CA 與舊版 AD 的弱金鑰憑證（客戶 2026-10-19） -->
        <template v-if="ldapTlsMode !== 'none'">
          <div class="fld">
            <label>{{ t("settings.system.ldap_ca") }}</label>
            <n-input v-model:value="ldapCaPem" type="textarea" :autosize="{ minRows: 2, maxRows: 8 }"
                     placeholder="-----BEGIN CERTIFICATE-----" data-testid="ldap-ca-pem"
                     style="font-family: var(--jt-mono, monospace); font-size: 12px" />
            <div class="hint">{{ t("settings.system.ldap_ca_hint") }}</div>
            <div v-if="ldap.ca_cert_subjects?.length" class="hint" data-testid="ldap-ca-current">
              {{ t("settings.system.ldap_ca_current", { names: ldap.ca_cert_subjects.join("；") }) }}
            </div>
          </div>
          <div class="fld">
            <n-space align="center" :size="8" :wrap-item="false">
              <n-switch v-model:value="ldap.allow_weak_tls" data-testid="ldap-weak-tls" />
              <span style="font-size:13px">{{ t("settings.system.ldap_weak_tls") }}</span>
            </n-space>
            <div class="hint">{{ t("settings.system.ldap_weak_tls_hint") }}</div>
          </div>
        </template>
        <div class="fld">
          <label>{{ t("settings.system.ldap_bind_dn") }}</label>
          <n-input v-model:value="ldap.bind_dn" placeholder="CN=svc-ipam,OU=Svc,DC=example,DC=com" />
        </div>
        <div class="fld">
          <label>{{ t("settings.system.ldap_bind_pw") }}</label>
          <div style="display:flex; gap:8px; align-items:center">
            <n-input v-model:value="ldapPw" type="password" show-password-on="click"
                     :placeholder="ldap.password_set ? t('settings.system.ldap_pw_set') : t('settings.system.ldap_pw_unset')"
                     style="flex:1" />
            <n-button v-if="ldap.password_set" size="small" quaternary type="error" @click="clearLdapPw"><template #icon><n-icon><DeleteIcon /></n-icon></template>
              {{ t("settings.system.ldap_pw_clear") }}
            </n-button>
          </div>
        </div>
        <div class="fld">
          <label>{{ t("settings.system.ldap_search_base") }}</label>
          <n-input v-model:value="ldap.search_base" placeholder="DC=example,DC=com" />
        </div>
        <div class="fld">
          <label>{{ t("settings.system.ldap_user_filter") }}</label>
          <n-input v-model:value="ldap.user_filter" placeholder="(sAMAccountName={username})" />
          <div class="hint" style="margin-top:4px">{{ t("settings.system.ldap_user_filter_hint") }}</div>
        </div>
        <div class="fld-grid" style="grid-template-columns:1fr 1fr 1fr">
          <div class="fld">
            <label>{{ t("settings.system.ldap_attr_email") }}</label>
            <n-input v-model:value="ldap.attr_email" placeholder="mail" />
          </div>
          <div class="fld">
            <label>{{ t("settings.system.ldap_attr_name") }}</label>
            <n-input v-model:value="ldap.attr_display_name" placeholder="displayName" />
          </div>
          <div class="fld">
            <label>{{ t("settings.system.ldap_attr_groups") }}</label>
            <n-input v-model:value="ldap.attr_member_of" placeholder="memberOf" />
          </div>
        </div>
        <div class="fld">
          <label>{{ t("settings.system.ldap_admin_groups") }}</label>
          <n-input v-model:value="ldapGroupsText" type="textarea" :autosize="{ minRows: 2, maxRows: 5 }"
                   placeholder="CN=IPAM-Admins,OU=Groups,DC=example,DC=com" />
          <div class="hint" style="margin-top:4px">{{ t("settings.system.ldap_admin_groups_hint") }}</div>
        </div>
        <div class="fld">
          <label>{{ t("settings.system.ldap_default_role") }}</label>
          <n-select v-model:value="ldapDefaultGroup" :options="ldapGroupOpts" />
          <div class="hint" style="margin-top:4px">{{ t("settings.system.ldap_default_role_hint") }}</div>
        </div>
        <n-space style="margin-top:6px">
          <n-button type="primary" :loading="ldapSaving" @click="saveLdap">
            <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
          </n-button>
          <n-button :loading="ldapTesting" @click="doTestLdap">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("settings.system.ldap_test") }}
          </n-button>
        </n-space>
        <div class="hint" style="margin-top:4px">{{ t("settings.system.ldap_test_saved_hint") }}</div>
        <div class="fld" style="margin-top:14px; border-top:1px dashed var(--n-border-color,#eee); padding-top:12px">
          <label>{{ t("settings.system.ldap_authtest") }}</label>
          <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap">
            <n-input v-model:value="ldapTestUser" :placeholder="t('settings.system.ldap_authtest_user')" style="flex:1; min-width:140px" />
            <n-input v-model:value="ldapTestPw" type="password" show-password-on="click" :placeholder="t('settings.system.ldap_authtest_pw')" style="flex:1; min-width:140px" @keyup.enter="doTestLdapAuth" />
            <n-button :loading="ldapAuthTesting" @click="doTestLdapAuth">
              <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("settings.system.ldap_authtest_btn") }}
            </n-button>
          </div>
          <div class="hint" style="margin-top:4px">{{ t("settings.system.ldap_authtest_hint") }}</div>
        </div>
        <div class="hint" style="line-height:1.6; margin-top:10px">{{ t("settings.system.ldap_hint") }}</div>
      </n-card>

      <!-- 單一登入 (OIDC) -->
      <n-card class="ss-group" size="small">
        <template #header><span class="ss-h">{{ t("settings.system.oidc_title") }}</span></template>
        <div class="fld">
          <n-space align="center">
            <n-switch v-model:value="oidc.enabled" />
            <span style="font-size:13px">{{ t("settings.system.oidc_enable") }}</span>
          </n-space>
        </div>
        <div class="fld">
          <label>{{ t("settings.system.oidc_issuer") }}</label>
          <n-input v-model:value="oidc.issuer" placeholder="https://idp.example.com/realms/main" />
        </div>
        <div class="fld" style="display:flex; gap:12px; flex-wrap:wrap">
          <div style="flex:1; min-width:200px">
            <label>{{ t("settings.system.oidc_client_id") }}</label>
            <n-input v-model:value="oidc.client_id" />
          </div>
          <div style="flex:1; min-width:200px">
            <label>{{ t("settings.system.oidc_client_secret") }}</label>
            <n-input v-model:value="oidcSecret" type="password" show-password-on="click"
                     :placeholder="oidc.client_secret_set ? t('settings.system.oidc_secret_keep') : ''" />
          </div>
        </div>
        <div class="fld">
          <label>{{ t("settings.system.oidc_redirect_uri") }}</label>
          <n-input v-model:value="oidc.redirect_uri" placeholder="https://ipam.example.com/api/v1/auth/oidc/callback" />
        </div>
        <div class="fld" style="display:flex; gap:12px; flex-wrap:wrap">
          <div style="flex:1; min-width:160px">
            <label>{{ t("settings.system.oidc_scope") }}</label>
            <n-input v-model:value="oidc.scope" />
          </div>
          <div style="flex:1; min-width:140px">
            <label>{{ t("settings.system.oidc_username_claim") }}</label>
            <n-input v-model:value="oidc.username_claim" />
          </div>
          <div style="flex:1; min-width:140px">
            <label>{{ t("settings.system.oidc_groups_claim") }}</label>
            <n-input v-model:value="oidc.groups_claim" />
          </div>
        </div>
        <div class="fld">
          <label>{{ t("settings.system.oidc_admin_groups") }}</label>
          <n-input v-model:value="oidcAdminGroupsText" :placeholder="t('settings.system.oidc_admin_groups_ph')" />
        </div>
        <n-space style="margin-top:8px">
          <n-button type="primary" :loading="oidcSaving" @click="saveOidc">
            <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
          </n-button>
          <n-button :loading="oidcTesting" @click="doTestOidc">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("settings.system.oidc_test") }}
          </n-button>
        </n-space>
        <div class="hint" style="line-height:1.6; margin-top:10px">{{ t("settings.system.oidc_hint") }}</div>
      </n-card>

      <!-- 單一登入 (SAML 2.0) -->
      <n-card class="ss-group" size="small">
        <template #header><span class="ss-h">{{ t("settings.system.saml_title") }}</span></template>
        <div class="fld">
          <n-space align="center">
            <n-switch v-model:value="saml.enabled" />
            <span style="font-size:13px">{{ t("settings.system.saml_enable") }}</span>
          </n-space>
        </div>
        <div class="fld">
          <label>{{ t("settings.system.saml_idp_metadata_url") }}</label>
          <n-input v-model:value="saml.idp_metadata_url" placeholder="https://idp.example.com/metadata" />
        </div>
        <div class="fld">
          <label>{{ t("settings.system.saml_idp_metadata_xml") }}</label>
          <n-input v-model:value="saml.idp_metadata_xml" type="textarea" :rows="3"
                   :placeholder="t('settings.system.saml_idp_metadata_xml_ph')" />
        </div>
        <div class="fld" style="display:flex; gap:12px; flex-wrap:wrap">
          <div style="flex:1; min-width:200px">
            <label>{{ t("settings.system.saml_sp_entity_id") }}</label>
            <n-input v-model:value="saml.sp_entity_id" :placeholder="t('settings.system.saml_auto_ph')" />
          </div>
          <div style="flex:1; min-width:200px">
            <label>{{ t("settings.system.saml_sp_acs_url") }}</label>
            <n-input v-model:value="saml.sp_acs_url" :placeholder="t('settings.system.saml_auto_ph')" />
          </div>
        </div>
        <div class="fld" style="display:flex; gap:12px; flex-wrap:wrap">
          <div style="flex:1; min-width:160px">
            <label>{{ t("settings.system.oidc_username_claim") }}</label>
            <n-input v-model:value="saml.attr_username" />
          </div>
          <div style="flex:1; min-width:160px">
            <label>{{ t("cols.email") }}</label>
            <n-input v-model:value="saml.attr_email" />
          </div>
          <div style="flex:1; min-width:160px">
            <label>{{ t("settings.system.oidc_groups_claim") }}</label>
            <n-input v-model:value="saml.attr_groups" />
          </div>
        </div>
        <div class="fld">
          <label>{{ t("settings.system.oidc_admin_groups") }}</label>
          <n-input v-model:value="samlAdminGroupsText" :placeholder="t('settings.system.oidc_admin_groups_ph')" />
        </div>
        <n-space align="center" style="margin-bottom:10px">
          <n-checkbox v-model:checked="saml.want_assertions_signed">{{ t("settings.system.saml_want_signed") }}</n-checkbox>
          <n-checkbox v-model:checked="saml.authn_requests_signed">{{ t("settings.system.saml_authn_signed") }}</n-checkbox>
          <n-checkbox v-model:checked="saml.want_assertions_encrypted">{{ t("settings.system.saml_want_encrypted") }}</n-checkbox>
        </n-space>
        <n-space>
          <n-button type="primary" :loading="samlSaving" @click="saveSaml">
            <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
          </n-button>
          <n-button :loading="samlTesting" @click="doTestSaml">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("settings.system.saml_test") }}
          </n-button>
          <a :href="`${origin}/api/v1/auth/saml/metadata`" target="_blank" rel="noopener"
             style="font-size:13px; align-self:center">{{ t("settings.system.saml_sp_metadata") }}</a>
        </n-space>
        <div class="hint" style="line-height:1.6; margin-top:10px">{{ t("settings.system.saml_hint") }}</div>
      </n-card>

      <!-- 稽核轉送到 Graylog -->
      <n-card class="ss-group" size="small">
        <template #header><span class="ss-h">{{ t("settings.system.af_title") }}</span></template>
        <div class="fld">
          <n-space align="center">
            <n-switch v-model:value="af.enabled" />
            <span style="font-size:13px">{{ t("settings.system.af_enable") }}</span>
          </n-space>
        </div>
        <div class="fld-grid" style="grid-template-columns:1fr 130px">
          <div class="fld">
            <label>{{ t("settings.system.af_host") }}</label>
            <n-input v-model:value="af.host" placeholder="graylog.example.com" />
          </div>
          <div class="fld">
            <label>{{ t("settings.system.af_port") }}</label>
            <n-input-number v-model:value="af.port" :min="1" :max="65535" style="width:100%" />
          </div>
        </div>
        <div class="fld-grid" style="grid-template-columns:1fr 1fr">
          <div class="fld">
            <label>{{ t("settings.system.af_protocol") }}</label>
            <n-select v-model:value="af.protocol" :options="afProtoOpts" />
          </div>
          <div class="fld">
            <label>{{ t("settings.system.af_format") }}</label>
            <n-select v-model:value="af.fmt" :options="afFmtOpts" />
          </div>
        </div>
        <n-space style="margin-top:6px">
          <n-button type="primary" :loading="afSaving" @click="saveAf">
            <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
          </n-button>
          <n-button :loading="afTesting" @click="doTestAf">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("settings.system.af_test") }}
          </n-button>
        </n-space>
        <div class="hint" style="line-height:1.6; margin-top:10px">{{ t("settings.system.af_hint") }}</div>
      </n-card>
      <!-- 依網卡 MAC 自動掛裝置。預設關閉：升級之後突然多出一個每 5 分鐘自動改
           資料的作業，本身就是不該發生的事。開啟前可以先預覽會動到什麼。 -->
      <n-card class="ss-group" size="small">
        <template #header><span class="ss-h">{{ t("autolink.title") }}</span></template>
        <div class="ss-grid">
          <div class="fld">
            <label>{{ t("autolink.enable") }}</label>
            <n-switch :value="autolink.enabled"
                      @update:value="(v: boolean) => saveAutolink({ enabled: v })" />
            <div class="hint">{{ t("autolink.enable_hint") }}</div>
          </div>
          <div class="fld">
            <label>{{ t("autolink.scope") }}</label>
            <n-select :value="autolink.scope_subnet_ids ?? []" multiple filterable
                      :options="subnetOptions" :placeholder="t('autolink.scope_all')"
                      @update:value="(v: string[]) => saveAutolink({ scope_subnet_ids: v })" />
            <div class="hint">{{ t("autolink.scope_hint") }}</div>
          </div>
        </div>
        <n-space align="center" style="margin-top:10px">
          <n-button size="small" :loading="autolinkBusy" @click="doPreviewAutolink">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>
            {{ t("autolink.preview") }}
          </n-button>
          <span v-if="autolinkPreview" class="hint" style="margin:0">
            {{ t("autolink.preview_result", { n: autolinkPreview.would_link }) }}
            <template v-if="Object.values(autolinkPreview.skipped).some((x) => x > 0)">
              · {{ t("autolink.preview_skipped", {
                    s: Object.entries(autolinkPreview.skipped)
                         .filter(([, v]) => v > 0)
                         .map(([k, v]) => `${t("autolink.skip_" + k)} ${v}`).join("、") }) }}
            </template>
          </span>
        </n-space>
        <div v-if="autolinkPreview?.samples?.length" class="hint" style="margin-top:6px">
          <div v-for="s in autolinkPreview.samples.slice(0, 8)" :key="s.ip">
            {{ s.ip }}<template v-if="s.hostname"> ({{ s.hostname }})</template> → {{ s.device }}
          </div>
          <div v-if="autolinkPreview.samples.length > 8">…</div>
        </div>
        <div class="hint" style="line-height:1.6; margin-top:10px">{{ t("autolink.rules_hint") }}</div>
      </n-card>
    </div>
  </div>
</template>

<style scoped>
/* 每個分類各自一張卡片，寬度撐滿內容區（原本全部擠在一張大卡片、右側留白） */
.ss-page { display: flex; flex-direction: column; gap: 16px; }
.ss-title { display: flex; align-items: center; gap: 10px; font-size: 18px; font-weight: 600;
  padding: 2px 2px 0; }
.ss-wrap { display: flex; flex-direction: column; gap: 16px; max-width: none; }
/* 卡片外觀（底色／邊框／深色模式）交給 n-card，不要自己刻 —— 自己刻就會與其他頁面不一致 */
.ss-group { border-radius: 14px; }
.ss-h { display: inline-block; font-size: 16px; font-weight: 700; padding-left: 12px;
  line-height: 1.25; border-left: 4px solid #18a058; }
/* 統一卡片內的垂直節奏。
   原本只對「卡片內容的直接子元素」下間距，但很多區塊是包在 grid / div 裡的，
   於是開關與底下的欄位就貼在一起（使用者回報：轉送稽核記錄那格最明顯）。
   這裡改成三條規則一起管：卡片第一層、相鄰的欄位、以及開關列與後面的東西。 */
/* 卡片內「直向堆疊」的區塊之間留白。
   ⚠️ 間距只能加在**直向容器**上，不可以用「相鄰兄弟就補上邊距」——
   欄位並排時（.ss-grid 的兩、三欄）那條規則會把右邊那欄整個往下推 14px，
   看起來就是左右沒對齊（使用者在「顯示與地圖」與 GeoIP 兩處都抓到）。 */
.ss-group :deep(.n-card__content) > * + * { margin-top: 16px; }
/* 直向排列的容器用 gap，橫向排列的（.ss-grid）由它自己的 gap 負責 */
.ss-group :deep(.n-card__content) { display: flex; flex-direction: column; }
.ss-group :deep(.n-card__content) > .fld + .fld { margin-top: 14px; }
/* 並排的欄位（主機＋埠、協定＋格式…）：跟上下的欄位一樣留 14px，標籤才不會貼著上一列的輸入框（使用者 2026-10-20） */
/* （用 `* + .fld-grid` 這種選擇器時，scoped 編譯會把它弄壞，所以直接給上邊距） */
.fld-grid { display: grid; gap: 12px; margin-top: 14px; }
/* 開關列之後一定要留白：開關本身沒有下邊界，視覺上會黏住下一個欄位的標題 */
.ss-group :deep(.n-switch) { margin-bottom: 2px; }
.ss-group :deep(.fld > .n-space) { margin-bottom: 6px; }
/* 按鈕列與底下的說明文字 */
.ss-group :deep(.n-button + .hint),
.ss-group :deep(.ss-row + .hint) { margin-top: 10px; }
.ss-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
/* 一列一個設定（資安區塊）：左邊名稱與說明、右邊控制項；警示與檢測結果整列寬放在下面 */
.ss-rows { display: flex; flex-direction: column; }
.ss-r { display: grid; grid-template-columns: minmax(0, 1fr) minmax(220px, 320px); gap: 8px 32px;
        align-items: center; padding: 12px 0; }
.ss-r + .ss-r { border-top: 1px solid var(--n-border-color, rgba(128, 128, 128, .16)); }
.ss-r:first-child { padding-top: 2px; }
.ss-r__text label { display: block; font-size: 13px; font-weight: 500; margin-bottom: 3px; }
.ss-r__text .hint { max-width: 760px; line-height: 1.6; }
.ss-r__ctl { display: flex; justify-content: flex-end; min-width: 0; }
.ss-r__ctl > :deep(.n-select) { width: 100%; }
.ss-r__full { grid-column: 1 / -1; }
@media (max-width: 760px) {
  .ss-r { grid-template-columns: 1fr; }
  .ss-r__ctl { justify-content: flex-start; }
}
/* 只有一個欄位時就別佔半格：右半邊空著、說明卻提早換行（使用者回報）。 */
.ss-grid > :only-child { grid-column: 1 / -1; }
/* 寬螢幕改三欄，把右邊的空間用掉 */
@media (min-width: 1500px) { .ss-grid { grid-template-columns: repeat(3, 1fr); } }
@media (max-width: 640px) { .ss-grid { grid-template-columns: 1fr; } }
/* 單一數字欄位：只把輸入框收窄，說明文字仍用滿卡片寬度。
   先前是把整格限成 320px，說明被擠成窄長一條、右邊整片空白（使用者回報）。 */
.ss-src-row { display: flex; align-items: flex-start; gap: 10px; padding: 12px 0; }
/* 種類之間畫一條淡線：只靠左邊的標題分辨，兩組黏在一起時看起來像同一堆 */
.ss-src-row + .ss-src-row { border-top: 1px solid var(--n-border-color, rgba(127, 127, 127, .18)); }
.ss-src-kind {
  flex: 0 0 96px; font-size: 12.5px; color: var(--n-text-color-3, #8a8a8a);
  line-height: 22px; text-align: right; font-weight: 500;
}
/* 固定欄寬的網格：每一格一樣寬，列與列才對得齊 */
.ss-src-grid {
  /* ⚠️ `min-width: 0` 不能省：flex 子項預設 min-width:auto，撐不小於內容的最小寬度，
     視窗一窄格線就整片衝出卡片外（實測 820px 視窗下超出 195px）。 */
  /* `width: 100%` 是關鍵：只給 flex 屬性時，格線的寬度仍會由內容決定（欄數 × 最小欄寬），
     視窗一窄就整片衝出卡片外。明確綁定容器寬度之後，欄數才會跟著縮。 */
  /* `auto-fill` 而不是 `auto-fit`：auto-fit 會把用不到的欄收掉、讓剩下的撐滿，
     於是 4 項的列是 4 欄、3 項的列變 3 欄（每欄更寬）、2 項變 2 欄，上下對不齊（使用者回報）。
     auto-fill 保留空欄，每一列的欄數與欄寬都一樣。 */
  flex: 1 1 auto; min-width: 0; width: 100%; display: grid; gap: 8px 14px;
  grid-template-columns: repeat(auto-fill, minmax(min(200px, 100%), 1fr)); max-width: 940px;
}
/* 選項本身也要能縮：長標籤（Wazuh 代理 keep-alive）在窄欄位裡要換行而不是撐開格線 */
.ss-src-item { min-width: 0; }
.ss-src-item :deep(.n-checkbox__label) { white-space: normal; }
.ss-src-item { line-height: 22px; min-width: 0; }
.ss-src-name { vertical-align: middle; }
.ss-src-tag { margin-left: 4px; vertical-align: middle; }
@media (max-width: 900px) {
  .ss-src-row { flex-direction: column; gap: 2px; }
  .ss-src-kind { text-align: left; flex-basis: auto; }
}
.fld-narrow-input :deep(.n-input-number) { max-width: 260px; }
.fld label { display: block; font-size: 13px; font-weight: 500; margin-bottom: 5px; }
.hint { font-size: 11px; opacity: 0.65; margin-top: 4px; }
/* 「不會過期」是會讓判定失真的選項 —— 從灰色說明裡拉出來，別讓它埋在一整段字裡 */
.ss-warn {
  display: flex; align-items: flex-start; gap: 6px; margin-top: 10px;
  padding: 8px 10px; border-radius: 8px; font-size: 12.5px; line-height: 1.6;
  color: #a2680a; background: rgba(240, 160, 32, 0.12);
  border: 1px solid rgba(240, 160, 32, 0.35);
}
.ss-row { display: flex; align-items: center; gap: 12px; margin-top: 14px; flex-wrap: wrap; }
.ss-status { margin-top: 12px; font-size: 12px; display: flex; flex-direction: column; gap: 3px; }
.db-row { display: flex; gap: 8px; align-items: center; }
.engine-rec { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.rdp-install-cmd {
  display: block; margin-top: 6px; padding: 5px 8px; border-radius: 4px;
  background: rgba(0, 0, 0, .06); font-size: 12px;
  white-space: pre-wrap; word-break: break-all; user-select: all;
}
</style>
