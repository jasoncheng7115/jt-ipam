<script setup lang="ts">
/**
 * 相容 RustDesk 的網頁連線（docs/SPEC_RUSTDESK_WEBCLIENT_zh-TW.md）。
 *
 * 換票證 → 開 WebSocket → 後端連 hbbs 會合、連 hbbr 中繼，之後只轉送密文。
 * 安全握手、加解密、登入、解碼畫面、鍵盤滑鼠全部在這個分頁裡做（src/rdweb/）：
 * 手動輸入的密碼只在瀏覽器算成雜湊（7.2），明文與 h1 都不送到後端。
 * 版面照 VNC／RDP 主控台：連線表單 → 工具列＋畫面，斷線時蓋一層斷線覆蓋層、可以重新連線。
 * 遠端游標（附錄 E）：對方的游標形狀當成本地游標；對方自己移動游標時，在畫面上方疊一層畫出它的位置。
 *
 * 記住密碼（附錄 D）：表單的版面與行為照 VNC 主控台（VncScreen.vue）的已存帳密：
 * - 第一列「已存密碼」下拉（預設選第一筆）；選了就不顯示密碼框與「記住密碼」，由後端算這次連線的 h2；
 *   清掉或選「使用其他密碼（手動輸入）」＝自己輸入；選了的時候旁邊有刪除鈕
 * - 「記住密碼」開關一列（開了可以取名）：**登入成功之後**才存進連線帳密金庫（protocol=rustdesk，同一個 IP 取代舊的）；失敗不存
 * - 已存的密碼被對方拒絕（Wrong Password）：顯示已失效、改請使用者輸入，不自動重試；回到表單時改成手動輸入
 * - 瀏覽器的儲存空間（localStorage 等）不放任何密碼或雜湊；等著登入成功再存的密碼只在記憶體裡
 *
 * 斷線後自動重新連線（附錄 G）：規則在 src/rdweb/reconnect.ts，這裡只接線。
 * - 連上過（收到 peer_info）之後意外中斷（對方沒說原因就結束、WebSocket 意外關閉、中繼／網路類錯誤）：
 *   不顯示「連線已中斷」，改成倒數（1、2、3、5、5、10、10、15 秒，共 8 次），提供「立即重連」與「取消」
 * - 每次重連都是完整的新連線：重新換票證、重新配對、重新收 Hash、重新算登入雜湊；已存的密碼每次重新 login_assist
 * - 重連用的密碼只在記憶體；唯讀檢視與剪貼簿沿用當下的設定；自動重連成功不重複存密碼
 * - 重連時要使用者介入（Wrong Password、2FA、等對方同意）就停止自動重連，照一般流程顯示
 * - G.5：同一個頁面的每一次連線都用同一個 session_id 與 my_name；重連時的 Wrong Password 提示一次性密碼可能換了；
 *   等對方同意卻收到 No Password Access 時提示受控端在登入畫面；Wayland 登入畫面的錯誤附說明（連結只當文字）
 * - G.6：Windows 的工作階段選擇（只有一個或記住的就是目前的那個時直接送；選別的會中斷再自動重連）
 * - G.7：Linux 受控端沒有桌面時請使用者輸入作業系統帳號密碼，同一條連線再登入一次；不儲存、不自動重送
 *
 * 多螢幕（附錄 H，協定在 src/rdweb/session.ts 與 displays.ts）：工具列「螢幕」選單（只有一個螢幕時不顯示），
 * 列出每個螢幕並標出目前的與主螢幕，選了就切換；一次只看一個。單一螢幕但可以改解析度時照樣顯示，只有解析度子選單。滑鼠座標是受控端虛擬桌面的絕對座標（加上目前螢幕的
 * 原點，5 像素以內貼齊邊緣），遠端游標的疊加層先減掉原點。螢幕被拔除時出提示並改看主螢幕；自動重連後回到使用者選的螢幕。
 * 解析度子選單只在可以控制時出現。
 * 畫質（附錄 I，規則在 src/rdweb/quality.ts）：工具列「畫質」選單（畫質、更新率上限、編碼偏好），改了立刻送出；
 * localStorage 只記這三個選項；狀態列的小字顯示延遲、位元率、每秒解出的張數與編碼名稱。
 *
 * Windows 免安裝受控端的系統管理員視窗、UAC 與請求提權（附錄 K，規則在 src/rdweb/elevation.ts，這裡只接線）：
 * - 平台標籤旁的「免安裝版」標籤（滑過有說明；輔助服務在跑時是「免安裝版・已提權」）
 * - 前景是系統管理員視窗、UAC 確認畫面：畫面上方的警告提示（不擋畫面），附「請求提權」
 * - 工具列「請求提權」下拉（由受控端確認／用系統管理員帳號）：Windows 免安裝、輔助服務沒在跑、有控制權、不是唯讀檢視才有
 * - 用帳號提權的對話框：密碼不記住、不寫進瀏覽器儲存、不送給 jt-ipam 後端，只在加密連線裡送給受控端；送出後立刻清空
 * - 稽核（瀏覽器自報）由 elevation.ts 在送出時與得到結果時各送一則；自動重連或連線結束時一切重新判斷
 */
import { computed, h, nextTick, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NForm, NFormItem, NInput, NButton, NButtonGroup, NSpace, NAlert, NIcon, NSpin, NTag, NSwitch, NCard,
  NPopconfirm, NTooltip, NModal, NCheckbox, NSelect, NRadioGroup, NRadio, NDropdown, NSlider, useMessage,
  type DropdownDividerOption, type DropdownGroupOption, type DropdownOption, type DropdownRenderOption,
} from "naive-ui";
import {
  requestRustDeskTicket, buildRustDeskWsUrl, listRustDeskCredentials, saveRustDeskPassword,
  deleteSavedRustDeskPassword, type RustDeskCredential,
} from "@/api/rustdeskWeb";
import { apiErrMsg } from "@/api/client";
import { srvText } from "@/utils/wsError";
import { fmtDateTime } from "@/utils/datetime";
import { RdSession, type CloseInfo, type OsLoginKind, type PasswordReason, type Phase } from "@/rdweb/session";
import { probeDecoding, VideoPipeline } from "@/rdweb/video";
import type {
  Decoding, ElevationRequest, MessageBox, PeerInfo, QualityOption, Resolution, SupportedEncoding, WindowsSessions,
} from "@/rdweb/messages";
import { randomSessionId } from "@/rdweb/crypto";
import { keyCodeFor, lockModifiers } from "@/rdweb/keymap";
import { planTyping, typeStrokes, TYPE_TEXT_MAX } from "@/rdweb/typeText";
import {
  domButton, mapPointer, MouseButton, mouseMask, MouseKind, mouseModifiers, unmapPointer, WheelAccumulator, type Rect,
} from "@/rdweb/input";
import { viewFromPeerInfo, type DisplayEventKind, type DisplayView } from "@/rdweb/displays";
import {
  clampCustom, codecChoices, codecLabel, CUSTOM_MAX, CUSTOM_MIN, DEFAULT_QUALITY, FPS_CHOICES, FpsMeter, loadQuality,
  loadShowStats, QUALITY_LEVELS, saveQuality, saveShowStats, toQualityOption, type CodecPref, type QualityLevel,
  type QualitySettings,
} from "@/rdweb/quality";
import {
  CursorCache, isRemoteMove, REMOTE_CURSOR_TIMEOUT_MS, renderCursorPng, type RenderedCursor,
} from "@/rdweb/cursor";
import { ClipboardSync, isPasteKey, pasteKeys, waitForPaste, writeLocalClipboard } from "@/rdweb/clipboard";
import {
  AutoReconnect, pickWindowsSession, RECONNECT_DELAYS_S, ticketFailure, TICKET_REFUSED, TICKET_UNAVAILABLE,
  type ReconnectAuth, type ReconnectView,
} from "@/rdweb/reconnect";
import {
  ElevationTracker, elevationUi, initialElevationState, type ElevationState,
} from "@/rdweb/elevation";
import { AdminIcon, PasteIcon, SendIcon, CopyIcon, LoginIcon } from "@/icons";
import { RustDeskIcon, CancelIcon, RefreshIcon, KeyIcon, LockIcon, ExpandIcon, ReduceIcon, DeleteIcon } from "@/icons";
import { CheckIcon, ChevronDownIcon, QualityIcon, renderIcon, ResolutionIcon, ScreensIcon } from "@/icons";
import ConnElapsed from "@/components/ConnElapsed.vue";
import ConsoleDisconnectedOverlay from "@/components/ConsoleDisconnectedOverlay.vue";

const props = withDefaults(defineProps<{
  addressId: string;
  ip: string;
  hostname?: string | null;
  deviceName?: string | null;
  peerId?: string | null;
  fullHeight?: boolean;
}>(), { fullHeight: false, hostname: null, deviceName: null, peerId: null });

const { t, te } = useI18n();
const msg = useMessage();

type UiState = "form" | "connecting" | "need_password" | "need_2fa" | "need_os_login" | "waiting_approval"
  | "connected" | "reconnecting" | "closed" | "error";
const ui = ref<UiState>("form");
const stage = ref<Phase>("connecting");
const errorMsg = ref("");
const form = reactive({ password: "", viewOnly: false });
const retry = reactive({
  password: "", code: "", wrong: false, wrong2fa: false, sending: false,
  notice: "",                       // 已存的密碼用不了時的說明（D.4），顯示在密碼框上面
  savedRejected: false,             // 這次是已存的密碼被對方拒絕：提供「刪除已存的密碼」
});

// ── 記住密碼（附錄 D）：照 VNC 主控台（VncScreen.vue）的已存帳密 ──
const savedCreds = ref<RustDeskCredential[]>([]);
const selectedCredId = ref<string | null>(null);
const remember = ref(false);        // 「記住密碼」：登入成功後才存
const rememberLabel = ref("");
const credOptions = ref<{ label: string; value: string }[]>([]);
/** 這次連線用的已存密碼（被對方拒絕時，畫面上的「刪除已存的密碼」刪的就是這筆） */
const usedCredId = ref<string | null>(null);

// ── 斷線後自動重新連線（附錄 G）──
// 重連用的密碼、開了「記住密碼」時等登入成功才存的密碼，都在 reconnect 裡（只在記憶體，連線結束就清掉）
const rcView = ref<ReconnectView>({ state: "idle", attempt: 0, total: RECONNECT_DELAYS_S.length, remaining: 0 });
const reconnect = new AutoReconnect({
  attempt: (_n, auth) => { void openSession({ auth }); },
  change: (v) => { rcView.value = v; },
});
/** 每次開始連線、取消、離開頁面時加一：還在等票證的那一次就不再往下做 */
let connectGen = 0;
/**
 * G.5：LoginRequest 的 session_id 在同一個頁面的所有連線都沿用同一個值（my_name 也是第一次拿到的那個）。
 * 受控端程式沒有重新啟動時（網路短暫中斷、jt-ipam 後端重新啟動），它會沿用當初的驗證狀態。
 */
const pageSessionId = randomSessionId();
let pageMyName: string | null = null;

// G.6：Windows 的工作階段選擇（顯示中的選單；這個頁面選過的 sid 只在記憶體）
const winPick = ref<(WindowsSessions & { selected: number }) | null>(null);
let chosenSid: number | null = null;

// G.7：作業系統帳號密碼（只在這個表單裡，送出後密碼立刻清掉；不儲存、不自動重送；連線結束時帳號也清掉）
const osForm = reactive({ username: "", password: "", rdPassword: "", needRd: false, notice: "" });
const osReady = computed(() => !!osForm.username.trim() && (!osForm.needRd || !!osForm.rdPassword));

async function loadCreds() {
  try {
    savedCreds.value = await listRustDeskCredentials(props.addressId);
    credOptions.value = [
      { label: t("rdweb.cred_manual"), value: null as unknown as string },
      ...savedCreds.value.map((c) => ({ label: c.label, value: c.id }))];
    if (selectedCredId.value && !savedCreds.value.some((c) => c.id === selectedCredId.value)) {
      selectedCredId.value = null;
    }
    if (!selectedCredId.value && savedCreds.value.length) {
      selectedCredId.value = savedCreds.value[0].id;
    }
  } catch { /* 靜默（同 VNC） */ }
}

async function deleteCred(id: string | null) {
  if (!id) return;
  try {
    await deleteSavedRustDeskPassword(id);
    if (selectedCredId.value === id) selectedCredId.value = null;
    if (usedCredId.value === id) usedCredId.value = null;
    retry.savedRejected = false;
    await loadCreds();
    msg.success(t("rdweb.saved_password_deleted"));
  } catch (e) {
    msg.error(apiErrMsg(e) || t("errors.server"));
  }
}
const delSelectedCred = () => deleteCred(selectedCredId.value);
const canDeleteUsed = computed(() => !!usedCredId.value && savedCreds.value.some((c) => c.id === usedCredId.value));

