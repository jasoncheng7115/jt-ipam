<script setup lang="ts">
import { credSuffix, defaultCredId } from "@/utils/savedCreds";
import { wsErrorText, revokedCloseText } from "@/utils/wsError";
import ConnElapsed from "@/components/ConnElapsed.vue";
import ConsoleRouteNote from "@/components/ConsoleRouteNote.vue";
/**
 * SFTP 檔案瀏覽器：先換 ticket → 開 WebSocket → 後端橋接 asyncssh 的 SFTP。
 *
 * 開關與 SSH 各自獨立（`sftp_enabled`），但**授權模型刻意完全相同**，憑證也共用同一個
 * 個人加密金庫 —— 能讀寫遠端檔案的人，實質能力與能開 shell 的人同一級。
 *
 * 版面刻意與 SshTerminal 一致（卡片式連線表單 → 狀態列 + 內容區）：同一套操作在不同
 * 協定間長得不一樣，使用者得重新學一次。
 *
 * 下載：小檔「收完再存檔」最單純。單檔上限在系統設定可以放大（2026-09-26 起，預設 100 MB），
 * 放到 GB 級之後整個檔案收進記憶體會把分頁撐爆 —— 所以超過 STREAM_OVER 的檔案改成
 * 「邊收邊寫進磁碟」（File System Access API，Chrome／Edge；會先問存到哪裡）。
 * 不支援的瀏覽器仍收進記憶體，但超過 MEMORY_MAX 就明講要換瀏覽器或用 scp。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { RateMeter } from "@/utils/transferRate";
import { sortEntries, type SortKey, type SortOrder } from "@/utils/sftpSort";
import { collectDroppedFiles, dirsToCreate, type PickedFile } from "@/utils/dropWalk";
import { getPreferences, updatePreferences } from "@/api/preferences";
import { useI18n } from "vue-i18n";
import {
  NAlert, NButton, NCard, NDataTable, NForm, NFormItem, NIcon, NInput,
  NInputNumber, NPopconfirm, NRadio, NRadioGroup, NSelect, NSpace, NSpin, NSwitch,
  NTooltip,
  NTag, NModal, NInputGroup, NCheckbox, useMessage,
} from "naive-ui";
import type { DataTableColumns } from "naive-ui";
import { h } from "vue";
import {
  buildSshWsUrl, createSshCredential, deleteSshCredential, listSshCredentials,
  requestSftpTicket, resetSshHostKey, type SftpEntry, type SshCredential,
} from "@/api/ssh";
import { fmtDateTime } from "@/utils/datetime";
import { useFileListPagination } from "@/composables/useFileListPagination";
import {
  RefreshIcon, FilesIcon, CancelIcon, DeleteIcon, EditIcon,
  DownloadIcon, UploadIcon, NewFolderIcon, FilterIcon, SortAscIcon, MoveIcon, UpLevelIcon, ChevronRightIcon, CheckIcon, CopyIcon, SaveIcon,
} from "@/icons";

const props = defineProps<{
  addressId: string; host: string;
  hostname?: string | null; deviceName?: string | null; fullHeight?: boolean;
}>();
const { t } = useI18n();
const msg = useMessage();

/** 連線階段 —— 與 SSH／RDP／VNC 主控台同一組狀態名，狀態列也才能共用同一套樣式。 */
const phase = ref<"form" | "connecting" | "connected" | "error" | "closed">("form");
/** 斷線時的 WebSocket 關閉代碼。1000＝正常收線；1006＝連線被硬切（多半是中間的
 *  反向代理逾時），兩者要查的方向完全不同 —— 沒有這個數字，使用者只能回報
 *  「又斷了」，我們也只能猜。 */
const closeCode = ref<number | null>(null);
const connecting = computed(() => phase.value === "connecting");
/** 是否已經在看檔案清單（連上了、或連上後才斷線）—— 其餘階段都停留在連線卡片上。 */
const onFileList = computed(() => phase.value === "connected" || phase.value === "closed");
const errorMsg = ref("");
// 錯誤代碼：主機金鑰不符時要多給一個「清除記住的金鑰」
const errorCode = ref("");
// 主機金鑰 TOFU：跟 SSH 主控台共用同一把釘選（同一台主機）；沒釘選時先給人看指紋
const hostKeyAsk = ref(false);
const hostKeyFp = ref("");
const resettingKey = ref(false);
const cwd = ref("/");
// 這條連線是否經由跳板（issue #24）：畫面上的位址是目標，實際路徑多了一跳
const viaJump = ref("");
const viaKind = ref("jump");   // "jump"／"agent"（經由掃描代理中繼，issue #24 階段二）
const entries = ref<SftpEntry[]>([]);
const truncated = ref(false);
const busy = ref(false);

// ── 連線設定（與 SSH 相同：已存憑證，或當次輸入；也可以順手存起來）
const creds = ref<SshCredential[]>([]);
const remember = ref(false);
const rememberLabel = ref("");
const form = ref({
  credential_id: null as string | null,
  username: "", port: 22, auth: "password" as "password" | "key",
  password: "", private_key: "", passphrase: "",
});
const credOptions = computed(() => [
  // 「用別組帳密」必須在**下拉裡**看得到 —— 只靠 hover 才出現的 ✕ 不算可發現
  { label: t("ssh.cred_manual"), value: null as unknown as string },
  ...creds.value.map((c) => ({
    // 標籤格式與 SSH 主控台一致
    label: `${c.label}（${c.username}・${c.auth_type === "key" ? t("ssh.auth_key") : t("ssh.auth_password")}）${credSuffix(t, c)}`,
    value: c.id,
  })),
]);

async function delSelectedCred() {
  const id = form.value.credential_id;
  if (!id) return;
  try {
    await deleteSshCredential(id);
    form.value.credential_id = null;
    await loadCreds();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? String(e)); }
}

let ws: WebSocket | null = null;
/** 是否曾經真的連上過。
 *
 * 不能用 phase 判斷「斷線時是不是已經連上」：使用者按「中斷連線」時，我們自己會先把
 * phase 設成 closed，接著 onclose 讀到的就不是 connected → 誤判成「還沒連上就斷了」，
 * 於是跳回連線表單並顯示「連線在建立完成前就被關閉（代碼 1005）」—— 明明是使用者
 * 自己按的，卻看起來像連線失敗。 */
let everConnected = false;
/** 下載中的檔案：收到 file_begin 後開始收二進位框，file_end 才落地。
 *  有 `writer` 時邊收邊寫進磁碟（寫入要照順序，所以串成一條 promise 鏈）；沒有就收進記憶體。 */
let incoming: {
  name: string; size: number; got: number; chunks: Uint8Array[];
  writer: any | null; chain: Promise<void>;
} | null = null;
/** download() 先選好存檔位置（要在按鈕的點擊裡問），file_begin 來時接上 */
let pendingWriter: any | null = null;
/** 單檔上限（伺服器在 ready 時告訴我們；舊版伺服器沒帶就用原本的 100 MB） */
const maxFileBytes = ref(100 * 1024 * 1024);
/** 超過這個大小、而且瀏覽器支援時，下載直接寫進磁碟 */
const STREAM_OVER = 64 * 1024 * 1024;
/** 瀏覽器不支援直接寫入磁碟時，收進記憶體的上限（再大就要換瀏覽器或用 scp） */
const MEMORY_MAX = 2 * 1024 * 1024 * 1024;
/** 下載進度（大檔要看得出在動） */
const downloadBytes = ref<{ got: number; total: number; name: string } | null>(null);
let lastDlPaint = 0;
/** 等待中的請求，**以請求編號為鍵**。
 *
 *  先前這裡只有一個沒有標記的欄位：伺服器回任何一個 `ok`，都會解掉「當時正在等的那件事」。
 *  只要協定有一次錯位（回覆遲到、訊息重複、順序顛倒），客戶端就會把別人的回覆當成
 *  自己的成功 —— 實機上看到的就是「畫面說檔案傳完了，伺服器其實一個位元組都沒收到」。
 *  帶上編號之後，這種錯配在結構上不可能發生：對不上的回覆會被忽略，該等的繼續等。 */
const pending = new Map<number, { resolve: (v: any) => void; reject: (e: Error) => void }>();
let nextReqId = 1;

/** 把回覆交給對應的請求。沒有編號的回覆（舊版伺服器）退回「解掉最早那一個」。 */
function settle(m: any, how: "resolve" | "reject", value: any) {
  const id = typeof m?.id === "number" ? m.id : null;
  const key = id !== null && pending.has(id) ? id : (id === null ? pending.keys().next().value : undefined);
  if (key === undefined) return;
  const p = pending.get(key)!;
  pending.delete(key);
  if (how === "resolve") p.resolve(value); else p.reject(value);
}

/** 連線斷掉時，所有還在等的請求都要收到拒絕，否則呼叫端會永遠停在那裡。 */
function rejectAllPending(err: Error) {
  for (const [, p] of pending) p.reject(err);
  pending.clear();
}
/** 這次的 list 只是「對話框在瀏覽目錄」，不要動主畫面的清單。 */
let browsingOnly = false;
/** 這次上傳是否已被伺服器中止（收到 error）—— 送出迴圈看到就停手。 */
let uploadAborted = false;
/** 收到伺服器上傳確認時要通知誰（只有正在上傳時才有值）。 */
let onPutAck: ((bytes: number) => void) | null = null;
/** 這條路徑目前敢讓多少資料同時在路上（位元組）。順利就加倍、卡住就減半。 */
let pathWindow = 32 * 1024;

function send(obj: Record<string, unknown>) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

/** 送一個請求並等它的回覆（依編號配對）。 */
function request(obj: Record<string, unknown>): Promise<any> {
  const id = nextReqId++;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    send({ ...obj, id });
  });
}

