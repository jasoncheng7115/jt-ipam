<script setup lang="ts">
/**
 * 相容 RustDesk 的網頁連線：檔案傳輸（docs/SPEC_RUSTDESK_WEBCLIENT_zh-TW.md 附錄 J）。
 *
 * 另外一條連線：換票證（kind = file）→ 開 WebSocket → 後端會合、中繼，之後只轉送密文；安全握手、登入（union
 * file_transfer）與所有檔案訊息都在這個分頁裡做（src/rdweb/fileSession.ts）。手動輸入的密碼只在瀏覽器算成雜湊。
 *
 * 版面照 SFTP 檔案瀏覽器（SftpBrowser.vue）：連線表單 → 狀態列＋路徑列＋清單；上傳（選檔／拖放）、下載、新增資料夾、
 * 改名、刪除（要確認）、傳輸佇列（進度、取消）。Windows 受控端最上層列出磁碟機。
 * - 同名：上傳時受控端說有同名就問「覆蓋／略過」（可套用到其餘的，J.4）；下載一律是另存新檔，不會覆蓋
 * - 下載：小檔在記憶體組好再存，大檔用瀏覽器的串流存檔（支援時）或分段寫入（fileSave.ts）；只下載單一檔案
 * - 一次一個傳輸工作，其他的排隊；不做續傳
 * - 稽核是瀏覽器自報的（後端看不到檔案），畫面上要講清楚
 * - 受控端給的檔名與路徑只當文字顯示（不用 v-html），下載的檔名先過濾（safeDownloadName）
 */
import { computed, h, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import ConnElapsed from "@/components/ConnElapsed.vue";
import { useI18n } from "vue-i18n";
import {
  NAlert, NButton, NCard, NCheckbox, NDataTable, NForm, NFormItem, NIcon, NInput, NModal, NPopconfirm, NProgress,
  NSelect, NSpace, NSpin, NSwitch, NTag, NTooltip, useMessage,
} from "naive-ui";
import type { DataTableColumns } from "naive-ui";
import {
  buildRustDeskWsUrl, deleteSavedRustDeskPassword, listRustDeskCredentials, requestRustDeskTicket,
  saveRustDeskPassword, type RustDeskCredential,
} from "@/api/rustdeskWeb";
import { apiErrMsg } from "@/api/client";
import { srvText } from "@/utils/wsError";
import { fmtDateTime } from "@/utils/datetime";
import { sortEntries } from "@/utils/sftpSort";
import { useFileListPagination } from "@/composables/useFileListPagination";
import {
  checkUploadLimits, FileOpError, RdFileSession, type ConflictAnswer, type ConflictInfo, type FileJobView,
  type FileLimits, type UploadSource,
} from "@/rdweb/fileSession";
import { pickDownloadSink } from "@/rdweb/fileSave";
import { FileType, isDirType, joinPath, parentPath, type FileEntry } from "@/rdweb/files";
import type { CloseInfo, PasswordReason, Phase } from "@/rdweb/session";
import type { MessageBox } from "@/rdweb/messages";
import {
  CancelIcon, ChevronRightIcon, DeleteIcon, DownloadIcon, EditIcon, FilesIcon, FilterIcon, NewFolderIcon, RefreshIcon,
  UpLevelIcon, UploadIcon, CheckIcon, LoginIcon, MoveIcon, SaveIcon,
} from "@/icons";

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

type UiState = "form" | "connecting" | "need_password" | "need_2fa" | "waiting_approval" | "connected" | "closed"
  | "error";
const ui = ref<UiState>("form");
const stage = ref<Phase>("connecting");
const errorMsg = ref("");
const peerIdShown = ref(props.peerId || "");
const serverName = ref("");
const platform = ref("");
const messageBoxes = ref<MessageBox[]>([]);
const form = reactive({ password: "" });
const retry = reactive({ password: "", code: "", wrong: false, wrong2fa: false, notice: "" });
const showHidden = ref(false);
/** 2 GB／單檔、10 GB／單次是伺服器的預設值；換到票證之後以伺服器給的為準 */
const MB = 1024 * 1024;
const limits = ref<FileLimits>({ maxFileBytes: 2048 * MB, maxTotalBytes: 10240 * MB });

// ── 記住密碼（附錄 D，與遠端桌面共用同一筆）──
const savedCreds = ref<RustDeskCredential[]>([]);
const selectedCredId = ref<string | null>(null);
const remember = ref(false);
const rememberLabel = ref("");
const credOptions = computed(() => [
  { label: t("rdweb.cred_manual"), value: null as unknown as string },
  ...savedCreds.value.map((c) => ({ label: c.label, value: c.id }))]);
/** 勾了「記住密碼」時等登入成功才存的密碼（只在記憶體，連線結束就清掉） */
let pendingRemember: string | null = null;

async function loadCreds() {
  try {
    savedCreds.value = await listRustDeskCredentials(props.addressId);
    if (selectedCredId.value && !savedCreds.value.some((c) => c.id === selectedCredId.value)) selectedCredId.value = null;
    if (!selectedCredId.value && savedCreds.value.length) selectedCredId.value = savedCreds.value[0].id;
  } catch { /* 沒有就手動輸入 */ }
}

async function delSelectedCred() {
  const id = selectedCredId.value;
  if (!id) return;
  try {
    await deleteSavedRustDeskPassword(id);
    selectedCredId.value = null;
    await loadCreds();
    msg.success(t("rdweb.saved_password_deleted"));
  } catch (e) {
    msg.error(apiErrMsg(e) || t("errors.server"));
  }
}

// ── 檔案清單 ──
const cwd = ref("");
const pathInput = ref("");
const entries = ref<FileEntry[]>([]);
const windows = ref(false);
const drives = ref(false);
const busy = ref(false);
const filterText = ref("");
const jobs = ref<FileJobView[]>([]);
const everListed = ref(false);

let session: RdFileSession | null = null;
let connectGen = 0;

const connecting = computed(() => ui.value === "connecting");
const onFileList = computed(() => everListed.value && (ui.value === "connected" || ui.value === "closed"
                                                      || ui.value === "error"));
const canUp = computed(() => ui.value === "connected" && parentPath(cwd.value, windows.value) !== null);
const activeJobs = computed(() => jobs.value.filter((j) => j.status === "queued" || j.status === "running").length);
const stageText = computed(() => {
  const k = `rdweb.stage_${stage.value}`;
  return te(k) ? t(k) : t("rdweb.state_connecting");
});
const statusText = computed(() => (ui.value === "connecting" ? stageText.value : t(`rdweb.state_${ui.value}`)));

interface Row {
  key: string;
  name: string;
  path: string;
  is_dir: boolean;
  is_link: boolean;
  drive: boolean;
  size: number | null;
  mtime: number | null;
}

const rows = computed<Row[]>(() => {
  const q = filterText.value.trim().toLowerCase();
  const list = entries.value
    .filter((e) => !q || e.name.toLowerCase().includes(q))
    .map((e): Row => ({
      key: `${e.type}:${e.name}`, name: e.name, path: joinPath(cwd.value, e.name, windows.value),
      is_dir: isDirType(e.type), is_link: e.type === FileType.DirLink || e.type === FileType.FileLink,
      drive: e.type === FileType.DirDrive, size: isDirType(e.type) ? null : e.size, mtime: e.mtime || null,
    }));
  return sortEntries(list, { key: "name", order: "ascend", dirsFirst: true });
});
// 與 SFTP 同一套：每頁 1000 筆，超過時左下角有「還有 N 筆在下一頁」
const { pagination, remaining: moreEntries, nextCount, next: nextPage, reset: resetPage } =
  useFileListPagination(computed(() => rows.value.length));
watch(cwd, () => resetPage());

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < MB) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * MB) return `${(n / MB).toFixed(1)} MB`;
  return `${(n / 1024 / MB).toFixed(2)} GB`;
}