/** 登入成功（收到 peer_info）之後才存（D.2）。後端會取代這個 IP 原本的那一筆。 */
async function rememberIfAsked(pw: string) {
  try {
    const saved = await saveRustDeskPassword({ addressId: props.addressId, peerId: peerIdShown.value, password: pw,
                                               label: rememberLabel.value });
    // 同 VNC：記進本地狀態，同一分頁「重新連線」直接沿用剛存的密碼
    selectedCredId.value = saved.id;
    remember.value = false;
    rememberLabel.value = "";
    await loadCreds();
    msg.success(t("rdweb.password_saved"));
  } catch (e) {
    msg.error(apiErrMsg(e) || t("rdweb.err_save_password"));
  }
}

function passwordNotice(reason: PasswordReason, code?: string): string {
  if (reason === "login_screen") return t("rdweb.login_screen_hint");
  if (reason === "saved_rejected") return t("errors.rd_saved_password_rejected");
  if (reason === "saved_failed") {
    return srvText({ code: code || "rd_saved_password_unavailable", params: {}, message: "" },
                   t("errors.rd_saved_password_unavailable"));
  }
  return "";
}
const peerIdShown = ref(props.peerId || "");
const serverName = ref("");
const peer = ref<PeerInfo | null>(null);
const keyboardBlocked = ref(false);
const messageBoxes = ref<MessageBox[]>([]);
const hasVideo = ref(false);
// 剪貼簿（附錄 F）：預設開；唯讀檢視時一律關
const clipOn = ref(true);
const clipPending = ref<{ text: string; html?: string } | null>(null);   // 寫不進本機剪貼簿、等使用者按一下
const sendTextDlg = reactive({ show: false, text: "" });
// 附錄 F.4：「直接打字輸入」的進度（字元數）；取消旗標只在記憶體
const typing = reactive({ active: false, done: 0, total: 0 });
let typingCancel = false;
const clipInputEl = ref<HTMLTextAreaElement | null>(null);

const canvasEl = ref<HTMLCanvasElement | null>(null);
const canvasBoxEl = ref<HTMLElement | null>(null);
let ctx: CanvasRenderingContext2D | null = null;
let session: RdSession | null = null;
let clip: ClipboardSync | null = null;
let pipeline: VideoPipeline | null = null;
let ro: ResizeObserver | null = null;
let display: Rect = { x: 0, y: 0, width: 0, height: 0 };
const wheel = new WheelAccumulator();
const pressed = new Set<string>();

// 遠端游標（附錄 E）：形狀快取、畫面本身是否已畫游標、對方自己移動游標時的疊加層
const cursors = new CursorCache();
let cursorEmbedded = false;
let remotePos: { x: number; y: number } | null = null;
let remoteCursorTimer: ReturnType<typeof setTimeout> | null = null;
const remoteCursor = ref<{ left: number; top: number; img: RenderedCursor | null } | null>(null);

// 畫面縮放：fit＝符合視窗（CSS 縮放）、native＝原始解析度（1:1，超出可捲），與 VNC 主控台一致
const scaleMode = ref<"fit" | "native">("fit");

// 多螢幕（附錄 H）：螢幕狀態由 session 給（displays.ts）；display（上面）是目前螢幕，座標換算以它為準（H.3）
const dispView = ref<DisplayView | null>(null);
/** H.5：使用者在這個頁面選過的螢幕（只在記憶體）：自動重連後回到它；手動重新連線時清掉 */
let chosenDisplay: number | null = null;

// 請求提權（附錄 K）：狀態只有這條連線收到的訊息（elevation.ts）；帳號密碼只在對話框的欄位裡，送出或關閉就清掉
const elevState = ref<ElevationState>(initialElevationState());
const elevation = new ElevationTracker({
  send: (req) => !!session?.requestElevation(req),
  audit: (method, result, detail) => session?.reportElevation(method, result, detail),
  change: (st) => { elevState.value = st; },
});
const elevDlg = reactive({ show: false, username: "", password: "" });
/** 網域帳號的寫法（放在 i18n 參數裡，避免反斜線進文案） */
const ELEV_USER_EXAMPLE = "DOMAIN\\user";

// 畫質（附錄 I）：localStorage 只記畫質、更新率、編碼偏好三個選項（quality.ts），讀不到就用預設值
const quality = reactive<QualitySettings>(loadQuality());
// 效能列預設不顯示（使用者 2026-10-06）：畫質選單裡勾「顯示效能資訊」才出現，記在這台瀏覽器
const showStats = ref(loadShowStats());
const decodeCaps = ref<Decoding>({ vp9: true, h264: false, vp8: false, av1: false });
const peerEncoding = ref<SupportedEncoding | null>(null);
const qualityMenuShow = ref(false);
let keepQualityMenu = false;        // 選了「自訂」：選單留著，讓使用者調滑桿
let customTimer: ReturnType<typeof setTimeout> | null = null;
// 狀態列的小字（I.2）：延遲、位元率（TestDelay 回聲）、每秒解出的張數、編碼名稱；-1／空字串＝還不知道
const stats = reactive({ delay: -1, bitrate: -1, fps: -1, codec: "" });
const fpsMeter = new FpsMeter();
let statsTimer: ReturnType<typeof setInterval> | null = null;

const busy = computed(() => ui.value === "connecting" || ui.value === "reconnecting");
const stageText = computed(() => {
  const k = `rdweb.stage_${stage.value}`;
  return te(k) ? t(k) : t("rdweb.state_connecting");
});
const platform = computed(() => peer.value?.platform || "");
/** 附錄 G.3：覆蓋層的第二行：「N 秒後自動重新連線（第 k 次）」或這一次進行到哪裡 */
const reconnectHint = computed(() => {
  if (ui.value !== "reconnecting") return undefined;
  const v = rcView.value;
  return v.state === "waiting"
    ? t("rdweb.reconnect_countdown", { n: v.remaining, k: v.attempt, total: v.total })
    : t("rdweb.reconnect_trying", { k: v.attempt, total: v.total, stage: stageText.value });
});

function applyScale() {
  const c = canvasEl.value, box = canvasBoxEl.value;
  if (!c || !box || !c.width || !c.height) return;
  if (scaleMode.value === "native") {
    c.style.width = ""; c.style.height = "";
  } else {
    const s = Math.min(box.clientWidth / c.width, box.clientHeight / c.height);
    c.style.width = Math.max(1, Math.round(c.width * s)) + "px";
    c.style.height = Math.max(1, Math.round(c.height * s)) + "px";
  }
  applyCursor();             // 比例變了：游標跟著縮放（同一個比例的結果有快取）
}
function setScale(m: "fit" | "native") {
  scaleMode.value = m;
  nextTick(applyScale);
}

// ── 錯誤文字：後端給代碼（errors.<code>），瀏覽器自己的也是同一套代碼；原文接在後面 ──
const LOGIN_ERRORS: Record<string, string> = {
  "Too many wrong attempts": "too_many_attempts",
  "Please try 1 minute later": "try_later",
  "Offline": "offline",
  "Your ip is blocked by the peer": "ip_blocked",
  "The main window is not open": "main_window",
  "Wayland login screen is not supported": "wayland_login",     // G.5
  "Desktop session another user login": "another_user",         // G.7：這三個結束、不重試
  "Desktop xorg not found": "xorg_missing",
  "Desktop none": "no_desktop",
};
/** G.5：Wayland 登入畫面的說明連結（只當文字顯示，§8.7） */
const WAYLAND_LOGIN_DOC = "https://rustdesk.com/docs/en/manual/linux/#login-screen";
function closeText(info: CloseInfo): string {
  if (info.code === TICKET_UNAVAILABLE || info.code === TICKET_REFUSED) return info.detail || t("rdweb.err_ticket");
  // 開著的連線被收回權限（core/console_guard）：detail 已是翻好的句子
  if (info.code === "console_revoked") return info.detail || t("errors.console_revoked");
  if (info.code === "rd_login_error") {
    const raw = info.detail || "";
    // 原文是受控端送來的：不可以比對到 constructor 這類原型上的名字
    const k = Object.prototype.hasOwnProperty.call(LOGIN_ERRORS, raw) ? LOGIN_ERRORS[raw] : "";
    const head = k ? t(`rdweb.login_err_${k}`, { url: WAYLAND_LOGIN_DOC }) : "";
    return head ? `${head}（${raw}）` : t("errors.rd_login_error", { reason: raw });
  }
  if (info.code === "rd_peer_closed") {
    const reason = info.peerReason || "";
    return reason ? `${t("errors.rd_peer_closed")}：${reason}` : t("errors.rd_peer_closed");
  }
  if (!info.code || info.code === "ws_closed") return t("rdweb.err_ws");
  const params: Record<string, unknown> = { reason: info.detail || "", ...(info.params || {}) };
  if (typeof params.at === "string") params.at = fmtDateTime(params.at);    // 被拒的時間（rd_peer_key_mismatch）
  const base = srvText({ code: info.code, params, message: info.detail || "" }, t("rdweb.err_generic"));
  const detail = info.detail || "";
  return detail && !base.includes(detail) ? `${base}（${detail}）` : base;
}

// 對方的 RustDesk 密碼不可以被自動填入：autocomplete=off 擋不住 Chrome 填入已儲存的 jt-ipam 登入密碼
// （送出去就是一次「密碼錯誤」，錯太多次對方會封鎖 jt-ipam）；new-password 才會停手，另外三個是密碼管理工具的「不要填」標記
const NO_AUTOFILL = { autocomplete: "new-password", "data-1p-ignore": "true", "data-lpignore": "true", "data-bwignore": "true" };
/** G.7 的作業系統帳號欄：同樣不讓瀏覽器把 jt-ipam 的登入帳號填進來 */
const NO_AUTOFILL_USER = { autocomplete: "off", "data-1p-ignore": "true", "data-lpignore": "true", "data-bwignore": "true" };

// ── 連線 ──

/** 使用者按「連線」：一次新的手動連線（上一輪的自動重連狀態一併清掉）。 */
function connect() {
  reconnect.cancel();
  chosenDisplay = null;                    // H.5：只有自動重連才回到上次選的螢幕
  void openSession(null);
}

/**
 * 開一條完整的新連線：換票證 → 開 WebSocket → 會合、中繼、握手、登入都重做（附錄 G：每次重連都是新的票證、
 * 新的配對、新的 Hash、新的登入雜湊）。auto 不是 null＝自動重連的其中一次，照 auto.auth 登入。
 */