async function connect() {
  errorMsg.value = "";
  errorCode.value = "";
  if (!form.value.credential_id && !form.value.username.trim()) {
    errorMsg.value = t("ssh.err_username"); return;
  }
  phase.value = "connecting";

  // 勾了「記住」→ 先存進金庫，之後就以 reference 連線（與 SSH 同一個金庫、同一套作法）
  if (!form.value.credential_id && remember.value) {
    try {
      const saved = await createSshCredential({
        label: rememberLabel.value.trim() || `${form.value.username.trim()}@${props.host}`,
        username: form.value.username.trim(),
        auth_type: form.value.auth,
        target_ip_id: props.addressId,
        password: form.value.auth === "password" ? form.value.password : undefined,
        private_key: form.value.auth === "key" ? form.value.private_key : undefined,
        passphrase: form.value.auth === "key" ? form.value.passphrase : undefined,
      });
      form.value.credential_id = saved.id;
      remember.value = false;
      void loadCreds();
    } catch (e: any) {
      phase.value = "error";
      errorMsg.value = e?.response?.data?.detail || t("ssh.err_save_cred");
      return;
    }
  }

  try {
    everConnected = false;
    const tk = await requestSftpTicket(props.addressId);
    ws = new WebSocket(buildSshWsUrl(tk.ws_path, tk.ticket));
    ws.binaryType = "arraybuffer";

    ws.onopen = () => {
      const cfg: Record<string, unknown> = { type: "config", port: form.value.port };
      if (form.value.credential_id) {
        cfg.credential_id = form.value.credential_id;
      } else {
        cfg.username = form.value.username;
        cfg.auth = form.value.auth;
        if (form.value.auth === "password") cfg.password = form.value.password;
        else { cfg.private_key = form.value.private_key; cfg.passphrase = form.value.passphrase; }
      }
      send(cfg);
      // 明文不留在畫面狀態裡
      form.value.password = ""; form.value.private_key = ""; form.value.passphrase = "";
    };

    ws.onmessage = (ev) => {
      if (typeof ev.data !== "string") {
        if (incoming) {
          const u8 = new Uint8Array(ev.data as ArrayBuffer);
          const cur = incoming;
          if (cur.writer) cur.chain = cur.chain.then(() => cur.writer.write(u8));
          else cur.chunks.push(u8);
          cur.got += u8.byteLength;
          // 進度不必每一塊都重畫（6 GB 是兩萬多塊），隔一小段時間更新一次就好
          const now = performance.now();
          if (now - lastDlPaint > 200 || cur.got >= cur.size) {
            lastDlPaint = now;
            downloadBytes.value = { got: cur.got, total: cur.size, name: cur.name };
          }
        }
        return;
      }
      const m = JSON.parse(ev.data);
      switch (m.type) {
        case "hostkey":
          hostKeyFp.value = m.fingerprint || "";
          hostKeyAsk.value = true;
          break;
        case "ready":
          everConnected = true;
          phase.value = "connected";
          viaJump.value = m.via_jump_host || "";
          viaKind.value = m.via_kind || "jump";
          if (typeof m.max_file_bytes === "number") maxFileBytes.value = m.max_file_bytes;
          cwd.value = m.cwd || "/";
          void refresh();
          break;
        case "list":
          // 移動對話框也用同一個 list 取目錄 —— 那時候不能動主畫面的狀態，
          // 否則「已勾選的那幾列」會被換掉，按下移動時就什麼都不剩了（實際踩過）。
          if (!browsingOnly) {
            cwd.value = m.path;
            entries.value = m.entries ?? [];
            truncated.value = !!m.truncated;
            clearCommandError();
          }
          settle(m, "resolve", m);
          break;
        case "file_begin":
          incoming = { name: m.name, size: m.size, chunks: [], got: 0,
                       writer: pendingWriter, chain: Promise.resolve() };
          pendingWriter = null;
          break;
        case "file_end": {
          const cur = incoming;
          incoming = null;
          if (cur?.writer) {
            // 等所有寫入完成再關檔 —— 關檔之前檔案只在暫存檔裡，不會出現半截的成品
            cur.chain.then(() => cur.writer.close())
              .then(() => { clearCommandError(); settle(m, "resolve", m); })
              .catch((e: unknown) => settle(m, "reject", e instanceof Error ? e : new Error(String(e))));
            break;
          }
          if (cur) {
            const blob = new Blob(cur.chunks as BlobPart[]);
            const a = document.createElement("a");
            a.href = URL.createObjectURL(blob);
            a.download = cur.name;
            a.click();
            setTimeout(() => URL.revokeObjectURL(a.href), 4000);
          }
          clearCommandError();
          settle(m, "resolve", m);
          break;
        }
        case "put_ack":
          // 伺服器確認「這些位元組真的收到了」。不解掉等待中的請求 ——
          // 它是過程回報，不是完成。
          onPutAck?.(m.bytes ?? 0);
          break;
        case "keepalive":
          // 伺服器的保活訊息：什麼都不做。**絕對不能**拿它去解決等待中的請求 ——
          // 那會讓下一個操作以為自己完成了。
          break;
        case "put_ready":
          settle(m, "resolve", m);
          break;
        case "ok":
          clearCommandError();
          settle(m, "resolve", m);
          break;
        case "error": {
          uploadAborted = true;          // 正在上傳的話，讓送出迴圈停下來
          // 同名檔案不是錯誤，是要問使用者的事（會跳出選擇視窗），不顯示錯誤列
          if (m.code !== "sftp_exists") errorMsg.value = wsErrorText(m, m.message ?? "");
          // 連線階段失敗要退回表單，否則使用者卡在一片空白、無從重試
          if (phase.value !== "connected") { phase.value = "error"; errorCode.value = m.code || ""; }
          // 帶上整個錯誤框：code／params 是給 wsErrorText 翻譯用的，was_empty 這種
          // 旗標則讓呼叫端分辨可補救的失敗（資料夾不是空的 → 問要不要連內容一起刪）。
          // 只丟字串的話兩件事都做不到。
          const err = Object.assign(new Error(m.message ?? ""), {
            code: m.code, params: m.params, was_empty: m.was_empty,
          });
          settle(m, "reject", err);
          break;
        }
      }
    };

    ws.onclose = (ev) => {
      // 下載到一半斷線：丟掉暫存檔，不要留下看起來完整、其實只有一半的檔案
      abortDownloadWriter();
      const wasConnected = everConnected;
      closeCode.value = ev.code || 0;
      const revoked = revokedCloseText(ev);
      if (revoked) errorMsg.value = revoked;
      phase.value = wasConnected && !revoked ? "closed" : "error";
      // 還沒連上就被關掉，而且後端也沒送 error —— 這時什麼都不說，畫面看起來像
      // 「按了沒反應」。實際遇過的原因是反向代理沒轉發 WebSocket 升級標頭
      // （nginx 的 location 少列 sftp），瀏覽器只看得到連線被關閉。
      if (!wasConnected && !errorMsg.value) {
        errorMsg.value = t("sftp.err_ws_closed", { code: ev.code || 0 });
      }
      rejectAllPending(new Error(t("sftp.disconnected")));
    };
  } catch (e: any) {
    errorMsg.value = e?.response?.data?.detail ?? String(e);
    phase.value = "error";
  }
}

function disconnect() {
  try { ws?.close(); } catch { /* noop */ }
  phase.value = "closed";
}

function acceptHostKey() {
  hostKeyAsk.value = false;
  send({ type: "hostkey_accept" });
}
function rejectHostKey() {
  hostKeyAsk.value = false;
  try { send({ type: "hostkey_reject" }); } catch { /* noop */ }
}

// 金鑰不符：位址換給別台設備、對方重灌都會這樣。清掉後重連會再跳出新指紋讓人核對，不是直接信任
async function resetHostKey() {
  resettingKey.value = true;
  try {
    await resetSshHostKey(props.addressId);
    msg.success(t("ssh.hostkey_reset_done"));
    errorCode.value = "";
    reconnect();
  } catch (e: any) {
    msg.error(e?.response?.data?.detail || t("ssh.hostkey_reset_failed"));
  } finally {
    resettingKey.value = false;
  }
}

/** 回到連線表單重連（沿用已選的憑證，不必重打帳密）。 */
function reconnect() {
  entries.value = [];
  errorMsg.value = "";
  phase.value = "form";
}

async function refresh(path?: string) {
  busy.value = true;
  checkedKeys.value = [];      // 換目錄還留著上一層的勾選 → 會刪錯東西
  try { await request({ type: "list", path: path ?? cwd.value }); }
  catch (e: any) { msg.error(wsErrorText(e, String(e))); }
  finally { busy.value = false; }
}

function enter(row: SftpEntry) {
  if (row.is_dir) void refresh(row.path);
}

function goUp() {
  const p = cwd.value.replace(/\/+$/, "");
  void refresh(p.slice(0, p.lastIndexOf("/")) || "/");
}

/** 指令成功了就清掉上一個指令留下的錯誤。只在已連上時清 —— 連線階段的錯誤要留著給人看。
 *  少了這個，打錯路徑（找不到 /rmnt）之後改回正確路徑、清單都列出來了，紅框還一直掛著（使用者回報）。 */
function clearCommandError() {
  if (phase.value === "connected") errorMsg.value = "";
}

function abortDownloadWriter() {
  const w = incoming?.writer ?? pendingWriter;
  incoming = null;          // 之後還到的殘餘資料塊不可以接到下一次下載上
  pendingWriter = null;
  if (w) void Promise.resolve(w.abort?.()).catch(() => {});
}

async function download(row: SftpEntry) {
  const size = Number(row.size) || 0;
  // 超過系統設定的上限：當場就講，不必先等伺服器拒絕
  if (size > maxFileBytes.value) {
    msg.error(t("errors.sftp_download_too_large", { max: Math.floor(maxFileBytes.value / 1024 / 1024) }));
    return;
  }
  // 大檔：先問存到哪裡，邊收邊寫。**這一段要在點擊的當下執行**（瀏覽器要求使用者手勢），
  // 所以放在任何 await 伺服器之前。
  let writer: any = null;
  if (size > STREAM_OVER) {
    const pick = (window as any).showSaveFilePicker;
    if (typeof pick === "function") {
      try {
        const handle = await pick.call(window, { suggestedName: row.name });
        writer = await handle.createWritable();
      } catch (e: any) {
        if (e?.name === "AbortError") return;          // 使用者取消了存檔對話框
        msg.error(t("sftp.err_save_file", { reason: String(e?.message ?? e) }));
        return;
      }
    } else if (size > MEMORY_MAX) {
      msg.error(t("sftp.err_big_needs_fs_api", { max: fmtBytes(MEMORY_MAX) }));
      return;
    }
  }
  pendingWriter = writer;
  busy.value = true;
  downloadBytes.value = { got: 0, total: size, name: row.name };
  try { await request({ type: "get", path: row.path }); }
  catch (e: any) {
    abortDownloadWriter();
    msg.error(wsErrorText(e, String(e)));
  }
  finally { busy.value = false; downloadBytes.value = null; pendingWriter = null; }
}