// ── 錯誤文字 ──

/** 受控端回的登入錯誤（§7.5）：常見的有翻譯，原文接在後面 */
const LOGIN_ERRORS: Record<string, string> = {
  "Too many wrong attempts": "rdweb.login_err_too_many_attempts",
  "Please try 1 minute later": "rdweb.login_err_try_later",
  "Offline": "rdweb.login_err_offline",
  "Your ip is blocked by the peer": "rdweb.login_err_ip_blocked",
  "The main window is not open": "rdweb.login_err_main_window",
  // J.1：受控端沒開「允許檔案傳輸」權限
  "No permission of file transfer": "rdfile.err_no_permission",
};

function closeText(info: CloseInfo): string {
  // 開著的連線被收回權限（core/console_guard）：detail 已是翻好的句子
  if (info.code === "console_revoked") return info.detail || t("errors.console_revoked");
  if (info.code === "rd_login_error") {
    const raw = info.detail || "";
    // 原文是受控端送來的：不可以比對到 constructor 這類原型上的名字
    const k = Object.prototype.hasOwnProperty.call(LOGIN_ERRORS, raw) ? LOGIN_ERRORS[raw] : "";
    return k ? `${t(k)}（${raw}）` : t("errors.rd_login_error", { reason: raw });
  }
  if (info.code === "rd_peer_closed") {
    const reason = info.peerReason || "";
    return reason ? `${t("errors.rd_peer_closed")}：${reason}` : t("errors.rd_peer_closed");
  }
  if (!info.code || info.code === "ws_closed") return t("rdweb.err_ws");
  const params: Record<string, unknown> = { reason: info.detail || "", server: serverName.value, ...(info.params || {}) };
  if (typeof params.at === "string") params.at = fmtDateTime(params.at);
  const base = srvText({ code: info.code, params, message: info.detail || "" }, t("rdweb.err_generic"));
  const detail = info.detail || "";
  return detail && !base.includes(detail) ? `${base}（${detail}）` : base;
}

/** 受控端回的檔案動作錯誤（FileResponse.error 的原文）：只當文字顯示 */
function peerErrText(raw: string): string {
  if (raw === "one-way-file-transfer-tip") return t("rdfile.err_one_way");
  return t("rdfile.err_peer", { reason: raw });
}

function opErrText(code?: string, peerError?: string): string {
  if (code === "peer") return peerErrText(peerError || "");
  const k = `rdfile.err_${code}`;
  return code && te(k) ? t(k, { reason: peerError || "" }) : t("rdfile.err_generic");
}

function errText(e: unknown): string {
  return e instanceof FileOpError ? opErrText(e.code, e.peerError) : t("rdfile.err_generic");
}

function jobErrText(v: FileJobView): string {
  return opErrText(v.code, v.error);
}

// 對方的 RustDesk 密碼不可以被瀏覽器自動填入（同遠端桌面的作法）
const NO_AUTOFILL = { autocomplete: "new-password", "data-1p-ignore": "true", "data-lpignore": "true",
                      "data-bwignore": "true" };

// ── 連線 ──

async function connect() {
  const gen = ++connectGen;
  errorMsg.value = "";
  messageBoxes.value = [];
  entries.value = [];
  everListed.value = false;
  jobs.value = [];
  retry.notice = "";
  stage.value = "connecting";
  ui.value = "connecting";
  let tk;
  try {
    tk = await requestRustDeskTicket(props.addressId, "file");
  } catch (e) {
    if (gen !== connectGen) return;
    ui.value = "error";
    errorMsg.value = apiErrMsg(e) || t("rdweb.err_ticket");
    return;
  }
  if (gen !== connectGen) return;
  peerIdShown.value = tk.peer_id;
  serverName.value = tk.server_name;
  if (tk.file_limits) {
    limits.value = { maxFileBytes: tk.file_limits.max_file_bytes, maxTotalBytes: tk.file_limits.max_total_bytes };
  }
  let password: string | undefined = form.password;
  let savedCredentialId: string | undefined;
  if (selectedCredId.value) {
    password = undefined;
    if (tk.has_saved_password) {
      savedCredentialId = selectedCredId.value;
    } else {
      selectedCredId.value = null;
      void loadCreds();
      retry.notice = t("errors.rd_saved_password_unavailable");
    }
  }
  pendingRemember = !savedCredentialId && remember.value && password ? password : null;
  form.password = "";
  session = new RdFileSession({
    url: buildRustDeskWsUrl(tk.ws_path, tk.ticket),
    peerId: tk.peer_id,
    myName: tk.my_name,
    password,
    savedCredentialId,
    showHidden: showHidden.value,
    limits: limits.value,
    events: {
      phase: (p) => { stage.value = p; },
      needPassword: onNeedPassword,
      need2fa: (wrong) => {
        retry.wrong2fa = wrong;
        retry.code = "";
        ui.value = "need_2fa";
      },
      waitingApproval: () => { ui.value = "waiting_approval"; },
      connected: (info) => {
        platform.value = info.platform;
        ui.value = "connected";
        const pw = pendingRemember;
        pendingRemember = null;
        if (pw) void rememberPassword(pw);
      },
      listing: (l) => {
        cwd.value = l.path;
        pathInput.value = l.path;
        entries.value = l.entries;
        windows.value = l.windows;
        drives.value = l.drives;
        everListed.value = true;
      },
      jobs: (v) => { jobs.value = v; },
      conflict: askConflict,
      messageBox: (mb) => { messageBoxes.value = [...messageBoxes.value.slice(-2), mb]; },
      closed: onClosed,
    },
  });
}