async function openSession(auto: { auth: ReconnectAuth | null } | null) {
  const gen = ++connectGen;
  errorMsg.value = "";
  messageBoxes.value = [];
  clipPending.value = null;
  keyboardBlocked.value = false;
  if (!auto) hasVideo.value = false;       // 自動重連時先留著斷線前的最後一個畫面（調暗、蓋在覆蓋層下面）
  peer.value = null;
  winPick.value = null;
  clearOsForm();
  resetCursor();
  cursorEmbedded = false;
  dispView.value = null;
  peerEncoding.value = null;
  elevation.reset();                        // 附錄 K.2：提權狀態與提示都不沿用（自動重連也是）
  closeElevLogon();
  stopStats();
  resetStats();
  stage.value = "connecting";
  ui.value = auto ? "reconnecting" : "connecting";

  const dec = await probeDecoding();
  if (gen !== connectGen) return;
  decodeCaps.value = { vp9: dec.vp9, h264: dec.h264, vp8: dec.vp8, av1: dec.av1 };
  if (!dec.webcodecs || !dec.vp9) {
    endWith({ code: "rd_decoder_unsupported", params: { codec: "VP9" } },
            t("errors.rd_decoder_unsupported", { codec: "VP9" }));
    return;
  }

  let ticket;
  try {
    ticket = await requestRustDeskTicket(props.addressId);
  } catch (e) {
    if (gen !== connectGen) return;
    // 沒有回應或閘道錯誤＝jt-ipam 後端重新啟動中：重連途中算「還沒回來」；401／403 等重試也不會好
    const status = (e as { response?: { status?: number } } | null)?.response?.status;
    endWith(ticketFailure(status, apiErrMsg(e) || t("rdweb.err_ticket")));
    return;
  }
  if (gen !== connectGen) return;
  peerIdShown.value = ticket.peer_id;
  serverName.value = ticket.server_name;
  await nextTick();
  if (gen !== connectGen) return;
  ctx = canvasEl.value?.getContext("2d") ?? null;

  pipeline?.close();
  pipeline = new VideoPipeline({
    frame: drawFrame,
    requestKeyframe: () => session?.requestKeyframe(),
    unsupported: (codec) => {
      session?.close();
      ui.value = "error";
      errorMsg.value = t("errors.rd_decoder_unsupported", { codec });
    },
  });

  // 附錄 D.5：用已存的密碼時不帶密碼，只帶金庫那一筆的 id。票證說沒有（例如在別的分頁刪掉了）就改問密碼
  let password: string | undefined;
  let savedCredentialId: string | undefined;
  retry.notice = "";
  usedCredId.value = null;
  if (auto) {
    // 附錄 G：使用者輸入的密碼從記憶體拿；已存的密碼每次重連照 D.3 重新 login_assist（瀏覽器只有 credential_id）
    const a = auto.auth;
    if (a?.kind === "typed") {
      password = a.password;
    } else if (a?.kind === "saved") {
      if (ticket.has_saved_password) {
        savedCredentialId = a.credentialId;
        usedCredId.value = savedCredentialId;
      } else {
        void loadCreds();
        retry.notice = t("errors.rd_saved_password_unavailable");
      }
    }
  } else {
    password = form.password;
    if (selectedCredId.value) {
      password = undefined;
      if (ticket.has_saved_password) {
        savedCredentialId = selectedCredId.value;
        usedCredId.value = savedCredentialId;
      } else {
        selectedCredId.value = null;
        void loadCreds();
        retry.notice = t("errors.rd_saved_password_unavailable");
      }
    }
    form.password = "";
    reconnect.begin(savedCredentialId ? { kind: "saved", credentialId: savedCredentialId }
      : password !== undefined ? { kind: "typed", password } : null, remember.value);
  }
  pageMyName ??= ticket.my_name;
  session = new RdSession({
    url: buildRustDeskWsUrl(ticket.ws_path, ticket.ticket),
    peerId: ticket.peer_id,
    myName: pageMyName,                // G.5：同一個頁面的連線 my_name 不變
    sessionId: pageSessionId,          // G.5：session_id 也不變
    decoding: { vp9: true, h264: dec.h264, vp8: dec.vp8, av1: dec.av1 },
    password,
    savedCredentialId,
    viewOnly: form.viewOnly,          // 附錄 G：重連沿用當下的唯讀檢視與剪貼簿設定
    clipboard: clipOn.value,
    // 附錄 I：登入時帶入目前的畫質設定（自動重連也沿用）；這時還不知道受控端能編哪些（在登入回應裡）
    quality: toQualityOption(quality, decodeCaps.value),
    // H.5：自動重連後回到使用者原本選的螢幕（還存在的話；session 會檢查）
    display: auto && chosenDisplay !== null ? chosenDisplay : undefined,
    events: {
      phase: (p) => { stage.value = p; },
      needPassword: (reason, code) => {
        const duringReconnect = reconnect.trying;
        reconnect.interrupt("password");     // G.3：要使用者輸入密碼就停止自動重連（記憶體裡的密碼已經不能用）
        hasVideo.value = false;
        retry.wrong = reason === "wrong";
        // G.5：重連時手動輸入的密碼被拒，多半是受控端重新啟動、一次性密碼換了
        const notice = reason === "wrong" && duringReconnect ? t("rdweb.reconnect_otp_hint")
          : passwordNotice(reason, code);
        if (notice || reason !== "initial") retry.notice = notice;
        retry.savedRejected = reason === "saved_rejected";
        if (reason === "saved_rejected") {
          // D.4：已失效。回到表單時改成手動輸入，不要預設再用它（使用者可以自己在下拉選回來）
          selectedCredId.value = null;
        }
        retry.password = "";
        retry.sending = false;
        ui.value = "need_password";
      },
      need2fa: (wrong) => {
        reconnect.interrupt("2fa");
        hasVideo.value = false;
        retry.wrong2fa = wrong;
        retry.code = "";
        retry.sending = false;
        ui.value = "need_2fa";
      },
      needOsLogin: onNeedOsLogin,
      waitingApproval: () => {
        reconnect.interrupt("approval");
        hasVideo.value = false;
        ui.value = "waiting_approval";
      },
      connected: (info) => {
        const wasAuto = reconnect.trying;
        const toSave = reconnect.connected();     // 計數歸零；自動重連成功不會交出要存的密碼
        if (wasAuto) hasVideo.value = false;      // 斷線前的畫面：等新的畫面進來
        onPeerInfo(info);
        elevation.login(info);                    // 附錄 K：平台與 is_installed 以登入回應為準
        peerEncoding.value = info.encoding ?? null;
        const v = viewFromPeerInfo(info);         // session 接著會送 displays（login），這裡先有起始值
        if (v) onDisplays(v, "login");
        startStats();
        ui.value = "connected";
        nextTick(() => canvasEl.value?.focus());
        if (toSave) void rememberIfAsked(toSave);
      },
      windowsSessions: onWindowsSessions,
      peerInfo: (info) => { onPeerInfo(info); elevation.peerInfo(info); },
      video: (codec, frames) => pipeline?.push(codec, frames),
      displays: onDisplays,
      delay: (d) => { stats.delay = d.lastDelay; stats.bitrate = d.targetBitrate; },
      encoding: (e) => { peerEncoding.value = e; },
      cursorData: (cd) => { if (cursors.receive(cd)) applyCursor(); },
      cursorId: (id) => { if (cursors.select(id)) applyCursor(); },
      cursorPosition: onRemoteCursorPosition,
      keyboardPermission: (enabled) => { keyboardBlocked.value = !enabled; },
      messageBox: (mb) => { messageBoxes.value = [...messageBoxes.value.slice(-2), mb]; },
      clipboard: onRemoteClipboard,
      // 附錄 K.1：Windows 免安裝受控端的權限狀態與提權結果
      uac: (v) => elevation.uac(v),
      foregroundElevated: (v) => elevation.foreground(v),
      elevationResponse: (text) => elevation.response(text),
      portableService: (v) => elevation.service(v),
      closed: onClosed,
    },
  });
  clip = new ClipboardSync({ enabled: () => !!session?.clipboardEnabled, send: (m) => !!session?.sendClipboard(m) });
}

// ── G.6：Windows 的工作階段選擇 ──

/** 登入回應帶 windows_sessions：受控端要等 selected_sid 才送畫面。只有一個、或記住的就是目前的那個時直接送。 */
function onWindowsSessions(ws: WindowsSessions) {
  const sid = pickWindowsSession(ws, chosenSid);
  if (sid !== null) {
    winPick.value = null;
    session?.selectWindowsSession(sid);
    return;
  }
  winPick.value = { ...ws, selected: ws.currentSid };
}

/** 送出選的工作階段。選了不是目前的那個：受控端會到那個工作階段重新啟動、直接中斷這條連線，接著自動重連（G.2）。 */
function submitWinSession() {
  const p = winPick.value;
  if (!p || !session) return;
  chosenSid = p.selected;
  winPick.value = null;
  session.selectWindowsSession(p.selected);
}

// ── G.7：Linux 受控端沒有桌面時的作業系統登入 ──

function clearOsForm() {
  osForm.username = "";
  osForm.password = "";
  osForm.rdPassword = "";
  osForm.needRd = false;
  osForm.notice = "";
}

function onNeedOsLogin(kind: OsLoginKind) {
  const needRd = kind === "password_empty" || kind === "password_wrong";
  // 要重輸 RustDesk 密碼時，記憶體裡的那一組已經不能用；只缺作業系統帳號密碼時照留（作業系統的不經過重連）
  reconnect.interrupt(needRd ? "password" : "os_login");
  hasVideo.value = false;
  osForm.password = "";
  osForm.rdPassword = "";
  osForm.needRd = needRd;
  osForm.notice = kind === "xsession_failed" ? t("rdweb.os_err_xsession")
    : kind === "password_wrong" ? t("rdweb.os_err_rd_password") : "";
  ui.value = "need_os_login";
}

async function submitOsLogin() {
  if (!session || !osReady.value) return;
  const username = osForm.username.trim();
  const osPassword = osForm.password;
  const rdPassword = osForm.needRd ? osForm.rdPassword : undefined;
  osForm.password = "";                // 送出就清掉，不留在畫面上
  osForm.rdPassword = "";
  ui.value = "connecting";
  if (rdPassword) reconnect.typed(rdPassword, remember.value);   // RustDesk 密碼：之後的重連用這一組
  await session.loginOs(username, osPassword, rdPassword);
}

/** PeerInfo（登入回應或連線中的更新）。螢幕狀態不從這裡拿：連線中的 peer_info 的 current_display 一律是 0（H.1）。 */
function onPeerInfo(info: PeerInfo) {
  peer.value = info;
}

// ── 多螢幕（附錄 H）──

/**
 * 螢幕狀態變了：座標換算與遠端游標以目前螢幕的 x、y、width、height 為準（H.3）。
 * 換螢幕（select／removed）時清掉游標快取（E.1），解碼器等新螢幕的關鍵影格（refresh_video_display 已經送了）；
 * 同一個螢幕的大小改了（resize）不是切換，只等新大小的關鍵影格。被拔除、螢幕數量變了都出一則提示。
 */
function onDisplays(v: DisplayView, kind: DisplayEventKind, removed?: number) {
  const before = dispView.value?.list.length ?? 0;
  dispView.value = v;
  display = { x: v.geometry.x, y: v.geometry.y, width: v.geometry.width, height: v.geometry.height };
  cursorEmbedded = v.geometry.cursorEmbedded;
  if (kind === "select" || kind === "removed") {
    resetCursor();
    pipeline?.waitForKeyframe(false);
  } else if (kind === "resize") {
    pipeline?.waitForKeyframe(false);
  }
  applyCursor();
  if (kind === "removed" && removed !== undefined) {
    msg.warning(t("rdweb.disp_removed", { n: removed + 1, to: v.current + 1 }));
  } else if (kind === "update" && before && before !== v.list.length) {
    msg.info(t("rdweb.disp_count_changed", { n: v.list.length }));
  }
}

const displayCount = computed(() => dispView.value?.list.length ?? 0);
/** H.4：解析度子選單只在可以控制時出現（受控端要鍵盤權限），而且受控端是桌面作業系統 */
const canChangeResolution = computed(() => !form.viewOnly && !keyboardBlocked.value
  && ["Windows", "Linux", "Mac OS"].includes(platform.value));
type MenuItem = DropdownOption | DropdownGroupOption | DropdownDividerOption | DropdownRenderOption;
const MENU_ICON = 16;
const checkIcon = (on: boolean) => (on ? renderIcon(CheckIcon, MENU_ICON) : undefined);

/** 選單項目的文字＋小標籤（「目前」「主螢幕」，同 G.6 工作階段選擇的標示） */
function labelWithTags(text: string, tags: { text: string; type: "info" | "default" }[]) {
  return () => h("span", { style: "display:inline-flex;align-items:center;gap:6px" }, [
    text, ...tags.map((tg) => h(NTag, { size: "tiny", round: true, bordered: false, type: tg.type }, () => tg.text)),
  ]);
}

/**
 * H.4：可選的解析度只來自登入時的 PeerInfo.resolutions 與切換後的 SwitchDisplay.resolutions；「原始解析度」排第一。
 * 能選的只有目前的大小時等於沒有東西可以選，回空的（子選單不出現）。
 */
function resolutionOptions(v: DisplayView): DropdownOption[] {
  const g = v.geometry;
  const isCurrent = (r: Resolution) => r.width === g.width && r.height === g.height;
  const o = v.originalResolution;
  const entries: { key: string; label: string; current: boolean }[] = [];
  if (o.width > 0 && o.height > 0) {          // 0×0＝受控端的虛擬螢幕，沒有原始解析度可以回去
    entries.push({ key: `res:${o.width}x${o.height}`, label: t("rdweb.disp_res_original", { w: o.width, h: o.height }),
                   current: isCurrent(o) });
  }
  for (const r of v.resolutions) {
    const key = `res:${r.width}x${r.height}`;
    if (!entries.some((x) => x.key === key)) entries.push({ key, label: `${r.width}×${r.height}`, current: isCurrent(r) });
  }
  if (!entries.some((e) => !e.current)) return [];
  return entries.map((e) => ({ key: e.key, label: e.label, icon: checkIcon(e.current) }));
}

/** 解析度子選單的項目：唯讀檢視、對方關閉控制權、不是桌面作業系統時沒有 */
const resolutionChoices = computed(() => (dispView.value && canChangeResolution.value
  ? resolutionOptions(dispView.value) : []));
/**
 * H.5：只有一個螢幕、而且沒有可選的解析度時才不顯示「螢幕」選單；
 * 單一螢幕但可以改解析度時照樣顯示，裡面只有解析度子選單。
 */