const uploadInput = ref<HTMLInputElement | null>(null);
/** 拖曳中（游標在面板上且帶著檔案）—— 用來顯示「放開以上傳」的提示。 */
const dragging = ref(false);
let dragDepth = 0;          // dragenter/leave 會在子元素間跳動，用計數才不會閃爍
/** 多檔上傳的進度：第幾個 / 共幾個。 */
const uploadProgress = ref<{ done: number; total: number; name: string } | null>(null);
/** 目前這個檔案送出了多少位元組／共多少。
 *
 *  兩個用途，而且第二個更重要：上傳時讓人看得出「有在動」，**斷線時讓人看得出停在哪裡**。
 *  只顯示「連線已中斷」的話，送出 0 位元組（問題在自己這台機器讀不到檔）和送出 95%
 *  （問題在連線途中）長得一模一樣，而這兩件事要查的方向完全相反。 */
const uploadBytes = ref<{ sent: number; total: number; name: string } | null>(null);

// 傳輸速率與剩餘時間（使用者要求：上傳中順便顯示速率）。最近 5 秒的平均，每秒用「現在」重算一次：
// 資料停了速率就往 0 掉並標「停住了」，不會一直掛著最後一次的數字
const upMeter = new RateMeter();
const downMeter = new RateMeter();
const rateTick = ref(0);
let rateTimer: ReturnType<typeof setInterval> | null = null;
function ensureRateTicker() {
  if (rateTimer) return;
  rateTimer = setInterval(() => {
    rateTick.value++;
    if (!uploadBytes.value && !downloadBytes.value && rateTimer) { clearInterval(rateTimer); rateTimer = null; }
  }, 1000);
}
watch(uploadBytes, (v, old) => {
  if (!v) { upMeter.reset(); return; }
  if (!old || old.name !== v.name) upMeter.reset();
  upMeter.add(v.sent);
  ensureRateTicker();
});
watch(downloadBytes, (v, old) => {
  if (!v) { downMeter.reset(); return; }
  if (!old || old.name !== v.name) downMeter.reset();
  downMeter.add(v.got);
  ensureRateTicker();
});
onBeforeUnmount(() => { if (rateTimer) clearInterval(rateTimer); });
function fmtEta(sec: number): string {
  const s = Math.ceil(sec);
  if (s < 60) return t("sftp.eta_s", { s });
  if (s < 3600) return t("sftp.eta_m", { m: Math.ceil(s / 60) });
  return t("sftp.eta_h", { h: Math.floor(s / 3600), m: Math.floor((s % 3600) / 60) });
}
function rateText(m: RateMeter, total: number): string {
  void rateTick.value;                 // 每秒重算（資料停了也要更新）
  const r = m.rate();
  if (r === null) return "";
  const eta = m.eta(total);
  return ` · ${t("sftp.rate", { rate: fmtBytes(Math.round(r)) })}`
    + (eta !== null ? ` · ${fmtEta(eta)}` : r === 0 ? ` · ${t("sftp.stalled")}` : "");
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

/** 送一個檔案到目前目錄。失敗直接往外拋，由呼叫端決定要不要繼續其他檔案。 */
/** 回傳遠端最後的完整路徑（選「兩份都留」時會是另一個檔名）。
 *  `onConflict`：遠端已有同名檔案時怎麼辦；沒給＝伺服器回 `sftp_exists`，由呼叫端問使用者 */
async function putOneFile(file: File, rel?: string, onConflict?: "overwrite" | "rename"): Promise<string> {
  if (!ws) throw new Error(t("sftp.disconnected"));
  const path = `${cwd.value.replace(/\/+$/, "")}/${rel || file.name}`;
  // 送出任何東西**之前**先確認這個項目真的讀得到。資料夾（或已被移走的檔案）在這裡
  // 就會失敗，於是連線完全不會被驚動 —— 比起讓伺服器開好檔案、等滿逾時再清掉，
  // 這樣使用者是立刻得到答案，而不是等半分鐘才看到錯誤。
  try {
    if (file.size > 0) await file.slice(0, 1).arrayBuffer();
  } catch {
    throw new Error(t("sftp.unreadable_item"));
  }
  const putId = nextReqId;          // request() 會用掉這個編號
  const ready = await request({ type: "put", path, size: file.size, ...(onConflict ? { on_conflict: onConflict } : {}) });
  // put_ready 之後才開始送二進位框，一次 256 KiB
  // 每個資料框 16 KiB，真正受控的是**同時在路上的資料量**（下面的窗）。
  //
  // 由來（2026-08-30 實機量測）：某條路徑上，256 KiB 的資料框一送出就讓連線斷掉；
  // 改成 16 KiB 的框連續送，則是到 **49152 位元組**就整個停住、30 秒後逾時。
  // 兩者其實是同一件事 —— 一個 256 KiB 的框等於一次把 256 KiB 丟上路。
  // 所以要控的不是框的大小，是**在路上的量**。
  //
  // 而那個門檻是**那條路徑的數字，不是通則**：別的站台可能高得多，也可能根本沒有
  // 這個問題。寫死一個小值等於讓所有人陪一條壞路徑吃虧 —— 跨網路、往返 100 毫秒時，
  // 32 KiB 的窗只有約 320 KB/s。所以這裡讓它自己探：從小開始，順利就加倍，
  // 卡住就減半重來。好的路徑幾個往返就爬到上限，壞的路徑會自己收斂到過得去的大小。
  const CHUNK = 16 * 1024;
  const FIRST_CHUNK = CHUNK;
  const FIRST_ACK_TIMEOUT = 15_000;
  const WINDOW_MIN = 16 * 1024;
  const WINDOW_MAX = 4 * 1024 * 1024;
  const ACK_TIMEOUT = 20_000;
  // 送出緩衝的上限。沒有這個限制時，大檔會被整個塞進瀏覽器的 WebSocket 送出緩衝
  // （伺服器來不及消化），Chrome 會直接把連線切掉 —— 實機上拖兩個檔案就重現：
  // 遠端留下 0 位元組的檔案、畫面顯示「連線已中斷」。
  const HIGH_WATER = 4 * 1024 * 1024;
  // 上傳被伺服器中止時要立刻停送。否則剩下的資料框會繼續往連線裡塞，
  // 對方已經回到「等下一個指令」的狀態，那些框就成了雜訊。
  uploadAborted = false;
  uploadBytes.value = { sent: 0, total: file.size, name: file.name };
  // 上傳完成的 `ok` 會帶著這次 `put` 的編號回來，所以先把編號留著再送資料
  const done = new Promise((resolve, reject) => { pending.set(putId, { resolve, reject }); });
  let sentAll = true;
  // ⚠️ 讀檔失敗也算「送到一半放棄」。`file.slice().arrayBuffer()` 是會**丟例外**的：
  // 檔案在拖進來之後被移走、外接磁碟斷線、iCloud 上還沒下載回本機… 都會在中途失敗。
  // 少了這個 try，例外會直接跳出這個函式，`put_abort` 不會送出，伺服器就一直等
  // 那些永遠不會來的位元組 —— 使用者看到的是「上傳沒反應、然後整條連線斷掉」。
  let readError: unknown = null;
  let ackedAny = false;
  let firstAckResolve: (() => void) | null = null;
  const firstAck = new Promise<void>((r) => { firstAckResolve = r; });
  let acked = 0;
  let ackWaiter: (() => void) | null = null;
  // 這條連線目前敢讓多少資料在路上。整條連線共用（同一批檔案不必重新探一次）。
  let win = pathWindow;
  onPutAck = (bytes) => {
    if (bytes > 0 && !ackedAny) { ackedAny = true; firstAckResolve?.(); }
    if (bytes > acked) {
      acked = bytes;
      // 一路順利就把窗放大 —— 好的路徑幾個往返就爬到上限
      if (win < WINDOW_MAX) { win = Math.min(win * 2, WINDOW_MAX); pathWindow = win; }
      uploadBytes.value = { sent: acked, total: file.size, name: file.name };
      ackWaiter?.();
    }
  };
  /** 等到在路上的資料降到窗以下。回 false 代表等太久（對方沒再收到東西）。 */
  const waitForWindow = (sentSoFar: number) => new Promise<boolean>((resolve) => {
    if (sentSoFar - acked < win) { resolve(true); return; }
    const timer = setTimeout(() => { ackWaiter = null; resolve(false); }, ACK_TIMEOUT);
    ackWaiter = () => {
      if (sentSoFar - acked < win) {
        clearTimeout(timer); ackWaiter = null; resolve(true);
      }
    };
  });
  try {
    let off = 0;
    while (off < file.size) {
      const step = off === 0 ? Math.min(FIRST_CHUNK, file.size) : CHUNK;
      const buf = await file.slice(off, off + step).arrayBuffer();
      // 等緩衝消化到水位以下再繼續送；中途斷線就別再送了
      while (ws.bufferedAmount > HIGH_WATER) {
        if (ws.readyState !== WebSocket.OPEN) { sentAll = false; break; }
        await new Promise((r) => setTimeout(r, 20));
      }
      if (!sentAll || uploadAborted || ws.readyState !== WebSocket.OPEN) { sentAll = false; break; }
      ws.send(buf);
      off += buf.byteLength;
      if (off < file.size && !(await waitForWindow(off))) {
        // 這個窗這條路徑吃不下 —— 減半記在連線上，讓上層重試一次
        pathWindow = Math.max(WINDOW_MIN, Math.floor(win / 2));
        sentAll = false;
        readError = new Error(pathWindow < win ? "retry" : "no_ack");
        break;
      }
      if (off === buf.byteLength && off < file.size) {
        // 第一塊送出去了 —— 等伺服器說「收到了」再繼續。等不到就不必再送剩下的：
        // 資料根本沒離開這台電腦或沒穿過中間的網路，繼續送只是把連線拖到自己死掉。
        const ok = await Promise.race([
          firstAck.then(() => true),
          new Promise<boolean>((r) => setTimeout(() => r(false), FIRST_ACK_TIMEOUT)),
        ]);
        if (!ok) { sentAll = false; readError = new Error("no_ack"); break; }
      }
    }
  } catch (e) {
    sentAll = false;
    readError = e;
  }
  if (!sentAll) {
    // 送到一半放棄時**一定要讓伺服器知道**：它還在等剩下的位元組，我們卻若無其事
    // 送出下一個指令 —— 伺服器讀到的就是型別不對的框。實機上這個組合會讓整條連線
    // 直接斷掉（後端已另外補上防護，但客戶端本來就不該這樣做）。
    send({ type: "put_abort", path });
    // 讀不到檔案要說是讀不到，不要含糊地說「連線已中斷」—— 那會讓人去查網路，
    // 而真正的原因在自己這台機器上。
    if (readError instanceof Error && readError.message === "retry") {
      const e = new Error("retry") as Error & { retry?: boolean };
      e.retry = true;
      throw e;
    }
    if (readError instanceof Error && readError.message === "no_ack") {
      throw new Error(t("sftp.err_no_ack"));
    }
    if (readError !== null) throw new Error(t("sftp.unreadable_item"));
    throw new Error(t("sftp.disconnected"));
  }
  const okMsg: any = await done;
  onPutAck = null;
  uploadBytes.value = null;
  return String(okMsg?.path || ready?.path || path);
}

// ── 遠端已有同名檔案時問一次（2026-10-04 起；以前直接覆蓋、事前不問）
type ConflictAction = "overwrite" | "rename" | "skip";
const conflictPrompt = ref<{ name: string; size: number; mtime: number | null; remaining: number;
  resolve: (v: { action: ConflictAction; all: boolean }) => void } | null>(null);
const conflictAll = ref(false);
function askConflict(params: any, remaining: number): Promise<{ action: ConflictAction; all: boolean }> {
  conflictAll.value = false;
  return new Promise((resolve) => {
    conflictPrompt.value = { name: String(params?.name ?? ""), size: Number(params?.size ?? 0),
      mtime: params?.mtime ?? null, remaining, resolve };
  });
}
function answerConflict(action: ConflictAction) {
  const p = conflictPrompt.value;
  conflictPrompt.value = null;
  p?.resolve({ action, all: conflictAll.value });
}

/** 上傳一批檔案：逐個送（同一條連線，並行只會互相排隊）。
 *
 *  拖資料夾進來時 `path` 會帶著子目錄，所以要**先把目錄建好再放檔案** ——
 *  順序反過來的話每個檔案都會因為「父目錄不存在」而失敗。 */
async function uploadFiles(picked: PickedFile[]) {
  if (!picked.length || !ws) return;
  busy.value = true;
  const failed: string[] = [];
  const skipped: string[] = [];
  let lastName = picked[0]?.path ?? "";
  // 「其餘同名的檔案也這樣處理」：問過一次就不再問
  let conflictForAll: ConflictAction | null = null;
  try {
    const base = cwd.value.replace(/\/+$/, "");
    for (const d of dirsToCreate(picked)) {
      // parents：缺的中間層一起建、已存在當成功，不然會收到一串假的失敗
      try { await request({ type: "mkdir", path: `${base}/${d}`, parents: true }); } catch (e: any) {
        failed.push(`${d}（${wsErrorText(e, String(e))}）`);
      }
    }
    for (const [i, item] of picked.entries()) {
      const file = item.file;
      uploadProgress.value = { done: i, total: picked.length, name: item.path };
      // 一個檔案失敗不該讓其餘的都不傳；最後一次講清楚是哪幾個
      // 窗被調小時同一個檔案要再試一次 —— 那不是失敗，是還沒找到這條路徑的容量
      let attempt = 0;
      let onConflict: "overwrite" | "rename" | undefined;
      for (;;) {
        try {
          const finalPath = await putOneFile(file, item.path, onConflict);
          lastName = finalPath.split("/").pop() || item.path;
          break;
        } catch (e: any) {
          if (e?.code === "sftp_exists" && !onConflict) {
            const remaining = picked.length - i - 1;
            const answer: { action: ConflictAction; all: boolean } = conflictForAll
              ? { action: conflictForAll, all: true } : await askConflict(e.params, remaining);
            if (answer.all) conflictForAll = answer.action;
            if (answer.action === "skip") { skipped.push(item.path); break; }
            onConflict = answer.action;
            continue;
          }
          if (e?.retry && attempt < 4 && ws && ws.readyState === WebSocket.OPEN) {
            attempt += 1;
            continue;
          }
          failed.push(`${item.path}（${wsErrorText(e, String(e))}）`);
          break;
        }
      }
    }
    const ok = picked.length - failed.length - skipped.length;
    if (failed.length) msg.error(t("sftp.upload_partial_fail", { ok, names: failed.join("、") }));
    else if (ok === 1 && picked.length === 1) msg.success(t("sftp.uploaded", { name: lastName }));
    else if (ok > 0) msg.success(t("sftp.uploaded_many", { n: ok }));
    if (skipped.length) msg.info(t("sftp.upload_skipped", { n: skipped.length, names: skipped.join("、") }));
    await refresh();
  } finally {
    uploadProgress.value = null;
    busy.value = false;
    if (uploadInput.value) uploadInput.value.value = "";
  }
}

async function onUpload(ev: Event) {
  const list = (ev.target as HTMLInputElement).files;
  await uploadFiles(list ? Array.from(list).map((f) => ({ file: f, path: f.name })) : []);
}

// ── 拖曳上傳
function onDragEnter(ev: DragEvent) {
  if (!ev.dataTransfer?.types?.includes("Files")) return;   // 拖文字進來不該亮起來
  dragDepth += 1;
  dragging.value = true;
}
function onDragOver(ev: DragEvent) {
  if (!ev.dataTransfer?.types?.includes("Files")) return;
  ev.preventDefault();                       // 不攔的話瀏覽器會直接開啟那個檔案
  ev.dataTransfer.dropEffect = "copy";
}
function onDragLeave() {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) dragging.value = false;
}
async function onDrop(ev: DragEvent) {
  ev.preventDefault();
  dragDepth = 0;
  dragging.value = false;
  if (phase.value !== "connected") return;
  const dt = ev.dataTransfer;
  if (!dt) return;
  // 資料夾要逐項對照 entry 來排除，**不可以用大小判斷**：macOS 交出來的資料夾
  // `File.size` 是 256，`size > 0` 會把它當成檔案送去上傳，然後在讀取內容時失敗 ——
  // 實機症狀是「已寫入 0/256 位元組」，而且伺服器要等滿逾時才放棄。
  const items = Array.from(dt.items ?? []);
  // ⚠️ `webkitGetAsEntry()` 必須在 await 之前就取完：`dataTransfer` 在事件處理函式
  // 結束後就失效了，晚一步拿到的會是 null，整批東西會安靜地變成零個檔案。
  const entries = items.map((it) => (it as any).webkitGetAsEntry?.() ?? null);
  const files = Array.from(dt.files ?? []);
  const { files: picked, skippedDirs: dirs, droppedOverLimit: over } =
    await collectDroppedFiles(files, entries);
  if (dirs) msg.warning(t("sftp.drop_dirs_skipped", { n: dirs }));
  if (over) msg.warning(t("sftp.drop_too_many", { n: over, max: picked.length }));
  await uploadFiles(picked);
}

