<script setup lang="ts">
/**
 * 戰情室（大螢幕看板）外框：JT Matrix v1.1 風格。規格：docs/SPEC_WARROOM_zh-TW.md（內部）。
 *
 * - 參考畫布 1920×1080，依視窗等比縮放（16:9 以外留黑邊）；4K 電視同一套版面
 * - 共用頁首（品牌、範圍、時間、資料時間）與頁尾（主題頁碼、資料子頁碼）
 * - 輪播：主題頁碼與資料子頁碼分開，每個主題最多播 max_subpages 頁（utils/warroom.nextStep）
 * - 鍵盤（使用者 10-22 調整）：數字鍵跳主題、← → 換主題、↑ ↓／PageUp PageDown 換資料頁、
 *   空白鍵暫停（畫面閃一下表示收到）、? 顯示按鍵說明；滑鼠點頁尾也能切。
 *   滑鼠一動才顯示游標與按鍵提示，電視上不會有殘留的游標
 * - 換頁動畫：換主題時標題與內容一起滑入（前進往左、後退往右），同主題換資料頁只換內容；
 *   降低動畫時直接切換。頁尾上緣的進度線是距離下次自動換頁的時間
 * - 連不上時保留上一次的資料，並標「顯示的是 HH:MM 的資料」，不假裝即時
 * - 進入方式：一般登入，或網址帶 ?display=<顯示權杖>（存進這個分頁後立刻從網址列拿掉）
 */
import { computed, nextTick, onBeforeUnmount, onMounted, provide, ref, watch } from "vue";
import { keyAction, warroomKeyRows } from "@/utils/warroomKeys";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import interUrl from "@fontsource-variable/inter/files/inter-latin-wght-normal.woff2?url";
import {
  displayToken, getIntel, getWarroomConfig, getWarroomPage, setDisplayToken, type PageData, type WarroomConfig,
} from "@/api/warroom";
import { useAuthStore } from "@/stores/auth";
import { nextStep, pad2, pageStep, topicByNumber, topicStep, type Step } from "@/utils/warroom";
import WrAnomalies from "./WrAnomalies.vue";
import WrStats from "./WrStats.vue";
import WrRelations from "./WrRelations.vue";
import WrAvailability from "./WrAvailability.vue";
import WrAiAudit from "./WrAiAudit.vue";
import WrAiIntel from "./WrAiIntel.vue";
import WrIco from "./WrIco.vue";
import { startAutoReload, takeResume } from "./autoReload";
import { loadNumberFont, NUMBER_FONTS } from "./warroomFonts";
import { WarnIcon } from "@/icons";
import WrAssets from "./WrAssets.vue";
import WrCapacity from "./WrCapacity.vue";
import WrCollection from "./WrCollection.vue";
import WrMetric from "./WrMetric.vue";
import WrFx from "./WrFx.vue";
import WrOverview from "./WrOverview.vue";
import WrServices from "./WrServices.vue";
import WrTopology from "./WrTopology.vue";

const route = useRoute();
const router = useRouter();
const { t, locale } = useI18n();

const cfg = ref<WarroomConfig | null>(null);
const step = ref<Step>({ key: "overview", page: 1 });
const data = ref<PageData | null>(null);
const fatal = ref<string | null>(null);           // 無法顯示（權杖無效、功能關閉）
const offlineSince = ref<string | null>(null);    // 連不上：顯示的是這個時間的資料
/** 資料逾時：連不上，或資料時間落後超過「三輪換頁且至少 3 分鐘」（每次換頁都會重抓，正常不會落後這麼多） */
const dataStale = computed(() => {
  if (offlineSince.value) return true;
  const at = data.value?.data_at ? new Date(data.value.data_at).getTime() : NaN;
  if (Number.isNaN(at)) return false;
  const limit = Math.max(3 * (cfg.value?.dwell_seconds ?? 60), 180) * 1000;
  return now.value.getTime() - at > limit;
});
const paused = ref(false);
// 釘選（P）：不換主題，同一主題的資料頁照常輪播、資料照常更新；再按一次恢復輪主題（10-26 使用者）
const pinned = ref(false);
const dir = ref<"fwd" | "back">("fwd");            // 換頁動畫的方向
const cycle = ref(0);                             // 每次重新計時加一：讓進度線從頭跑
const showHelp = ref(false);
const operator = ref(false);                      // 有人在操作（滑鼠剛動過）：顯示游標（按鍵提示 10-22 起常態顯示）
const scale = ref(1);
const now = ref(new Date());
const reduceMotion = computed(() => !!cfg.value?.reduce_motion
  || (typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches));
provide("wrReduceMotion", reduceMotion);
provide("wrPageDir", dir);                       // 同主題換資料頁：各主題自己的分頁區塊用（paged.ts）
provide("wrPaused", paused);
/** 資訊展示模式（11-03）：只呈現現況與數量。問題資料後端已經拿掉，這裡收掉剩下的警示色與頁首的逾時紅字 */
const infoMode = computed(() => !!cfg.value?.info_mode);
provide("wrInfoMode", infoMode);
// 12 AI 情報的顯示設定（版面、配色、打字速度…）
provide("wrIntel", computed(() => cfg.value?.intel));                     // 主題內自己輪播的區塊（AI 巡檢的焦點）暫停時也要停

const PAGE_COMPONENTS: Record<string, unknown> = {
  overview: WrOverview, capacity: WrCapacity, topology: WrTopology, services: WrServices,
  anomalies: WrAnomalies, assets: WrAssets, collection: WrCollection,
  stats: WrStats, relations: WrRelations, availability: WrAvailability, ai_audit: WrAiAudit, ai_intel: WrAiIntel,
};
const playlist = computed(() => cfg.value?.pages ?? []);
/** 釘選而且這個主題只有一頁：到時間也不會換任何東西 */
const idleSweep = computed(() => pinned.value && (data.value?.pages ?? 1) <= 1);
/** 頁尾主題列：只寫名稱、不加編號（10-26 使用者：「主題名稱不需要加編號」；數字鍵照播放順序跳）。
 *  放不下時分段收：1 縮小項目間距 → 2 字再小一級 → 3 再小一級 → 4 才把沒在播的名稱截短（全名在 title）。
 *  畫布固定 1920 寬，只跟清單、語言、字型、右邊的頁碼文字有關 */
const navEl = ref<HTMLElement | null>(null);
const navLevel = ref(0);
const navCompact = computed(() => navLevel.value >= 4);
async function fitNav(from = 0) {
  for (let lv = from; lv <= 4; lv++) {
    navLevel.value = lv;
    await nextTick();
    const el = navEl.value;
    if (!el || el.scrollWidth <= el.clientWidth + 1) return;
  }
}
watch([playlist, locale], () => { void fitNav(0); }, { flush: "post" });
// 右邊的頁碼文字（例如「子網路 01–04 / 8」）出現或變長時可用寬度會變：主題列是 flex:1，寬度只跟右邊有關，不會自己觸發迴圈
let navObserver: ResizeObserver | null = null;
watch(navEl, (el) => {
  navObserver?.disconnect();
  if (el && typeof ResizeObserver !== "undefined") {
    let w = el.clientWidth;
    // 只往收的方向調（從目前這一段往上找）：換到沒有頁碼的主題時不再放回去，免得每換一個主題頁尾就跳一次
    navObserver = new ResizeObserver(() => { if (el.clientWidth !== w) { w = el.clientWidth; void fitNav(navLevel.value); } });
    navObserver.observe(el);
  }
});
const posOf = (key: string) => playlist.value.indexOf(key) + 1;   // 播放順序（清單可拖拉排序）；數字鍵照這個跳

