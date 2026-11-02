<script setup lang="ts">
/**
 * 12 AI 情報（2026-10-26，設計稿 v1.2）：把授權範圍內的資料整理成短篇播報，逐字播放。
 *
 * - 內容每 15 秒讀一次 /warroom/intel（批次變了才帶正文；這個讀取不呼叫模型）；產生由後端排程
 * - 三區：左 68% 現況播報（最近幾則，目前這則強調）、右上持續關注（或局部 WARNING）、右下來源狀態；
 *   專注：單一寬幅文字區，標題下預留一列給 WARNING
 * - 新一批在目前這則播完才換；全部播完會重播並標「摘要重播 · 時間」（不把舊的當新的）
 * - 重大事件（管理員設定的異常類別）每 15 秒讀一次，局部區塊立即出現、不等 AI；正文在句尾插播一則，每分鐘最多一則
 * - 配色只套在這個主題的內容區（CSS 變數），不改頁首、頁尾
 * - 整個戰情室暫停時打字也停（畫面標「播放已暫停」）；分頁隱藏時停動畫；降低動畫時整段直接顯示
 */
import { computed, inject, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch, type Ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  getIntel, getIntelEvents,
  type IntelBatch, type IntelDisplay, type IntelEvent, type IntelEvents, type IntelItem, type IntelState, type PageData,
} from "@/api/warroom";
import { anomalyCategoryLabel } from "./categories";
import { afterItem, charDelay, graphemes, pauseAfter, seedOf, tokens } from "./intelType";
import { categoryLabel, eventText, hmOf, itemText } from "./intelText";

const props = defineProps<{
  data?: PageData;
  /** 設定頁的預覽：固定的示範資料，不輪詢、不呼叫模型 */
  preview?: { state: IntelState; events: IntelEvents; display: IntelDisplay };
}>();
const { t, te } = useI18n();
const injectedCfg = inject<Ref<IntelDisplay | undefined>>("wrIntel", ref(undefined));
const paused = inject<Ref<boolean>>("wrPaused", ref(false));
const reduceGlobal = inject<Ref<boolean>>("wrReduceMotion", ref(false));
/** 告訴戰情室：播完一輪（輪播時才換下一個主題）或沒有東西可播（改照停留秒數）；設定頁預覽沒有這個 */
const signal = inject<(kind: "done" | "empty" | "refresh") => void>("wrIntelSignal", () => {});

const DEFAULT: IntelDisplay = {
  interval_minutes: 5, dwell_seconds: 90, layout: "three_panel", playback: "rotate", item_limit: 8, domains: [],
  speed: "fast", rhythm: true, cursor: true, effects: "cinema", scanlines: "low", noise: "low",
  palette: "phosphor", colors: { bg: "#0a1012", text: "#dce8e4", secondary: "#a8a3ce", accent: "#88ebc1",
                                 warn: "#e9b861", critical: "#f07866" },
  mask_ips: false, warning_enabled: true, warning_breathe: true,
};
const cfg = computed<IntelDisplay>(() => props.preview?.display ?? injectedCfg.value ?? DEFAULT);
const reduce = computed(() => reduceGlobal.value);

/** 預設配色（設計稿第 8 節）；自訂用設定裡的六色 */
const PRESETS: Record<string, IntelDisplay["colors"]> = {
  phosphor: { bg: "#0a1012", text: "#dce8e4", secondary: "#a8a3ce", accent: "#88ebc1", warn: "#e9b861", critical: "#f07866" },
  amber: { bg: "#10100d", text: "#eee5cc", secondary: "#a8a3ce", accent: "#e9b861", warn: "#f0a868", critical: "#f07866" },
  ice: { bg: "#0b1018", text: "#dde7ef", secondary: "#a8a3ce", accent: "#8cd5ef", warn: "#e9b861", critical: "#f07866" },
};
const colors = computed(() => cfg.value.palette === "custom" ? cfg.value.colors : PRESETS[cfg.value.palette] ?? PRESETS.phosphor);
const vars = computed(() => ({
  "--ai-bg": colors.value.bg, "--ai-text": colors.value.text, "--ai-sec": colors.value.secondary,
  "--ai-accent": colors.value.accent, "--ai-warn": colors.value.warn, "--ai-crit": colors.value.critical,
}));

// ─────────────────────────── 資料：每 15 秒讀一次 ───────────────────────────
const POLL_MS = 15000;
const POLL_FAST_MS = 3000;                    // 產生中：新的一批（先系統摘要、再 AI）一出來就換上
const st = ref<IntelState | null>(props.preview?.state ?? null);
const batch = ref<IntelBatch | null>(props.preview?.state.batch ?? null);
const pending = ref<IntelBatch | null>(null);
const events = ref<IntelEvents | null>(props.preview?.events ?? null);
let pollTimer: ReturnType<typeof setTimeout> | null = null;