// ── 名稱輸入對話框（新增資料夾 / 重新命名）
// 用應用程式自己的對話框，不用 window.prompt：瀏覽器原生對話框長得跟系統警告一樣、
// 不受主題影響、也沒辦法做驗證與說明。
const nameDlg = ref<{ mode: "mkdir" | "rename"; value: string; row: SftpEntry | null } | null>(null);
const nameDlgTitle = computed(() =>
  nameDlg.value?.mode === "mkdir" ? t("sftp.new_folder") : t("sftp.rename"));
const nameDlgShow = computed({
  get: () => nameDlg.value !== null,
  set: (v: boolean) => { if (!v) nameDlg.value = null; },
});

function doMkdir() {
  nameDlg.value = { mode: "mkdir", value: "", row: null };
}

function doRename(row: SftpEntry) {
  nameDlg.value = { mode: "rename", value: row.name, row };
}

async function submitNameDlg() {
  const d = nameDlg.value;
  if (!d) return;
  const name = d.value.trim();
  if (!name) return;
  // 名稱不能含 /：那會變成搬移到別的目錄，是另一件事（要搬請用「移動」）
  if (name.includes("/")) { msg.error(t("sftp.rename_no_slash")); return; }
  if (d.mode === "rename" && d.row && name === d.row.name) { nameDlg.value = null; return; }
  busy.value = true;
  try {
    const dir = cwd.value.replace(/\/+$/, "");
    if (d.mode === "mkdir") await request({ type: "mkdir", path: `${dir}/${name}` });
    else await request({ type: "rename", path: d.row!.path, to: `${dir}/${name}` });
    nameDlg.value = null;
    await refresh();
  } catch (e: any) { msg.error(wsErrorText(e, String(e))); }
  finally { busy.value = false; }
}

async function doDelete(row: SftpEntry) {
  busy.value = true;
  try {
    await request({ type: "delete", path: row.path, is_dir: row.is_dir });
    await refresh();
  } catch (e: any) {
    // 資料夾有內容：SFTP 不會刪有東西的資料夾。與其只說失敗，不如把後端查到的
    // 項目數講出來，並讓使用者明確決定要不要連內容一起刪（這是破壞性的，要問過）
    // was_empty 由後端給（它實際列過那個目錄）；代碼本身已經是翻譯鍵，不再拿來判斷流程
    if (e?.was_empty === false) {
      errorMsg.value = "";
      confirmRecursive.value = { path: row.path, detail: wsErrorText(e, "") };
    } else {
      msg.error(wsErrorText(e, String(e)));
    }
  } finally { busy.value = false; }
}

/** 待確認的「連同內容刪除」；null＝沒有待確認的 */
const confirmRecursive = ref<{ path: string; detail: string } | null>(null);