let rotateTimer: ReturnType<typeof setTimeout> | null = null;
let clockTimer: ReturnType<typeof setInterval> | null = null;
let stopAutoReload: (() => void) | null = null;
let operatorTimer: ReturnType<typeof setTimeout> | null = null;
let loadSeq = 0;

function fit() {
  scale.value = Math.min(window.innerWidth / 1920, window.innerHeight / 1080);
}

function errCode(e: unknown): string | null {
  const d = (e as { response?: { status?: number; data?: { detail?: unknown; detail_code?: string } } })?.response;
  if (!d) return null;
  const code = d.data?.detail_code ?? (typeof d.data?.detail === "object" ? (d.data?.detail as { code?: string })?.code : null);
  return code ?? (d.status === 401 ? "warroom_token_invalid" : d.status === 403 ? "warroom_disabled" : null);
}

async function load(s: Step) {
  const seq = ++loadSeq;
  try {
    const d = await getWarroomPage(s.key, s.page);
    if (seq !== loadSeq) return;                  // 連按好幾下：只採最後一次，不讓先發後到的蓋掉
    data.value = d;                               // 動畫交給 <Transition>（舊的滑出後新的才滑入）
    // 12 AI 情報設成「固定顯示此主題」：播到它就釘住（跟按 P 一樣，再按 P 回到輪播）
    if (s.key === "ai_intel" && step.value.key !== "ai_intel" && cfg.value?.intel?.playback === "pinned") pinned.value = true;
    step.value = { key: s.key, page: d.page };
    offlineSince.value = null;
  } catch (e) {
    const code = errCode(e);
    if (code && /^warroom_(token|disabled)/.test(code)) {
      fatal.value = code;
      return;
    }
    // 連不上：保留畫面上的資料，標出它是幾點的
    if (data.value && !offlineSince.value) offlineSince.value = data.value.data_at;
  }
}

/** 管理員改了戰情室設定（停留秒數、播放清單、語言…），已經開著的電視也要跟著換：
 *  換頁時順便重讀，最多每分鐘一次；讀不到就沿用原本的設定（10-24「改了秒數已經開的畫面會套用嗎」） */
let cfgReadAt = Date.now();
async function refreshConfig() {
  if (Date.now() - cfgReadAt < 60_000) return;
  cfgReadAt = Date.now();
  try {
    const c = await getWarroomConfig();
    if (JSON.stringify(c) !== JSON.stringify(cfg.value)) {
      cfg.value = c;
      if (c.language && c.language !== locale.value) locale.value = c.language;
    }
  } catch { /* 暫時讀不到設定：照舊播 */ }
}

/** 停在這個主題多久：12 AI 情報有自己的停留秒數（跟 AI 更新間隔、文字速度、其他主題的停留都分開） */
function dwellOf(key: string): number {
  return key === "ai_intel" && cfg.value?.intel ? cfg.value.intel.dwell_seconds : (cfg.value?.dwell_seconds ?? 20);
}

function schedule() {
  if (rotateTimer) clearTimeout(rotateTimer);
  cycle.value++;
  if (!cfg.value || fatal.value) return;
  if (paused.value) {
    // 暫停只是不換頁，資料照樣更新：停在某一頁的電視不可以一直顯示舊數字（頁首「資料 HH:MM」會跟著走）。
    // 同一頁重抓，主題與頁碼不變，所以不會播換頁動畫（10-22 使用者問「這裡時間何時變動」時發現）
    rotateTimer = setTimeout(async () => { await refreshConfig(); await load(step.value); schedule(); },
                             Math.max(dwellOf(step.value.key), 30) * 1000);
    return;
  }
  // 12 AI 情報輪播時播完一輪才換（使用者 10-26）：不倒數，等播報那邊說播完；10 分鐘是保險上限
  intelWaiting.value = step.value.key === "ai_intel" && !pinned.value;
  rotateTimer = setTimeout(advance, (intelWaiting.value ? INTEL_MAX_SECONDS : dwellOf(step.value.key)) * 1000);
}

async function advance() {
  if (rotateTimer) clearTimeout(rotateTimer);
  await refreshConfig();
  if (!playlist.value.length) { fatal.value = "warroom_no_pages"; return; }
  const pages = data.value?.key === step.value.key ? data.value.pages : 1;
  dir.value = "fwd";
  // 釘選中只在這個主題的資料頁之間循環（只有一頁就原地更新）；主題被拿出播放清單就恢復輪播
  if (pinned.value && playlist.value.includes(step.value.key)) await load(pageStep(step.value, pages, 1));
  else await load(nextStep(playlist.value, step.value, pages, cfg.value!.max_subpages));
  schedule();
}

/** AI 情報那邊回報：done＝每一則都播過一輪了 → 換下一個主題；empty＝還沒有東西可播 → 改照停留秒數 */
const INTEL_MAX_SECONDS = 600;
const intelWaiting = ref(false);
provide("wrIntelSignal", (kind: "done" | "empty" | "refresh") => {
  // 換上新的一批：重抓標題列的指標與資料時間（同一頁重抓，不播換頁動畫）
  if (kind === "refresh") { if (step.value.key === "ai_intel" && !fatal.value) void load(step.value); return; }
  if (!intelWaiting.value || paused.value || step.value.key !== "ai_intel") return;
  intelWaiting.value = false;
  if (rotateTimer) clearTimeout(rotateTimer);
  if (kind === "done") void advance();
  else { cycle.value++; rotateTimer = setTimeout(advance, dwellOf("ai_intel") * 1000); }
});

/** 手動切換：方向照目的地在播放清單裡的前後決定（同主題就比頁碼） */
async function go(s: Step) {
  const pl = playlist.value;
  const a = pl.indexOf(step.value.key), b = pl.indexOf(s.key);
  dir.value = b < a || (b === a && s.page < step.value.page) ? "back" : "fwd";
  schedule();                                     // 先重新計時，避免載入途中剛好自動換頁
  await load(s);
  schedule();
}

const keyRows = computed(() => warroomKeyRows(playlist.value.length));