async function poll() {
  if (props.preview) return;
  try {
    const s = await getIntel(batch.value?.id ?? null);
    st.value = s;
    if (s.batch?.items && s.batch.id !== batch.value?.id) {
      // 手上這批已經過期（很久沒人看）：新的一到就換，不等這則播完（10-26：一打開先播了幾十秒的舊內容）
      if (!batch.value || !playing.value || staleBatch.value) adopt(s.batch);
      else pending.value = s.batch;                         // 目前這則播完再換
    } else if (s.batch && batch.value && s.batch.id === batch.value.id) {
      batch.value = { ...batch.value, ...s.batch, items: batch.value.items };   // 同一批：更新重新檢查時間、來源
    }
  } catch { /* 暫時讀不到：照舊播手上這一批 */ }
  if (!batch.value?.items?.length) signal("empty");
  if (paused.value) return;                                   // 暫停：事件清單先不更新，按繼續後下一輪再套用
  try { events.value = await getIntelEvents(); } catch { /* 同上 */ }
}

// ─────────────────────────── 播放 ───────────────────────────
interface Para { key: string; label: string; time: string; text: string; state: string; event?: boolean }
// 播完的段落：由上往下長，換下一輪也不清掉（使用者 10-26），超出畫面的才從最上面移除；另有 40 則的硬上限（固定 DOM 上限）
const done = ref<Para[]>([]);
const cur = ref<Para | null>(null);
const shown = ref(0);                         // 目前這則顯示到第幾個字素
let curG: string[] = [];
const idx = ref(0);                           // 播到這批的第幾則
const replay = ref(false);
const playing = ref(false);
let timer: ReturnType<typeof setTimeout> | null = null;
let lastInsert = 0;
const announced = new Set<string>();
const hidden = ref(typeof document !== "undefined" && document.visibilityState === "hidden");

const items = computed<IntelItem[]>(() => batch.value?.items ?? []);
function para(it: IntelItem): Para {
  const label = categoryLabel(it.category, t, te);
  return { key: `${batch.value?.id}:${it.id}:${replay.value ? "r" : ""}`,
           label: replay.value ? `${label} · ${t("warroom.intel.replay_tag")}` : label,
           time: hmOf(batch.value?.generated_at), text: itemText(it, t, te), state: it.state };
}

/** 手上這批是不是過期的（超過兩個更新間隔沒有重新檢查） */
const staleBatch = computed(() => !!batch.value && Date.now() - Date.parse(batch.value.checked_at)
  > 2 * (cfg.value.interval_minutes || 5) * 60000);

function adopt(b: IntelBatch) {
  const isNew = !!batch.value && batch.value.id !== b.id;
  batch.value = b;
  if (isNew) signal("refresh");
  pending.value = null;
  idx.value = 0;
  replay.value = false;
  start();                                              // 已播的段落留著，新的一輪接在下面
}

function stop() {
  if (timer) clearTimeout(timer);
  timer = null;
  playing.value = false;
}

/** 開始播第 idx 則（或插播的事件） */
function start(p?: Para) {
  stop();
  const next = p ?? (items.value[idx.value] ? para(items.value[idx.value]) : null);
  if (!next) return;
  if (cur.value) done.value = [...done.value, cur.value].slice(-40);
  cur.value = next;
  curG = graphemes(next.text);
  shown.value = reduce.value ? curG.length : 0;
  playing.value = true;
  schedule();
}

function schedule() {
  if (timer) clearTimeout(timer);
  if (!cur.value || paused.value || hidden.value) return;
  const seed = seedOf(cur.value.key);
  if (shown.value >= curG.length) {
    timer = setTimeout(advance, reduce.value ? Math.max(afterItem(cur.value.text, seed) - 1200, 4000)
                                             : afterItem(cur.value.text, seed));
    return;
  }
  const g = curG[shown.value];
  const wait = charDelay(g, shown.value, { speed: cfg.value.speed, rhythm: cfg.value.rhythm, seed })
    + (shown.value > 0 ? pauseAfter(curG[shown.value - 1], shown.value - 1, seed) : 0);
  timer = setTimeout(() => { shown.value += 1; trim(); schedule(); }, wait);
}

/** 段落區放不下時，從最上面移除已播完的段落（不會讓上面空著，也不會有被切一半的段落） */
const parasEl = ref<HTMLElement | null>(null);
let sliding = false;
function trim() {
  const el = parasEl.value;
  if (!el || sliding || !done.value.length || el.scrollHeight <= el.clientHeight + 1) return;
  const first = el.querySelector<HTMLElement>(".ai-p");
  if (reduce.value || !first) { done.value = done.value.slice(1); return; }
  // 整段往上滑出一則的高度（含間距），最上面那則同時淡出；滑完才真的移除，再把位移歸零（畫面不會跳）
  sliding = true;
  const h = first.offsetHeight + 12;
  el.style.setProperty("--slide", `-${h}px`);
  el.classList.add("sliding");
  first.classList.add("leaving");
  setTimeout(() => {
    el.classList.remove("sliding");
    el.style.removeProperty("--slide");
    done.value = done.value.slice(1);
    sliding = false;
  }, 520);
}
// 移除一則後等畫面更新再量一次，直到放得下
watch(done, () => { void nextTick(trim); });

