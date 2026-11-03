<script setup lang="ts">
/**
 * 08 IP 統計：左邊已登記 IP 的上線／離線／未知分布（精確數字＋共用尺度的方格），右邊子網路登記量排行
 * （每頁四個，換資料頁只換這一區），底部 IPv4／IPv6 登記數與統計口徑說明。
 * 數字與格數全部由後端算（services/warroom/stats.py）；三列方格共用同一個「每格 N 個」，不足一格算一格，
 * 剩下的格子畫成底軌，各列長度可以直接比較。未知是空心格，不用正常的綠色。
 * 稽核事件是操作紀錄、不是告警：指標用中性色（WarRoom.vue），這裡只寫口徑。
 */
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import type { PageData } from "@/api/warroom";
import { pad2 } from "@/utils/warroom";
import WrMatrix from "./WrMatrix.vue";
import WrName from "./WrName.vue";
import WrIco from "./WrIco.vue";
import { usePageTransition } from "./paged";
import { pctText, statusRowCells } from "./statsAvail";
import { SECTION_ICONS } from "./wrIcons";

const props = defineProps<{ data: PageData }>();
const pageTrans = usePageTransition();     // 換資料頁只換排行這一區（paged.ts）
const { t } = useI18n();

interface StatusRow { status: "online" | "offline" | "unknown"; count: number; cells: number; pct: number | null }
interface Status { state: string; total: number; unit: number; row_cells: number; row_cols: number; online_arp_only: number; rows: StatusRow[] }
interface RankItem { id: string; rank: number; label: string; cidr: string; version: number; count: number; pct: number | null }
interface Ranking { state: string; total: number; items: RankItem[] }
interface Defs { scope: "all" | "scoped" | "visible"; audit_window_hours: number; audit_scoped: boolean }

const status = computed(() => props.data.status as Status);
const ranking = computed(() => props.data.ranking as Ranking);
const families = computed(() => props.data.families as { v4: number; v6: number });
const defs = computed(() => props.data.definitions as Defs);
const auditVisible = computed(() => props.data.metrics?.audit_24h != null);

/** 方格狀態對應到 WrMatrix 既有的顏色語意：上線＝黃綠、離線＝珊瑚橘、未知＝空心格、底軌＝深灰 */
const CELL: Record<string, string> = { online: "ok", offline: "anomaly", unknown: "unknown" };
const rowCells = (r: StatusRow) =>
  statusRowCells(CELL[r.status], r.cells, status.value.row_cells).map((c) => (c === "track" ? "unscanned" : c));
const tone = (s: string) => (s === "online" ? "tone-accent" : s === "offline" ? "tone-coral" : "tone-ink");
const famTotal = computed(() => families.value.v4 + families.value.v6);
</script>