const showDisplayMenu = computed(() => displayCount.value > 1 || resolutionChoices.value.length > 0);

const displayMenu = computed<MenuItem[]>(() => {
  const v = dispView.value;
  if (!v) return [];
  // 只有一個螢幕時不列螢幕清單（沒有東西可以切換），只留解析度子選單
  const items: MenuItem[] = v.list.length < 2 ? [] : v.list.map((d, i) => ({
    key: `d:${i}`,
    icon: renderIcon(ScreensIcon, MENU_ICON),
    label: labelWithTags(t("rdweb.disp_item", { n: i + 1, w: d.width, h: d.height }), [
      ...(i === v.current ? [{ text: t("rdweb.disp_current"), type: "info" as const }] : []),
      ...(i === v.primary ? [{ text: t("rdweb.disp_primary"), type: "default" as const }] : []),
    ]),
  }));
  const res = resolutionChoices.value;
  if (res.length) {
    if (items.length) items.push({ type: "divider", key: "d_res" });
    items.push({ key: "res", label: t("rdweb.disp_resolution"), icon: renderIcon(ResolutionIcon, MENU_ICON), children: res });
  }
  return items;
});

/** 「螢幕」選單：切換（H.2，一次只看一個）或改解析度（H.4） */
function onDisplaySelect(key: string | number) {
  const k = String(key);
  if (k.startsWith("d:")) {
    const n = Number(k.slice(2));
    chosenDisplay = n;
    session?.switchDisplay(n);
  } else if (k.startsWith("res:")) {
    const [w, hh] = k.slice(4).split("x").map(Number);
    if (session?.changeResolution({ width: w, height: hh })) msg.info(t("rdweb.disp_res_requested", { w, h: hh }));
  }
  canvasEl.value?.focus({ preventScroll: true });
}

// ── 請求提權（附錄 K）──

/** K.2 的顯示條件（標籤、前景／UAC 提示、工具列選單），規則在 elevation.ts */
const elevUi = computed(() => elevationUi(elevState.value, {
  connected: ui.value === "connected", viewOnly: form.viewOnly, keyboardBlocked: keyboardBlocked.value,
}));

/** 「請求提權」的兩種方式（工具列與提示裡共用），最後一行說明稽核是瀏覽器自報的 */
const elevMenu = computed<MenuItem[]>(() => [
  { key: "direct", label: t("rdweb.elev_direct"), icon: renderIcon(AdminIcon, MENU_ICON) },
  { key: "logon", label: t("rdweb.elev_logon"), icon: renderIcon(KeyIcon, MENU_ICON) },
  { type: "divider", key: "d_note" },
  { type: "render", key: "elev_note",
    render: () => h("div", { style: "padding:4px 14px 6px;max-width:260px;font-size:11px;line-height:1.5;opacity:.7" },
                    t("rdweb.elev_audit_note")) },
]);

function onElevSelect(key: string | number) {
  if (key === "direct") {
    requestElevation({ method: "direct" });
    canvasEl.value?.focus({ preventScroll: true });
  } else if (key === "logon") {
    elevDlg.username = "";
    elevDlg.password = "";
    elevDlg.show = true;
  }
}

/** 交給 elevation.ts；session 沒有送出（唯讀、對方剛關閉控制權、已斷線）時說明原因 */
function requestElevation(req: ElevationRequest) {
  if (!elevation.request(req)) msg.warning(t("rdweb.elev_unavailable"));
}

/** 對話框的欄位清掉（帳號密碼不留在畫面上） */
function closeElevLogon() {
  elevDlg.show = false;
  elevDlg.username = "";
  elevDlg.password = "";
}

/** K.2：帳號密碼只在這一則加密的訊息裡送給受控端；送出前就清掉欄位（不記住、不進瀏覽器儲存、不送給後端） */
function submitElevLogon() {
  const username = elevDlg.username.trim();
  const password = elevDlg.password;
  if (!username) return;
  closeElevLogon();
  requestElevation({ method: "logon", username, password });
  canvasEl.value?.focus({ preventScroll: true });
}

/** 已送出／成功／錯誤／逾時的提示 */
const elevNotice = computed<{ type: "info" | "success" | "error" | "warning"; text: string; busy: boolean } | null>(() => {
  const n = elevState.value.notice;
  if (!n) return null;
  if (n.kind === "requesting") return { type: "info", text: t("rdweb.elev_requesting"), busy: true };
  if (n.kind === "sent") {
    return { type: "info", text: t(n.method === "direct" ? "rdweb.elev_sent_direct" : "rdweb.elev_sent_logon"), busy: true };
  }
  if (n.kind === "ok") return { type: "success", text: t("rdweb.elev_ok"), busy: false };
  if (n.kind === "timeout") return { type: "warning", text: t("rdweb.elev_timeout"), busy: false };
  // 三種常見的原文翻譯成說明（附上原文），其他照原文顯示
  const text = n.reason ? t(`rdweb.elev_err_${n.reason}`, { raw: n.text }) : t("rdweb.elev_err_raw", { raw: n.text });
  return { type: "error", text, busy: false };
});

// ── 畫質（附錄 I）──

/** 編碼偏好只列「瀏覽器解得了、受控端也編得出來」的（I.2） */
const codecOptions = computed(() => codecChoices(decodeCaps.value, peerEncoding.value));
/** 選單上勾的編碼：偏好的這台受控端不支援時顯示「自動」 */
const shownCodec = computed<CodecPref>(() => (codecOptions.value.includes(quality.codec) ? quality.codec : "auto"));

/** 自訂畫質的滑桿（10～100）。在下拉選單裡畫，用行內樣式（scoped 樣式管不到選單） */
function renderCustomSlider() {
  return h("div", { style: "padding:2px 14px 8px;width:220px;box-sizing:border-box" }, [
    h("div", { style: "font-size:12px;opacity:.75;margin-bottom:4px" }, t("rdweb.q_custom_value", { n: quality.custom })),
    h(NSlider, { value: quality.custom, min: CUSTOM_MIN, max: CUSTOM_MAX, step: 1, tooltip: false,
                 "onUpdate:value": onCustomSlide }),
  ]);
}

const qualityMenu = computed<MenuItem[]>(() => {
  const items: MenuItem[] = [{
    type: "group", key: "g_q", label: t("rdweb.q_quality"),
    children: QUALITY_LEVELS.map((l) => ({ key: `q:${l}`, label: t(`rdweb.q_${l}`), icon: checkIcon(quality.level === l) })),
  }];
  if (quality.level === "custom") items.push({ type: "render", key: "q_slider", render: renderCustomSlider });
  items.push(
    { type: "divider", key: "d_fps" },
    { type: "group", key: "g_fps", label: t("rdweb.q_fps"),
      children: FPS_CHOICES.map((n) => ({
        key: `fps:${n}`, icon: checkIcon(quality.fps === n),
        label: n === DEFAULT_QUALITY.fps ? t("rdweb.q_fps_default", { n }) : t("rdweb.q_fps_item", { n }),
      })) },
    { type: "divider", key: "d_codec" },
    { type: "group", key: "g_codec", label: t("rdweb.q_codec"),
      children: codecOptions.value.map((c) => ({
        key: `codec:${c}`, icon: checkIcon(shownCodec.value === c),
        label: c === "auto" ? t("rdweb.q_codec_auto") : codecLabel(c),
      })) },
    { type: "divider", key: "d_stats" },
    { key: "stats:toggle", label: t("rdweb.q_show_stats"), icon: checkIcon(showStats.value) },
    { type: "divider", key: "d_note" },
    // I.2：多人同時看同一台時，畫質以最後設定的為準
    { type: "render", key: "q_note",
      render: () => h("div", { style: "padding:4px 14px 6px;max-width:240px;font-size:11px;line-height:1.5;opacity:.7" },
                      t("rdweb.q_shared_note")) },
  );
  return items;
});

/** 改了立刻用 Misc.option 送出（只送改變的欄位）；記進 localStorage（只有三個選項，失敗不影響連線） */
function applyQuality(touched: (keyof QualityOption)[]) {
  saveQuality(quality);
  session?.setQuality(toQualityOption(quality, decodeCaps.value, peerEncoding.value), touched);
}

function onQualitySelect(key: string | number) {
  const k = String(key);
  const at = k.indexOf(":");
  const group = k.slice(0, at), val = k.slice(at + 1);
  if (group === "q") {
    quality.level = val as QualityLevel;
    keepQualityMenu = val === "custom";
    applyQuality(["imageQuality"]);
  } else if (group === "fps") {
    quality.fps = Number(val);
    applyQuality(["customFps"]);
  } else if (group === "codec") {
    quality.codec = val as CodecPref;
    applyQuality(["prefer"]);
  } else if (group === "stats") {
    showStats.value = !showStats.value;
    saveShowStats(showStats.value);
  }
}

function onQualityMenuShow(v: boolean) {
  if (!v && keepQualityMenu) {
    keepQualityMenu = false;
    return;
  }
  qualityMenuShow.value = v;
  if (!v) canvasEl.value?.focus({ preventScroll: true });
}

/** 滑桿拖動時不要每一格都送：停 300 毫秒再送 */
function onCustomSlide(v: number | number[]) {
  quality.custom = clampCustom(Array.isArray(v) ? v[0] : v);
  if (customTimer) clearTimeout(customTimer);
  customTimer = setTimeout(() => {
    customTimer = null;
    applyQuality(["imageQuality"]);
  }, 300);
}

// 狀態列的小字（I.2）
const statsText = computed(() => {
  const parts: string[] = [];
  if (stats.delay >= 0) parts.push(t("rdweb.stats_delay", { n: stats.delay }));
  if (stats.bitrate > 0) parts.push(t("rdweb.stats_bitrate", { n: stats.bitrate }));
  if (stats.fps >= 0) parts.push(t("rdweb.stats_fps", { n: stats.fps }));
  if (stats.codec) parts.push(stats.codec);
  return parts.join(" · ");
});

function resetStats() {
  stats.delay = -1;
  stats.bitrate = -1;
  stats.fps = -1;
  stats.codec = "";
  fpsMeter.reset();
}

/** 每秒一次：瀏覽器這一秒解出幾張（不是受控端送了幾張）、目前在解的編碼 */
function sampleStats() {
  const fps = fpsMeter.sample(pipeline?.decoded ?? 0, performance.now());
  if (fps !== null) stats.fps = fps;
  stats.codec = codecLabel(pipeline?.currentCodec);
}

function startStats() {
  stopStats();
  fpsMeter.reset();
  statsTimer = setInterval(sampleStats, 1000);
}

function stopStats() {
  if (statsTimer) clearInterval(statsTimer);
  statsTimer = null;
}

// ── 遠端游標（附錄 E）──

/** 1 個受控端像素佔幾個 CSS 像素（與 9.1 的座標換算同一個比例）。 */
function viewScale(): number {
  const c = canvasEl.value;
  if (!c || display.width <= 0 || !c.clientWidth) return 1;
  return c.clientWidth / display.width;
}

/** E.1：對方的游標形狀當成本地游標。畫面本身已經畫了游標（cursor_embedded）時維持一般游標，避免同時看到兩個。 */
function applyCursor() {
  const c = canvasEl.value;
  if (c) c.style.cursor = cursorEmbedded ? "" : cursors.css(viewScale(), renderCursorPng);
  placeRemoteCursor();
}

/** 斷線、重連、換螢幕：清掉快取、恢復一般游標、收起疊加層。 */
function resetCursor() {
  cursors.clear();
  hideRemoteCursor();
  if (canvasEl.value) canvasEl.value.style.cursor = "";
}

function hideRemoteCursor() {
  if (remoteCursorTimer) clearTimeout(remoteCursorTimer);
  remoteCursorTimer = null;
  remotePos = null;
  remoteCursor.value = null;
}

/** E.2：位置跟我們最後送出的差超過 4 個受控端像素才畫（代表是對方自己在動，不是回音）；3 秒沒有新的位置就收起。 */
function onRemoteCursorPosition(p: { x: number; y: number }) {
  if (cursorEmbedded || !isRemoteMove(p, session?.lastPointer ?? null)) {
    hideRemoteCursor();
    return;
  }
  remotePos = p;
  if (remoteCursorTimer) clearTimeout(remoteCursorTimer);
  remoteCursorTimer = setTimeout(hideRemoteCursor, REMOTE_CURSOR_TIMEOUT_MS);
  placeRemoteCursor();
}