function onKey(e: KeyboardEvent) {
  if (!cfg.value || e.ctrlKey || e.altKey || e.metaKey) return;
  const pl = playlist.value;
  let target: Step | null = null;
  // 哪個鍵做什麼只寫在 utils/warroomKeys.ts（按鍵說明與設定頁的按鍵表也從那裡畫）
  const action = keyAction(e.key);
  if (action === "topic_next") target = topicStep(pl, step.value, 1);
  else if (action === "topic_prev") target = topicStep(pl, step.value, -1);
  else if (action === "page_next") target = pageStep(step.value, data.value?.pages ?? 1, 1);   // 同主題內循環
  else if (action === "page_prev") target = pageStep(step.value, data.value?.pages ?? 1, -1);
  else if (action === "first") target = pl.length ? { key: pl[0], page: 1 } : null;
  else if (action === "number") target = topicByNumber(pl, Number(e.key));
  else if (action === "pause") { togglePause(); e.preventDefault(); return; }
  else if (action === "pin") { togglePin(); return; }
  else if (action === "help") { showHelp.value = !showHelp.value; return; }
  else if (action === "close") { showHelp.value = false; return; }
  else return;
  e.preventDefault();
  if (target) void go(target);
}

/**
 * 換頁轉場的名稱。降低動畫時用一個沒有任何樣式的名稱（立即換頁）——
 * 不能用 :css="false"：out-in 模式下沒有 JS 鉤子時舊的離場不會結束，新的永遠進不來，
 * 標題與內容整塊消失（e2e warroom.spec.ts 抓到的）
 */
/** 大數字的字體（戰情室設定可選，warroomFonts.ts）：設定一到（或換頁重讀到新值）就載入，載好才套上 */
const bigFont = ref(NUMBER_FONTS.inter);
watch(() => cfg.value?.number_font, async (k) => { bigFont.value = await loadNumberFont(k ?? "exo2"); }, { immediate: true });   // 預設 Exo 2
const bigFontStyle = computed(() => ({
  "--wr-big": `"${bigFont.value.family}", "WR Inter", ui-sans-serif, sans-serif`,
  "--wr-big-scale": String(bigFont.value.scale),
}));
const TRANSITIONS = new Set(["slide", "fade", "zoom", "rise", "wipe", "flip"]);
const transName = computed(() => {
  if (reduceMotion.value) return "wr-none";
  const style = TRANSITIONS.has(cfg.value?.transition ?? "") ? cfg.value!.transition : "slide";
  return `wr-${style}-${dir.value}`;
});

/** 暫停／繼續：畫面快閃一下，現場的人才知道按到了（使用者 10-22）。降低動畫時改成很淡的一下 */
const flashing = ref(false);
/** 閃光的顏色：暫停琥珀、繼續綠（使用者 10-26：進入與離開暫停要分得出來）、釘選白 */
const flashKind = ref<"pause" | "resume" | "pin">("pin");
let flashTimer: ReturnType<typeof setTimeout> | null = null;
function togglePin() {
  pinned.value = !pinned.value;
  flashKind.value = "pin";
  flashOnce();
  // 重新排時間：釘住 AI 情報後改照停留秒數更新資料（不再等它播完一輪）；放開後恢復等播完（10-26）
  schedule();
}
function togglePause() {
  paused.value = !paused.value;
  flashKind.value = paused.value ? "pause" : "resume";
  flashOnce();
}
function flashOnce() {
  flashing.value = false;
  if (flashTimer) clearTimeout(flashTimer);
  requestAnimationFrame(() => {
    flashing.value = true;
    flashTimer = setTimeout(() => { flashing.value = false; }, 450);
  });
}

function onMouse() {
  operator.value = true;
  if (operatorTimer) clearTimeout(operatorTimer);
  operatorTimer = setTimeout(() => { operator.value = false; }, 4000);
}

async function ensureAccess(): Promise<boolean> {
  const q = route.query.display;
  if (typeof q === "string" && q) {
    setDisplayToken(q);
    await router.replace({ query: {} });          // 權杖不留在網址列（也不會進瀏覽紀錄）
  }
  if (displayToken()) return true;
  const auth = useAuthStore();
  if (!auth.isAuthenticated && !(await auth.ensureSession())) {
    await router.replace({ name: "login", query: { next: "/warroom" } });
    return false;
  }
  if (auth.me === null) await auth.fetchMe().catch(() => undefined);
  return true;
}

async function loadFont() {
  try {
    const f = new FontFace("WR Inter", `url(${interUrl})`, { weight: "100 900" });
    (document as unknown as { fonts: FontFaceSet }).fonts.add(await f.load());
  } catch { /* 字型載不到就用系統字型 */ }
}

onMounted(async () => {
  fit();
  window.addEventListener("resize", fit);
  window.addEventListener("keydown", onKey);
  window.addEventListener("mousemove", onMouse);
  clockTimer = setInterval(() => { now.value = new Date(); }, 15000);
  void loadFont().then(() => fitNav(0));
  if (!(await ensureAccess())) return;
  try {
    cfg.value = await getWarroomConfig();
  } catch (e) {
    fatal.value = errCode(e) ?? "warroom_unavailable";
    return;
  }
  if (cfg.value.language) locale.value = cfg.value.language;
  if (!playlist.value.length) { fatal.value = "warroom_no_pages"; return; }
  // 新版自動重新載入後接著播原本的主題（autoReload.ts）
  const resume = takeResume();
  stopAutoReload = startAutoReload(() => ({ key: step.value.key, page: step.value.page, paused: paused.value,
                                            pinned: pinned.value }));
  if (resume && playlist.value.includes(resume.key)) {
    paused.value = resume.paused;
    pinned.value = !!resume.pinned;
    await go({ key: resume.key, page: resume.page });
  } else {
    await go({ key: playlist.value[0], page: 1 });
  }
});
// 播放清單有 12 AI 情報、但正在播別的主題：每分鐘告訴後端「有人在看」，輪到它時摘要已經是新的
// （只讀資訊、不帶正文、不呼叫模型；正在播 AI 情報時由主題自己每 15 秒讀）
const intelBeat = setInterval(() => {
  if (playlist.value.includes("ai_intel") && step.value.key !== "ai_intel" && !fatal.value) {
    void getIntel(null, true).catch(() => undefined);
  }
}, 60000);

onBeforeUnmount(() => {
  clearInterval(intelBeat);
  window.removeEventListener("resize", fit);
  window.removeEventListener("keydown", onKey);
  window.removeEventListener("mousemove", onMouse);
  if (rotateTimer) clearTimeout(rotateTimer);
  if (operatorTimer) clearTimeout(operatorTimer);
  if (clockTimer) clearInterval(clockTimer);
  stopAutoReload?.();
  navObserver?.disconnect();
});
watch(paused, () => schedule());

const hm = (d: Date | string) => new Date(d).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
const scopeText = computed(() => {
  const sc = cfg.value?.scope;
  if (sc?.names?.length) {
    const names = sc.names.join(locale.value.startsWith("en") ? ", " : "、");
    return sc.more ? t("warroom.scope_part_more", { names, n: sc.more }) : t("warroom.scope_part", { names });
  }
  return cfg.value?.is_admin ? t("warroom.scope_all") : t("warroom.scope_mine");
});