async function doDeleteRecursive() {
  const target = confirmRecursive.value;
  confirmRecursive.value = null;
  if (!target) return;
  busy.value = true;
  try {
    const r = await request({ type: "delete", path: target.path, is_dir: true,
                              recursive: true });
    msg.success(t("sftp.deleted_recursive", { n: r?.removed ?? 0 }));
    await refresh();
  } catch (e: any) { msg.error(wsErrorText(e, String(e))); }
  finally { busy.value = false; }
}

function fmtSize(n: number | null): string {
  // null＝遠端沒回報，不要顯示成 0 B —— 那是兩件事
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/** 小按鈕：icon + 文字（表格操作欄用）。 */
function actionBtn(icon: any, label: string, onClick: () => void, type?: "error") {
  return h(NButton, { size: "tiny", secondary: true, type, onClick }, {
    icon: () => h(NIcon, null, { default: () => h(icon) }),
    default: () => label,
  });
}

const cols = computed<DataTableColumns<SftpEntry>>(() => [
  { type: "selection" },
  {
    title: t("sftp.col_name"), key: "name", minWidth: 240,
    render: (r) => h("a", {
      class: ["sftp-name", r.is_dir ? "sftp-dir" : "sftp-file"],
      // 行內樣式而非 scoped CSS：這些元素是 render function 產生的，不帶 data-v 標記，
      // scoped 樣式套不到它們（量到檔案名稱比資料夾少縮排 16px 就是這個原因）
      style: "display:inline-flex;align-items:center;gap:6px",
      onClick: () => enter(r),
    }, [
      // 檔案沒有 icon，仍要佔掉與資料夾完全相同的寬度，否則兩種列的名稱對不齊。
      // 用固定尺寸的 icon 元件而不是 emoji —— emoji 寬度由字型決定，量到過差 17px。
      h("span", { style: "width:16px;height:16px;flex:none;display:inline-flex;"
                       + "align-items:center;justify-content:center;overflow:hidden" },
        r.is_dir ? [h(NIcon, { size: 16 }, { default: () => h(FilesIcon) })] : []),
      h("span", null, `${r.name}${r.is_link ? " ↗" : ""}`),
    ]),
    sorter: true,
    sortOrder: sortKey.value === "name" ? sortOrder.value : false,
  },
  { title: t("sftp.col_size"), key: "size", width: 110,
    render: (r) => (r.is_dir ? "—" : fmtSize(r.size)),
    sorter: true, sortOrder: sortKey.value === "size" ? sortOrder.value : false },
  { title: t("sftp.col_mtime"), key: "mtime", width: 170,
    render: (r) => (r.mtime ? fmtDateTime(new Date(r.mtime * 1000).toISOString()) : "—"),
    sorter: true, sortOrder: sortKey.value === "mtime" ? sortOrder.value : false },
  { title: t("sftp.col_mode"), key: "mode", width: 120,
    // 權限位元要等寬才對得齊（drwxr-xr-x 每個位置都有意義，錯位就得一個字一個字數）。
    // 這裡走 inline style 而非 class：render function 產生的節點在表格內部，拿不到本元件的
    // scoped 樣式作用域 —— 之前掛 class="mono" 完全沒生效就是這個原因。
    render: (r) => h("span", {
      style: "font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; white-space: nowrap;",
    }, r.mode ?? "—") },
  {
    title: t("common.actions"), key: "actions", width: 230,
    render: (r) => h(NSpace, { size: 4, wrap: false }, () => [
      r.is_dir ? null : actionBtn(DownloadIcon, t("sftp.download"), () => download(r)),
      actionBtn(EditIcon, t("sftp.rename"), () => doRename(r)),
      h(NPopconfirm, { onPositiveClick: () => doDelete(r) }, {
        trigger: () => h(NButton, { size: "tiny", secondary: true, type: "error" }, {
          icon: () => h(NIcon, null, { default: () => h(DeleteIcon) }),
          default: () => t("common.delete"),
        }),
        default: () => t("sftp.delete_confirm", { name: r.name }),
      }),
    ]),
  },
]);

// ── 排序：自己做，不交給表格的 sorter
// 因為「資料夾優先」必須在升冪／降冪**之外**成立 —— 寫進比較函式的話，切成降冪
// 會連分組一起反轉，資料夾跑到最後面。表格的 sorter 拿不到方向，所以改成受控排序。
const sortKey = ref<SortKey>("name");
const sortOrder = ref<SortOrder>("ascend");
const dirsFirst = ref(true);          // 預設與檔案總管一致

const sortModeOptions = computed(() => [
  { label: t("sftp.sort_dirs_first"), value: "dirs" },
  { label: t("sftp.sort_mixed"), value: "mixed" },
]);

/** 選中值前面掛排序 icon —— 工具列上其他控制項都是「icon + 文字」，少一個看得出來。 */
function renderSortTag({ option }: { option: { label?: unknown } }) {
  return h("span", { style: "display:flex;align-items:center;gap:6px;" }, [
    h(NIcon, null, { default: () => h(SortAscIcon) }),
    String(option?.label ?? ""),
  ]);
}

function onSorterChange(s: { columnKey?: string | number; order?: SortOrder } | null) {
  if (!s || !s.order) { sortOrder.value = false; return; }
  sortKey.value = String(s.columnKey ?? "name") as SortKey;
  sortOrder.value = s.order;
}

async function setDirsFirst(v: boolean) {
  dirsFirst.value = v;
  // 存進使用者偏好（跨裝置），與每頁筆數同一個機制；失敗不影響當下操作
  try { await updatePreferences({ sftp_sort_dirs_first: v }); } catch { /* 靜默 */ }
}

onMounted(async () => {
  try {
    const p = await getPreferences();
    if (typeof p.sftp_sort_dirs_first === "boolean") dirsFirst.value = p.sftp_sort_dirs_first;
  } catch { /* 讀不到就用預設 */ }
});

// ── 篩選：只篩目前這一頁的清單（遠端不重撈，因為列出來的就是全部了）
const filterText = ref("");
const shownEntries = computed(() => {
  const q = filterText.value.trim().toLowerCase();
  const base = q
    ? entries.value.filter((e) => e.name.toLowerCase().includes(q))
    : entries.value;
  return sortEntries(base, {
    key: sortKey.value, order: sortOrder.value, dirsFirst: dirsFirst.value,
  });
});

// 目錄很大時分頁顯示 —— 原本只能截斷後說「只顯示一部分」，現在整份都拿得到、翻頁看。
// 每頁 1000 筆，超過時左下角有「還有 N 筆在下一頁」（見 useFileListPagination）
const { pagination, remaining: moreEntries, nextCount, next: nextPage, reset: resetPage } =
  useFileListPagination(computed(() => shownEntries.value.length));
watch(cwd, () => resetPage());

// ── 批次作業
const checkedKeys = ref<string[]>([]);
const checkedRows = computed(() =>
  entries.value.filter((e) => checkedKeys.value.includes(e.path)));

/** 每次換目錄或重新整理都清空勾選 —— 留著上一個目錄的選取會刪錯東西。 */
function clearSelection() { checkedKeys.value = []; }

async function batchDownload() {
  const files = checkedRows.value.filter((r) => !r.is_dir);
  const dirs = checkedRows.value.length - files.length;
  if (!files.length) { msg.warning(t("sftp.batch_no_files")); return; }
  busy.value = true;
  try {
    // 逐個下載：每個檔案都是獨立的一次傳輸，同時進行只會互相排隊
    for (const f of files) await request({ type: "get", path: f.path });
    // 資料夾不能當檔案下載 —— 要講出來，不能安靜地少傳幾個
    msg.success(dirs
      ? t("sftp.batch_downloaded_skipped_dirs", { n: files.length, dirs })
      : t("sftp.batch_downloaded", { n: files.length }));
  } catch (e: any) { msg.error(wsErrorText(e, String(e))); }
  finally { busy.value = false; }
}

async function batchDelete() {
  const rows = checkedRows.value;
  if (!rows.length) return;
  busy.value = true;
  const failed: string[] = [];
  try {
    const nonEmpty: string[] = [];
    for (const r of rows) {
      try { await request({ type: "delete", path: r.path, is_dir: r.is_dir }); }
      catch (e: any) {
        // 有內容的資料夾另外列：那不是「刪不掉」，而是要先決定要不要連內容刪，
        // 混在一般失敗裡會讓人以為壞了
        if (e?.was_empty === false) nonEmpty.push(r.name);
        else failed.push(r.name);           // 一個失敗不該讓其他的也不做
      }
    }
    // 部分失敗要說清楚是哪幾個，否則使用者以為全刪了
    if (nonEmpty.length) {
      msg.warning(t("sftp.batch_dirs_not_empty", { names: nonEmpty.join("、") }),
                  { duration: 8000 });
    }
    if (failed.length) msg.error(t("sftp.batch_partial_fail", { names: failed.join("、") }));
    else if (!nonEmpty.length) msg.success(t("sftp.batch_deleted", { n: rows.length }));
  } finally {
    clearSelection();
    await refresh();
    busy.value = false;
  }
}

// ── 移動對話框：可直接打路徑，也可以點目錄一層層瀏覽
const moveDlg = ref(false);
const moveDest = ref("/");            // 目的地（輸入框與瀏覽同步）
const moveDirs = ref<SftpEntry[]>([]); // 目前瀏覽層的子目錄
const moveLoading = ref(false);

/** 按下「移動」當下要搬的項目 —— 快照起來，不受之後瀏覽目錄影響。 */
const movingRows = ref<SftpEntry[]>([]);

function openMoveDlg() {
  if (!checkedRows.value.length) return;
  movingRows.value = [...checkedRows.value];
  moveDest.value = cwd.value;
  moveDlg.value = true;
  void browseMove(cwd.value);
}

/** 列出某層的子目錄給對話框點選（只顯示目錄 —— 檔案不能當搬移目的地）。 */
async function browseMove(path: string) {
  moveLoading.value = true;
  browsingOnly = true;
  try {
    const m = await request({ type: "list", path });
    moveDest.value = m.path;
    moveDirs.value = (m.entries ?? []).filter((e: SftpEntry) => e.is_dir);
  } catch (e: any) {
    msg.error(wsErrorText(e, String(e)));
  } finally {
    browsingOnly = false;
    moveLoading.value = false;
  }
}

function moveUp() {
  const p = moveDest.value.replace(/\/+$/, "");
  void browseMove(p.slice(0, p.lastIndexOf("/")) || "/");
}

async function confirmMove() {
  const dir = moveDest.value.trim().replace(/\/+$/, "") || "/";
  moveDlg.value = false;
  await batchMove(dir);
  await refresh();          // 對話框瀏覽過別的目錄，回到原本這一層
}

async function batchMove(dest: string) {
  const rows = movingRows.value.length ? movingRows.value : checkedRows.value;
  if (!rows.length) return;
  const dir = dest.replace(/\/+$/, "") || "/";
  busy.value = true;
  const failed: string[] = [];
  try {
    for (const r of rows) {
      try { await request({ type: "rename", path: r.path, to: `${dir}/${r.name}` }); }
      catch { failed.push(r.name); }
    }
    if (failed.length) msg.error(t("sftp.batch_partial_fail", { names: failed.join("、") }));
    else msg.success(t("sftp.batch_moved", { n: rows.length, dir }));
  } finally {
    clearSelection();
    await refresh();
    busy.value = false;
  }
}

async function loadCreds() {
  try {
    creds.value = await listSshCredentials(props.addressId);
    // 有已存帳密就預設選最近一筆（與 SSH 主控台一致）：不必再輸入，直接按連線；
    // 要改用其他帳密把下拉清空即可，手動欄位就會回來。
    // 位址換手前存的不自動帶入（utils/savedCreds）
    if (!form.value.credential_id) form.value.credential_id = defaultCredId(creds.value);
  } catch { /* 沒有就手動輸入 */ }
}
void loadCreds();

onBeforeUnmount(() => { try { ws?.close(); } catch { /* 已關閉 */ } });
</script>

<template>
  <div class="sftp-wrap"
       :class="{ 'sftp-full': fullHeight, 'sftp-center': fullHeight && !onFileList }">
    <!-- 連線設定表單（版面與 SSH 終端機一致：卡片 + 左標籤表單 + 說明 + 右下連線鈕）
         連線中也留在這裡：按下連線後把卡片挪走會讓畫面整個跳一下，而失敗時又跳回來 -->
    <div v-if="!onFileList" class="sftp-form">
      <n-card size="small" :bordered="true" class="console-connect-card">
        <template #header>
          <span style="display:flex;align-items:center;gap:8px">
            <n-icon :component="FilesIcon" :size="18" />
            <span>{{ t("sftp.connect_to", { ip: host }) }}</span>
          </span>
        </template>

        <n-alert v-if="errorMsg" type="error" :bordered="false" style="margin-bottom:12px">
          {{ errorMsg }}
          <div v-if="errorCode === 'console_host_key_mismatch'" class="hostkey-reset" data-testid="sftp-hostkey-reset">
            <div class="hostkey-reset-hint">{{ t("ssh.hostkey_reset_hint") }}</div>
            <n-popconfirm @positive-click="resetHostKey">
              <template #trigger>
                <n-button size="small" type="error" ghost :loading="resettingKey" :disabled="resettingKey">
                  <template #icon><n-icon :component="DeleteIcon" /></template>{{ t("ssh.hostkey_reset") }}
                </n-button>
              </template>
              {{ t("ssh.hostkey_reset_confirm") }}
            </n-popconfirm>
          </div>
        </n-alert>

        <!-- 已存帳密（個人保管）：選一筆即以 reference 連線 -->
        <div v-if="creds.length" class="sftp-saved-row">
          <span class="sftp-saved-label">{{ t("ssh.saved_cred") }}</span>
          <n-select v-model:value="form.credential_id" :options="credOptions" clearable size="small"
                    :placeholder="t('ssh.saved_cred_ph')" style="flex:1" />
          <n-popconfirm v-if="form.credential_id" @positive-click="delSelectedCred">
            <template #trigger>
              <n-button quaternary type="error" size="small">
                <template #icon><n-icon :component="DeleteIcon" /></template>
              </n-button>
            </template>
            {{ t("ssh.saved_cred_del_confirm") }}
          </n-popconfirm>
        </div>

        <n-form label-placement="left" :label-width="92" size="small">
          <!-- 手動輸入（未選已存帳密時才顯示）-->
          <template v-if="!form.credential_id">
            <n-form-item :label="t('ssh.auth_method')">
              <n-radio-group v-model:value="form.auth">
                <n-radio value="password">{{ t("ssh.auth_password") }}</n-radio>
                <n-radio value="key">{{ t("ssh.auth_key") }}</n-radio>
              </n-radio-group>
            </n-form-item>
            <n-form-item :label="t('ssh.username')">
              <n-input v-model:value="form.username" placeholder="root" autofocus
                       @keyup.enter="connect" />
            </n-form-item>
            <n-form-item v-if="form.auth === 'password'" :label="t('ssh.password')">
              <n-input v-model:value="form.password" type="password" show-password-on="click"
                       @keyup.enter="connect" />
            </n-form-item>
            <template v-else>
              <n-form-item :label="t('ssh.private_key')">
                <n-input v-model:value="form.private_key" type="textarea"
                         :autosize="{ minRows: 4, maxRows: 8 }"
                         placeholder="-----BEGIN OPENSSH PRIVATE KEY-----" />
              </n-form-item>
              <n-form-item :label="t('ssh.passphrase')">
                <n-input v-model:value="form.passphrase" type="password" show-password-on="click" />
              </n-form-item>
            </template>
          </template>

          <n-form-item :label="t('ssh.port')">
            <n-input-number v-model:value="form.port" :min="1" :max="65535" style="width:140px" />
          </n-form-item>

          <!-- 記住此帳密（僅手動模式）-->
          <n-form-item v-if="!form.credential_id" :label="t('ssh.remember')">
            <n-space vertical :size="4" style="width:100%">
              <n-switch v-model:value="remember" />
              <n-input v-if="remember" v-model:value="rememberLabel" size="small"
                       :placeholder="t('ssh.remember_label_ph')" />
            </n-space>
          </n-form-item>

          <!-- 按連線之前就看得到會走哪條路（直連／跳板／掃描代理）、走不通的話原因 -->

          <ConsoleRouteNote :address-id="props.addressId" />

          <n-alert :show-icon="false" type="info" style="margin-bottom:10px">
            {{ form.credential_id ? t("ssh.use_saved_hint") : (remember ? t("ssh.store_hint") : t("ssh.no_store_hint")) }}
          </n-alert>
          <n-space justify="end">
            <n-button type="primary" :loading="connecting" @click="connect">
              <template #icon><n-icon :component="FilesIcon" /></template>
              {{ t("sftp.connect") }}
            </n-button>
          </n-space>
        </n-form>
      </n-card>
    </div>

    <!-- 已連線／已中斷：狀態列 + 檔案清單（狀態列與 SSH 主控台同一套） -->
    <div v-else class="sftp-area" :class="{ 'sftp-full': fullHeight }">
      <div class="sftp-toolbar">
        <span class="sftp-status" :data-state="phase">
          <n-spin v-if="phase === 'connecting'" :size="12" />
          <span v-else class="sftp-dot" />
          <span>{{ t(`ssh.state_${phase}`) }}</span>
          <span class="sftp-ip">{{ host }}</span>
          <n-tag v-if="hostname" size="small" :bordered="false" round>{{ hostname }}</n-tag>
          <span class="conn-proto conn-proto--sftp">SFTP</span>
          <n-tag v-if="viaJump" size="small" type="warning" :bordered="false" round>
            {{ viaKind === "agent" ? t("relay.via_agent") : t("jump_hosts.via") }}：{{ viaJump }}
          </n-tag>
          <n-tag v-if="deviceName" size="small" type="info" :bordered="false" round>{{ deviceName }}</n-tag>
          <ConnElapsed :active="phase === 'connected'" />
        </span>
        <n-space :size="8" align="center">
          <n-button v-if="phase === 'connected'" size="tiny" type="error" ghost @click="disconnect">
            <template #icon><n-icon :component="CancelIcon" /></template>{{ t("ssh.disconnect") }}
          </n-button>
          <n-button v-else size="tiny" type="primary" ghost @click="reconnect"><template #icon><n-icon><RefreshIcon /></n-icon></template>
            {{ t("ssh.reconnect") }}
          </n-button>
        </n-space>
      </div>

      <!-- 檔案操作區：路徑、操作與清單包在同一個框裡（狀態列刻意留在框外，
           與 SSH 主控台一致：那一列講的是連線，不是檔案）。 -->
      <div class="sftp-panel"
           :class="{ 'sftp-full': fullHeight, 'sftp-dropping': dragging,
                     'sftp-offline': phase !== 'connected' }"
           @dragenter="onDragEnter" @dragover="onDragOver"
           @dragleave="onDragLeave" @drop="onDrop">
        <!-- 拖曳中：整塊面板都是放置區，並講明會放到哪個目錄 -->
        <div v-if="dragging" class="sftp-dropzone">
          <n-icon :size="28"><UploadIcon /></n-icon>
          <span>{{ t("sftp.drop_here", { dir: cwd }) }}</span>
        </div>
        <!-- 連線中斷：整塊面板一次遮起來，不逐塊套樣式。
             逐塊套的話總會漏掉一塊（分頁列、錯誤列都曾經還亮著、還能點），
             而且「能點但送不出去」比「明顯不能點」更難懂。 -->
        <div v-if="phase !== 'connected'" class="sftp-offline-mask">
          <div class="sftp-offline-box">
            <n-icon :size="24"><CancelIcon /></n-icon>
            <span class="sftp-offline-title">{{ t("sftp.offline_title") }}</span>
            <span class="sftp-offline-hint">{{ t("sftp.offline_hint") }}</span>
            <span v-if="uploadBytes" class="sftp-offline-code">
              {{ t("sftp.offline_upload", {
                name: uploadBytes.name,
                sent: fmtBytes(uploadBytes.sent), total: fmtBytes(uploadBytes.total) }) }}
            </span>
            <span v-if="closeCode !== null" class="sftp-offline-code">
              {{ t("sftp.offline_code", { code: closeCode }) }}
            </span>
            <n-button size="small" type="primary" @click="reconnect"><template #icon><n-icon><RefreshIcon /></n-icon></template>
              {{ t("ssh.reconnect") }}
            </n-button>
          </div>
        </div>
      <n-alert v-if="errorMsg" type="error" :bordered="false" style="margin-bottom:8px">
        {{ errorMsg }}
      </n-alert>

      <!-- 路徑列與操作 -->
      <n-space align="center" class="sftp-pathbar">
        <n-button size="small" :disabled="cwd === '/'" @click="goUp">
          <template #icon><n-icon><UpLevelIcon /></n-icon></template>
          {{ t("sftp.up") }}
        </n-button>
        <n-input :value="cwd" class="mono" style="width: 320px"
                 @update:value="(v: string) => (cwd = v)"
                 @keyup.enter="() => refresh()" />
        <n-button size="small" :loading="busy" @click="() => refresh()">
          <template #icon><n-icon><RefreshIcon /></n-icon></template>
          {{ t("common.refresh") }}
        </n-button>
        <n-button size="small" @click="doMkdir">
          <template #icon><n-icon><NewFolderIcon /></n-icon></template>
          {{ t("sftp.new_folder") }}
        </n-button>
        <n-button size="small" type="primary" @click="() => uploadInput?.click()">
          <template #icon><n-icon><UploadIcon /></n-icon></template>
          {{ t("sftp.upload") }}
        </n-button>
        <input ref="uploadInput" type="file" multiple style="display:none" @change="onUpload" />
        <!-- 篩選只作用在目前這個目錄的清單（列出來的就是全部，不必回遠端重撈） -->
        <n-input v-model:value="filterText" clearable style="width: 200px"
                 :placeholder="t('sftp.filter_ph')">
          <template #prefix><n-icon><FilterIcon /></n-icon></template>
        </n-input>
        <!-- 排序方式：兩派都有道理（檔案總管 vs ls），所以讓使用者自己選；存進偏好跨裝置 -->
        <n-tooltip trigger="hover">
          <template #trigger>
            <!-- 尺寸與 icon 都比照旁邊的篩選框：工具列上少一個 icon、矮一截都看得出來 -->
            <n-select :value="dirsFirst ? 'dirs' : 'mixed'" style="width: 190px"
                      :options="sortModeOptions" :render-tag="renderSortTag"
                      @update:value="(v: string) => setDirsFirst(v === 'dirs')" />
          </template>
          {{ t("sftp.sort_mode_hint") }}
        </n-tooltip>
      </n-space>

      <!-- 勾選後才出現的批次列：沒選東西時不佔版面，也不會讓人誤按 -->
      <n-space v-if="checkedKeys.length" align="center" class="sftp-batchbar">
        <span class="sftp-batch-count">{{ t("sftp.batch_selected", { n: checkedKeys.length }) }}</span>
        <n-button size="small" :loading="busy" @click="batchDownload">
          <template #icon><n-icon><DownloadIcon /></n-icon></template>
          {{ t("sftp.batch_download") }}
        </n-button>
        <n-button size="small" :loading="busy" @click="openMoveDlg">
          <template #icon><n-icon><MoveIcon /></n-icon></template>
          {{ t("sftp.batch_move") }}
        </n-button>
        <n-popconfirm @positive-click="batchDelete">
          <template #trigger>
            <n-button size="small" type="error" secondary :loading="busy">
              <template #icon><n-icon><DeleteIcon /></n-icon></template>
              {{ t("sftp.batch_delete") }}
            </n-button>
          </template>
          {{ t("sftp.batch_delete_confirm", { n: checkedKeys.length }) }}
        </n-popconfirm>
        <n-button size="small" quaternary @click="clearSelection">
          <template #icon><n-icon><CancelIcon /></n-icon></template>
          {{ t("sftp.batch_clear") }}
        </n-button>
      </n-space>

      <!-- 下載進度：幾 GB 的檔案要看得出在動 -->
      <div v-if="downloadBytes && phase === 'connected'" class="sftp-upload-note sftp-download-note">
        {{ t("sftp.download_bytes", {
          name: downloadBytes.name,
          got: fmtBytes(downloadBytes.got), total: fmtBytes(downloadBytes.total),
          pct: Math.floor((downloadBytes.got / Math.max(1, downloadBytes.total)) * 100) }) }}<span
          class="sftp-rate" data-testid="sftp-download-rate">{{ rateText(downMeter, downloadBytes.total) }}</span>
      </div>
      <!-- 多檔上傳時講出進度：不然畫面只是卡著，不知道還有幾個 -->
      <div v-if="uploadBytes && phase === 'connected'" class="sftp-upload-note">
        {{ t("sftp.upload_bytes", {
          name: uploadBytes.name,
          sent: fmtBytes(uploadBytes.sent), total: fmtBytes(uploadBytes.total),
          pct: Math.floor((uploadBytes.sent / Math.max(1, uploadBytes.total)) * 100) }) }}<span
          class="sftp-rate" data-testid="sftp-upload-rate">{{ rateText(upMeter, uploadBytes.total) }}</span>
      </div>
      <div v-if="uploadProgress && uploadProgress.total > 1" class="sftp-filter-note">
        {{ t("sftp.uploading_progress", {
          done: uploadProgress.done + 1, total: uploadProgress.total, name: uploadProgress.name }) }}
      </div>

      <!-- 截斷要明講：畫面上少幾千個檔案而不說，等於騙人 -->
      <n-alert v-if="truncated" type="warning" :bordered="false" style="margin-bottom: 8px">
        {{ t("sftp.truncated") }}
      </n-alert>
      <!-- 篩選掉了多少也要說，否則會以為目錄裡就只有這幾個 -->
      <div v-if="filterText.trim()" class="sftp-filter-note">
        {{ t("sftp.filter_note", { shown: shownEntries.length, total: entries.length }) }}
      </div>

      <!-- 名稱輸入（新增資料夾 / 重新命名）：應用程式自己的對話框 -->
      <n-modal v-model:show="nameDlgShow" preset="card" style="width: 420px; max-width: 92vw"
               :title="nameDlgTitle">
        <n-input v-if="nameDlg" v-model:value="nameDlg.value" autofocus
                 :placeholder="t('sftp.name_ph')" @keyup.enter="submitNameDlg" />
        <template #footer>
          <n-space justify="end">
            <n-button @click="nameDlg = null"><template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}</n-button>
            <n-button type="primary" :loading="busy" @click="submitNameDlg"><template #icon><n-icon><CheckIcon /></n-icon></template>
              {{ t("common.confirm") }}
            </n-button>
          </n-space>
        </template>
      </n-modal>

      <!-- 資料夾有內容：刪掉整棵樹是破壞性的，要明確問過，並且把數量講出來 -->
      <!-- 遠端已有同名檔案：覆蓋／兩份都留／略過（多檔時可套用到其餘） -->
      <n-modal :show="!!conflictPrompt" preset="card" style="width: 480px; max-width: 92vw"
               :title="t('sftp.conflict_title')" :mask-closable="false" data-testid="sftp-conflict"
               @update:show="(v: boolean) => { if (!v) answerConflict('skip'); }">
        <div style="line-height: 1.7">
          {{ t("sftp.conflict_body", { name: conflictPrompt?.name ?? "", size: fmtBytes(conflictPrompt?.size ?? 0),
                                     mtime: conflictPrompt?.mtime ? fmtDateTime(conflictPrompt.mtime * 1000) : "—" }) }}
        </div>
        <div class="sftp-conflict-note">{{ t("sftp.conflict_safe") }}</div>
        <n-checkbox v-if="(conflictPrompt?.remaining ?? 0) > 0" v-model:checked="conflictAll" style="margin-top: 10px">
          {{ t("sftp.conflict_apply_all") }}
        </n-checkbox>
        <template #footer>
          <n-space justify="end">
            <n-button data-testid="sftp-conflict-skip" @click="answerConflict('skip')"><template #icon><n-icon><MoveIcon /></n-icon></template>{{ t("sftp.conflict_skip") }}</n-button>
            <n-button data-testid="sftp-conflict-rename" @click="answerConflict('rename')"><template #icon><n-icon><CopyIcon /></n-icon></template>{{ t("sftp.conflict_keep_both") }}</n-button>
            <n-button type="warning" data-testid="sftp-conflict-overwrite" @click="answerConflict('overwrite')"><template #icon><n-icon><SaveIcon /></n-icon></template>
              {{ t("sftp.conflict_overwrite") }}
            </n-button>
          </n-space>
        </template>
      </n-modal>
      <n-modal :show="!!confirmRecursive" preset="card" style="width: 460px; max-width: 92vw"
               :title="t('sftp.delete_dir_title')" @update:show="(v: boolean) => {
                 if (!v) confirmRecursive = null;
               }">
        <n-space vertical size="small">
          <span>{{ confirmRecursive?.detail }}</span>
          <n-alert type="warning" :bordered="false" :show-icon="true">
            {{ t("sftp.delete_dir_warning") }}
          </n-alert>
        </n-space>
        <template #footer>
          <n-space justify="end">
            <n-button @click="confirmRecursive = null"><template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}</n-button>
            <n-button type="error" :loading="busy" @click="doDeleteRecursive"><template #icon><n-icon><DeleteIcon /></n-icon></template>
              {{ t("sftp.delete_dir_confirm") }}
            </n-button>
          </n-space>
        </template>
      </n-modal>

      <!-- 移動：可直接打路徑，也可以點目錄一層層瀏覽 -->
      <n-modal v-model:show="moveDlg" preset="card" style="width: 560px; max-width: 94vw"
               :title="t('sftp.move_title', { n: checkedKeys.length })">
        <n-space vertical :size="10" style="width:100%">
          <n-input-group>
            <n-input v-model:value="moveDest" class="mono" :placeholder="t('sftp.move_path_ph')"
                     @keyup.enter="browseMove(moveDest)" />
            <n-button :loading="moveLoading" @click="browseMove(moveDest)"><template #icon><n-icon><MoveIcon /></n-icon></template>
              {{ t("sftp.move_go") }}
            </n-button>
          </n-input-group>
          <div class="move-tree">
            <div class="move-row" @click="moveUp">
              <n-icon :size="16"><UpLevelIcon /></n-icon><span>{{ t("sftp.up") }}</span>
            </div>
            <div v-for="d in moveDirs" :key="d.path" class="move-row" @click="browseMove(d.path)">
              <n-icon :size="16" color="#18a058"><FilesIcon /></n-icon><span>{{ d.name }}</span>
            </div>
            <!-- 空目錄也要講出來，不然會以為是壞掉了 -->
            <div v-if="!moveDirs.length && !moveLoading" class="move-empty">
              {{ t("sftp.move_no_subdirs") }}
            </div>
          </div>
        </n-space>
        <template #footer>
          <n-space justify="end">
            <n-button @click="moveDlg = false"><template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}</n-button>
            <n-button type="primary" :loading="busy" @click="confirmMove"><template #icon><n-icon><MoveIcon /></n-icon></template>
              {{ t("sftp.move_here", { dir: moveDest }) }}
            </n-button>
          </n-space>
        </template>
      </n-modal>

      <div class="sftp-table" :class="{ 'sftp-full': fullHeight }">
        <n-data-table :columns="cols" :data="shownEntries" :loading="busy" size="small"
                      :row-key="(r: SftpEntry) => r.path"
                      :checked-row-keys="checkedKeys"
                      :pagination="pagination"
                      :bordered="false" flex-height style="height:100%" virtual-scroll
                      @update:checked-row-keys="(k: any) => (checkedKeys = k as string[])"
                      @update:sorter="onSorterChange" />
      </div>
      <div v-if="moreEntries > 0" class="file-more" data-testid="sftp-more">
        <n-button size="small" @click="nextPage">
          {{ t("sftp.more_on_next_page", { n: nextCount, total: moreEntries }) }}
          <template #icon><n-icon><ChevronRightIcon /></n-icon></template>
        </n-button>
      </div>
      </div>
    </div>

    <!-- 主機金鑰 TOFU 確認（與 SSH 主控台相同） -->
    <n-modal v-model:show="hostKeyAsk" preset="dialog" :title="t('ssh.hostkey_title')"
             :positive-text="t('ssh.hostkey_trust')" :negative-text="t('common.cancel')"
             @positive-click="acceptHostKey" @negative-click="rejectHostKey"
             :mask-closable="false">
      <div style="line-height:1.6">
        <div>{{ t("ssh.hostkey_body", { ip: host }) }}</div>
        <code class="ssh-fp">{{ hostKeyFp }}</code>
        <div style="opacity:.7;font-size:12px">{{ t("ssh.hostkey_hint") }}</div>
      </div>
    </n-modal>
  </div>