/** 這個事件本批播報已經寫到了（同一個 IP 出現在正文或參數裡）：不再插播（10-26：1.232 播了兩次） */
function covered(e: IntelEvent): boolean {
  const key = e.ip || e.label;
  if (!key) return false;
  return items.value.some((it) => (it.text ?? "").includes(key) || JSON.stringify(it.params ?? {}).includes(`"${key}"`));
}

/** 一則播完：有新的一批就換；有沒播報過的重大事件就插播（每分鐘最多一則）；否則下一則，播完重播 */
function advance() {
  playing.value = false;
  if (pending.value) { adopt(pending.value); return; }
  const ev = (events.value?.items ?? []).find((e) => e.state !== "resolved" && !announced.has(e.id) && !covered(e));
  if (ev && Date.now() - lastInsert > 60000 && !cur.value?.event) {
    announced.add(ev.id);
    lastInsert = Date.now();
    start({ key: `ev:${ev.id}`, label: t("warroom.intel.cat.event"), time: hmOf(ev.first_seen_at),
            text: eventText(ev, t, te), state: "new", event: true });
    return;
  }
  idx.value += 1;                                       // 插播的事件不佔序號：插播完接原本的下一則
  if (idx.value >= items.value.length) { idx.value = 0; replay.value = true; signal("done"); }
  start();
}

watch([paused, hidden], () => { if (playing.value || cur.value) schedule(); });
watch(reduce, (r) => { if (r && cur.value) shown.value = curG.length; });
function onVisibility() { hidden.value = document.visibilityState === "hidden"; }

/** 正文片段：識別碼整塊不拆行；逐字顯示時只顯示到 shown */
const curPieces = computed(() => {
  if (!cur.value) return [];
  const visible = curG.slice(0, shown.value).join("");
  const out: { text: string; atomic: boolean }[] = [];
  let left = visible.length;
  for (const tk of tokens(cur.value.text)) {
    if (left <= 0) break;
    out.push({ text: tk.text.slice(0, left), atomic: tk.atomic });
    left -= tk.text.length;
  }
  return out;
});
const typing = computed(() => !!cur.value && shown.value < curG.length);

// ─────────────────────────── 局部 WARNING ───────────────────────────
// 有未解除的重大事件就持續呼吸燈（使用者 10-26：「要有明顯像呼吸燈效果」；設計稿原本只閃 20 秒）。
// 不想閃就在設定關掉呼吸燈；降低動畫時常亮。（10-26 使用者：拿掉畫面上的「停止閃動」）
const WARN_ROTATE_MS = 12000;
const warn = reactive({ i: 0, recoveredUntil: 0 });
const openEvents = computed<IntelEvent[]>(() => (events.value?.items ?? []).filter((e) => e.state !== "resolved"));
const shownEvent = computed(() => openEvents.value.length ? openEvents.value[warn.i % openEvents.value.length] : null);
const warnActive = computed(() => cfg.value.warning_enabled && (openEvents.value.length > 0 || warn.recoveredUntil > now.value));
const breathing = computed(() => cfg.value.warning_breathe !== false && !reduce.value && openEvents.value.length > 0);
/** 局部區塊內容換了（輪到下一項、數量或狀態變了）就重播一次掃描進場 */
const warnKey = computed(() => shownEvent.value
  ? `${shownEvent.value.id}:${shownEvent.value.state}:${openEvents.value.length}` : warnActive.value ? "recovered" : "none");
const now = ref(Date.now());
let clock: ReturnType<typeof setInterval> | null = null;
let warnTimer: ReturnType<typeof setInterval> | null = null;

let hadOpen = false;
watch(openEvents, (list) => {
  if (hadOpen && !list.length) warn.recoveredUntil = Date.now() + 10000;     // 全部恢復：顯示 10 秒
  hadOpen = list.length > 0;
}, { immediate: true });