/** 疊加層的位置：受控端座標反向換算成畫面座標（9.1 的反向），落在目前螢幕之外就不畫。 */
function placeRemoteCursor() {
  const c = canvasEl.value;
  const shape = cursors.current;
  const at = remotePos && c && !cursorEmbedded
    ? unmapPointer(remotePos.x, remotePos.y, c.clientWidth, c.clientHeight, display) : null;
  if (!c || !at || shape?.transparent) {          // 對方隱藏了游標：疊加層也不畫
    remoteCursor.value = null;
    return;
  }
  // 用目前的游標形狀；還沒有形狀（或畫不出來）時畫簡單的箭頭
  const img = shape ? cursors.rendered(viewScale(), renderCursorPng) : null;
  remoteCursor.value = { left: c.offsetLeft + at.px, top: c.offsetTop + at.py, img };
}

function onClosed(info: CloseInfo) {
  winPick.value = null;
  elevation.reset();
  closeElevLogon();
  stopStats();
  qualityMenuShow.value = false;
  clearOsForm();
  releaseAll();
  resetCursor();
  pipeline?.close();
  pipeline = null;
  session = null;
  endWith(info);
}

/**
 * 連線結束（或開連線之前就失敗）：先問自動重連要不要接手（附錄 G），不要才照原本的方式顯示。
 * text＝不重連時要顯示的訊息（沒給就由結束原因組）。
 */
function endWith(info: CloseInfo, text?: string) {
  const d = reconnect.ended(info);
  if (d.kind === "wait") {
    ui.value = "reconnecting";                 // 倒數中：覆蓋層顯示「N 秒後自動重新連線（第 k 次）」
    return;
  }
  if (d.kind === "gave_up") {
    ui.value = "error";
    errorMsg.value = t("rdweb.reconnect_gave_up", { n: d.attempts, reason: closeText(d.last) });
    return;
  }
  if (info.byUser) {
    ui.value = "closed";
    return;
  }
  if (info.code === "rd_peer_closed" && !info.peerReason && hasVideo.value) {
    ui.value = "closed";
    return;
  }
  ui.value = "error";
  errorMsg.value = text ?? closeText(info);
}

/** 「立即重連」：不等倒數。 */
function reconnectNow() {
  reconnect.now();
}

/** 「取消」自動重連：回到「連線已中斷」與手動「重新連線」，記憶體裡的密碼清掉。 */
function cancelReconnect() {
  connectGen++;                  // 還在換票證的那一次不再往下做
  reconnect.cancel();
  session?.close();              // 進行中的那一次：當成使用者自己結束
  pipeline?.close();
  pipeline = null;
  ui.value = "closed";
}

function drawFrame(frame: VideoFrame) {
  const c = canvasEl.value;
  try {
    if (!c) return;
    const w = frame.displayWidth, h = frame.displayHeight;
    if (c.width !== w || c.height !== h) {
      c.width = w;
      c.height = h;
      ctx = c.getContext("2d");
      nextTick(applyScale);
    }
    ctx?.drawImage(frame, 0, 0, w, h);
    if (!hasVideo.value) {
      hasVideo.value = true;
      nextTick(() => {
        applyScale();
        if (!ro && canvasBoxEl.value) { ro = new ResizeObserver(() => applyScale()); ro.observe(canvasBoxEl.value); }
      });
    }
  } finally {
    frame.close();             // 8.2：畫完一定要 close
  }
}

async function submitPassword() {
  if (!session || retry.sending) return;
  retry.sending = true;
  ui.value = "connecting";
  const pw = retry.password;
  retry.password = "";
  reconnect.typed(pw, remember.value);     // 手動登入：之後的重連用這一組；勾了記住、登入成功才存
  await session.login(pw);
}

function submit2fa() {
  if (!session || !retry.code.trim()) return;
  ui.value = "connecting";
  session.submit2fa(retry.code);
  retry.code = "";
}

function disconnect() {
  if (session) {
    session.close();
    return;
  }
  // 還在準備（檢查解碼器、換票證）時 session 還沒建立：以前按了沒反應、連線照樣接下去。
  // 讓進行中的那一次作廢、取消自動重連，停在「已關閉」
  connectGen++;
  reconnect.cancel();
  pipeline?.close();
  pipeline = null;
  ui.value = "closed";
}

function backToForm() {
  connectGen++;
  reconnect.cancel();
  session?.close();
  session = null;
  pipeline?.close();
  pipeline = null;
  resetCursor();
  ro?.disconnect(); ro = null;
  ui.value = "form";
}

// ── 滑鼠（9.1）──
let movePending: { x: number; y: number } | null = null;
let rafId = 0;

/** H.3：受控端虛擬桌面的絕對座標；超出目前螢幕 5 像素以上回 null（不送），force（左鍵放開）照送 */
function pointer(e: MouseEvent, force = false): { x: number; y: number } | null {
  const c = canvasEl.value!;
  return mapPointer(e.offsetX, e.offsetY, c.clientWidth, c.clientHeight, display, force);
}
function canInput(): boolean {
  return ui.value === "connected" && !!session && !keyboardBlocked.value && !form.viewOnly && display.width > 0;
}
/** 送出滑鼠事件；真的送出了就收起遠端游標的疊加層（E.2：使用者自己一動滑鼠就隱藏）。 */
function sendMouse(mask: number, x: number, y: number, modifiers: number[] = []) {
  if (session?.sendMouse(mask, x, y, modifiers)) hideRemoteCursor();
}
function onMouseMove(e: MouseEvent) {
  if (!canInput()) return;
  const p = pointer(e);
  if (!p) return;
  movePending = p;
  if (rafId) return;
  // 移動事件合併到每個畫面最多一則（requestAnimationFrame）；按下與放開不合併
  rafId = requestAnimationFrame(() => {
    rafId = 0;
    if (movePending) sendMouse(mouseMask(MouseKind.Move), movePending.x, movePending.y);
    movePending = null;
  });
}
function onMouseButton(e: MouseEvent, down: boolean) {
  e.preventDefault();
  if (down) canvasEl.value?.focus();
  if (!canInput()) return;
  const b = domButton(e.button);
  if (!b) return;
  const p = pointer(e, !down && b === MouseButton.Left);
  if (!p) return;
  sendMouse(mouseMask(down ? MouseKind.Down : MouseKind.Up, b), p.x, p.y, down ? mouseModifiers(e) : []);
}
function onWheel(e: WheelEvent) {
  e.preventDefault();
  if (!canInput()) return;
  for (const [x, y] of wheel.push(e.deltaX, e.deltaY, e.deltaMode)) sendMouse(mouseMask(MouseKind.Wheel), x, y);
}

// ── 鍵盤（9.2，map 模式）──
function onKey(e: KeyboardEvent, down: boolean) {
  if (ui.value !== "connected") return;
  if (down && onPasteKey(e)) return;     // Ctrl+V：先把本機剪貼簿送過去（附錄 F）
  e.preventDefault();         // F5、Ctrl+W 這類不可以作用在 jt-ipam 頁面上
  if (!canInput()) return;
  const chr = keyCodeFor(e.code, platform.value);
  if (chr === null) return;   // 附錄 A 沒有的鍵不送
  if (down) pressed.add(e.code);
  else pressed.delete(e.code);
  const mods = lockModifiers(e.code, e.getModifierState("CapsLock"), e.getModifierState("NumLock"));
  afterPaste(() => session?.sendKey(down, chr, mods));
}
/** 失去焦點時把還按著的鍵送「放開」，避免受控端卡鍵。 */
function releaseAll(e?: FocusEvent) {
  if (e?.relatedTarget && e.relatedTarget === clipInputEl.value) return;   // Ctrl+V 時焦點暫時移到輸入框（附錄 F）
  keyGen++;
  if (session && pressed.size && ui.value === "connected") {
    for (const code of pressed) {
      const chr = keyCodeFor(code, platform.value);
      if (chr !== null) session.sendKey(false, chr, []);
    }
  }
  pressed.clear();
  wheel.reset();
}

function sendCtrlAltDel() {
  session?.sendCtrlAltDel();
  canvasEl.value?.focus();
}
function sendLockScreen() {
  session?.sendLockScreen();
  canvasEl.value?.focus();
}

// ── 剪貼簿（附錄 F）──
const MAC_CONTROLLER = typeof navigator !== "undefined" && /Macintosh|Mac OS X|iPhone|iPad/.test(navigator.userAgent);
let pasteChain: Promise<void> | null = null;   // Ctrl+V 等本機剪貼簿時，之後的按鍵排在後面依序送
let keyGen = 0;                                 // releaseAll 時加一：排隊中的按鍵就不送了（不然對方會卡鍵）
let clipWrite: Promise<void> = Promise.resolve();

function enqueue(fn: () => void | Promise<void>) {
  const next = (pasteChain ?? Promise.resolve()).then(fn).catch(() => undefined);
  pasteChain = next;
  void next.then(() => { if (pasteChain === next) pasteChain = null; });
}
function afterPaste(fn: () => void) {
  if (!pasteChain) { fn(); return; }
  const gen = keyGen;
  enqueue(() => { if (gen === keyGen) fn(); });
}

/**
 * Ctrl+V（macOS 也接受 Cmd+V）：先送本機剪貼簿，之後才送 V，對方貼上的才會是新的內容（F.2）。
 * 不攔預設動作，讓瀏覽器觸發 paste 事件（不需要權限）；焦點暫時移到看不見的輸入框，因為有些瀏覽器只在可編輯的
 * 元素上觸發。只在這一下移過去：焦點一直停在輸入框的話，本機的輸入法會攔下按鍵（map 模式要的是實體按鍵）。
 */
function onPasteKey(e: KeyboardEvent): boolean {
  if (e.repeat || !clip || !session?.clipboardEnabled || !canInput() || !isPasteKey(e, MAC_CONTROLLER)) return false;
  const text = waitForPaste(document);         // 要在 keydown 裡同步掛上
  clipInputEl.value?.focus({ preventScroll: true });
  const cmdToCtrl = !e.ctrlKey && platform.value !== "Mac OS";   // 受控端是 macOS 時 Cmd+V 本來就是貼上
  const mods = lockModifiers("KeyV", e.getModifierState("CapsLock"), e.getModifierState("NumLock"));
  // 現在就記下按著的鍵：之後的放開會先改到 pressed（實際送出排在這次貼上之後），等到送的時候已經不準了
  const keys = pasteKeys(cmdToCtrl, pressed);
  if (!cmdToCtrl) pressed.add("KeyV");
  const gen = keyGen;
  enqueue(async () => {
    const s = await text;
    if (document.activeElement === clipInputEl.value) canvasEl.value?.focus({ preventScroll: true });
    if (s === null) msg.warning(t("rdweb.clip_read_failed"));
    else if (s && clip?.send(s) === "too_large") msg.warning(t("rdweb.clip_too_large"));
    if (gen !== keyGen || !session || !canInput()) return;
    for (const k of keys) {
      const chr = keyCodeFor(k.code, platform.value);
      if (chr !== null) session.sendKey(k.down, chr, k.code === "KeyV" ? mods : []);
    }
  });
  return true;
}
function onClipInputBlur(e: FocusEvent) {
  if (e.relatedTarget !== canvasEl.value) releaseAll();
}

/** 受控端的剪貼簿 → 本機。依收到的順序寫（寫入是非同步的，後到的不可以被先到的蓋掉）。 */
function onRemoteClipboard(kind: "clipboard" | "multi_clipboards", body: Uint8Array) {
  const r = clip?.receive(kind, body);
  if (!r || r.kind === "none") return;
  if (r.kind === "too_large") {
    msg.warning(t("rdweb.clip_in_too_large"));
    return;
  }
  if (!r.text) return;
  const { text, html } = r;
  clipWrite = clipWrite.then(async () => {
    // 瀏覽器拒絕（分頁沒有焦點、要使用者手勢）：顯示提示，使用者按一下再寫
    clipPending.value = (await writeLocalClipboard(text, html)) ? null : { text, html };
  });
}
async function copyPending() {
  const p = clipPending.value;
  if (!p) return;
  if (await writeLocalClipboard(p.text, p.html)) {
    clipPending.value = null;
    msg.success(t("common.copied_clipboard"));
  } else {
    msg.error(t("rdweb.clip_write_failed"));
  }
  canvasEl.value?.focus({ preventScroll: true });
}

function setClipboard(on: boolean) {
  clipOn.value = on;
  session?.setClipboard(on);
  clip?.reset();                 // 關著的期間對方不會通知剪貼簿的變動
  if (!on) clipPending.value = null;
  canvasEl.value?.focus({ preventScroll: true });
}
function openSendText() {
  if (typing.active) return;
  sendTextDlg.text = "";
  sendTextDlg.show = true;
}
/** 附錄 F.4：唯讀檢視或對方關閉鍵盤權限時，「傳送文字」的兩個動作都不可用（同其他輸入） */
const canSendText = computed(() => ui.value === "connected" && !form.viewOnly && !keyboardBlocked.value);