function onNeedPassword(reason: PasswordReason, code?: string) {
  pendingRemember = null;
  retry.wrong = reason === "wrong";
  if (reason === "saved_rejected") {
    retry.notice = t("errors.rd_saved_password_rejected");
    selectedCredId.value = null;
  } else if (reason === "saved_failed") {
    retry.notice = srvText({ code: code || "rd_saved_password_unavailable", params: {}, message: "" },
                           t("errors.rd_saved_password_unavailable"));
  } else if (reason === "login_screen") {
    retry.notice = t("rdweb.login_screen_hint");
  }
  retry.password = "";
  ui.value = "need_password";
}

async function rememberPassword(pw: string) {
  try {
    const saved = await saveRustDeskPassword({ addressId: props.addressId, peerId: peerIdShown.value, password: pw,
                                               label: rememberLabel.value });
    selectedCredId.value = saved.id;
    remember.value = false;
    rememberLabel.value = "";
    await loadCreds();
    msg.success(t("rdweb.password_saved"));
  } catch (e) {
    msg.error(apiErrMsg(e) || t("rdweb.err_save_password"));
  }
}

function onClosed(info: CloseInfo) {
  session = null;
  pendingRemember = null;
  // 還開著的同名詢問：當成略過（工作已經跟著連線結束）
  if (conflictPrompt.value) answerConflict("skip");
  if (info.byUser) {
    ui.value = everListed.value ? "closed" : "form";
    return;
  }
  ui.value = everListed.value && info.code === "rd_peer_closed" && !info.peerReason ? "closed" : "error";
  errorMsg.value = closeText(info);
}

async function submitPassword() {
  if (!session) return;
  ui.value = "connecting";
  const pw = retry.password;
  retry.password = "";
  if (remember.value && pw) pendingRemember = pw;
  await session.login(pw);
}

function submit2fa() {
  if (!session || !retry.code.trim()) return;
  ui.value = "connecting";
  session.submit2fa(retry.code);
  retry.code = "";
}

/** 中斷連線（還有傳輸工作時，按鈕外面包一層確認，見 template）。進行中與排隊的工作會跟著取消。 */
function disconnect() {
  session?.close();
}

function backToForm() {
  connectGen++;
  session?.close();
  session = null;
  ui.value = "form";
  everListed.value = false;
  void loadCreds();
}

// ── 瀏覽 ──

async function navigate(path: string) {
  if (!session) return;
  busy.value = true;
  try {
    await session.list(path);
  } catch (e) {
    msg.error(errText(e));
    pathInput.value = cwd.value;
  } finally {
    busy.value = false;
  }
}

function goUp() {
  const up = parentPath(cwd.value, windows.value);
  if (up !== null) void navigate(up);
}

function enter(r: Row) {
  if (r.is_dir) void navigate(r.path);
}

async function onShowHidden(v: boolean) {
  showHidden.value = v;
  const p = session?.setShowHidden(v);
  if (!p) return;
  busy.value = true;
  try { await p; } catch (e) { msg.error(errText(e)); } finally { busy.value = false; }
}

// ── 新增資料夾、改名（名稱不可以含路徑分隔字元）──
const nameDlg = ref<{ mode: "mkdir" | "rename"; value: string; row: Row | null } | null>(null);
const nameDlgShow = computed({
  get: () => nameDlg.value !== null,
  set: (v: boolean) => { if (!v) nameDlg.value = null; },
});

function nameProblem(name: string): string {
  if (!name || name === "." || name === ".." || /[\\/\u0000-\u001f]/.test(name) || name.length > 255) {
    return t("rdfile.name_invalid");
  }
  return "";
}

async function submitNameDlg() {
  const d = nameDlg.value;
  if (!d || !session) return;
  const name = d.value.trim();
  const bad = nameProblem(name);
  if (bad) { msg.error(bad); return; }
  if (d.mode === "rename" && d.row && name === d.row.name) { nameDlg.value = null; return; }
  busy.value = true;
  try {
    if (d.mode === "mkdir") await session.mkdir(joinPath(cwd.value, name, windows.value));
    else await session.rename(d.row!.path, name);
    nameDlg.value = null;
    await navigate(cwd.value);
  } catch (e) {
    msg.error(errText(e));
  } finally {
    busy.value = false;
  }
}

// ── 刪除（檔案用 popconfirm；資料夾連同內容，另外再確認一次）──
const confirmDir = ref<Row | null>(null);

async function doDelete(r: Row) {
  if (!session) return;
  busy.value = true;
  try {
    await session.remove({ path: r.path, dir: r.is_dir, size: r.size ?? 0 });
    msg.success(t("rdfile.deleted", { name: r.name }));
    await navigate(cwd.value);
  } catch (e) {
    msg.error(errText(e));
  } finally {
    busy.value = false;
  }
}

function confirmDirDelete() {
  const r = confirmDir.value;
  confirmDir.value = null;
  if (r) void doDelete(r);
}

// ── 下載（單一檔案；小檔記憶體、大檔串流存檔或分段寫入）──

async function download(r: Row) {
  if (!session || r.is_dir) return;
  const size = r.size ?? 0;
  let sink;
  try {
    // 要在點擊的當下問存到哪裡（瀏覽器要求使用者手勢），所以放在任何其他 await 之前
    sink = await pickDownloadSink(r.name, size);
  } catch (e) {
    msg.error(t("rdfile.err_write", { reason: e instanceof Error ? e.message : String(e) }));
    return;
  }
  if (!sink) return;
  const s = session;
  if (!s) { void Promise.resolve(sink.abort()).catch(() => undefined); return; }
  let handed = false;
  const job = s.download(r.path, {
    name: r.name, size,
    // 只收第一個檔案（資料夾下載之後另議）；同一個 sink 不重複交出去
    sink: (_e, i) => { if (i !== 0 || handed) return null; handed = true; return sink; },
  });
  void job.done.then((v) => {
    if (v.status !== "done") void Promise.resolve(sink.abort()).catch(() => undefined);
    if (v.status === "done") msg.success(t("rdfile.downloaded", { name: r.name }));
    else if (v.status === "error") msg.error(jobErrText(v));
  });
}

// ── 上傳（選檔、拖放；超過伺服器設定的上限不送）──
const uploadInput = ref<HTMLInputElement | null>(null);
const dragging = ref(false);
let dragDepth = 0;

