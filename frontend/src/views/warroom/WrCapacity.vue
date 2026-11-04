<script setup lang="ts">
/**
 * 02 位址容量：2×2 四區，每區名稱在上（固定兩行）、方格在中、配置率在下。
 * - 一格一個位址時照位址順序；彙整時標「每格 N 個位址」
 * - 集區標「有效租約使用率」（與子網路的配置率分開）
 * - 危險／警戒是方格外的標籤，不改變格子顏色；資料逾時整塊空心
 */
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import type { PageData } from "@/api/warroom";
import WrMatrix from "./WrMatrix.vue";
import WrName from "./WrName.vue";
import { usePageTransition } from "./paged";
import WrIco from "./WrIco.vue";
import { SECTION_ICONS } from "./wrIcons";

const props = defineProps<{ data: PageData }>();
const pageTrans = usePageTransition();     // 換資料頁只換分頁的那一區（paged.ts）
const { t } = useI18n();

interface Item {
  kind: "subnet" | "pool"; id: string; label: string; ident: string; cidr: string | null; ranges?: [string, string][]; server?: string | null; capacity: number;
  used: number; reserved: number; free: number; pct: number | null; level: string; scan: string; unit: number;
  order: string; cells: string[];
}
const items = computed(() => (props.data.items as Item[]) ?? []);
function pctText(p: number | null): string {
  if (p == null) return "—";
  return p >= 10 || p === 0 ? String(Math.round(p)) : p.toFixed(1);
}
</script>

<template>
  <Transition :name="pageTrans" mode="out-in"><div :key="data.page" class="cap">
    <div v-if="!items.length" class="wr-empty">{{ t("warroom.no_subnets") }}</div>
    <section v-for="it in items" :key="it.kind + it.id" class="cap-q" data-testid="wr-cap-item">
      <header class="cap-h">
        <span class="cap-h__ico"><WrIco :icon="it.kind === 'pool' ? SECTION_ICONS.pool : SECTION_ICONS.subnet" /></span><WrName :text="it.label" class="cap-h__n" />
        <div class="cap-h__id">
          <!-- 同一個網段設了好幾段範圍（issue #57）：一段一行，太多就只列前兩段 -->
          <template v-if="it.ranges && it.ranges.length > 1">
            <div v-for="r in it.ranges.slice(0, 2)" :key="r[0]" class="cap-h__ident cap-h__ident--multi"
                 data-testid="wr-cap-range">{{ r[0] }} - {{ r[1] }}</div>
            <div v-if="it.ranges.length > 2" class="cap-h__sub">{{ t("warroom.more_ranges", { n: it.ranges.length - 2 }) }}</div>
          </template>
          <div v-else class="cap-h__ident">{{ it.ident }}</div>
          <!-- 集區：標題跟子網路卡片一樣是子網路名稱，這裡標明是 DHCP 集區與發放的伺服器 -->
          <div v-if="it.kind === 'pool'" class="cap-h__sub">{{ t("warroom.dhcp_pool") }}<template v-if="it.server"> · {{ it.server }}</template></div>
          <div v-else class="cap-h__sub">{{ it.capacity.toLocaleString() }} {{ t("warroom.addresses") }}</div>
        </div>
      </header>
      <WrMatrix :cells="it.cells" :cols="32" :size="17" :gap="4" :hollow="it.scan === 'stale'" />
      <footer class="cap-f">
        <div class="cap-f__l">{{ it.kind === 'pool' ? t("warroom.lease_usage") : t("warroom.allocation") }}</div>
        <div class="cap-f__v">{{ pctText(it.pct) }}<span class="cap-f__pc">%</span></div>
        <div class="cap-f__tags">
          <span v-if="it.level && it.level !== 'ok'" class="wr-tag" :class="`lv-${it.level}`">
            {{ t(`warroom.level_${it.level}`) }}
          </span>
          <span v-if="it.scan === 'stale'" class="wr-tag lv-stale">{{ t("warroom.state_stale") }}</span>
          <span v-if="it.unit > 1" class="wr-unit">{{ t("warroom.unit_addresses", { n: it.unit }) }}</span>
        </div>
      </footer>
    </section>
  </div>
  </Transition>
</template>

<style scoped>
/* 列高固定為可用高度（minmax(0, …)）：某一區內容多時曾把整列撐高，另一區的下半部被推出畫面（10-22） */
.cap { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); grid-template-rows: minmax(0, 1fr) minmax(0, 1fr); height: 100%; }
.cap-q { padding: 16px 36px 12px 0; display: flex; flex-direction: column; gap: 12px; min-width: 0;
  border-bottom: 1px solid var(--wr-line); }
.cap-q:nth-child(2n) { padding-left: 36px; padding-right: 0; border-left: 1px solid var(--wr-line); }
.cap-q:nth-child(n+3) { border-bottom: 0; }
.cap-h { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 0 24px; align-items: start; }
.cap-h__ico { font-size: 30px; color: var(--wr-muted); line-height: 1.25; margin-right: -14px; }
.cap-h__n { font-size: 30px; font-weight: 700; color: var(--wr-ink); }
.cap-h__id { text-align: right; font-family: var(--wr-num); }
.cap-h__ident { font-size: 26px; color: var(--wr-ink); white-space: nowrap; }
.cap-h__ident--multi { font-size: 22px; line-height: 1.25; }
.cap-h__sub { font-size: 20px; color: var(--wr-muted); white-space: nowrap; margin-top: 4px; }
.cap-f { display: flex; align-items: baseline; gap: 18px; margin-top: auto; }
.cap-f__l { font-size: 24px; color: var(--wr-ink); white-space: nowrap; }
.cap-f__v { font-family: var(--wr-num); font-weight: 600; font-size: 72px; line-height: .9; color: var(--wr-accent);
  font-variant-numeric: tabular-nums; letter-spacing: -0.02em; }
.cap-f__pc { font-size: 44px; margin-left: 2px; }
.cap-f__tags { display: flex; gap: 12px; align-items: center; margin-left: auto; flex-wrap: wrap; justify-content: flex-end; }
</style>
