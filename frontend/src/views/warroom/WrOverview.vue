<script setup lang="ts">
/**
 * 01 維運總覽：依站台分組的子網路狀態矩陣（一格一個子網路；太多時一格代表多個並標示），
 * 右側優先處理事項（管理員限定；沒有權限時顯示「無權限檢視」，不帶數量）。
 */
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import type { PageData } from "@/api/warroom";
import { anomalyCategoryLabel } from "./categories";
import WrMatrix from "./WrMatrix.vue";
import WrName from "./WrName.vue";
import { usePageTransition } from "./paged";
import type { IpHints } from "./hints";
import WrHints from "./WrHints.vue";
import WrIco from "./WrIco.vue";
import { SECTION_ICONS, categoryIcon } from "./wrIcons";

const props = defineProps<{ data: PageData }>();
const pageTrans = usePageTransition();     // 換資料頁只換分頁的那一區（paged.ts）
const { t, te } = useI18n();

interface Group { id: string | null; name: string | null; counts: Record<string, number>; subnets: number; unit: number; cells: string[] }
interface Prio { kind: string; category?: string; value: string; days?: number; severity: string; at: string | null; hints?: IpHints }

const groups = computed(() => (props.data.groups as Group[]) ?? []);
const prio = computed(() => (props.data.priority as { state: string; items?: Prio[]; total?: number }) ?? { state: "empty" });
const STATES = ["ok", "warn", "anomaly", "stale", "unscanned"] as const;

function groupName(g: Group): string {
  if (g.name) return g.name;
  return t(`warroom.unassigned_${props.data.group_by as string}`);
}
function prioTitle(p: Prio): string {
  if (p.kind === "cert") return t("warroom.prio_cert");
  if (p.kind === "dns") return t("warroom.prio_dns");
  return anomalyCategoryLabel(p.category ?? "", t, te);
}
function prioIcon(p: Prio) {
  if (p.kind === "cert") return SECTION_ICONS.cert;
  if (p.kind === "dns") return SECTION_ICONS.dns;
  return categoryIcon(p.category ?? "");
}
function relTime(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const now = new Date();
  const hm = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  const sameDay = d.toDateString() === now.toDateString();
  return sameDay ? t("warroom.today_at", { time: hm }) : d.toLocaleDateString([], { month: "2-digit", day: "2-digit" }) + " " + hm;
}
</script>

<template>
  <!-- 資訊展示模式（11-03）：格子不分狀態（後端已經都給 ok）、不畫各狀態數量與優先處理事項 -->
  <div class="ov" :class="{ 'ov--info': data.info_mode }">
    <Transition :name="pageTrans" mode="out-in"><div :key="data.page" class="ov-groups">
      <div v-if="!groups.length" class="wr-empty">{{ t("warroom.no_subnets") }}</div>
      <div v-for="g in groups" :key="g.id ?? 'none'" class="ov-group" data-testid="wr-group">
        <div class="ov-name">
          <WrName :text="groupName(g)" class="ov-name__t" />
          <div class="ov-name__m">{{ t("warroom.subnets_n", { n: g.subnets.toLocaleString() }) }}</div>
        </div>
        <div class="ov-matrix">
          <WrMatrix :cells="g.cells" :cols="12" :size="34" :gap="8" />
          <div v-if="g.unit > 1" class="wr-unit">{{ t("warroom.unit_subnets", { n: g.unit }) }}</div>
        </div>
        <div v-if="!data.info_mode" class="ov-counts">
          <div v-for="s in STATES" :key="s" class="ov-count" :class="{ zero: !g.counts[s] }">
            <span class="wr-swatch wr-swatch--sm" :class="`s-${s}`" />
            <span class="ov-count__n">{{ (g.counts[s] ?? 0).toLocaleString() }}</span>
            <span class="ov-count__l">{{ t(`warroom.state_${s}`) }}</span>
          </div>
        </div>
      </div>
    </div>
    </Transition>

    <aside v-if="!data.info_mode" class="ov-side">
      <div class="wr-h2"><WrIco :icon="SECTION_ICONS.priority" />{{ t("warroom.priority") }}<span v-if="prio.total" class="wr-h2__n">{{ prio.total }}</span></div>
      <div v-if="prio.state === 'forbidden'" class="wr-muted">{{ t("warroom.forbidden") }}</div>
      <div v-else-if="!prio.items?.length" class="wr-muted">{{ t("warroom.no_priority") }}</div>
      <div v-for="(p, i) in prio.items ?? []" :key="i" class="ov-prio" :class="`sev-${p.severity}`" data-testid="wr-prio">
        <div class="ov-prio__top">
          <span class="ov-prio__t"><WrIco :icon="prioIcon(p)" />{{ prioTitle(p) }}</span>
          <span class="ov-prio__s">{{ t(`warroom.sev_${p.severity}`) }}</span>
        </div>
        <div class="ov-prio__v">
          <template v-if="p.kind === 'cert'">
            <WrName :text="p.value" :lines="1" />
            <div class="ov-prio__days">
              {{ p.days != null && p.days < 0 ? t("warroom.cert_expired") : t("warroom.days_left", { n: p.days }) }}
            </div>
          </template>
          <template v-else>
            <WrName :text="p.value" />
            <WrHints :hints="p.hints" />
          </template>
        </div>
        <div class="ov-prio__at">{{ relTime(p.at) }}</div>
      </div>
    </aside>
  </div>