// ─────────────────────────── 來源：一頁放得下幾列就放幾列、15 秒換頁 ───────────────────────────
// 列數用實際高度算（寫死 6 列在 1080p 有 WARNING 時最後一列會被框裁掉）
const ROW_PX = 56;
const srcList = ref<HTMLElement | null>(null);
const srcPer = ref(5);
let srcRo: ResizeObserver | null = null;
function measureSrc() {
  const el = srcList.value;
  if (!el) return;
  const row = el.querySelector<HTMLElement>(".ai-row");
  srcPer.value = Math.max(1, Math.floor(el.clientHeight / (row?.offsetHeight || ROW_PX)));
}
const srcPage = ref(0);
const sources = computed(() => batch.value?.sources ?? []);
const srcPages = computed(() => Math.max(1, Math.ceil(sources.value.length / srcPer.value)));
const srcShown = computed(() => {
  const p = srcPage.value % srcPages.value;
  return sources.value.slice(p * srcPer.value, (p + 1) * srcPer.value);
});
const srcBad = computed(() => sources.value.filter((s) => s.state === "stale" || s.state === "failed").length);
let srcTimer: ReturnType<typeof setInterval> | null = null;

// ─────────────────────────── 標題列右側（原本的底列拿掉，讓出高度給現況播報：使用者 10-26） ───────────────────────────
/** 只有不正常時才寫狀態（產生失敗、過期、系統摘要、產生中…）；正常的 AI 摘要不多佔字 */
const flag = computed<{ text: string; tone: "warn" | "muted" } | null>(() => {
  const s = st.value;
  if (paused.value) return { text: t("warroom.intel.text_paused"), tone: "warn" };
  if (!s) return { text: t("warroom.intel.st_loading"), tone: "muted" };
  if (s.generating && (!batch.value || s.stale)) return { text: t("warroom.intel.st_generating"), tone: "muted" };
  if (s.last_error_code && batch.value) return { text: t("warroom.intel.st_failed", { at: hmOf(s.last_attempt_at) }), tone: "warn" };
  if (s.stale) return { text: t("warroom.intel.st_stale"), tone: "warn" };
  if (!batch.value) return { text: t("warroom.intel.st_none"), tone: "muted" };
  if (batch.value.status === "empty") return { text: t("warroom.intel.st_empty"), tone: "muted" };
  if (batch.value.origin === "template") return { text: t(s.ai_enabled ? "warroom.intel.st_template" : "warroom.intel.st_no_ai"), tone: "warn" };
  return null;
});
/** 在哪裡：VM 寫節點，實體裝置寫交換器的接入埠（後端從 MAC 表挑看到最少 MAC 的那個埠） */
function whereText(e: IntelEvent): string {
  const w = e.where;
  if (!w) return "";
  if (w.kind === "vm") return t(w.node ? "warroom.intel.where_vm_node" : "warroom.intel.where_vm", { vm: w.vm, node: w.node ?? "" });
  return t(w.vlan != null ? "warroom.intel.where_port_vlan" : "warroom.intel.where_port",
           { switch: w.switch, port: w.port, vlan: w.vlan ?? "" });
}
const sourceLabel = (state: string) => t(`warroom.intel.src_${state}`);
const pinnedLabel = (p: { kind: string; category?: string; label: string }) =>
  p.kind === "anomaly" ? anomalyCategoryLabel(p.category ?? "", t, te) : p.label;
const pinnedState = (p: { kind: string; state: string }) =>
  p.kind === "anomaly" ? t("warroom.intel.pin_ongoing") : sourceLabel(p.state);

onMounted(() => {
  document.addEventListener("visibilitychange", onVisibility);
  clock = setInterval(() => { now.value = Date.now(); }, 1000);
  // 整個戰情室暫停時右邊兩塊也不換（使用者 10-26）
  warnTimer = setInterval(() => { if (!paused.value && openEvents.value.length > 1) warn.i += 1; }, WARN_ROTATE_MS);
  srcTimer = setInterval(() => { if (!paused.value && srcPages.value > 1) srcPage.value += 1; }, 15000);
  if (typeof ResizeObserver !== "undefined") {
    srcRo = new ResizeObserver(measureSrc);
    watch(srcList, (el) => { srcRo?.disconnect(); if (el) { srcRo?.observe(el); measureSrc(); } }, { immediate: true });
  }
  if (batch.value?.items) start();
  if (!props.preview) {
    const loop = async () => {
      await poll();
      pollTimer = setTimeout(loop, st.value?.generating ? POLL_FAST_MS : POLL_MS);
    };
    void loop();
  }
});
onBeforeUnmount(() => {
  stop();
  srcRo?.disconnect();
  document.removeEventListener("visibilitychange", onVisibility);
  if (pollTimer) clearTimeout(pollTimer);
  for (const h of [clock, warnTimer, srcTimer]) if (h) clearInterval(h);
});
watch(() => props.preview?.state, (s) => { if (s?.batch) { st.value = s; adopt(s.batch); } });
</script>