/**
 * 附錄 F.4：「直接打字輸入」：逐字換成 map 模式的按鍵送出（美式鍵盤配置；可列印的 ASCII、換行、Tab），
 * 需要 Shift 的前後加送 Shift。有其他字元就不打、提示改用剪貼簿；最多 TYPE_TEXT_MAX 個字元；可以取消。
 */
async function submitTypeText() {
  if (typing.active) return;
  if (!canSendText.value) {
    msg.warning(t("rdweb.type_unavailable"));
    return;
  }
  const plan = planTyping(sendTextDlg.text);
  if (!plan.ok) {
    if (plan.reason === "empty") msg.warning(t("rdweb.clip_empty"));
    else if (plan.reason === "too_long") msg.warning(t("rdweb.type_too_long", { max: TYPE_TEXT_MAX }));
    else if (plan.reason === "unsupported") {
      msg.warning(t("rdweb.type_unsupported", { chars: plan.bad.join(" ") }), { duration: 8000 });
    }
    return;
  }
  typingCancel = false;
  typing.active = true;
  typing.done = 0;
  typing.total = plan.chars;
  const out = await typeStrokes(plan.strokes, {
    // 跟一般按鍵同一道檢查（連線中、沒有唯讀、對方沒關閉控制權）；鎖定鍵不帶（對方的 Caps Lock 狀態我們不知道）
    send: (s) => {
      if (!session || !canInput()) return false;
      const chr = keyCodeFor(s.code, platform.value);
      if (chr === null) return false;
      session.sendKey(s.down, chr, []);
      return true;
    },
    cancelled: () => typingCancel,
    progress: (n) => { typing.done = n; },
  });
  typing.active = false;
  if (out.result === "done") {
    msg.success(t("rdweb.type_done", { n: out.chars }));
    sendTextDlg.show = false;
    sendTextDlg.text = "";
  } else if (out.result === "cancelled") {
    msg.info(t("rdweb.type_cancelled", { n: out.chars }));
  } else {
    msg.error(t("rdweb.type_failed", { n: out.chars }));
  }
}
function stopTyping() {
  typingCancel = true;
}

/** 「傳送文字」的「放到對方剪貼簿」：只更新對方的剪貼簿，不送按鍵（瀏覽器不讓網頁讀剪貼簿時的退路）。 */
function submitSendText() {
  const text = sendTextDlg.text;
  const r = clip?.send(text, true);
  if (r === "sent") {
    msg.success(t("rdweb.clip_sent", { n: Array.from(text).length }));
    sendTextDlg.show = false;
    sendTextDlg.text = "";
  } else if (r === "too_large") {
    msg.warning(t("rdweb.clip_too_large"));
  } else if (r === "empty") {
    msg.warning(t("rdweb.clip_empty"));
  } else {
    msg.warning(t("rdweb.clip_unavailable"));
  }
}

function onBeforeUnload() {
  connectGen++;
  reconnect.cancel();            // 離開頁面：停止自動重連、清掉記憶體裡的密碼
  session?.close();
}
onMounted(() => {
  window.addEventListener("beforeunload", onBeforeUnload);
  void loadCreds();
});
onBeforeUnmount(() => {
  window.removeEventListener("beforeunload", onBeforeUnload);
  connectGen++;
  reconnect.dispose();
  if (rafId) cancelAnimationFrame(rafId);
  if (remoteCursorTimer) clearTimeout(remoteCursorTimer);
  if (customTimer) clearTimeout(customTimer);
  stopStats();
  session?.close();
  elevation.reset();
  pipeline?.close();
  ro?.disconnect();
});
</script>