interface MetricDef { key: string; label: string; tone: "accent" | "lilac" | "coral" | "ink" }
const METRICS: Record<string, MetricDef[]> = {
  overview: [
    { key: "registered_ips", label: "warroom.m_registered_ips", tone: "accent" },
    { key: "open_anomalies", label: "warroom.m_open_anomalies", tone: "coral" },
    { key: "capacity_alerts", label: "warroom.m_capacity_alerts", tone: "lilac" },
  ],
  capacity: [
    { key: "registered_ips", label: "warroom.m_registered_ips", tone: "accent" },
    { key: "high_util_subnets", label: "warroom.m_high_util", tone: "lilac" },
    { key: "pools_alert", label: "warroom.m_pools_alert", tone: "coral" },
  ],
  topology: [
    { key: "devices", label: "warroom.m_devices", tone: "accent" },
    { key: "links", label: "warroom.m_links", tone: "ink" },
    { key: "inferred", label: "warroom.m_inferred_links", tone: "lilac" },
  ],
  services: [
    { key: "dns_mismatch", label: "warroom.m_dns_mismatch", tone: "coral" },
    { key: "pools_alert", label: "warroom.m_pools_alert", tone: "lilac" },
    { key: "certs_30d", label: "warroom.m_certs_30d", tone: "coral" },
  ],
  anomalies: [
    { key: "open_anomalies", label: "warroom.m_open_anomalies", tone: "coral" },
    { key: "new_6h", label: "warroom.m_new_6h", tone: "lilac" },
    { key: "resolved_6h", label: "warroom.m_resolved_6h", tone: "accent" },
  ],
  assets: [
    { key: "hosts", label: "warroom.m_hosts", tone: "ink" },
    { key: "vms", label: "warroom.m_vms", tone: "accent" },
    { key: "gaps", label: "warroom.m_gaps", tone: "coral" },
  ],
  collection: [
    { key: "agents_ok", label: "warroom.m_agents_ok", tone: "accent" },
    { key: "agents_stale", label: "warroom.m_agents_stale", tone: "coral" },
    { key: "integrations_failing", label: "warroom.m_integrations_failing", tone: "lilac" },
  ],
  // 稽核事件是操作紀錄、不是告警：中性色（規格 §2 第 2 點）
  stats: [
    { key: "registered_ips", label: "warroom.m_registered_ips", tone: "accent" },
    { key: "subnets", label: "warroom.st_m_subnets", tone: "lilac" },
    { key: "sections", label: "warroom.st_m_sections", tone: "lilac" },
    { key: "audit_24h", label: "warroom.st_m_audit_24h", tone: "ink" },
  ],
  availability: [
    { key: "tracked", label: "warroom.av_m_tracked", tone: "accent" },
    { key: "down_now", label: "warroom.av_m_down", tone: "coral" },
    { key: "flapping", label: "warroom.av_m_flapping", tone: "lilac" },
  ],
  relations: [
    { key: "ips", label: "warroom.rl_m_ips", tone: "accent" },
    { key: "devices", label: "warroom.rl_m_devices", tone: "lilac" },
    { key: "virt", label: "warroom.rl_m_virt", tone: "lilac" },
  ],
  // 三者可重疊（頁內次列寫明不能相加）；沒有可用結果時後端給「—」
  ai_intel: [
    { key: "open_anomalies", label: "warroom.m_open_anomalies", tone: "coral" },
    { key: "watch", label: "warroom.intel.m_watch", tone: "lilac" },
    { key: "stale_sources", label: "warroom.intel.m_stale_sources", tone: "ink" },
  ],
  ai_audit: [
    { key: "high", label: "warroom.ai_m_high", tone: "coral" },
    { key: "open", label: "warroom.ai_m_open", tone: "lilac" },
    { key: "insufficient", label: "warroom.ai_m_insufficient", tone: "ink" },
  ],
};
/** 資訊展示模式的標題列：問題類數字換成中性的總量（後端也只給這些） */
const METRICS_INFO: Record<string, MetricDef[]> = {
  overview: [
    { key: "registered_ips", label: "warroom.m_registered_ips", tone: "accent" },
    { key: "subnets", label: "warroom.st_m_subnets", tone: "lilac" },
  ],
  capacity: [
    { key: "registered_ips", label: "warroom.m_registered_ips", tone: "accent" },
    { key: "subnets", label: "warroom.st_m_subnets", tone: "lilac" },
    { key: "pools", label: "warroom.info_m_pools", tone: "ink" },
  ],
  services: [
    { key: "dns_groups", label: "warroom.info_m_dns_groups", tone: "accent" },
    { key: "pools", label: "warroom.info_m_pools", tone: "lilac" },
    { key: "certs", label: "warroom.info_m_certs", tone: "ink" },
  ],
  assets: [
    { key: "hosts", label: "warroom.m_hosts", tone: "ink" },
    { key: "vms", label: "warroom.m_vms", tone: "accent" },
  ],
  ai_intel: [
    { key: "registered_ips", label: "warroom.m_registered_ips", tone: "accent" },
    { key: "subnets", label: "warroom.st_m_subnets", tone: "lilac" },
  ],
};
const metrics = computed(() => {
  if (!data.value) return [];
  const key = data.value.key;
  return (infoMode.value ? METRICS_INFO[key] : undefined) ?? METRICS[key] ?? [];
});
/** 長標題（例如英文的 Anomalies and exposure）改成兩行、字小一級，讓三個大指標仍有位置；不再縮更小 */
const titleLong = computed(() => {
  if (!data.value) return false;
  const text = t(`warroom.title_${data.value.key}`);
  let w = 0;
  for (const ch of text) w += /[\u2E80-\uFFEF]/.test(ch) ? 2 : 1;
  return w > 10;                                  // 日文「アドレス容量」12 寬曾把指標名稱擠成一字一行
});
const metricValue = (k: string) => (data.value?.metrics ? data.value.metrics[k] : null);
/** 指標是 null 但原因不是權限（09 關係頁的虛擬化物件：沒有設定虛擬化整合）：顯示「未設定」而不是「無權限」 */
const metricNa = (k: string) => {
  const st = (data.value?.metrics_state as Record<string, string> | undefined)?.[k];
  return st === "not_configured" ? t("warroom.rl_na_not_configured") : undefined;
};

const subLabel = computed(() => {
  const d = data.value;
  if (!d || !d.total || (d.pages ?? 1) <= 1) return "";
  const per = (d.per_page as number) || 1;
  const from = (d.page - 1) * per + 1;
  const to = Math.min(d.page * per, d.total);
  return t(`warroom.sub_${d.key}`, { from: pad2(from), to: pad2(to), total: d.total });
});
/** 標題列右邊的圖例：[格子狀態, 翻譯鍵]；其餘頁的圖例在各自的圖旁邊 */
const legend = computed<[string, string][]>(() => {
  const k = data.value?.key;
  if (k === "capacity") return [["allocated", "legend_allocated"], ["free", "legend_free"], ["reserved", "legend_reserved"]];
  if (k === "collection") return [["ok", "legend_ok"], ["busy", "legend_busy"], ["overloaded", "legend_overloaded"], ["none", "legend_none"]];
  return [];
});
</script>