function toSource(f: File): UploadSource {
  return { name: f.name, size: f.size, lastModified: f.lastModified,
           read: async (off, len) => new Uint8Array(await f.slice(off, off + len).arrayBuffer()) };
}

function startUpload(files: File[]) {
  const s = session;
  if (!s || !files.length) return;
  if (drives.value) { msg.warning(t("rdfile.pick_folder_first")); return; }
  const chk = checkUploadLimits(files.map(toSource), limits.value);
  if (chk.tooLarge.length) {
    msg.warning(t("rdfile.too_large_files", { max: fmtBytes(limits.value.maxFileBytes), names: chk.tooLarge.join("、") }),
                { duration: 8000 });
  }
  if (chk.totalTooLarge) {
    msg.error(t("rdfile.total_too_large", { max: fmtBytes(limits.value.maxTotalBytes) }), { duration: 8000 });
    return;
  }
  if (!chk.ok.length) return;
  const dest = cwd.value;
  try {
    const job = s.upload(dest, chk.ok);
    void job.done.then((v) => {
      if (v.status === "done") {
        const n = v.files - v.skipped;
        if (n > 0) msg.success(t("rdfile.uploaded", { n }));
        if (v.skipped) msg.info(t("rdfile.upload_skipped", { n: v.skipped }));
      } else if (v.status === "error") {
        msg.error(jobErrText(v));
      }
      if (session && cwd.value === dest) void navigate(dest);
    });
  } catch (e) {
    msg.error(errText(e));
  }
}

function onPick(ev: Event) {
  const input = ev.target as HTMLInputElement;
  startUpload(input.files ? Array.from(input.files) : []);
  input.value = "";
}

function onDragEnter(ev: DragEvent) {
  if (!ev.dataTransfer?.types?.includes("Files")) return;
  dragDepth += 1;
  dragging.value = true;
}
function onDragOver(ev: DragEvent) {
  if (!ev.dataTransfer?.types?.includes("Files")) return;
  ev.preventDefault();
  ev.dataTransfer.dropEffect = "copy";
}
function onDragLeave() {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) dragging.value = false;
}
function onDrop(ev: DragEvent) {
  ev.preventDefault();
  dragDepth = 0;
  dragging.value = false;
  const dt = ev.dataTransfer;
  if (!dt || ui.value !== "connected") return;
  // 資料夾上傳先不做：只收檔案。型別要看 entry，不可以看大小（macOS 的資料夾 size 不是 0）；
  // dataTransfer 在事件結束後就失效，所以這裡同步取完
  const files: File[] = [];
  let dirs = 0;
  const items = Array.from(dt.items ?? []).filter((it) => it.kind === "file");
  if (items.length) {
    for (const it of items) {
      const entry = (it as DataTransferItem & { webkitGetAsEntry?: () => { isDirectory?: boolean } | null })
        .webkitGetAsEntry?.();
      if (entry?.isDirectory) { dirs += 1; continue; }
      const f = it.getAsFile();
      if (f) files.push(f);
    }
  } else {
    files.push(...Array.from(dt.files ?? []));
  }
  if (dirs) msg.warning(t("rdfile.drop_dirs_skipped", { n: dirs }));
  startUpload(files);
}

// ── 同名（J.4 第 2 步）：覆蓋／略過，可以套用到其餘的 ──
const conflictPrompt = ref<(ConflictInfo & { resolve: (a: ConflictAnswer) => void }) | null>(null);
/** 視窗裡顯示的內容：回答之後留著（淡出的那一下才不會變成空的檔名與 0 B），下一次詢問才換 */
const conflictShown = ref<ConflictInfo | null>(null);
const conflictAll = ref(false);

function askConflict(c: ConflictInfo): Promise<ConflictAnswer> {
  conflictAll.value = false;
  conflictShown.value = { ...c };
  return new Promise((resolve) => { conflictPrompt.value = { ...c, resolve }; });
}

function answerConflict(action: "overwrite" | "skip") {
  const p = conflictPrompt.value;
  conflictPrompt.value = null;
  p?.resolve({ action, all: conflictAll.value });
}

// ── 傳輸佇列 ──

function cancelJob(j: FileJobView) {
  session?.cancel(j.key);
}

function clearFinished() {
  session?.clearFinished();
  jobs.value = jobs.value.filter((j) => j.status === "queued" || j.status === "running");
}

function jobPercent(j: FileJobView): number {
  if (j.status === "done") return 100;
  return j.total > 0 ? Math.min(100, Math.floor((j.done / j.total) * 100)) : 0;
}

const JOB_TAG: Record<FileJobView["status"], "default" | "info" | "success" | "error" | "warning"> = {
  queued: "default", running: "info", done: "success", error: "error", cancelled: "warning",
};

// ── 表格 ──

function actionBtn(icon: unknown, label: string, onClick: () => void, testid: string) {
  return h(NButton, { size: "tiny", secondary: true, onClick, "data-testid": testid }, {
    icon: () => h(NIcon, null, { default: () => h(icon as never) }),
    default: () => label,
  });
}

const cols = computed<DataTableColumns<Row>>(() => [
  {
    title: t("sftp.col_name"), key: "name", minWidth: 240,
    // 受控端給的名稱只當文字顯示（render function 產生的是文字節點，不是 HTML）
    render: (r) => h("a", {
      class: r.is_dir ? "rdf-dir" : "rdf-file",
      style: "display:inline-flex;align-items:center;gap:6px",
      onClick: () => enter(r),
      "data-testid": "rdfile-entry",
    }, [
      h("span", { style: "width:16px;height:16px;flex:none;display:inline-flex;align-items:center;justify-content:center" },
        r.is_dir ? [h(NIcon, { size: 16 }, { default: () => h(FilesIcon) })] : []),
      h("span", null, `${r.name}${r.is_link ? " ↗" : ""}`),
    ]),
  },
  { title: t("sftp.col_size"), key: "size", width: 110, render: (r) => (r.size == null ? "—" : fmtBytes(r.size)) },
  { title: t("sftp.col_mtime"), key: "mtime", width: 170,
    render: (r) => (r.mtime ? fmtDateTime(new Date(r.mtime * 1000).toISOString()) : "—") },
  {
    title: t("common.actions"), key: "actions", width: 250,
    render: (r) => (r.drive ? null : h(NSpace, { size: 4, wrap: false }, () => [
      r.is_dir ? null : actionBtn(DownloadIcon, t("sftp.download"), () => void download(r), "rdfile-download"),
      actionBtn(EditIcon, t("sftp.rename"), () => { nameDlg.value = { mode: "rename", value: r.name, row: r }; },
                "rdfile-rename"),
      r.is_dir
        ? h(NButton, { size: "tiny", secondary: true, type: "error", onClick: () => { confirmDir.value = r; },
                       "data-testid": "rdfile-delete" }, {
          icon: () => h(NIcon, null, { default: () => h(DeleteIcon) }), default: () => t("common.delete") })
        : h(NPopconfirm, { onPositiveClick: () => void doDelete(r) }, {
          trigger: () => h(NButton, { size: "tiny", secondary: true, type: "error", "data-testid": "rdfile-delete" }, {
            icon: () => h(NIcon, null, { default: () => h(DeleteIcon) }), default: () => t("common.delete") }),
          default: () => t("sftp.delete_confirm", { name: r.name }),
        }),
    ])),
  },
]);