</template>

<style scoped>
.hostkey-reset { margin-top: 8px; display: flex; flex-direction: column; align-items: flex-start; gap: 6px; }
.hostkey-reset-hint { font-size: 12px; opacity: .85; }
.ssh-fp { display: block; margin: 8px 0; padding: 6px 8px; background: rgba(128,128,128,.12);
  border-radius: 4px; word-break: break-all; font-size: 13px; }
.sftp-conflict-note { margin-top: 8px; font-size: 12px; opacity: .7; line-height: 1.6; }
/* 版面與 SshTerminal 對齊：全頁模式時表單置中、內容區填滿剩餘高度 */
.sftp-wrap { width: 100%; }
.sftp-wrap.sftp-full { height: 100%; display: flex; flex-direction: column; }
.sftp-wrap.sftp-center { justify-content: center; align-items: center; }
.sftp-wrap.sftp-center .sftp-form { width: 560px; max-width: 92vw; }
.sftp-form { max-width: 560px; }
.sftp-area { display: flex; flex-direction: column; }
/* 檔案操作區的外框：與 SSH 終端機那個深色框同一個角色 —— 把「這一塊是遠端主機的內容」
   框起來，狀態列留在框外。 */
.sftp-panel { border: 1px solid rgba(128, 128, 128, .28); border-radius: 8px;
  padding: 10px; background: #fff; box-shadow: 0 1px 3px rgba(0, 0, 0, .06);
  display: flex; flex-direction: column; min-height: 0; }