<template>
  <div class="wr-viewport" :class="{ 'wr-viewport--op': operator }">
    <div class="wr" :style="{ transform: `translate(-50%, -50%) scale(${scale})`, ...bigFontStyle }"
         :class="{ rm: reduceMotion, 'wr--info': infoMode }" :data-info-mode="infoMode ? '1' : '0'">
      <header class="wr-top">
        <div class="wr-brand"><b>jt-ipam</b><span class="wr-brand__sep" />{{ t("warroom.brand_sub") }}</div>
        <div class="wr-top__r">
          <span>{{ scopeText }}</span><span class="wr-vsep" />
          <span class="wr-clock">{{ hm(now) }}</span>
          <template v-if="data"><span class="wr-vsep" />
            <!-- 資料太久沒更新（連不上或伺服器回的是舊資料）：紅字＋警示圖示（10-23 使用者要求） -->
            <!-- 資訊展示模式：只寫資料時間，不用紅字、警示圖示與「連不上」 -->
            <span v-if="infoMode" data-testid="wr-data-at" data-stale="0">
              {{ t("warroom.data_at", { time: hm(offlineSince ?? data.data_at) }) }}
            </span>
            <span v-else :class="{ 'wr-off': dataStale }" data-testid="wr-data-at" :data-stale="dataStale ? '1' : '0'">
              <WrIco v-if="dataStale" :icon="WarnIcon" />
              {{ offlineSince ? t("warroom.offline_at", { time: hm(offlineSince) }) : t("warroom.data_at", { time: hm(data.data_at) }) }}
            </span>
          </template>
          <span v-if="pinned" class="wr-pinned" data-testid="wr-pinned">{{ t("warroom.pinned") }}</span>
          <span v-if="paused" class="wr-paused">{{ t("warroom.paused") }}</span>
        </div>
      </header>

      <div v-if="fatal" class="wr-fatal" data-testid="wr-fatal">
        <div class="wr-fatal__t">{{ t(`warroom.err.${fatal}`) }}</div>
        <div class="wr-fatal__d">{{ fatal === "warroom_token_disabled" ? t("warroom.err_hint_disabled") : t("warroom.err_hint") }}</div>
      </div>

      <template v-else-if="data">
        <Transition :name="transName" mode="out-in">
        <section :key="data.key" class="wr-title">
          <h1 :class="{ long: titleLong }">{{ t(`warroom.title_${data.key}`) }}</h1>
          <div class="wr-metrics" :class="{ dense: metrics.length > 3 }">
            <WrMetric v-for="m in metrics" :key="`${data.key}:${m.key}`" :value="metricValue(m.key)" :label="t(m.label)" :tone="m.tone"
                      :na-text="metricNa(m.key)" />
          </div>
          <div v-if="legend.length" class="wr-legend">
            <div v-for="[s, l] in legend" :key="s"><span class="wr-swatch" :class="`s-${s}`" />{{ t(`warroom.${l}`) }}</div>
          </div>
        </section>
        </Transition>
        <Transition :name="transName" mode="out-in">
          <main :key="data.key" class="wr-main">
            <component :is="PAGE_COMPONENTS[data.key]" :data="data" />
          </main>
        </Transition>
      </template>
      <div v-else class="wr-loading">{{ t("warroom.loading") }}</div>

      <footer class="wr-foot">
        <!-- 釘選在只有一頁的主題（例如 AI 情報）時不會換頁，進度條沒有意義：改成來回掃描的光點，表示「釘選、持續更新中」（使用者 10-26） -->
        <div v-if="cfg && !paused && !fatal && (idleSweep || intelWaiting)" class="wr-progress wr-progress--sweep"
             data-testid="wr-progress" :data-mode="idleSweep ? 'sweep' : 'briefing'"><i /></div>
        <div v-else-if="cfg && !paused && !fatal" :key="cycle" class="wr-progress" data-testid="wr-progress" data-mode="dwell"
             :style="{ animationDuration: `${dwellOf(step.key)}s` }" />
        <nav ref="navEl" class="wr-nav" :class="[`lv${navLevel}`, { compact: navCompact }]" data-testid="wr-nav">
          <button v-for="k in playlist" :key="k" type="button" class="wr-nav__i" :class="{ on: data?.key === k }"
                  :title="t(`warroom.nav_${k}`)" :data-key="k" :data-pos="posOf(k)" @click="go({ key: k, page: 1 })">
            <span class="wr-nav__t">{{ t(`warroom.nav_${k}`) }}</span>
          </button>
        </nav>
        <div class="wr-sub">
          <span v-if="subLabel" class="wr-sub__label">{{ subLabel }}</span>
          <button v-if="(data?.pages ?? 1) > 1" type="button" class="wr-arrow" aria-label="prev"
                  data-testid="wr-page-prev" @click="go(pageStep(step, data?.pages ?? 1, -1))">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 15l6-6 6 6" /></svg>
          </button>
          <button v-if="(data?.pages ?? 1) > 1" type="button" class="wr-arrow" aria-label="next"
                  data-testid="wr-page-next" @click="go(pageStep(step, data?.pages ?? 1, 1))">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6" /></svg>
          </button>
          <!-- 按鍵提示常駐但要精簡（英文的整句曾把頁尾擠到換行）：鍵盤圖示＋H，完整說明在 title；放最右邊，頁碼與上下頁在它左邊（10-24） -->
          <!-- 說明打開時只隱藏、不拿掉：在最右邊，拿掉的話頁碼與箭頭會往右跳 -->
          <button type="button" class="wr-keyhint" data-testid="wr-keyhint" :style="{ visibility: showHelp ? 'hidden' : 'visible' }"
                  :title="t('warroom.keys_hint')" :aria-label="t('warroom.keys_hint')" @click="showHelp = true">
            <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="2.5" y="6" width="19" height="12" rx="2" />
              <path d="M6 10h.01M9.5 10h.01M13 10h.01M16.5 10h.01M8 14h8" /></svg>
            <span>H</span>
          </button>
        </div>
      </footer>

      <div v-if="flashing" class="wr-flash" :class="[`f-${flashKind}`, { soft: reduceMotion }]" :data-kind="flashKind"
           aria-hidden="true" data-testid="wr-flash" />
      <!-- 12 AI 情報的畫面效果蓋在整個畫布上（使用者 10-26：全版面都要有效） -->
      <WrFx v-if="data?.key === 'ai_intel' && cfg?.intel" :effects="cfg.intel.effects" :scanlines="cfg.intel.scanlines"
            :noise="cfg.intel.noise" :reduce="reduceMotion" />
      <aside v-if="showHelp" class="wr-help" data-testid="wr-help" @click="showHelp = false">
        <h2>{{ t("warroom.keys_title") }}</h2>
        <dl>
          <template v-for="row in keyRows" :key="row.label">
            <dt>
              <template v-for="(k, i) in row.keys" :key="k"><span v-if="row.range && i" class="wr-help__dash">–</span><kbd>{{ k }}</kbd></template>
            </dt>
            <dd>{{ t(row.label) }}</dd>
          </template>
        </dl>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.wr-viewport { position: fixed; inset: 0; background: #0b0c0f; overflow: hidden; cursor: none; }
.wr-viewport--op { cursor: default; }
.wr {
  --wr-bg: #101216; --wr-ink: #ecede5; --wr-accent: #cbf46a; --wr-lilac: #a398d5; --wr-coral: #f37859;
  /* 可配置（free）是中深灰：原本的淺灰跟已配置的黃綠亮度太接近，電視上分不出來（10-22） */
  --wr-muted: #a5a9ad; --wr-free: #4b5059; --wr-deep: #2b2e35; --wr-deep-line: #3a3e46; --wr-outline: #7d828a;
  --wr-line: #2c2f36; --wr-panel: #181b20; --wr-stop: #6b7079;
  --wr-num: "WR Inter", "Inter", ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  position: absolute; left: 50%; top: 50%; width: 1920px; height: 1080px; transform-origin: center center;
  background: var(--wr-bg); color: var(--wr-ink);
  font-family: "WR Inter", "Noto Sans TC", "Noto Sans CJK TC", "PingFang TC", "Microsoft JhengHei", sans-serif;
  display: grid; grid-template-rows: 72px auto 1fr 84px; grid-template-columns: minmax(0, 1fr);
  padding: 0 48px; box-sizing: border-box; overflow: hidden;
  -webkit-font-smoothing: antialiased;
}
/* 資訊展示模式：表示問題的珊瑚紅收成一般的灰（後端已經不給問題資料，這是剩下的保險） */
.wr--info { --wr-coral: var(--wr-muted); }
.wr-top { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--wr-line);
  font-size: 22px; }