// ── 頁面生命週期 ──

function onBeforeUnload(e: BeforeUnloadEvent) {
  if (activeJobs.value) {
    e.preventDefault();
    e.returnValue = "";
    return;
  }
  connectGen++;
  session?.close();
}

onMounted(() => {
  window.addEventListener("beforeunload", onBeforeUnload);
  void loadCreds();
});
onBeforeUnmount(() => {
  window.removeEventListener("beforeunload", onBeforeUnload);
  connectGen++;
  session?.close();
});
</script>

<template>
  <div class="rdf-wrap" :class="{ 'rdf-full': fullHeight, 'rdf-center': fullHeight && ui === 'form' }"
       data-testid="rdfile-browser">
    <!-- 連線表單（照遠端桌面的表單：已存密碼、密碼、記住密碼） -->
    <div v-if="ui === 'form'" class="rdf-form">
      <n-card size="small" :bordered="true" class="console-connect-card">
        <template #header>
          <span style="display:flex;align-items:center;gap:8px">
            <n-icon :component="FilesIcon" :size="18" />
            <span>{{ t("rdfile.connect_to", { ip }) }}</span>
          </span>
        </template>
        <n-alert v-if="retry.notice" type="warning" :show-icon="false" style="margin-bottom:10px">
          {{ retry.notice }}
        </n-alert>
        <div v-if="savedCreds.length" class="rdf-saved-row">
          <span class="rdf-saved-label">{{ t("rdweb.saved_cred") }}</span>
          <n-select v-model:value="selectedCredId" :options="credOptions" clearable size="small"
                    :placeholder="t('rdweb.saved_cred_ph')" style="flex:1" data-testid="rdfile-saved-password" />
          <n-popconfirm v-if="selectedCredId" @positive-click="delSelectedCred">
            <template #trigger>
              <n-button quaternary type="error" size="small">
                <template #icon><n-icon :component="DeleteIcon" /></template>
              </n-button>
            </template>
            {{ t("rdweb.saved_cred_del_confirm") }}
          </n-popconfirm>
        </div>
        <n-form label-placement="left" :label-width="104" size="small" @submit.prevent="connect">
          <n-form-item v-if="peerIdShown" :label="t('rdweb.peer_id')">
            <span class="rdf-mono">{{ peerIdShown }}</span>
          </n-form-item>
          <n-form-item v-if="!selectedCredId" :label="t('rdweb.password')">
            <n-space vertical :size="2" style="width:100%">
              <n-input v-model:value="form.password" type="password" show-password-on="click"
                       :placeholder="t('rdweb.password_ph')" :input-props="NO_AUTOFILL"
                       data-testid="rdfile-password" @keyup.enter="connect" />
              <div class="rdf-note">{{ t("rdweb.password_note") }}</div>
            </n-space>
          </n-form-item>
          <n-form-item v-if="!selectedCredId" :label="t('rdweb.remember_password')">
            <n-space vertical :size="4" style="width:100%">
              <n-switch v-model:value="remember" />
              <n-input v-if="remember" v-model:value="rememberLabel" size="small"
                       :placeholder="t('rdweb.remember_label_ph')" />
            </n-space>
          </n-form-item>
          <n-form-item :label="t('rdfile.show_hidden')">
            <n-switch v-model:value="showHidden" />
          </n-form-item>
          <n-alert :show-icon="false" type="info" style="margin-bottom:10px">
            {{ selectedCredId ? t("rdweb.use_saved_hint") : (remember ? t("rdweb.store_hint") : t("rdweb.no_store_hint")) }}
          </n-alert>
          <!-- J.6：稽核是瀏覽器自報的，畫面要講清楚 -->
          <n-alert :show-icon="false" type="default" style="margin-bottom:10px" data-testid="rdfile-audit-note">
            {{ t("rdfile.audit_note") }}
          </n-alert>
          <n-space justify="end">
            <n-button type="primary" :loading="connecting" data-testid="rdfile-connect" @click="connect">
              <template #icon><n-icon :component="FilesIcon" /></template>
              {{ t("rdweb.connect") }}
            </n-button>
          </n-space>
        </n-form>
      </n-card>
    </div>

    <div v-else class="rdf-area" :class="{ 'rdf-full': fullHeight }">
      <!-- 狀態列（與 SFTP／遠端桌面同一套） -->
      <div class="rdf-toolbar">
        <span class="rdf-status" :data-state="ui" data-testid="rdfile-status">
          <n-spin v-if="connecting" :size="12" />
          <span v-else class="rdf-dot" />
          <span>{{ statusText }}</span>
          <span class="rdf-ip">{{ ip }}</span>
          <n-tag v-if="hostname" size="small" :bordered="false" round>{{ hostname }}</n-tag>
          <span class="conn-proto conn-proto--rd">RustDesk</span>
          <n-tag size="small" type="info" :bordered="false" round>{{ t("rdfile.badge") }}</n-tag>
          <n-tag v-if="peerIdShown" size="small" :bordered="false" round :title="serverName || undefined">ID {{ peerIdShown }}</n-tag>
          <n-tag v-if="platform" size="small" :bordered="false" round>{{ platform }}</n-tag>
          <n-tag v-if="deviceName" size="small" type="info" :bordered="false" round>{{ deviceName }}</n-tag>
          <ConnElapsed :active="ui === 'connected'" />
        </span>
        <n-space :size="8" align="center">
          <n-popconfirm v-if="ui !== 'closed' && ui !== 'error' && activeJobs" @positive-click="disconnect">
            <template #trigger>
              <n-button size="tiny" type="error" ghost data-testid="rdfile-disconnect">
                <template #icon><n-icon :component="CancelIcon" /></template>{{ t("vnc.disconnect") }}
              </n-button>
            </template>
            {{ t("rdfile.disconnect_confirm") }}
          </n-popconfirm>
          <n-button v-else-if="ui !== 'closed' && ui !== 'error'" size="tiny" type="error" ghost
                    data-testid="rdfile-disconnect" @click="disconnect">
            <template #icon><n-icon :component="CancelIcon" /></template>{{ t("vnc.disconnect") }}
          </n-button>
          <n-button v-else size="tiny" type="primary" ghost data-testid="rdfile-reconnect" @click="backToForm">
            <template #icon><n-icon :component="RefreshIcon" /></template>{{ t("vnc.reconnect") }}
          </n-button>
        </n-space>
      </div>

      <n-alert v-if="errorMsg && (ui === 'error' || ui === 'closed')" type="error" :show-icon="true"
               style="margin:8px 0" data-testid="rdfile-error">
        {{ errorMsg }}
      </n-alert>
      <n-alert v-for="(mb, i) in messageBoxes" :key="i" type="info" closable style="margin:8px 0"
               :title="mb.title || undefined" @close="messageBoxes.splice(i, 1)">
        {{ mb.text }}<span v-if="mb.link" class="rdf-link">（{{ mb.link }}）</span>
      </n-alert>

      <!-- 還沒登入：輸入密碼、兩步驟驗證、等待對方同意、連線中 -->
      <div v-if="!onFileList && ui !== 'error' && ui !== 'closed'" class="rdf-wait-box">
        <n-card v-if="ui === 'need_password'" size="small" class="rdf-prompt" data-testid="rdfile-password-prompt">
          <n-alert v-if="retry.notice" type="warning" :show-icon="false" style="margin-bottom:8px">{{ retry.notice }}</n-alert>
          <div class="rdf-prompt-title">{{ retry.wrong ? t("rdweb.wrong_password") : t("rdweb.enter_password") }}</div>
          <n-input v-model:value="retry.password" type="password" show-password-on="click" size="small"
                   :placeholder="t('rdweb.password_ph')" :input-props="NO_AUTOFILL"
                   data-testid="rdfile-retry-password" @keyup.enter="submitPassword" />
          <div class="rdf-note" style="margin-top:6px">{{ t("rdweb.rate_limit_hint") }}</div>
          <n-space justify="space-between" align="center" style="margin-top:10px">
            <n-checkbox v-model:checked="remember" size="small">{{ t("rdweb.remember_password") }}</n-checkbox>
            <n-button size="small" type="primary" data-testid="rdfile-retry-submit" @click="submitPassword"><template #icon><n-icon><LoginIcon /></n-icon></template>
              {{ t("rdweb.login") }}
            </n-button>
          </n-space>
        </n-card>
        <n-card v-else-if="ui === 'need_2fa'" size="small" class="rdf-prompt">
          <div class="rdf-prompt-title">{{ retry.wrong2fa ? t("rdweb.wrong_2fa") : t("rdweb.enter_2fa") }}</div>
          <n-input v-model:value="retry.code" size="small" :maxlength="12"
                   :input-props="{ autocomplete: 'one-time-code', inputmode: 'numeric' }" @keyup.enter="submit2fa" />
          <n-space justify="end" style="margin-top:10px">
            <n-button size="small" type="primary" @click="submit2fa"><template #icon><n-icon><LoginIcon /></n-icon></template>{{ t("rdweb.login") }}</n-button>
          </n-space>
        </n-card>
        <div v-else class="rdf-wait">
          <n-spin :size="22" />
          <div>{{ ui === "waiting_approval" ? t("rdweb.waiting_approval") : ui === "connected" ? t("rdfile.listing") : stageText }}</div>
          <n-button v-if="ui === 'waiting_approval'" size="small" ghost @click="disconnect"><template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}</n-button>
        </div>
      </div>

      <!-- 檔案清單（連上之後；斷線後留著、整片罩住） -->
      <div v-if="onFileList" class="rdf-panel"
           :class="{ 'rdf-full': fullHeight, 'rdf-dropping': dragging, 'rdf-offline': ui !== 'connected' }"
           @dragenter="onDragEnter" @dragover="onDragOver" @dragleave="onDragLeave" @drop="onDrop">
        <div v-if="dragging" class="rdf-dropzone">
          <n-icon :size="28"><UploadIcon /></n-icon>
          <span>{{ t("sftp.drop_here", { dir: cwd }) }}</span>
        </div>
        <div v-if="ui !== 'connected'" class="rdf-offline-mask">
          <div class="rdf-offline-box">
            <n-icon :size="24"><CancelIcon /></n-icon>
            <span class="rdf-offline-title">{{ t("sftp.offline_title") }}</span>
            <n-button size="small" type="primary" @click="backToForm"><template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("vnc.reconnect") }}</n-button>
          </div>
        </div>
        <n-space align="center" class="rdf-pathbar">
          <n-button size="small" :disabled="!canUp" data-testid="rdfile-up" @click="goUp">
            <template #icon><n-icon><UpLevelIcon /></n-icon></template>
            {{ t("sftp.up") }}
          </n-button>
          <n-input v-model:value="pathInput" class="rdf-mono-input" style="width: 320px"
                   :placeholder="drives ? t('rdfile.drives') : ''" data-testid="rdfile-path"
                   @keyup.enter="() => navigate(pathInput.trim())" />
          <n-button size="small" :loading="busy" data-testid="rdfile-refresh" @click="() => navigate(cwd)">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>
            {{ t("common.refresh") }}
          </n-button>
          <n-button size="small" :disabled="drives" @click="nameDlg = { mode: 'mkdir', value: '', row: null }">
            <template #icon><n-icon><NewFolderIcon /></n-icon></template>
            {{ t("sftp.new_folder") }}
          </n-button>
          <n-tooltip :delay="200">
            <template #trigger>
              <n-button size="small" type="primary" :disabled="drives" data-testid="rdfile-upload"
                        @click="() => uploadInput?.click()">
                <template #icon><n-icon><UploadIcon /></n-icon></template>
                {{ t("sftp.upload") }}
              </n-button>
            </template>
            {{ t("rdfile.limits_hint", { file: fmtBytes(limits.maxFileBytes), total: fmtBytes(limits.maxTotalBytes) }) }}
          </n-tooltip>
          <input ref="uploadInput" type="file" multiple style="display:none" @change="onPick" />
          <span class="rdf-switch">
            <n-switch size="small" :value="showHidden" data-testid="rdfile-show-hidden" @update:value="onShowHidden" />
            <span>{{ t("rdfile.show_hidden") }}</span>
          </span>
          <n-input v-model:value="filterText" clearable size="small" style="width: 200px" :placeholder="t('sftp.filter_ph')">
            <template #prefix><n-icon><FilterIcon /></n-icon></template>
          </n-input>
        </n-space>
        <div v-if="drives" class="rdf-hint">{{ t("rdfile.drives_hint") }}</div>
        <div v-if="filterText.trim()" class="rdf-hint">
          {{ t("sftp.filter_note", { shown: rows.length, total: entries.length }) }}
        </div>
        <div class="rdf-table" :class="{ 'rdf-full': fullHeight }">
          <n-data-table :columns="cols" :data="rows" :loading="busy" size="small" :row-key="(r: Row) => r.key"
                        :pagination="pagination" :bordered="false" flex-height style="height:100%" virtual-scroll />
        </div>
        <div v-if="moreEntries > 0" class="file-more" data-testid="rdfile-more">
          <n-button size="small" @click="nextPage">
            {{ t("sftp.more_on_next_page", { n: nextCount, total: moreEntries }) }}
            <template #icon><n-icon><ChevronRightIcon /></n-icon></template>
          </n-button>
        </div>
      </div>

      <!-- 傳輸佇列：一次一個，其他的排隊 -->
      <div v-if="jobs.length" class="rdf-queue" data-testid="rdfile-queue">
        <div class="rdf-queue-head">
          <span class="rdf-queue-title">{{ t("rdfile.queue") }}</span>
          <span class="rdf-note">{{ t("rdfile.queue_note") }}</span>
          <n-button size="tiny" quaternary @click="clearFinished"><template #icon><n-icon><DeleteIcon /></n-icon></template>{{ t("rdfile.queue_clear") }}</n-button>
        </div>
        <div v-for="j in jobs" :key="j.key" class="rdf-job" data-testid="rdfile-job">
          <n-icon :size="16" class="rdf-job-icon"><component :is="j.kind === 'download' ? DownloadIcon : UploadIcon" /></n-icon>
          <div class="rdf-job-main">
            <div class="rdf-job-line">
              <span class="rdf-job-name">{{ j.current || j.name }}</span>
              <span v-if="j.files > 1" class="rdf-note">{{ t("rdfile.job_files", { i: Math.min(j.fileIndex + 1, j.files), n: j.files }) }}</span>
              <n-tag size="tiny" :type="JOB_TAG[j.status]" :bordered="false">{{ t(`rdfile.status_${j.status}`) }}</n-tag>
            </div>
            <n-progress type="line" :percentage="jobPercent(j)" :show-indicator="false" :height="6"
                        :status="j.status === 'error' ? 'error' : j.status === 'done' ? 'success' : 'default'" />
            <div class="rdf-note">
              {{ fmtBytes(j.done) }} / {{ fmtBytes(j.total) }}
              <span v-if="j.skipped">・{{ t("rdfile.job_skipped", { n: j.skipped }) }}</span>
              <span v-if="j.status === 'error'" class="rdf-job-err">・{{ jobErrText(j) }}</span>
            </div>
          </div>
          <n-button v-if="j.status === 'queued' || j.status === 'running'" size="tiny" secondary
                    data-testid="rdfile-job-cancel" @click="cancelJob(j)">
            <template #icon><n-icon :component="CancelIcon" /></template>{{ t("common.cancel") }}
          </n-button>
        </div>
      </div>
    </div>

    <!-- 新增資料夾／改名 -->
    <n-modal v-model:show="nameDlgShow" preset="card" style="width: 420px; max-width: 92vw"
             :title="nameDlg?.mode === 'mkdir' ? t('sftp.new_folder') : t('sftp.rename')">
      <n-input v-if="nameDlg" v-model:value="nameDlg.value" autofocus :placeholder="t('sftp.name_ph')"
               data-testid="rdfile-name-input" @keyup.enter="submitNameDlg" />
      <template #footer>
        <n-space justify="end">
          <n-button @click="nameDlg = null"><template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}</n-button>
          <n-button type="primary" :loading="busy" data-testid="rdfile-name-submit" @click="submitNameDlg"><template #icon><n-icon><CheckIcon /></n-icon></template>
            {{ t("common.confirm") }}
          </n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 刪除資料夾：連同內容，再確認一次 -->
    <n-modal :show="!!confirmDir" preset="card" style="width: 460px; max-width: 92vw" :title="t('rdfile.delete_dir_title')"
             @update:show="(v: boolean) => { if (!v) confirmDir = null; }">
      <n-alert type="warning" :bordered="false">{{ t("rdfile.delete_dir_body", { name: confirmDir?.name ?? "" }) }}</n-alert>
      <template #footer>
        <n-space justify="end">
          <n-button @click="confirmDir = null"><template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}</n-button>
          <n-button type="error" :loading="busy" data-testid="rdfile-delete-dir-confirm" @click="confirmDirDelete"><template #icon><n-icon><DeleteIcon /></n-icon></template>
            {{ t("rdfile.delete_dir_confirm") }}
          </n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 上傳遇到同名（J.4）：覆蓋／略過，可以套用到其餘的 -->
    <n-modal :show="!!conflictPrompt" preset="card" style="width: 480px; max-width: 92vw" :title="t('rdfile.conflict_title')"
             :mask-closable="false" data-testid="rdfile-conflict"
             @update:show="(v: boolean) => { if (!v) answerConflict('skip'); }">
      <!-- 檔名與大小是這次上傳的；受控端那個檔案的大小與時間只有 digest 有帶才顯示（實機 1.4.1 不帶） -->
      <div style="line-height: 1.7" data-testid="rdfile-conflict-body">
        {{ t("rdfile.conflict_body", { name: conflictShown?.name ?? "" }) }}
      </div>
      <div class="rdf-note" style="margin-top: 6px">
        {{ t("rdfile.conflict_ours", { size: fmtBytes(conflictShown?.size ?? 0) }) }}
      </div>
      <div v-if="conflictShown?.peerSize" class="rdf-note">
        {{ conflictShown.peerModified
          ? t("rdfile.conflict_theirs_time", { size: fmtBytes(conflictShown.peerSize),
                                               time: fmtDateTime(new Date(conflictShown.peerModified * 1000).toISOString()) })
          : t("rdfile.conflict_theirs", { size: fmtBytes(conflictShown.peerSize) }) }}
      </div>
      <div v-if="conflictShown?.identical" class="rdf-note" style="margin-top: 6px" data-testid="rdfile-conflict-identical">
        {{ t("rdfile.conflict_identical") }}
      </div>
      <n-checkbox v-if="(conflictShown?.remaining ?? 0) > 0" v-model:checked="conflictAll" style="margin-top: 10px">
        {{ t("sftp.conflict_apply_all") }}
      </n-checkbox>
      <template #footer>
        <n-space justify="end">
          <n-button data-testid="rdfile-conflict-skip" @click="answerConflict('skip')"><template #icon><n-icon><MoveIcon /></n-icon></template>{{ t("sftp.conflict_skip") }}</n-button>
          <n-button type="warning" data-testid="rdfile-conflict-overwrite" @click="answerConflict('overwrite')"><template #icon><n-icon><SaveIcon /></n-icon></template>
            {{ t("sftp.conflict_overwrite") }}
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.rdf-wrap { width: 100%; }
.rdf-wrap.rdf-full { height: 100%; display: flex; flex-direction: column; }
.rdf-wrap.rdf-center { justify-content: center; align-items: center; }
.rdf-wrap.rdf-center .rdf-form { width: 540px; max-width: 92vw; }
.rdf-form { max-width: 540px; }
.rdf-note { font-size: 11.5px; opacity: .7; line-height: 1.5; }
.rdf-hint { font-size: 12px; opacity: .7; margin-bottom: 6px; }
.rdf-mono { font-family: var(--jt-mono, ui-monospace, monospace); }
.rdf-mono-input :deep(input) { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12.5px; }
.rdf-saved-row { display: flex; align-items: center; margin-bottom: 18px; }
.rdf-saved-label { width: 104px; flex: none; box-sizing: border-box; text-align: right; padding-right: 12px; font-size: 14px; }
.rdf-saved-row :deep(.n-button) { margin-left: 6px; }
.rdf-area { display: flex; flex-direction: column; min-height: 0; }
.rdf-area.rdf-full { flex: 1; }
.rdf-toolbar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; padding: 4px 2px;
  gap: 8px; margin-bottom: 8px; }