.sftp-panel.sftp-full { flex: 1; }
/* 拖曳中：整塊面板變成放置區 */
.sftp-panel { position: relative; }
.sftp-panel.sftp-dropping { border-color: #18a058; border-style: dashed; }
.sftp-dropzone { position: absolute; inset: 0; z-index: 5; display: flex; gap: 10px;
  flex-direction: column; align-items: center; justify-content: center; pointer-events: none;
  background: rgba(24, 160, 88, .10); color: #18a058; font-weight: 600; border-radius: 8px; }
html[data-theme="dark"] .sftp-panel { background: #10161f; border-color: rgba(200, 210, 230, .18);
  box-shadow: none; }
.sftp-area.sftp-full { flex: 1; min-height: 0; }
.sftp-table { height: 420px; }
.sftp-table.sftp-full { flex: 1; height: auto; min-height: 0; }
.sftp-pathbar { margin-bottom: 10px; }

/* 狀態列 —— 與 SSH 主控台同一套（同樣的圓角膠囊、同樣的狀態配色） */
.sftp-toolbar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap;
  padding: 4px 2px; gap: 8px; margin-bottom: 8px; }
.sftp-status { font-size: 13px; display: inline-flex; align-items: center; gap: 7px;
  padding: 3px 11px; border-radius: 999px; font-weight: 500;
  background: rgba(128, 128, 128, .12); color: #888; }
/* 手機：內容放不下時整顆標籤換到下一行，不要把「連線錯誤」擠成直排、也不要超出畫面 */
.sftp-status { flex-wrap: wrap; row-gap: 4px; max-width: 100%; min-width: 0; }
.sftp-status > * { flex: none; max-width: 100%; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
@media (max-width: 640px) { .sftp-status { border-radius: 14px; } }
.sftp-dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; flex: none; }
.sftp-ip { opacity: .7; font-variant-numeric: tabular-nums; }
.sftp-status[data-state="connected"] { color: #18a058; background: rgba(24, 160, 88, .14); }
.sftp-status[data-state="connected"] .sftp-dot { animation: sftp-pulse 1.8s infinite; }
.sftp-status[data-state="connecting"] { color: #d99812; background: rgba(217, 152, 18, .14); }
.sftp-status[data-state="error"] { color: #d03050; background: rgba(208, 48, 80, .14); }
.sftp-status[data-state="closed"] { color: #888; background: rgba(128, 128, 128, .14); }
@keyframes sftp-pulse {
  0%   { box-shadow: 0 0 0 0 rgba(24, 160, 88, .5); }
  70%  { box-shadow: 0 0 0 6px rgba(24, 160, 88, 0); }
  100% { box-shadow: 0 0 0 0 rgba(24, 160, 88, 0); }
}
/* 協定標籤（與 SSH 的 conn-proto 同一套，換個色） */
.conn-proto { font-weight: 700; font-size: 11px; letter-spacing: .4px; line-height: 1;
  padding: 2px 7px; border-radius: 999px; }
.conn-proto--sftp { color: #2080f0; background: rgba(32,128,240,.16); }
/* 已中斷：反灰並停用互動，讓使用者一眼看出連線沒了（與 SSH 相同處理） */
.term-dim { filter: grayscale(1) brightness(.55); pointer-events: none; transition: filter .25s; }
/* 連線中斷：面板整片罩住。用 ::after 疊一層半透明底，內容維持在下面看得到
   （知道原本有什麼），但整片不可互動 —— 逐塊 disabled 一定會漏掉某一塊。 */
.sftp-panel.sftp-offline { position: relative; }
.sftp-panel.sftp-offline > *:not(.sftp-offline-mask) {
  filter: grayscale(1) brightness(.6);
  pointer-events: none;
  user-select: none;
}
.sftp-offline-mask {
  position: absolute; inset: 0; z-index: 3;
  display: flex; align-items: center; justify-content: center;
  background: rgba(128, 128, 128, .18);
  backdrop-filter: blur(1.5px);
  border-radius: inherit;
}
.sftp-offline-box {
  display: flex; flex-direction: column; align-items: center; gap: 8px;
  padding: 20px 26px; border-radius: 10px;
  background: var(--jt-card-bg, #fff);
  box-shadow: 0 6px 24px rgba(0, 0, 0, .18);
  max-width: 420px; text-align: center;
}
.sftp-offline-title { font-weight: 600; }
.sftp-offline-code {
  font-size: 12px;
  opacity: 0.65;
  font-family: var(--jt-mono, ui-monospace, SFMono-Regular, Menlo, Consolas, monospace);
}
.sftp-offline-hint { font-size: 12.5px; opacity: .75; line-height: 1.5; }
:deep(.n-card > .n-card-header) { display: flex; align-items: center; padding-top: 12px; padding-bottom: 12px; }
.sftp-saved-row { display: flex; align-items: center; margin-bottom: 18px; }
.sftp-saved-label { width: 92px; flex: none; box-sizing: border-box; text-align: right;
  padding-right: 12px; font-size: 14px; }
.sftp-saved-row :deep(.n-button) { margin-left: 6px; }
.mono :deep(input) { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12.5px; }
/* 表格內的名稱由 render function 產生（不帶 data-v），所以要用 :deep 才套得到；
   對齊本身走行內樣式，見 cols 的 render。 */
:deep(.sftp-dir) { color: var(--primary-color, #18a058); cursor: pointer; font-weight: 600; }
:deep(.sftp-file) { cursor: default; }
.sftp-batchbar { margin-bottom: 10px; padding: 6px 10px; border-radius: 6px;
  background: rgba(32, 128, 240, .08); }
.sftp-batch-count { font-size: 13px; font-weight: 500; }
.sftp-filter-note { font-size: 12px; opacity: .7; margin-bottom: 6px; }
/* 上傳進度自己一個 class：跟篩選提示共用時，「畫面上有幾個提示」這種
   斷言會同時命中兩個，測試會以 strict mode 失敗（實際踩過）。 */
.sftp-upload-note { font-size: 12px; opacity: .7; margin-bottom: 6px; }
.file-more { display: flex; justify-content: flex-start; padding: 6px 2px 0; }
</style>