</template>

<style scoped>
/* 列高固定為可用高度：右側優先事項很多時曾把整列撐高，左邊第三個地點被推出畫面（10-22） */
.ov { display: grid; grid-template-columns: 1fr 520px; grid-template-rows: minmax(0, 1fr); gap: 48px; height: 100%; }
.ov--info { grid-template-columns: 1fr; }
.ov--info .ov-group { grid-template-columns: 400px 1fr; }
.ov-groups { display: flex; flex-direction: column; min-height: 0; }
.ov-group { display: grid; grid-template-columns: 400px 496px 1fr; gap: 40px; align-items: center;
  padding: 12px 0; border-bottom: 1px solid var(--wr-line); flex: 1; min-height: 0; }
.ov-group:last-child { border-bottom: 0; }
.ov-name__t { font-size: 38px; font-weight: 700; color: var(--wr-ink); }
.ov-name__m { margin-top: 10px; font-size: 22px; color: var(--wr-muted); font-family: var(--wr-num); }
.ov-matrix { display: flex; flex-direction: column; gap: 10px; }
.ov-counts { display: grid; grid-template-rows: repeat(5, auto); gap: 2px; justify-self: start; }
.ov-count { display: grid; grid-template-columns: 20px 84px auto; align-items: center; gap: 12px; font-size: 21px; line-height: 1.3; }
.ov-count.zero { opacity: .45; }
.ov-count__n { font-family: var(--wr-num); font-size: 26px; text-align: right; font-variant-numeric: tabular-nums; }
.ov-count__l { color: var(--wr-muted); }
.ov-side { border-left: 1px solid var(--wr-line); padding-left: 40px; display: flex; flex-direction: column; gap: 18px;
  min-height: 0; overflow: hidden; }
/* 嚴重度看標題顏色（珊瑚橘／淡紫），不另加左側色條（使用者 10-21：色塊旁加色條看起來很 low） */
.ov-prio { background: var(--wr-panel); padding: 18px 22px; display: grid; gap: 6px; }
.ov-prio__top { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
.ov-prio__t { font-size: 30px; font-weight: 700; color: var(--wr-lilac); }
.sev-high .ov-prio__t { color: var(--wr-coral); }
.ov-prio__s { font-size: 20px; color: var(--wr-muted); white-space: nowrap; }
.ov-prio__v { font-size: 30px; font-weight: 600; font-family: var(--wr-num); color: var(--wr-ink); }
.ov-prio__days { font-size: 30px; }
.ov-prio__at { font-size: 20px; color: var(--wr-muted); text-align: right; }
</style>