.wr-brand { display: flex; align-items: center; gap: 18px; letter-spacing: .08em; text-transform: uppercase;
  color: var(--wr-ink); font-size: 22px; }
.wr-brand b { font-size: 28px; letter-spacing: 0; text-transform: none; font-weight: 700; }
.wr-brand__sep, .wr-vsep { display: inline-block; width: 1px; height: 26px; background: var(--wr-line); }
.wr-top__r { display: flex; align-items: center; gap: 18px; color: var(--wr-muted); }
.wr-clock { font-family: var(--wr-num); font-size: 26px; color: var(--wr-ink); font-variant-numeric: tabular-nums; }
.wr-off { color: var(--wr-coral); }
.wr-paused { color: var(--wr-lilac); border: 1px solid var(--wr-lilac); padding: 2px 10px; }
.wr-pinned { color: var(--wr-accent); border: 1px solid var(--wr-accent); padding: 2px 10px; }
.wr-title { display: flex; align-items: center; gap: 56px; padding: 28px 0 24px; border-bottom: 1px solid var(--wr-line); }
.wr-title h1 { margin: 0; font-size: 104px; font-weight: 800; letter-spacing: .02em; line-height: 1; white-space: nowrap;
  padding-right: 56px; border-right: 1px solid var(--wr-line); }
.wr-title h1.long { font-size: 68px; line-height: 1.05; white-space: normal; max-width: 660px; letter-spacing: 0;
  word-break: keep-all; }
.wr-metrics { display: flex; gap: 48px; flex: 1; min-width: 0; }
.wr-metrics > * + * { padding-left: 48px; border-left: 1px solid var(--wr-line); }
/* 四個指標（08 IP 統計）：數字在上、名稱在下，百萬筆的數字也放得下（不縮主標題） */
.wr-metrics.dense { gap: 36px; }
.wr-metrics.dense > * + * { padding-left: 36px; }
.wr-metrics.dense :deep(.wr-metric) { flex-direction: column; align-items: flex-start; gap: 10px; }
.wr-metrics.dense :deep(.wr-metric__v) { font-size: 84px; }
.wr-metrics.dense :deep(.wr-metric__l) { font-size: 24px; max-width: 320px; }
.wr-legend { display: grid; gap: 6px; font-size: 20px; color: var(--wr-ink); padding-left: 32px; border-left: 1px solid var(--wr-line); }
.wr-legend > div { display: flex; align-items: center; gap: 12px; white-space: nowrap; }
.wr-main { padding: 18px 0; min-height: 0; overflow: hidden; }
/* 換主題的過場（戰情室設定可選，10-24）。命名：wr-<效果>-<fwd|back>；內容比標題晚一拍進場。
   滑動（預設）：舊的往反方向退一小段，新的從前進方向滑入 */
.wr-slide-fwd-leave-active, .wr-slide-back-leave-active,
.wr-zoom-fwd-leave-active, .wr-zoom-back-leave-active,
.wr-rise-fwd-leave-active, .wr-rise-back-leave-active,
.wr-flip-fwd-leave-active, .wr-flip-back-leave-active { transition: opacity .22s ease-in, transform .22s ease-in; }
.wr-slide-fwd-enter-active, .wr-slide-back-enter-active,
.wr-zoom-fwd-enter-active, .wr-zoom-back-enter-active,
.wr-rise-fwd-enter-active, .wr-rise-back-enter-active,
.wr-flip-fwd-enter-active, .wr-flip-back-enter-active { transition: opacity .45s ease-out, transform .45s cubic-bezier(.2, .7, .2, 1); }
.wr-main[class*="-enter-active"] { transition-delay: .08s; }
.wr-slide-fwd-enter-from { opacity: 0; transform: translateX(72px); }
.wr-slide-fwd-leave-to { opacity: 0; transform: translateX(-48px); }
.wr-slide-back-enter-from { opacity: 0; transform: translateX(-72px); }
.wr-slide-back-leave-to { opacity: 0; transform: translateX(48px); }
/* 淡入淡出 */
.wr-fade-fwd-enter-active, .wr-fade-back-enter-active { transition: opacity .5s ease-out; }
.wr-fade-fwd-leave-active, .wr-fade-back-leave-active { transition: opacity .25s ease-in; }
.wr-fade-fwd-enter-from, .wr-fade-back-enter-from, .wr-fade-fwd-leave-to, .wr-fade-back-leave-to { opacity: 0; }
/* 縮放：新的從略小放大進來，舊的略為放大淡出 */
.wr-zoom-fwd-enter-from, .wr-zoom-back-enter-from { opacity: 0; transform: scale(.94); }
.wr-zoom-fwd-leave-to, .wr-zoom-back-leave-to { opacity: 0; transform: scale(1.04); }
/* 上升：新的由下往上浮現（往回則由上往下） */
.wr-rise-fwd-enter-from { opacity: 0; transform: translateY(56px); }
.wr-rise-fwd-leave-to { opacity: 0; transform: translateY(-36px); }
.wr-rise-back-enter-from { opacity: 0; transform: translateY(-56px); }
.wr-rise-back-leave-to { opacity: 0; transform: translateY(36px); }
/* 擦除：新的從前進方向一路揭開 */
.wr-wipe-fwd-enter-active, .wr-wipe-back-enter-active { transition: clip-path .55s cubic-bezier(.6, 0, .2, 1); }
.wr-wipe-fwd-leave-active, .wr-wipe-back-leave-active { transition: opacity .2s ease-in; }
.wr-wipe-fwd-enter-from { clip-path: inset(0 100% 0 0); }
.wr-wipe-back-enter-from { clip-path: inset(0 0 0 100%); }
.wr-wipe-fwd-enter-to, .wr-wipe-back-enter-to { clip-path: inset(0 0 0 0); }
.wr-wipe-fwd-leave-to, .wr-wipe-back-leave-to { opacity: 0; }
/* 翻頁：像翻一張看板，帶一點透視 */
.wr-flip-fwd-enter-from { opacity: 0; transform: perspective(1800px) rotateY(-18deg); transform-origin: left center; }
.wr-flip-fwd-leave-to { opacity: 0; transform: perspective(1800px) rotateY(12deg); transform-origin: right center; }
.wr-flip-back-enter-from { opacity: 0; transform: perspective(1800px) rotateY(18deg); transform-origin: right center; }
.wr-flip-back-leave-to { opacity: 0; transform: perspective(1800px) rotateY(-12deg); transform-origin: left center; }
.wr-foot { position: relative; display: flex; align-items: center; justify-content: space-between;
  border-top: 1px solid var(--wr-line); }