<template>
  <div class="rdw-wrap" :class="{ 'rdw-full': fullHeight, 'rdw-center': fullHeight && ui === 'form' }"
       data-testid="rdweb-screen">
    <!-- 連線表單 -->
    <div v-if="ui === 'form'" class="rdw-form">
      <n-card size="small" :bordered="true" class="console-connect-card">
        <template #header>
          <span style="display:flex;align-items:center;gap:8px">
            <n-icon :component="RustDeskIcon" :size="18" />
            <span>{{ t("rdweb.connect_to", { ip }) }}</span>
          </span>
        </template>
        <!-- 已存密碼（照 VNC 主控台的「已存帳密」） -->
        <div v-if="savedCreds.length" class="rdw-saved-row" data-testid="rdweb-saved-row">
          <span class="rdw-saved-label">{{ t("rdweb.saved_cred") }}</span>
          <n-select v-model:value="selectedCredId" :options="credOptions" clearable size="small"
                    :placeholder="t('rdweb.saved_cred_ph')" style="flex:1" data-testid="rdweb-saved-password" />
          <n-popconfirm v-if="selectedCredId" @positive-click="delSelectedCred">
            <template #trigger>
              <n-button quaternary type="error" size="small" data-testid="rdweb-delete-saved">
                <template #icon><n-icon :component="DeleteIcon" /></template>
              </n-button>
            </template>
            {{ t("rdweb.saved_cred_del_confirm") }}
          </n-popconfirm>
        </div>

        <n-form label-placement="left" :label-width="104" size="small" @submit.prevent="connect">
          <n-form-item v-if="peerIdShown" :label="t('rdweb.peer_id')">
            <span class="rdw-mono" data-testid="rdweb-peer-id">{{ peerIdShown }}</span>
          </n-form-item>
          <n-form-item v-if="!selectedCredId" :label="t('rdweb.password')">
            <n-space vertical :size="2" style="width:100%">
              <n-input v-model:value="form.password" type="password" show-password-on="click"
                       :placeholder="t('rdweb.password_ph')" :input-props="NO_AUTOFILL"
                       data-testid="rdweb-password" @keyup.enter="connect" />
              <div class="rdw-note">{{ remember ? t("rdweb.password_note_remember") : t("rdweb.password_note") }}</div>
            </n-space>
          </n-form-item>
          <n-form-item :label="t('rdweb.view_only')">
            <n-space vertical :size="2">
              <n-switch v-model:value="form.viewOnly" data-testid="rdweb-view-only" />
              <div class="rdw-note">{{ t("rdweb.view_only_hint") }}</div>
            </n-space>
          </n-form-item>

          <n-form-item v-if="!selectedCredId" :label="t('rdweb.remember_password')">
            <n-space vertical :size="4" style="width:100%">
              <n-switch v-model:value="remember" data-testid="rdweb-remember" />
              <n-input v-if="remember" v-model:value="rememberLabel" size="small"
                       :placeholder="t('rdweb.remember_label_ph')" data-testid="rdweb-remember-label" />
            </n-space>
          </n-form-item>

          <n-alert :show-icon="false" type="info" style="margin-bottom:10px" data-testid="rdweb-store-hint">
            {{ selectedCredId ? t("rdweb.use_saved_hint") : (remember ? t("rdweb.store_hint") : t("rdweb.no_store_hint")) }}
          </n-alert>
          <n-space justify="end">
            <n-button type="primary" data-testid="rdweb-connect" @click="connect">
              <template #icon><n-icon :component="RustDeskIcon" /></template>
              {{ t("rdweb.connect") }}
            </n-button>
          </n-space>
        </n-form>
      </n-card>
    </div>

    <!-- 畫面 -->
    <div v-show="ui !== 'form'" class="rdw-screen-area" :class="{ 'rdw-full': fullHeight }">
      <div class="rdw-toolbar">
        <span class="rdw-status" :data-state="ui" data-testid="rdweb-status">
          <n-spin v-if="busy" :size="12" />
          <span v-else class="rdw-dot" />
          <span>{{ ui === 'connecting' ? stageText : t(`rdweb.state_${ui}`) }}</span>
          <span class="rdw-ip">{{ ip }}</span>
          <n-tag v-if="hostname" size="small" :bordered="false" round>{{ hostname }}</n-tag>
          <span class="conn-proto conn-proto--rd">RustDesk</span>
          <n-tag v-if="peerIdShown" size="small" :bordered="false" round :title="serverName || undefined">ID {{ peerIdShown }}</n-tag>
          <n-tag v-if="deviceName" size="small" type="info" :bordered="false" round>{{ deviceName }}</n-tag>
          <n-tag v-if="peer?.platform" size="small" :bordered="false" round>{{ peer.platform }}</n-tag>
          <!-- 附錄 K.2：Windows 免安裝受控端（is_installed = false） -->
          <n-tooltip v-if="elevUi.tag" :delay="200">
            <template #trigger>
              <n-tag size="small" :type="elevUi.tag === 'elevated' ? 'success' : 'warning'" :bordered="false" round
                     data-testid="rdweb-elev-tag">
                {{ elevUi.tag === "elevated" ? t("rdweb.elev_tag_elevated") : t("rdweb.elev_tag_portable") }}
              </n-tag>
            </template>
            <span class="rdw-tip">
              {{ elevUi.tag === "elevated" ? t("rdweb.elev_tag_elevated_hint") : t("rdweb.elev_tag_hint") }}
            </span>
          </n-tooltip>
          <n-tag v-if="form.viewOnly && ui === 'connected'" size="small" type="warning" :bordered="false" round>
            {{ t("rdweb.view_only") }}
          </n-tag>
          <!-- 連線時間：自動重連中（附錄 G）照樣計時，重連成功不歸零 -->
          <ConnElapsed :active="ui === 'connected'" :paused="rcView.state !== 'idle'" />
        </span>
        <n-space :size="8" align="center">
          <template v-if="ui === 'connected'">
            <n-tooltip :delay="200">
              <template #trigger>
                <n-button size="tiny" :disabled="form.viewOnly || keyboardBlocked" data-testid="rdweb-cad"
                          @click="sendCtrlAltDel">
                  <template #icon><n-icon :component="KeyIcon" /></template>Ctrl+Alt+Del
                </n-button>
              </template>
              {{ t("rdweb.cad_hint") }}
            </n-tooltip>
            <n-button size="tiny" :disabled="form.viewOnly || keyboardBlocked" @click="sendLockScreen">
              <template #icon><n-icon :component="LockIcon" /></template>{{ t("rdweb.lock_screen") }}
            </n-button>
            <!-- 附錄 K.2：Windows 免安裝受控端、輔助服務沒在跑、有控制權、不是唯讀檢視時才有 -->
            <n-dropdown v-if="elevUi.menu" trigger="click" size="small" :options="elevMenu" :disabled="elevUi.busy"
                        @select="onElevSelect">
              <n-button size="tiny" :disabled="elevUi.busy" data-testid="rdweb-elev-menu">
                <template #icon><n-icon :component="AdminIcon" /></template>
                {{ t("rdweb.elev_menu") }}<n-icon :component="ChevronDownIcon" style="margin-left:2px" />
              </n-button>
            </n-dropdown>
            <!-- 剪貼簿（附錄 F） -->
            <n-tooltip :delay="200">
              <template #trigger>
                <span class="rdw-clip">
                  <n-switch size="small" :value="clipOn && !form.viewOnly" :disabled="form.viewOnly"
                            data-testid="rdweb-clipboard" @update:value="setClipboard" />
                  <span>{{ t("rdweb.clipboard") }}</span>
                </span>
              </template>
              {{ form.viewOnly ? t("rdweb.clipboard_view_only") : t("rdweb.clipboard_hint") }}
            </n-tooltip>
            <n-button size="tiny" :disabled="!canSendText" data-testid="rdweb-send-text"
                      @click="openSendText">
              <template #icon><n-icon :component="PasteIcon" /></template>{{ t("rdweb.send_text") }}
            </n-button>
            <!-- 附錄 H：多螢幕與解析度（只有一個螢幕、而且沒有可選的解析度時不顯示） -->
            <n-dropdown v-if="showDisplayMenu" trigger="click" size="small" :options="displayMenu"
                        @select="onDisplaySelect">
              <n-button size="tiny" data-testid="rdweb-displays">
                <template #icon><n-icon :component="ScreensIcon" /></template>
                {{ t("rdweb.disp_menu") }}<n-icon :component="ChevronDownIcon" style="margin-left:2px" />
              </n-button>
            </n-dropdown>
            <!-- 附錄 I：畫質、更新率上限、編碼偏好 -->
            <n-dropdown trigger="click" size="small" :options="qualityMenu" :show="qualityMenuShow"
                        @update:show="onQualityMenuShow" @select="onQualitySelect">
              <n-button size="tiny" data-testid="rdweb-quality">
                <template #icon><n-icon :component="QualityIcon" /></template>
                {{ t("rdweb.q_menu") }}<n-icon :component="ChevronDownIcon" style="margin-left:2px" />
              </n-button>
            </n-dropdown>
            <n-button-group size="tiny">
              <n-button :type="scaleMode === 'fit' ? 'primary' : 'default'" @click="setScale('fit')">
                <template #icon><n-icon :component="ExpandIcon" /></template>{{ t("vnc.scale_fit") }}
              </n-button>
              <n-button :type="scaleMode === 'native' ? 'primary' : 'default'" @click="setScale('native')">
                <template #icon><n-icon :component="ReduceIcon" /></template>{{ t("vnc.scale_native") }}
              </n-button>
            </n-button-group>
          </template>
          <!-- 附錄 G.3：自動重連中（倒數或進行中）：「立即重連」與「取消」取代「中斷」 -->
          <template v-if="ui === 'reconnecting'">
            <n-button size="tiny" :disabled="rcView.state !== 'waiting'" data-testid="rdweb-reconnect-now"
                      @click="reconnectNow">
              <template #icon><n-icon :component="RefreshIcon" /></template>{{ t("rdweb.reconnect_now") }}
            </n-button>
            <n-button size="tiny" type="error" ghost data-testid="rdweb-reconnect-cancel" @click="cancelReconnect">
              <template #icon><n-icon :component="CancelIcon" /></template>{{ t("common.cancel") }}
            </n-button>
          </template>
          <n-button v-else-if="ui !== 'closed' && ui !== 'error'" size="tiny" type="error" ghost
                    data-testid="rdweb-disconnect" @click="disconnect">
            <template #icon><n-icon :component="CancelIcon" /></template>{{ t("vnc.disconnect") }}
          </n-button>
          <n-button v-if="ui === 'closed' || ui === 'error'" size="tiny" data-testid="rdweb-reconnect"
                    @click="backToForm">
            <template #icon><n-icon :component="RefreshIcon" /></template>{{ t("vnc.reconnect") }}
          </n-button>
        </n-space>
      </div>
      <!-- 附錄 I.2：延遲、位元率、每秒解出的張數、編碼。獨立一行：跟按鈕擠在同一行時，寬度不夠會把整排按鈕
           推到第二行（使用者 2026-10-05） -->
      <div v-if="ui === 'connected' && showStats && statsText" class="rdw-stats-row">
        <n-tooltip :delay="200">
          <template #trigger>
            <span class="rdw-stats" data-testid="rdweb-stats">{{ statsText }}</span>
          </template>
          {{ t("rdweb.stats_hint") }}
        </n-tooltip>
      </div>
      <n-alert v-if="ui === 'error'" type="error" :show-icon="true" style="margin:8px 0" data-testid="rdweb-error">
        {{ errorMsg }}
      </n-alert>
      <n-alert v-if="keyboardBlocked && ui === 'connected'" type="warning" style="margin:8px 0">
        {{ t("rdweb.keyboard_blocked") }}
      </n-alert>
      <!-- 附錄 K.2：請求提權的進度與結果 -->
      <n-alert v-if="elevNotice && ui === 'connected'" :type="elevNotice.type" :closable="!elevNotice.busy"
               style="margin:8px 0" data-testid="rdweb-elev-notice" @close="elevation.dismiss()">
        <span class="rdw-alert-row"><n-spin v-if="elevNotice.busy" :size="12" />{{ elevNotice.text }}</span>
      </n-alert>
      <!-- 附錄 K.2：前景是系統管理員視窗、UAC 確認畫面（不擋畫面，照常可看） -->
      <n-alert v-if="elevUi.foregroundAlert" type="warning" style="margin:8px 0" data-testid="rdweb-elev-foreground">
        <span class="rdw-alert-row">
          {{ t("rdweb.elev_foreground") }}
          <n-dropdown trigger="click" size="small" :options="elevMenu" :disabled="elevUi.busy" @select="onElevSelect">
            <n-button size="tiny" :disabled="elevUi.busy" data-testid="rdweb-elev-foreground-request">
              <template #icon><n-icon :component="AdminIcon" /></template>{{ t("rdweb.elev_menu") }}
            </n-button>
          </n-dropdown>
        </span>
      </n-alert>
      <n-alert v-if="elevUi.uacAlert" type="warning" style="margin:8px 0" data-testid="rdweb-elev-uac">
        <span class="rdw-alert-row">
          {{ t("rdweb.elev_uac") }}
          <n-dropdown trigger="click" size="small" :options="elevMenu" :disabled="elevUi.busy" @select="onElevSelect">
            <n-button size="tiny" :disabled="elevUi.busy" data-testid="rdweb-elev-uac-request">
              <template #icon><n-icon :component="AdminIcon" /></template>{{ t("rdweb.elev_menu") }}
            </n-button>
          </n-dropdown>
        </span>
      </n-alert>
      <n-alert v-for="(mb, i) in messageBoxes" :key="i" type="info" closable style="margin:8px 0"
               :title="mb.title || undefined" @close="messageBoxes.splice(i, 1)">
        {{ mb.text }}<span v-if="mb.link" class="rdw-link">（{{ mb.link }}）</span>
      </n-alert>
      <n-alert v-if="clipPending && ui === 'connected'" type="info" closable style="margin:8px 0"
               data-testid="rdweb-clip-pending" @close="clipPending = null">
        <span class="rdw-clip-pending">
          {{ t("rdweb.clip_copy_prompt") }}
          <n-button size="tiny" type="primary" data-testid="rdweb-clip-copy" @click="copyPending"><template #icon><n-icon><CopyIcon /></n-icon></template>
            {{ t("rdweb.clip_copy_button") }}
          </n-button>
        </span>
      </n-alert>
      <div class="rdw-disp" :class="{ 'rdw-full': fullHeight }">
        <div ref="canvasBoxEl" class="rdw-canvas-box"
             :class="{ 'rdw-full': fullHeight, 'rdw-fit': scaleMode === 'fit', 'rdw-native': scaleMode !== 'fit',
                       'term-dim': ui === 'closed' || ui === 'reconnecting' }">
          <canvas v-show="hasVideo" ref="canvasEl" class="rdw-canvas" tabindex="0" data-testid="rdweb-canvas"
                  @mousemove="onMouseMove" @mousedown="onMouseButton($event, true)"
                  @mouseup="onMouseButton($event, false)" @wheel="onWheel" @contextmenu.prevent
                  @keydown="onKey($event, true)" @keyup="onKey($event, false)" @blur="releaseAll" />
          <!-- E.2：對方自己移動的游標（不攔截滑鼠事件） -->
          <div v-if="remoteCursor && hasVideo && ui === 'connected'" class="rdw-remote-cursor" aria-hidden="true"
               data-testid="rdweb-remote-cursor"
               :style="{ left: remoteCursor.left + 'px', top: remoteCursor.top + 'px' }">
            <img v-if="remoteCursor.img" :src="remoteCursor.img.url" alt="" draggable="false"
                 :width="remoteCursor.img.width" :height="remoteCursor.img.height"
                 :style="{ left: -remoteCursor.img.hotx + 'px', top: -remoteCursor.img.hoty + 'px' }" />
            <svg v-else width="12" height="19" viewBox="0 0 12 19">
              <path d="M0.5 0.5 L0.5 15 L4 11.5 L6.7 18 L9 17 L6.4 10.7 L11 10.7 Z" fill="#fff" stroke="#000"
                    stroke-width="1" stroke-linejoin="round" />
            </svg>
          </div>
          <div v-if="!hasVideo && ui !== 'error' && ui !== 'closed' && ui !== 'reconnecting'" class="rdw-placeholder">
            <!-- 等待：會合／中繼／握手、輸入密碼、兩步驟驗證、等待對方同意 -->
            <n-card v-if="ui === 'need_password'" size="small" class="rdw-prompt" data-testid="rdweb-password-prompt">
              <!-- 附錄 D.4：已存的密碼用不了（已失效／後端回失敗）時先說原因 -->
              <n-alert v-if="retry.notice" type="warning" :show-icon="false" class="rdw-inline-alert"
                       style="margin-bottom:8px" data-testid="rdweb-saved-notice">{{ retry.notice }}</n-alert>
              <div class="rdw-prompt-title">{{ retry.wrong ? t("rdweb.wrong_password") : t("rdweb.enter_password") }}</div>
              <n-input v-model:value="retry.password" type="password" show-password-on="click" size="small"
                       :placeholder="t('rdweb.password_ph')" :input-props="NO_AUTOFILL"
                       data-testid="rdweb-retry-password" @keyup.enter="submitPassword" />
              <div class="rdw-note" style="margin-top:6px">{{ t("rdweb.rate_limit_hint") }}</div>
              <n-space justify="space-between" align="center" style="margin-top:10px">
                <n-space :size="10" align="center">
                  <n-checkbox v-model:checked="remember" size="small" data-testid="rdweb-retry-remember">
                    {{ t("rdweb.remember_password") }}
                  </n-checkbox>
                  <n-popconfirm v-if="retry.savedRejected && canDeleteUsed" @positive-click="deleteCred(usedCredId)">
                    <template #trigger>
                      <n-button text size="tiny" type="error" data-testid="rdweb-retry-delete-saved">
                        <template #icon><n-icon :component="DeleteIcon" /></template>
                        {{ t("rdweb.delete_saved_password") }}
                      </n-button>
                    </template>
                    {{ t("rdweb.saved_cred_del_confirm") }}
                  </n-popconfirm>
                </n-space>
                <n-button size="small" type="primary" data-testid="rdweb-retry-submit" @click="submitPassword"><template #icon><n-icon><LoginIcon /></n-icon></template>
                  {{ t("rdweb.login") }}
                </n-button>
              </n-space>
            </n-card>
            <n-card v-else-if="ui === 'need_2fa'" size="small" class="rdw-prompt" data-testid="rdweb-2fa-prompt">
              <div class="rdw-prompt-title">{{ retry.wrong2fa ? t("rdweb.wrong_2fa") : t("rdweb.enter_2fa") }}</div>
              <n-input v-model:value="retry.code" size="small" :maxlength="12"
                       :input-props="{ autocomplete: 'one-time-code', inputmode: 'numeric' }"
                       @keyup.enter="submit2fa" />
              <n-space justify="end" style="margin-top:10px">
                <n-button size="small" type="primary" @click="submit2fa"><template #icon><n-icon><LoginIcon /></n-icon></template>{{ t("rdweb.login") }}</n-button>
              </n-space>
            </n-card>
            <!-- G.7：Linux 受控端沒有桌面：作業系統帳號密碼（需要時加 RustDesk 密碼），同一條連線再登入一次 -->
            <n-card v-else-if="ui === 'need_os_login'" size="small" class="rdw-prompt" data-testid="rdweb-os-login">
              <n-alert v-if="osForm.notice" type="warning" :show-icon="false" class="rdw-inline-alert"
                       style="margin-bottom:8px">{{ osForm.notice }}</n-alert>
              <div class="rdw-prompt-title">{{ t("rdweb.os_login_title") }}</div>
              <n-form label-placement="top" size="small" :show-feedback="false" @submit.prevent="submitOsLogin">
                <n-space vertical :size="8">
                  <n-form-item :label="t('rdweb.os_user')">
                    <n-input v-model:value="osForm.username" size="small" placeholder="" :input-props="NO_AUTOFILL_USER"
                             data-testid="rdweb-os-user" @keyup.enter="submitOsLogin" />
                  </n-form-item>
                  <n-form-item :label="t('rdweb.os_password')">
                    <n-input v-model:value="osForm.password" type="password" show-password-on="click" size="small"
                             placeholder="" :input-props="NO_AUTOFILL" data-testid="rdweb-os-password"
                             @keyup.enter="submitOsLogin" />
                  </n-form-item>
                  <n-form-item v-if="osForm.needRd" :label="t('rdweb.os_rd_password')">
                    <n-input v-model:value="osForm.rdPassword" type="password" show-password-on="click" size="small"
                             :placeholder="t('rdweb.password_ph')" :input-props="NO_AUTOFILL"
                             data-testid="rdweb-os-rdpassword" @keyup.enter="submitOsLogin" />
                  </n-form-item>
                </n-space>
              </n-form>
              <div class="rdw-note" style="margin-top:6px">{{ t("rdweb.os_login_note") }}</div>
              <n-space justify="end" style="margin-top:10px">
                <n-button size="small" type="primary" :disabled="!osReady" data-testid="rdweb-os-submit"
                          @click="submitOsLogin"><template #icon><n-icon><LoginIcon /></n-icon></template>{{ t("rdweb.login") }}</n-button>
              </n-space>
            </n-card>
            <!-- G.6：Windows 的工作階段選擇（受控端要等這個才送畫面） -->
            <n-card v-else-if="ui === 'connected' && winPick" size="small" class="rdw-prompt"
                    data-testid="rdweb-winsession">
              <div class="rdw-prompt-title">{{ t("rdweb.win_session_title") }}</div>
              <n-radio-group v-model:value="winPick.selected" size="small">
                <n-space vertical :size="6">
                  <n-radio v-for="s in winPick.sessions" :key="s.sid" :value="s.sid"
                           :data-testid="`rdweb-winsession-${s.sid}`">
                    <span class="rdw-winsession">
                      <span>{{ s.name || `#${s.sid}` }}</span>
                      <n-tag v-if="s.sid === winPick.currentSid" size="tiny" type="info" :bordered="false" round>
                        {{ t("rdweb.win_session_current") }}
                      </n-tag>
                    </span>
                  </n-radio>
                </n-space>
              </n-radio-group>
              <div class="rdw-note" style="margin-top:8px">{{ t("rdweb.win_session_note") }}</div>
              <n-space justify="end" style="margin-top:10px">
                <n-button size="small" type="primary" data-testid="rdweb-winsession-submit" @click="submitWinSession"><template #icon><n-icon><CheckIcon /></n-icon></template>
                  {{ t("rdweb.win_session_submit") }}
                </n-button>
              </n-space>
            </n-card>
            <div v-else-if="ui === 'waiting_approval'" class="rdw-wait" data-testid="rdweb-waiting">
              <n-spin :size="22" />
              <div>{{ t("rdweb.waiting_approval") }}</div>
              <n-popconfirm @positive-click="disconnect">
                <template #trigger><n-button size="small" ghost><template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}</n-button></template>
                {{ t("rdweb.cancel_confirm") }}
              </n-popconfirm>
            </div>
            <div v-else class="rdw-wait">
              <n-spin :size="22" />
              <div>{{ ui === 'connected' ? t("rdweb.waiting_video") : stageText }}</div>
            </div>
          </div>
        </div>
        <!-- Ctrl+V 時暫時接收貼上的輸入框（附錄 F）；平常焦點在畫面上 -->
        <textarea ref="clipInputEl" class="rdw-clip-input" tabindex="-1" aria-hidden="true" autocomplete="off"
                  spellcheck="false" @keydown="onKey($event, true)" @keyup="onKey($event, false)"
                  @blur="onClipInputBlur" />
        <!-- 附錄 G.3：自動重連中不顯示「連線已中斷」的終止狀態，改成「連線中斷，N 秒後自動重新連線（第 k 次）」 -->
        <ConsoleDisconnectedOverlay :show="ui === 'closed' || ui === 'error' || ui === 'reconnecting'"
                                    :error="ui === 'error'"
                                    :message="ui === 'reconnecting' ? t('rdweb.reconnect_title') : undefined"
                                    :hint="reconnectHint" />
      </div>
    </div>
    <!-- 「傳送文字」（附錄 F.2、F.4）：放到對方剪貼簿，或直接打字輸入；關閉視窗也會停止輸入 -->
    <n-modal v-model:show="sendTextDlg.show" preset="card" :title="t('rdweb.send_text_title')"
             style="width:560px;max-width:92vw" data-testid="rdweb-send-text-modal"
             @update:show="(v: boolean) => { if (!v) stopTyping(); }" @after-leave="canvasEl?.focus()">
      <n-input v-model:value="sendTextDlg.text" type="textarea" :autosize="{ minRows: 6, maxRows: 14 }"
               :disabled="typing.active" :placeholder="t('rdweb.send_text_ph')" data-testid="rdweb-send-text-input" />
      <div class="rdw-note" style="margin-top:6px">{{ t("rdweb.send_text_note") }}</div>
      <div class="rdw-note" style="margin-top:4px" data-testid="rdweb-type-note">
        {{ t("rdweb.send_text_type_note", { max: TYPE_TEXT_MAX }) }}
      </div>
      <div v-if="typing.active" class="rdw-type-progress" data-testid="rdweb-type-progress">
        <n-spin :size="12" />{{ t("rdweb.type_progress", { done: typing.done, total: typing.total }) }}
      </div>
      <template #footer>
        <n-space justify="end">
          <n-button v-if="typing.active" size="small" type="warning" data-testid="rdweb-type-stop" @click="stopTyping">
            <template #icon><n-icon :component="CancelIcon" /></template>{{ t("rdweb.type_stop") }}
          </n-button>
          <n-button v-else size="small" @click="sendTextDlg.show = false"><template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}</n-button>
          <n-button size="small" :disabled="!canSendText || typing.active" data-testid="rdweb-type-submit"
                    @click="submitTypeText">
            <template #icon><n-icon :component="KeyIcon" /></template>{{ t("rdweb.send_text_type") }}
          </n-button>
          <n-button size="small" type="primary" :disabled="!clipOn || !canSendText || typing.active"
                    data-testid="rdweb-send-text-submit" @click="submitSendText">
            <template #icon><n-icon :component="SendIcon" /></template>{{ t("rdweb.send_text_paste") }}
          </n-button>
        </n-space>
      </template>
    </n-modal>
    <!-- 附錄 K.2：用受控端的系統管理員帳號提權。密碼不記住、不進瀏覽器儲存、不送給 jt-ipam 後端；送出或關閉就清空 -->
    <n-modal :show="elevDlg.show" preset="card" :title="t('rdweb.elev_logon_title')" style="width:440px;max-width:92vw"
             data-testid="rdweb-elev-logon" @update:show="(v: boolean) => { if (!v) closeElevLogon(); }">
      <n-form label-placement="top" size="small" :show-feedback="false" @submit.prevent="submitElevLogon">
        <n-space vertical :size="8">
          <n-form-item :label="t('rdweb.elev_logon_user')">
            <n-input v-model:value="elevDlg.username" size="small" :input-props="NO_AUTOFILL_USER"
                     :placeholder="t('rdweb.elev_logon_user_ph', { ex: ELEV_USER_EXAMPLE })"
                     data-testid="rdweb-elev-user" @keyup.enter="submitElevLogon" />
          </n-form-item>
          <n-form-item :label="t('rdweb.elev_logon_password')">
            <n-input v-model:value="elevDlg.password" type="password" show-password-on="click" size="small"
                     placeholder="" :input-props="NO_AUTOFILL" data-testid="rdweb-elev-password"
                     @keyup.enter="submitElevLogon" />
          </n-form-item>
        </n-space>
      </n-form>
      <div class="rdw-note" style="margin-top:8px">{{ t("rdweb.elev_logon_note") }}</div>
      <div class="rdw-note" style="margin-top:4px">{{ t("rdweb.elev_audit_note") }}</div>
      <template #footer>
        <n-space justify="end">
          <n-button size="small" data-testid="rdweb-elev-logon-cancel" @click="closeElevLogon"><template #icon><n-icon><CancelIcon /></n-icon></template>
            {{ t("common.cancel") }}
          </n-button>
          <n-button size="small" type="primary" :disabled="!elevDlg.username.trim()"
                    data-testid="rdweb-elev-logon-submit" @click="submitElevLogon">
            <template #icon><n-icon :component="AdminIcon" /></template>{{ t("rdweb.elev_logon_submit") }}
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.rdw-wrap { width: 100%; }
.rdw-wrap.rdw-full { height: 100%; display: flex; flex-direction: column; }
.rdw-wrap.rdw-center { justify-content: center; align-items: center; }
.rdw-wrap.rdw-center .rdw-form { width: 520px; max-width: 92vw; }
.rdw-form { max-width: 520px; }
.rdw-note { font-size: 11px; opacity: .7; line-height: 1.5; }
.rdw-mono { font-family: var(--jt-mono, ui-monospace, monospace); }
/* 已存密碼列：同 VNC 主控台的 .vnc-saved-row（標籤寬度跟著這個表單的 label-width） */
.rdw-saved-row { display: flex; align-items: center; margin-bottom: 18px; }
.rdw-saved-label { width: 104px; flex: none; box-sizing: border-box; text-align: right;
  padding-right: 12px; font-size: 14px; }