<template>
  <div class="ai" :class="[`ai--${cfg.layout}`, `fx-${cfg.effects}`, `sl-${cfg.scanlines}`, `nz-${cfg.noise}`,
                          { reduce, 'no-cursor': !cfg.cursor }]" :style="vars" data-testid="wr-intel">
    <!-- 專注模式：標題下預留一列給 WARNING（沒有事件時顯示低對比的摘要，不會讓段落跳動） -->
    <div v-if="cfg.layout === 'focus'" class="ai-strip" :class="{ warn: warnActive, breathe: breathing }"
         data-testid="wr-intel-strip">
      <div :key="warnKey" class="ai-swap">
        <i class="ai-scan" aria-hidden="true" />
        <template v-if="warnActive && shownEvent">
          <b class="ai-warn-k">WARNING</b><span>·</span>
          <span>{{ t("warroom.intel.warn_count", { n: openEvents.length }) }}</span><span class="ai-sep">|</span>
          <span class="ai-atom">{{ shownEvent.ip || shownEvent.label }}</span>
          <span>{{ anomalyCategoryLabel(shownEvent.category, t, te) }}</span><span class="ai-sep">|</span>
          <template v-if="whereText(shownEvent)"><span class="ai-where">{{ whereText(shownEvent) }}</span><span class="ai-sep">|</span></template>
          <span>{{ hmOf(shownEvent.first_seen_at) }}</span><span class="ai-sep">|</span>
          <span :class="`ev-${shownEvent.state}`">{{ t(`warroom.intel.ev_${shownEvent.state}`) }}</span>
        </template>
        <template v-else-if="warnActive">
          <span class="ev-resolved">{{ t("warroom.intel.recovered") }}</span>
        </template>
        <template v-else>
          <span>{{ t("warroom.intel.watch_n", { n: batch?.pinned.length ?? 0 }) }}</span><span class="ai-sep">|</span>
          <span>{{ t("warroom.intel.src_bad_n", { n: srcBad }) }}</span>
        </template>
      </div>
    </div>

    <div class="ai-body">
      <!-- CH-01 現況播報：標題列右側是播報序號、摘要時間、下次更新，不正常時多一個狀態 -->
      <section class="ai-panel ai-main" data-testid="wr-intel-main">
        <header v-if="cfg.layout === 'three_panel' || flag" class="ai-h">
          <template v-if="cfg.layout === 'three_panel'">
            <span class="ai-ht">{{ t("warroom.intel.ch_main") }}</span>
          </template>
          <!-- 只有不正常時才寫狀態（產生失敗、過期、系統摘要、暫停）；摘要時間在每一則左邊、序號與下次更新對看板沒有用（使用者 10-26） -->
          <span class="ai-meta" data-testid="wr-intel-meta">
            <span v-if="flag" class="ai-flag" :class="`f-${flag.tone}`" data-testid="wr-intel-flag">{{ flag.text }}</span>
          </span>
        </header>
        <div ref="parasEl" class="ai-paras">
          <div v-if="!cur" class="ai-empty">{{ st?.generating ? t("warroom.intel.st_generating") : t("warroom.intel.st_none") }}</div>
          <article v-for="(p, i) in done" :key="p.key + i" class="ai-p old" :class="{ ev: p.event }">
            <div class="ai-gutter"><span class="ai-t">[{{ p.time }}]</span><span class="ai-l">{{ p.label }}</span></div>
            <div class="ai-tx">{{ p.text }}</div>
          </article>
          <article v-if="cur" :key="cur.key" class="ai-p now" :class="{ ev: cur.event }" data-testid="wr-intel-current">
            <div class="ai-gutter"><span class="ai-t">[{{ cur.time }}]</span><span class="ai-l">{{ cur.label }}</span></div>
            <div class="ai-tx"><template v-for="(pc, j) in curPieces" :key="j"><span v-if="pc.atomic" class="ai-atom">{{ pc.text }}</span><template v-else>{{ pc.text }}</template></template><span class="ai-cur" :class="{ typing, still: paused }" aria-hidden="true" /></div>
          </article>
        </div>
      </section>

      <div v-if="cfg.layout === 'three_panel'" class="ai-side">
        <!-- CH-02 持續關注；有重大事件時這一塊變成局部 WARNING（大小不變），持續呼吸燈 -->
        <section class="ai-panel ai-watch" :class="{ warn: warnActive, breathe: breathing }" data-testid="wr-intel-watch">
          <div :key="warnKey" class="ai-swap ai-swap--col">
            <i class="ai-scan" aria-hidden="true" />
            <header class="ai-h">
              <template v-if="warnActive">
                <b class="ai-warn-k">WARNING</b><span class="ai-ht">· {{ t("warroom.intel.warn_count", { n: openEvents.length }) }}</span>
              </template>
              <template v-else><span class="ai-ht">{{ t("warroom.intel.ch_watch") }}</span></template>
            </header>
            <div v-if="warnActive && shownEvent" class="ai-ev" data-testid="wr-intel-warning">
              <div class="ai-ev__name">{{ anomalyCategoryLabel(shownEvent.category, t, te) }}</div>
              <div class="ai-ev__subj ai-atom">{{ shownEvent.ip || shownEvent.label }}</div>
              <div v-if="whereText(shownEvent)" class="ai-ev__where" data-testid="wr-intel-where">{{ whereText(shownEvent) }}</div>
              <div class="ai-ev__meta">
                {{ t("warroom.intel.ev_at", { at: hmOf(shownEvent.first_seen_at) }) }} ·
                <span :class="`ev-${shownEvent.state}`">{{ t(`warroom.intel.ev_${shownEvent.state}`) }}</span>
              </div>
              <div v-if="events?.checked_at" class="ai-ev__src">{{ t("warroom.intel.ev_checked", { at: hmOf(events.checked_at) }) }}</div>
            </div>
            <div v-else-if="warnActive" class="ai-ev"><div class="ai-ev__name ev-resolved">{{ t("warroom.intel.recovered") }}</div></div>
            <ul v-else class="ai-rows">
              <li v-for="(p, i) in batch?.pinned ?? []" :key="`${p.id}:${p.state}`" class="ai-row ai-in" :style="{ '--d': `${i * 70}ms` }">
                <span class="ai-row__k">{{ pinnedLabel(p) }}</span>
                <span class="ai-row__v ai-atom">{{ p.ip || "" }}</span>
                <span class="ai-row__s">{{ pinnedState(p) }}</span>
              </li>
              <li v-if="batch && !batch.pinned.length" class="ai-row ai-muted">{{ t("warroom.intel.watch_none") }}</li>
              <li v-else-if="!batch" class="ai-row ai-muted">{{ t("warroom.intel.st_generating") }}</li>
            </ul>
          </div>
        </section>
        <!-- CH-03 來源狀態：總數與過期數固定看得到；值變了那一格掃描換字，換頁時逐列進場 -->
        <section class="ai-panel ai-src" data-testid="wr-intel-sources">
          <header class="ai-h"><span class="ai-ht">{{ t("warroom.intel.ch_sources") }}</span>
            <span :key="`${sources.length}:${srcBad}`" class="ai-hr ai-flip">{{ batch ? t("warroom.intel.src_total", { n: sources.length, bad: srcBad }) : "" }}</span></header>
          <ul ref="srcList" class="ai-rows ai-src__rows">
            <li v-for="(s, i) in srcShown" :key="`${srcPage}:${s.id}`" class="ai-row ai-in" :style="{ '--d': `${i * 70}ms` }">
              <span class="ai-row__k">{{ s.name }}</span>
              <span :key="hmOf(s.last_success_at)" class="ai-row__v ai-flip">{{ hmOf(s.last_success_at) }}</span>
              <span :key="s.state" class="ai-row__s ai-flip" :class="`src-${s.state}`">{{ sourceLabel(s.state) }}</span>
            </li>
            <li v-if="!batch" class="ai-row ai-muted">{{ t("warroom.intel.st_generating") }}</li>
            <li v-else-if="!sources.length" class="ai-row ai-muted">{{ t("warroom.intel.src_none") }}</li>
          </ul>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* isolation：內部的 z-index 不外溢，按 H 的按鍵說明才蓋得住這個主題（10-26 使用者截圖：說明被內容壓住） */