/* 距離下次自動換頁的時間：從左到右填滿，填滿就換頁 */
.wr-progress { position: absolute; left: 0; top: -2px; height: 3px; width: 100%; background: var(--wr-accent);
  opacity: .55; transform-origin: left center; animation: wr-progress linear forwards; }
@keyframes wr-progress { from { transform: scaleX(0); } to { transform: scaleX(1); } }
/* 釘選時的掃描：一段帶光暈的光點在底線上來回移動，底線本身維持細細的暗色 */
.wr-progress--sweep { animation: none; transform: none; opacity: 1; overflow: visible;
  background: color-mix(in srgb, var(--wr-accent) 18%, transparent); }
.wr-progress--sweep i { position: absolute; top: -1px; height: 5px; width: 16%; left: 0;
  background: linear-gradient(90deg, transparent, var(--wr-accent) 45%, #fff 50%, var(--wr-accent) 55%, transparent);
  box-shadow: 0 0 14px 2px color-mix(in srgb, var(--wr-accent) 60%, transparent);
  animation: wr-sweep 3.2s cubic-bezier(.45, 0, .55, 1) infinite alternate; }
@keyframes wr-sweep { from { left: 0; } to { left: 84%; } }
.wr.rm .wr-progress--sweep i { animation: none; left: 42%; }
/* 暫停／繼續的閃光：整個畫布亮一下再退掉 */
.wr-flash { position: absolute; inset: 0; background: #ffffff; pointer-events: none; z-index: 50;
  animation: wr-flash .45s ease-out forwards; }
.wr-flash.soft { animation-name: wr-flash-soft; }
.wr-flash.f-pause { background: #e9b861; }
.wr-flash.f-resume { background: var(--wr-accent); }
@keyframes wr-flash { 0% { opacity: .85; } 100% { opacity: 0; } }
@keyframes wr-flash-soft { 0% { opacity: .2; } 100% { opacity: 0; } }
.wr-keyhint { background: none; border: 1px solid var(--wr-line); color: var(--wr-muted); font: inherit; font-size: 20px;
  padding: 4px 10px; cursor: pointer; display: inline-flex; align-items: center; gap: 8px; white-space: nowrap; }
.wr-keyhint svg { width: 26px; height: 26px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; }
.wr-sub__label { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.wr-help { position: absolute; z-index: 30; right: 48px; bottom: 108px; width: 760px; background: var(--wr-panel);
  border: 1px solid var(--wr-deep-line); padding: 32px 40px; box-shadow: 0 24px 64px rgba(0, 0, 0, .5); }
.wr-help h2 { margin: 0 0 20px; font-size: 34px; }
.wr-help dl { display: grid; grid-template-columns: auto 1fr; gap: 16px 28px; margin: 0; font-size: 24px; align-items: center; }
.wr-help dt { white-space: nowrap; display: flex; gap: 6px; align-items: center; }
.wr-help dd { margin: 0; color: var(--wr-muted); }
.wr-help kbd { font-family: var(--wr-num); font-size: 20px; min-width: 36px; text-align: center; padding: 3px 10px;
  border: 1px solid var(--wr-outline); border-bottom-width: 3px; color: var(--wr-ink); }
.wr-nav { flex: 1 1 auto; display: flex; gap: 4px; min-width: 0; overflow: hidden; }
.wr-nav.lv1 .wr-nav__i { padding-left: 10px; padding-right: 10px; gap: 7px; }
.wr-nav.lv2 .wr-nav__i { padding-left: 8px; padding-right: 8px; gap: 6px; font-size: 22px; }
.wr-nav.lv3 .wr-nav__i, .wr-nav.lv4 .wr-nav__i { padding-left: 7px; padding-right: 7px; font-size: 20px; }
/* 最後一段：沒在播的名稱可以被截短（…），正在播的完整 */
.wr-nav.compact .wr-nav__i:not(.on) { flex: 0 1 auto; min-width: 2.6em; overflow: hidden; }
.wr-nav.compact .wr-nav__i:not(.on) .wr-nav__t { display: block; overflow: hidden; text-overflow: ellipsis; }
.wr-nav__i { background: none; border: 0; color: var(--wr-muted); font: inherit; font-size: 25px; padding: 18px 16px 14px; white-space: nowrap;
  border-bottom: 4px solid transparent; cursor: pointer; display: flex; gap: 9px; align-items: baseline; position: relative; }
/* 主題之間一條細直線：編號緊貼自己的名稱、主題與主題之間有分隔，一眼看得出「02」屬於右邊的「容量」（10-24） */
.wr-nav__i + .wr-nav__i::before { content: ""; position: absolute; left: -2px; top: 34%; bottom: 30%; width: 1px;
  background: var(--wr-outline); }
.wr-nav__i.on { color: var(--wr-accent); border-bottom-color: var(--wr-accent); font-weight: 700; }
/* 右側固定保留寬度（最寬的「Pending 01–04 / 27」約 470px）：不管這個主題有沒有頁碼，主題列能用的寬度都一樣，
   字級與間距只由播放清單與語言決定，換主題不會一下大一下小（10-24 使用者） */
.wr-sub { display: flex; align-items: center; justify-content: flex-end; gap: 14px; flex: none; width: 480px;
  font-size: 24px; color: var(--wr-ink); font-family: var(--wr-num); }
.wr-arrow { background: none; border: 0; color: var(--wr-ink); line-height: 0; cursor: pointer; padding: 0 6px; }
.wr-arrow svg { width: 36px; height: 36px; fill: none; stroke: currentColor; stroke-width: 2.4; stroke-linecap: round; stroke-linejoin: round; }
.wr-fatal, .wr-loading { grid-row: 2 / 4; display: flex; flex-direction: column; justify-content: center; align-items: flex-start;
  gap: 18px; }
.wr-fatal__t { font-size: 64px; font-weight: 800; color: var(--wr-coral); }
.wr-fatal__d { font-size: 28px; color: var(--wr-muted); }
.wr-loading { font-size: 36px; color: var(--wr-muted); }
</style>

<style>
/* 大數字（指標、百分比、排名編號）用設定選的字體；寬體字用 zoom 縮一點，寬度跟 Inter 差不多（warroomFonts.ts 的 scale） */
.wr :is(.wr-metric__v, .cap-f__v, .col-num__v, .as-cl__v, .as-chg__v, .rl-count__v, .rl-pend__v, .st-row__v,
        .st-item__v, .st-fam__v, .svc-pool__v, .svc-cert__v, .svc-bk__v, .an-ev__v,
        .aa-item__rank, .aa-f__rank) { font-family: var(--wr-big, var(--wr-num)); zoom: var(--wr-big-scale, 1); }
/* 異常卡片的編號方塊有底色：zoom 會連方塊一起縮，只換字體 */
.wr .an-item__rank { font-family: var(--wr-big, var(--wr-num)); }
/* 戰情室圖示（wrIcons.ts）：跟文字同高、顏色跟文字，線條細一點才不搶數字 */
.wr .wr-ico { width: .9em; height: .9em; flex: none; stroke-width: 1.6; vertical-align: -0.12em; margin-right: .35em; }
.wr .wr-h2 .wr-ico { color: var(--wr-muted); margin-right: -6px; }
.wr .wr-ico + * { min-width: 0; }
/* 判斷線索（未授權 IP 等）：一行小字、最多兩行，外部回報的文字只當純文字 */
/* 判斷線索（WrHints.vue）：逐項圖示＋文字，項目間留空不加分隔點；最多兩排，超過的整項收起 */
.wr .wr-hints { display: flex; flex-wrap: wrap; gap: 6px 22px; font-size: 20px; line-height: 1.3; color: var(--wr-muted);
  max-height: calc(2 * 1.3em + 6px); overflow: hidden; }
.wr .wr-hint { display: inline-flex; align-items: center; white-space: nowrap; min-width: 0; max-width: 100%; }
.wr .wr-hint .wr-ico { color: var(--wr-outline); margin-right: .4em; flex: none; }
.wr .wr-hint__t { overflow: hidden; text-overflow: ellipsis; }
.wr .wr-hint--strong { color: var(--wr-ink); font-weight: 600; }
.wr .wr-hint--mono .wr-hint__t { font-family: var(--wr-num); font-variant-numeric: tabular-nums; letter-spacing: .02em; }
/* 同主題換資料頁（paged.ts）：只有分頁的那一區上下換，方向跟上下鍵一致 */
.wr .wr-pg-fwd-leave-active, .wr .wr-pg-back-leave-active { transition: opacity .18s ease-in, transform .18s ease-in; }
.wr .wr-pg-fwd-enter-active, .wr .wr-pg-back-enter-active { transition: opacity .4s ease-out, transform .4s cubic-bezier(.2, .7, .2, 1); }
.wr .wr-pg-fwd-enter-from { opacity: 0; transform: translateY(48px); }
.wr .wr-pg-fwd-leave-to { opacity: 0; transform: translateY(-32px); }
.wr .wr-pg-back-enter-from { opacity: 0; transform: translateY(-48px); }
.wr .wr-pg-back-leave-to { opacity: 0; transform: translateY(32px); }
/* 三個主題頁共用的小元件（子元件自己的模板吃不到上面的 scoped 樣式；一律掛在 .wr 底下） */
.wr .wr-empty { font-size: 40px; color: var(--wr-muted); padding: 80px 0; }
.wr .wr-muted { font-size: 26px; color: var(--wr-muted); }
.wr .wr-h2 { font-size: 34px; font-weight: 700; display: flex; align-items: baseline; gap: 16px; }
.wr .wr-h2__n { font-family: var(--wr-num); font-size: 26px; color: var(--wr-muted); font-weight: 400; }
.wr .wr-tag { font-size: 22px; font-weight: 700; padding: 4px 12px; border: 2px solid currentColor; white-space: nowrap; }
.wr .wr-tag.lv-crit, .wr .wr-tag.lv-stale, .wr .wr-tag.lv-never, .wr .wr-tag.lv-overloaded { color: var(--wr-coral); }
.wr .wr-tag.lv-warn, .wr .wr-tag.lv-busy { color: var(--wr-lilac); }
.wr .wr-unit { font-size: 20px; color: var(--wr-muted); white-space: nowrap; }
.wr .wr-swatch { display: inline-block; width: 22px; height: 22px; box-sizing: border-box; flex: none; }
.wr .wr-swatch--sm { width: 18px; height: 18px; }
.wr .wr-swatch.s-ok, .wr .wr-swatch.s-allocated, .wr .wr-swatch.s-running, .wr .wr-swatch.s-covered { background: var(--wr-accent); }
.wr .wr-swatch.s-warn, .wr .wr-swatch.s-reserved, .wr .wr-swatch.s-busy, .wr .wr-swatch.s-partial,
.wr .wr-swatch.s-other, .wr .wr-swatch.s-active { background: var(--wr-lilac); }
.wr .wr-swatch.s-anomaly, .wr .wr-swatch.s-overloaded, .wr .wr-swatch.s-missing, .wr .wr-swatch.s-gap,
.wr .wr-swatch.s-new { background: var(--wr-coral); }
.wr .wr-swatch.s-free { background: var(--wr-free); }
.wr .wr-swatch.s-stopped { background: var(--wr-stop); }
.wr .wr-swatch.s-unscanned, .wr .wr-swatch.s-resolved { background: var(--wr-deep); }
.wr .wr-swatch.s-stale, .wr .wr-swatch.s-pending, .wr .wr-swatch.s-unknown { border: 2px solid var(--wr-outline); }
.wr .wr-swatch.s-none, .wr .wr-swatch.s-nodata { border: 2px dashed var(--wr-deep-line); }
.wr .wr-swatch.s-quiet { border: 1px solid var(--wr-deep-line); }
.wr .wr-index { font-family: var(--wr-num); font-size: 22px; color: var(--wr-muted); font-variant-numeric: tabular-nums; }
.wr .wr-big { font-family: var(--wr-big, var(--wr-num)); font-weight: 300; line-height: .9; font-variant-numeric: tabular-nums;
  letter-spacing: -0.02em; }
.wr .tone-accent { color: var(--wr-accent); }
.wr .tone-lilac { color: var(--wr-lilac); }
.wr .tone-coral { color: var(--wr-coral); }
.wr .tone-ink { color: var(--wr-ink); }
.wr .tone-muted { color: var(--wr-muted); }
.wr .wr-dot { display: inline-block; width: 14px; height: 14px; border-radius: 50%; background: currentColor; }
.wr .wr-dot.d-ok { color: var(--wr-accent); }
.wr .wr-dot.d-error { color: var(--wr-coral); }
.wr .wr-dot.d-stale, .wr .wr-dot.d-never { color: var(--wr-lilac); }
</style>