<template>
  <div class="st">
    <section class="st-dist" data-testid="wr-st-dist">
      <header class="st-h">
        <div class="wr-h2"><WrIco :icon="SECTION_ICONS.subnet" />{{ t("warroom.st_dist_title") }}</div>
        <div class="st-scale" data-testid="wr-st-scale">
          <span>{{ t("warroom.st_scale", { n: status.unit.toLocaleString() }) }}</span>
          <span class="st-scale__x">{{ t("warroom.st_scale_exact") }}</span>
        </div>
      </header>
      <div v-if="status.state === 'empty'" class="wr-empty">{{ t("warroom.st_no_ips") }}</div>
      <template v-else>
        <div v-for="r in status.rows" :key="r.status" class="st-row" data-testid="wr-st-row" :data-status="r.status">
          <!-- 名稱在上、數字在下：百萬筆的數字（1,234,567）也放得下，不會壓到名稱（e2e 大數字） -->
          <div class="st-row__n">
            <div class="st-row__l">
              <span class="st-sw" :class="`st-sw--${r.status}`" />{{ t(`warroom.st_${r.status}`) }}
              <!-- 資訊展示模式：只剩上線一列，不寫百分比（百分比等於在說其他的不在線） -->
              <span v-if="!data.info_mode" class="st-row__p">{{ pctText(r.pct) }}%</span>
            </div>
            <div class="wr-big st-row__v" :class="tone(r.status)" data-testid="wr-st-count">{{ r.count.toLocaleString() }}</div>
          </div>
          <WrMatrix :cells="rowCells(r)" :cols="status.row_cols" :size="16" :gap="4" />
        </div>
        <footer v-if="!data.info_mode" class="st-sum">
          <span data-testid="wr-st-sum">{{ t("warroom.st_sum", { n: status.total.toLocaleString() }) }}</span>
          <span v-if="status.online_arp_only" class="st-sum__arp">{{ t("warroom.st_arp_only", { n: status.online_arp_only.toLocaleString() }) }}</span>
        </footer>
      </template>
    </section>

    <aside class="st-rank" data-testid="wr-st-rank">
      <header class="st-h st-h--col">
        <div class="wr-h2"><WrIco :icon="SECTION_ICONS.subnet" />{{ t("warroom.st_rank_title") }}</div>
        <div class="st-sub">{{ t("warroom.st_rank_sub") }}</div>
      </header>
      <Transition :name="pageTrans" mode="out-in"><div :key="data.page" class="st-list">
        <div v-if="!ranking.items.length" class="wr-muted">{{ t("warroom.st_no_ips") }}</div>
        <div v-for="it in ranking.items" :key="it.id" class="st-item" data-testid="wr-st-item">
          <div class="wr-index st-item__i">{{ pad2(it.rank) }}</div>
          <div class="st-item__b">
            <WrName :text="it.label" class="st-item__n" />
            <div v-if="it.label !== it.cidr" class="st-item__c">{{ it.cidr }}</div>
          </div>
          <div class="st-item__r">
            <div class="wr-big st-item__v tone-accent">{{ it.count.toLocaleString() }}</div>
            <div class="st-item__p">{{ t("warroom.st_share", { p: pctText(it.pct) }) }}</div>
          </div>
        </div>
      </div></Transition>
    </aside>

    <footer class="st-foot">
      <div class="st-fam" data-testid="wr-st-families">
        <div class="st-fam__k">{{ t("warroom.st_families") }}</div>
        <div class="st-fam__i"><span class="st-fam__l"><span class="st-fam__sw st-fam__v4" />IPv4</span><span class="wr-big st-fam__v">{{ families.v4.toLocaleString() }}</span></div>
        <div class="st-fam__i"><span class="st-fam__l"><span class="st-fam__sw st-fam__v6" />IPv6</span><span class="wr-big st-fam__v">{{ families.v6.toLocaleString() }}</span></div>
        <div v-if="famTotal" class="st-fam__bar" aria-hidden="true">
          <span class="st-fam__v4" :style="{ flexGrow: families.v4 }" /><span class="st-fam__v6" :style="{ flexGrow: families.v6 }" />
        </div>
      </div>
      <div class="st-notes" data-testid="wr-st-notes">
        <div class="st-notes__h">{{ t(`warroom.st_scope_${defs.scope}`) }}</div>
        <div v-if="!data.info_mode">{{ t("warroom.st_note_registered") }}</div>
        <div v-if="auditVisible && !data.info_mode">{{ t("warroom.st_note_audit", { h: defs.audit_window_hours }) }}<template v-if="defs.audit_scoped">{{ t("warroom.st_note_audit_scoped") }}</template></div>
      </div>
    </footer>
  </div>
</template>

<style scoped>
.st { display: grid; grid-template-columns: minmax(0, 1fr) 620px; grid-template-rows: minmax(0, 1fr) auto; column-gap: 44px;
  height: 100%; }