.ai { --ai-line: color-mix(in srgb, var(--ai-accent) 38%, transparent); position: relative; height: 100%; isolation: isolate;
  display: flex; flex-direction: column; gap: 16px; background: var(--ai-bg); color: var(--ai-text);
  padding: 18px 22px; box-sizing: border-box; overflow: hidden; font-size: 30px; line-height: 1.55; }
.ai-body { flex: 1; min-height: 0; display: grid; gap: 24px; position: relative; z-index: 1; }
.ai--three_panel .ai-body { grid-template-columns: 68fr 32fr; }
.ai--focus .ai-body { grid-template-columns: 1fr; }
.ai-panel { position: relative; border: 1px solid var(--ai-line); padding: 14px 20px; min-height: 0; overflow: hidden;
  display: flex; flex-direction: column; }
/* 角標：細線、平面（不照抄設計稿的漸層或粗框） */
.ai-panel::before, .ai-panel::after { content: ""; position: absolute; width: 14px; height: 14px; border: 2px solid var(--ai-accent); }
.ai-panel::before { top: -1px; left: -1px; border-right: 0; border-bottom: 0; }
.ai-panel::after { bottom: -1px; right: -1px; border-left: 0; border-top: 0; }
.ai--focus .ai-panel { border: 0; padding: 0 40px; }
.ai--focus .ai-panel::before, .ai--focus .ai-panel::after { display: none; }
.ai-h { display: flex; align-items: baseline; gap: 14px; padding-bottom: 10px; margin-bottom: 12px;
  border-bottom: 1px solid var(--ai-line); font-size: 24px; }