.rdw-saved-row :deep(.n-button) { margin-left: 6px; }
.rdw-inline-alert :deep(.n-alert-body) { padding: 6px 10px; font-size: 12px; }
.rdw-screen-area { display: flex; flex-direction: column; }
.rdw-screen-area.rdw-full { flex: 1; min-height: 0; }
.rdw-disp { position: relative; }
.rdw-disp.rdw-full { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.rdw-toolbar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; padding: 4px 2px; gap: 8px; }
.rdw-status { font-size: 13px; display: inline-flex; align-items: center; gap: 7px; padding: 3px 11px;
  border-radius: 999px; font-weight: 500; background: rgba(128, 128, 128, .12); color: #888;
  flex-wrap: wrap; row-gap: 4px; max-width: 100%; min-width: 0; }
.rdw-status > * { flex: none; max-width: 100%; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
@media (max-width: 640px) { .rdw-status { border-radius: 14px; } }
.rdw-dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; flex: none; }
.rdw-ip { opacity: .7; font-variant-numeric: tabular-nums; }
.rdw-status[data-state="connected"] { color: #18a058; background: rgba(24, 160, 88, .14); }
.rdw-status[data-state="connecting"], .rdw-status[data-state="need_password"], .rdw-status[data-state="need_2fa"],
.rdw-status[data-state="need_os_login"], .rdw-status[data-state="waiting_approval"],
.rdw-status[data-state="reconnecting"] {
  color: #d99812; background: rgba(217, 152, 18, .14); }
.rdw-status[data-state="error"] { color: #d03050; background: rgba(208, 48, 80, .14); }
.rdw-status[data-state="closed"] { color: #888; background: rgba(128, 128, 128, .14); }
/* 協定標籤（主機名稱右邊） */
.conn-proto { font-weight: 700; font-size: 11px; letter-spacing: .4px; line-height: 1; padding: 2px 7px; border-radius: 999px; }
.conn-proto--rd { color: #2f7cd3; background: rgba(47, 124, 211, .16); }
.rdw-canvas-box { background: #000; border-radius: 10px; border: 1px solid #2b2b30; min-height: 320px;
  box-shadow: 0 10px 30px rgba(0,0,0,.30), 0 3px 10px rgba(0,0,0,.20); overflow: auto; position: relative; }
.rdw-canvas-box.rdw-full { flex: 1; min-height: 0; }
.rdw-canvas-box.rdw-fit { overflow: hidden; display: flex; align-items: center; justify-content: center; }
.rdw-canvas-box.rdw-native { overflow: auto; }
.rdw-canvas { display: block; outline: none; background: #000; cursor: default; }
/* 遠端游標疊加層：定位點＝熱點；不攔截滑鼠事件 */
.rdw-remote-cursor { position: absolute; width: 0; height: 0; pointer-events: none; z-index: 2; }
.rdw-remote-cursor > img, .rdw-remote-cursor > svg { position: absolute; left: 0; top: 0; max-width: none;
  pointer-events: none; user-select: none; }
.rdw-placeholder { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; padding: 16px; }
.rdw-wait { display: flex; flex-direction: column; align-items: center; gap: 12px; color: #d8dee9; font-size: 14px; text-align: center; }
.rdw-prompt { width: 360px; max-width: 100%; }
.rdw-prompt-title { font-weight: 600; margin-bottom: 8px; }
.rdw-winsession { display: inline-flex; align-items: center; gap: 6px; }
.rdw-link { word-break: break-all; opacity: .8; }
:deep(.n-card > .n-card-header) { display: flex; align-items: center; padding-top: 12px; padding-bottom: 12px; }
.term-dim { filter: grayscale(1) brightness(.45); pointer-events: none; transition: filter .25s; }
/* 剪貼簿（附錄 F） */
/* 跟旁邊的 tiny 按鈕同高、垂直置中：行內元素會落在文字基線上，看起來偏上（使用者 2026-10-05） */
.rdw-clip { display: flex; align-items: center; gap: 6px; height: 22px; font-size: 12px; line-height: 1; }
.rdw-clip-pending { display: inline-flex; align-items: center; gap: 8px; flex-wrap: wrap; }
/* 請求提權（附錄 K）：提示的文字與按鈕同一行，放不下就換行；標籤的說明不要太寬 */
.rdw-alert-row { display: inline-flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.rdw-tip { display: inline-block; max-width: 320px; line-height: 1.5; }
.rdw-type-progress { display: flex; align-items: center; gap: 8px; margin-top: 8px; font-size: 12.5px; }
/* 狀態列的小字（附錄 I.2）：工具列下方獨立一行，左邊對齊狀態膠囊裡的文字 */
.rdw-stats-row { display: flex; padding: 0 2px 4px 13px; }
.rdw-stats { display: inline-flex; align-items: center; font-size: 11px; opacity: .7; line-height: 16px;
  font-variant-numeric: tabular-nums; white-space: nowrap; }
.rdw-clip-input { position: absolute; left: 0; top: 0; width: 1px; height: 1px; padding: 0; border: 0; opacity: 0;
  resize: none; overflow: hidden; pointer-events: none; }
</style>