.rdf-status { font-size: 13px; display: inline-flex; align-items: center; gap: 7px; padding: 3px 11px;
  border-radius: 999px; font-weight: 500; background: rgba(128, 128, 128, .12); color: #888;
  flex-wrap: wrap; row-gap: 4px; max-width: 100%; min-width: 0; }
.rdf-status > * { flex: none; max-width: 100%; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
@media (max-width: 640px) { .rdf-status { border-radius: 14px; } }
.rdf-dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; flex: none; }
.rdf-ip { opacity: .7; font-variant-numeric: tabular-nums; }
.rdf-status[data-state="connected"] { color: #18a058; background: rgba(24, 160, 88, .14); }
.rdf-status[data-state="connecting"], .rdf-status[data-state="need_password"], .rdf-status[data-state="need_2fa"],
.rdf-status[data-state="waiting_approval"] { color: #d99812; background: rgba(217, 152, 18, .14); }
.rdf-status[data-state="error"] { color: #d03050; background: rgba(208, 48, 80, .14); }
.rdf-status[data-state="closed"] { color: #888; background: rgba(128, 128, 128, .14); }
.conn-proto { font-weight: 700; font-size: 11px; letter-spacing: .4px; line-height: 1; padding: 2px 7px; border-radius: 999px; }
.conn-proto--rd { color: #2f7cd3; background: rgba(47, 124, 211, .16); }
.rdf-link { word-break: break-all; opacity: .8; }
.rdf-wait-box { display: flex; align-items: center; justify-content: center; padding: 40px 16px; }
.rdf-wait { display: flex; flex-direction: column; align-items: center; gap: 12px; font-size: 14px; text-align: center; }
.rdf-prompt { width: 360px; max-width: 100%; }
.rdf-prompt-title { font-weight: 600; margin-bottom: 8px; }
.rdf-panel { position: relative; border: 1px solid rgba(128, 128, 128, .28); border-radius: 8px; padding: 10px;
  background: #fff; box-shadow: 0 1px 3px rgba(0, 0, 0, .06); display: flex; flex-direction: column; min-height: 0; }
html[data-theme="dark"] .rdf-panel { background: #10161f; border-color: rgba(200, 210, 230, .18); box-shadow: none; }
.rdf-panel.rdf-full { flex: 1; }
.rdf-panel.rdf-dropping { border-color: #18a058; border-style: dashed; }
.rdf-dropzone { position: absolute; inset: 0; z-index: 5; display: flex; gap: 10px; flex-direction: column;
  align-items: center; justify-content: center; pointer-events: none; background: rgba(24, 160, 88, .10);
  color: #18a058; font-weight: 600; border-radius: 8px; }
.rdf-panel.rdf-offline > *:not(.rdf-offline-mask) { filter: grayscale(1) brightness(.6); pointer-events: none; user-select: none; }
.rdf-offline-mask { position: absolute; inset: 0; z-index: 3; display: flex; align-items: center; justify-content: center;
  background: rgba(128, 128, 128, .18); backdrop-filter: blur(1.5px); border-radius: inherit; }
.rdf-offline-box { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 20px 26px;
  border-radius: 10px; background: var(--jt-card-bg, #fff); box-shadow: 0 6px 24px rgba(0, 0, 0, .18); }
.rdf-offline-title { font-weight: 600; }
.rdf-pathbar { margin-bottom: 10px; }
.rdf-switch { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; }
.rdf-table { height: 420px; }
.rdf-table.rdf-full { flex: 1; height: auto; min-height: 0; }
:deep(.rdf-dir) { color: var(--primary-color, #18a058); cursor: pointer; font-weight: 600; }
:deep(.rdf-file) { cursor: default; }
.rdf-queue { margin-top: 10px; border: 1px solid rgba(128, 128, 128, .28); border-radius: 8px; padding: 8px 10px;
  max-height: 32vh; overflow: auto; }
.rdf-queue-head { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
.rdf-queue-title { font-weight: 600; font-size: 13px; }
.rdf-queue-head .n-button { margin-left: auto; }
.rdf-job { display: flex; align-items: center; gap: 10px; padding: 6px 0; border-top: 1px solid rgba(128, 128, 128, .14); }
.rdf-job:first-of-type { border-top: 0; }
.rdf-job-icon { flex: none; opacity: .75; }
.rdf-job-main { flex: 1; min-width: 0; }
.rdf-job-line { display: flex; align-items: center; gap: 8px; min-width: 0; margin-bottom: 3px; }
.rdf-job-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; }
.rdf-job-err { color: #d03050; opacity: 1; }
:deep(.n-card > .n-card-header) { display: flex; align-items: center; padding-top: 12px; padding-bottom: 12px; }
.file-more { display: flex; justify-content: flex-start; padding: 6px 2px 0; }
</style>