.ai-ht { color: var(--ai-sec); font-weight: 700; }
.ai-hr { margin-left: auto; color: var(--ai-sec); font-size: 20px; white-space: nowrap; }
.ai-paras { flex: 1; min-height: 0; display: flex; flex-direction: column; justify-content: flex-start; gap: 12px; overflow: hidden; }
.ai-paras.sliding > .ai-p { transform: translateY(var(--slide)); transition: transform .5s cubic-bezier(.3, .7, .2, 1); }
.ai-paras .ai-p.leaving { opacity: 0 !important; transition: opacity .45s ease, transform .5s cubic-bezier(.3, .7, .2, 1); }
.ai-empty { color: var(--ai-sec); font-size: 24px; margin: auto 0; }
.ai-p { display: grid; grid-template-columns: 190px minmax(0, 1fr); gap: 22px; padding: 10px 12px; }
.ai-p.old { opacity: .55; }
.ai-p.old + .ai-p.old, .ai-p.old + .ai-p.now { border-top: 1px solid color-mix(in srgb, var(--ai-line) 60%, transparent); }
.ai-p.now { background: color-mix(in srgb, var(--ai-accent) 10%, transparent); box-shadow: inset 3px 0 0 var(--ai-accent);
  animation: ai-in .12s ease-out; }
.ai-p.now .ai-tx { color: var(--ai-accent); }
/* 重大事件插播：字色跟一般的目前這則一樣（使用者 10-26：「為何有時字是綠的有時是白的」），只用紅色標記與紅色標籤區分 */
.ai-p.now.ev { box-shadow: inset 3px 0 0 var(--ai-crit); }
.ai-p.ev .ai-l { color: var(--ai-crit); font-weight: 700; }
.reduce .ai-p.now { animation: none; }
@keyframes ai-in { from { opacity: 0; } to { opacity: 1; } }
.ai-gutter { display: flex; flex-direction: column; gap: 2px; font-size: 20px; color: var(--ai-sec); }
.ai-t { font-family: var(--wr-num); }
.ai-l { overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.ai-tx { overflow-wrap: anywhere; }
.ai-atom { white-space: nowrap; font-family: var(--wr-num); }

.ai--focus .ai-tx { font-size: 40px; line-height: 1.5; }
.ai--focus .ai-p { grid-template-columns: 220px minmax(0, 1fr); background: none; box-shadow: none; }
.ai--focus .ai-p.now { box-shadow: inset 3px 0 0 var(--ai-accent); background: none; }
/* 游標：打字時常亮，停下時 600ms 亮暗；只有目前插入點一個 */
.ai-cur { display: inline-block; width: .55em; height: 1.05em; margin-left: 4px; vertical-align: -0.15em; background: var(--ai-accent);
  animation: ai-blink 1.2s steps(1) infinite; }
.ai-cur.typing { animation: none; }
.no-cursor .ai-cur, .reduce .ai-cur { animation: none; }
.no-cursor .ai-cur { display: none; }
@keyframes ai-blink { 50% { opacity: 0; } }
.ai-side { display: grid; grid-template-rows: minmax(0, 1.3fr) minmax(0, 1fr); gap: 24px; min-height: 0; }
.ai-rows { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; font-size: 24px; }
.ai-row { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr) auto; gap: 14px; align-items: baseline;
  padding: 9px 0; border-bottom: 1px solid color-mix(in srgb, var(--ai-line) 55%, transparent); }