.st-dist { display: flex; flex-direction: column; min-width: 0; min-height: 0; overflow: hidden; }
.st-h { display: flex; justify-content: space-between; align-items: baseline; gap: 24px; padding-bottom: 10px; }
.st-h--col { flex-direction: column; align-items: flex-start; gap: 4px; }
.st-scale { display: flex; flex-direction: column; align-items: flex-end; font-size: 21px; color: var(--wr-ink); text-align: right; }
.st-scale__x { color: var(--wr-muted); font-size: 19px; }
.st-sub { font-size: 21px; color: var(--wr-muted); }
.st-row { display: grid; grid-template-columns: 300px auto; align-items: center; gap: 16px; padding: 14px 0;
  border-top: 1px solid var(--wr-line); }
.st-row__l { font-size: 28px; font-weight: 700; display: flex; align-items: center; gap: 12px; white-space: nowrap; }
.st-sw { width: 20px; height: 20px; flex: none; box-sizing: border-box; }
.st-sw--online { background: var(--wr-accent); }
.st-sw--offline { background: var(--wr-coral); }
.st-sw--unknown { border: 2px solid var(--wr-outline); }
.st-row__n { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.st-row__v { font-size: 68px; }
.st-row__p { font-family: var(--wr-num); font-size: 21px; font-weight: 400; color: var(--wr-muted); font-variant-numeric: tabular-nums;
  margin-left: auto; }
.st-sum { display: flex; flex-wrap: wrap; gap: 6px 28px; padding-top: 12px; border-top: 1px solid var(--wr-line);
  font-size: 22px; color: var(--wr-ink); }
.st-sum__arp { color: var(--wr-muted); }
.st-rank { border-left: 1px solid var(--wr-line); padding-left: 40px; display: flex; flex-direction: column; min-width: 0;
  min-height: 0; overflow: hidden; }
.st-list { display: flex; flex-direction: column; }
.st-item { display: grid; grid-template-columns: 44px minmax(0, 1fr) auto; gap: 18px; align-items: center; padding: 10px 0;
  border-top: 1px solid var(--wr-line); }
.st-item__i { align-self: start; padding-top: 6px; }
.st-item__b { min-width: 0; display: flex; flex-direction: column; gap: 4px; }
/* 短名稱不必佔兩行高度（WrName 預設保留兩行）；長名稱照樣換到第二行 */
.st-item__n { font-size: 26px; font-weight: 700; min-height: 0 !important; }
.st-item__c { font-family: var(--wr-num); font-size: 21px; color: var(--wr-muted); overflow-wrap: anywhere; }
.st-item__r { display: flex; flex-direction: column; align-items: flex-end; gap: 4px; }
.st-item__v { font-size: 52px; }
.st-item__p { font-size: 19px; color: var(--wr-muted); white-space: nowrap; }
.st-foot { grid-column: 1 / -1; display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 44px;
  border-top: 1px solid var(--wr-line); padding-top: 14px; }
.st-fam { display: flex; align-items: center; gap: 28px; min-width: 0; }
.st-fam__k { font-size: 24px; font-weight: 700; white-space: nowrap; padding-right: 28px; border-right: 1px solid var(--wr-line); }
.st-fam__i { display: flex; align-items: baseline; gap: 12px; }
.st-fam__l { font-size: 22px; color: var(--wr-muted); font-family: var(--wr-num); display: inline-flex; align-items: center; gap: 8px; }
.st-fam__sw { width: 16px; height: 16px; display: inline-block; }
.st-fam__v { font-size: 42px; }
.st-fam__bar { flex: 1; min-width: 80px; height: 12px; display: flex; gap: 3px; }
.st-fam__v4 { background: var(--wr-accent); min-width: 3px; }
.st-fam__v6 { background: var(--wr-lilac); min-width: 3px; }
.st-fam__v4[style*="flex-grow: 0"], .st-fam__v6[style*="flex-grow: 0"] { display: none; }
.st-notes { font-size: 20px; color: var(--wr-muted); line-height: 1.4; display: flex; flex-direction: column; gap: 2px;
  padding-left: 28px; border-left: 1px solid var(--wr-line); }
.st-notes__h { font-size: 22px; color: var(--wr-ink); font-weight: 600; }
</style>