.ai-row__k { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ai-row__v { color: var(--ai-sec); font-family: var(--wr-num); overflow: hidden; text-overflow: ellipsis; }
.ai-row__s { font-weight: 700; color: var(--ai-warn); white-space: nowrap; }
.ai-row__s.src-fresh { color: var(--ai-accent); }
.ai-row__s.src-failed { color: var(--ai-crit); }
.ai-row__s.src-not_configured, .ai-row__s.src-unknown { color: var(--ai-sec); }
.ai-muted { color: var(--ai-sec); display: block; }
.ai-src__rows { flex: 1; min-height: 0; overflow: hidden; }
/* 局部 WARNING：只在這一塊；紅框與淡紅底 2 秒一個週期慢慢明暗，文字常亮 */
.ai-watch.warn { border-color: var(--ai-crit); background: color-mix(in srgb, var(--ai-crit) 9%, transparent); }
.ai-watch.warn::before, .ai-watch.warn::after { border-color: var(--ai-crit); }
.ai-watch.warn .ai-h { border-bottom-color: color-mix(in srgb, var(--ai-crit) 50%, transparent); }
.ai-warn-k { color: var(--ai-crit); letter-spacing: .08em; }
.ai-ev { display: flex; flex-direction: column; gap: 6px; font-size: 26px; }
.ai-ev__name { font-weight: 700; font-size: 30px; }
.ai-ev__subj { font-size: 30px; }
.ai-ev__meta, .ai-ev__src { color: var(--ai-sec); font-size: 22px; }
.ai-ev__where { font-size: 24px; color: var(--ai-text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ai-where { overflow: hidden; text-overflow: ellipsis; }
.ev-open { color: var(--ai-crit); font-weight: 700; }
.ev-unknown { color: var(--ai-warn); font-weight: 700; }
.ev-resolved { color: var(--ai-accent); font-weight: 700; }
.ai-strip { position: relative; z-index: 1; height: 56px; display: flex; align-items: center; gap: 12px; padding: 0 18px;
  border: 1px solid var(--ai-line); color: var(--ai-sec); font-size: 22px; white-space: nowrap; overflow: hidden; }
.ai-strip.warn { border-color: var(--ai-crit); background: color-mix(in srgb, var(--ai-crit) 9%, transparent); color: var(--ai-text); }
.ai-sep { opacity: .5; }
/* 標題列右側：序號、摘要時間、下次更新；不正常時前面多一個狀態 */
.ai-meta { margin-left: auto; display: flex; align-items: baseline; gap: 22px; color: var(--ai-sec); font-size: 20px; white-space: nowrap; }
.ai-num { font-family: var(--wr-num); }
.ai-flag { font-weight: 700; }
.ai-flag.f-warn { color: var(--ai-warn); }
.ai-flag.f-muted { color: var(--ai-sec); }

/* WARNING 呼吸燈：紅框、底色、外光暈一起慢慢亮暗（2.4 秒一個週期），文字常亮 */
.breathe { animation: ai-breathe 2.4s ease-in-out infinite; }
.breathe::before, .breathe::after { animation: ai-breathe-corner 2.4s ease-in-out infinite; }
@keyframes ai-breathe {
  0%, 100% { border-color: color-mix(in srgb, var(--ai-crit) 45%, transparent);
    background-color: color-mix(in srgb, var(--ai-crit) 5%, transparent);
    box-shadow: 0 0 0 0 transparent, inset 0 0 0 0 transparent; }
  50% { border-color: var(--ai-crit); background-color: color-mix(in srgb, var(--ai-crit) 20%, transparent);
    box-shadow: 0 0 34px 2px color-mix(in srgb, var(--ai-crit) 50%, transparent),
                inset 0 0 30px color-mix(in srgb, var(--ai-crit) 28%, transparent); }
}
@keyframes ai-breathe-corner { 50% { filter: drop-shadow(0 0 6px var(--ai-crit)); } }

/* 內容換了：一道掃描線由左往右掃過，新內容跟著顯現（不用亂碼解碼、不抖動，字始終讀得到） */
.ai-swap { position: relative; display: flex; align-items: center; gap: 12px; min-width: 0; flex: 1;
  animation: ai-reveal .7s cubic-bezier(.2, .7, .2, 1) both; }
.ai-swap--col { flex-direction: column; align-items: stretch; gap: 0; height: 100%; }
.ai-scan { position: absolute; top: -4px; bottom: -4px; left: 0; width: 3px; pointer-events: none; z-index: 2;
  background: var(--ai-accent); box-shadow: 0 0 14px 3px color-mix(in srgb, var(--ai-accent) 70%, transparent);
  animation: ai-scan-x .7s cubic-bezier(.2, .7, .2, 1) both; }
.warn .ai-scan { background: var(--ai-crit); box-shadow: 0 0 14px 3px color-mix(in srgb, var(--ai-crit) 70%, transparent); }
@keyframes ai-reveal { from { clip-path: inset(0 100% 0 0); } to { clip-path: inset(0 0 0 0); } }
@keyframes ai-scan-x { from { left: 0; opacity: 1; } 90% { opacity: 1; } to { left: 100%; opacity: 0; } }

/* 數值變了：新的字由下方滑入、帶一下光暈後回到原色 */
.ai-flip { display: inline-block; animation: ai-flip-in .55s cubic-bezier(.2, .8, .2, 1) both; }
@keyframes ai-flip-in {
  from { transform: translateY(70%); opacity: 0; filter: blur(3px); text-shadow: 0 0 14px var(--ai-accent); }
  60% { opacity: 1; filter: none; text-shadow: 0 0 12px var(--ai-accent); }
  to { transform: none; text-shadow: none; }
}
/* 換頁、清單換了：逐列從左側滑入（每列錯開 70ms） */
.ai-in { animation: ai-row-in .45s cubic-bezier(.2, .8, .2, 1) var(--d, 0ms) both; }
@keyframes ai-row-in { from { opacity: 0; transform: translateX(-24px); } to { opacity: 1; transform: none; } }

.reduce .breathe, .reduce .breathe::before, .reduce .breathe::after, .reduce .ai-swap, .reduce .ai-scan,
.reduce .ai-flip, .reduce .ai-in { animation: none; }
.reduce .ai-scan { display: none; }
</style>
