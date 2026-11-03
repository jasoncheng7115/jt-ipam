<script setup lang="ts">
/**
 * 04 DNS／DHCP／憑證：左上 DNS 比對群組（每個來源一塊方格）、右上 DHCP 集區（有效租約使用率＋條帶）、
 * 左下最急的憑證（剩餘天數）、右下憑證到期區間（互斥）。
 * 三塊各自輪播（第 N 個子頁顯示各自的第 N 項），所以每塊標自己的「第幾項／共幾項」。
 * DNS 與憑證是管理員限定：沒有權限時顯示「無權限檢視」，不帶數量。
 */
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import type { PageData } from "@/api/warroom";
import { pad2 } from "@/utils/warroom";
import WrMatrix from "./WrMatrix.vue";
import WrName from "./WrName.vue";
import { usePageTransition } from "./paged";
import WrIco from "./WrIco.vue";
import { SECTION_ICONS } from "./wrIcons";

const props = defineProps<{ data: PageData }>();
const pageTrans = usePageTransition();     // 換資料頁只換分頁的那一區（paged.ts）
const { t } = useI18n();

interface Source { id: string; name: string; counts: Record<string, number>; total: number; unit: number; cells: string[] }
interface DnsSec { state: string; index?: number; total?: number;
  group?: { name: string; status: string | null; members: number; more_sources: number; diffs: number; confirmed: number; sources: Source[] } }
interface PoolSec { state: string; index?: number; total?: number;
  item?: { label: string; ident: string; cidr: string | null; server?: string | null; capacity: number; used: number; free: number; pct: number | null; level: string; unit: number; cells: string[] } }
interface CertSec { state: string; index?: number; total?: number; soon?: number; all?: number; buckets?: Record<string, number>;
  item?: { name: string; days: number; not_after: string } }

const dns = computed(() => props.data.dns as DnsSec);
const pool = computed(() => props.data.pool as PoolSec);
const certs = computed(() => props.data.certs as CertSec);
/** 資訊展示模式（11-03）：後端已經不分一致與否，只剩總數；憑證只給張數 */
const info = computed(() => !!props.data.info_mode);
const SRC_STATES = computed(() => (info.value ? ["ok"] : ["ok", "partial", "pending", "missing"]) as readonly string[]);
const BUCKETS = [
  { key: "expired", tone: "coral" }, { key: "d7", tone: "coral" }, { key: "d14", tone: "lilac" }, { key: "d30", tone: "ink" },
] as const;

/** 來源越多格子越小（版面寬度固定，不縮字） */
const srcSize = computed(() => {
  const n = dns.value.group?.sources.length ?? 1;
  return n <= 2 ? 20 : n === 3 ? 16 : 12;
});
function certTone(days: number): string {
  return days <= 7 ? "coral" : days <= 14 ? "lilac" : "ink";
}
function certStatus(days: number): string {
  if (days < 0) return t("warroom.cert_expired");
  if (days <= 7) return t("warroom.cert_renew");
  if (days <= 30) return t("warroom.cert_soon");
  return t("warroom.cert_valid");
}
function pctText(p: number | null | undefined): string {
  if (p == null) return "—";
  return p >= 10 || p === 0 ? String(Math.round(p)) : p.toFixed(1);
}
function idx(s: { index?: number; total?: number }): string {
  return s.total && s.total > 1 ? `${pad2(s.index ?? 1)} / ${pad2(s.total)}` : "";
}
function bucketCells(n: number): string[] {
  return Array.from({ length: 8 }, (_, i) => (i < n ? "fill" : "empty"));
}
</script>

<template>
  <div class="svc">
    <!-- DNS 比對群組 -->
    <Transition :name="pageTrans" mode="out-in"><section :key="data.page" class="svc-q svc-dns" data-testid="wr-svc-dns">
      <header class="svc-h">
        <div class="wr-h2"><WrIco :icon="SECTION_ICONS.dns" />{{ t("warroom.dns_group") }}</div>
        <div class="svc-legend">
          <span v-for="k in SRC_STATES" :key="k"><span class="wr-swatch wr-swatch--sm" :class="`s-${k}`" />{{ info ? t("warroom.info_records") : t(`warroom.dnsrc_${k}`) }}</span>
        </div>
        <span class="wr-index">{{ idx(dns) }}</span>
      </header>
      <div v-if="dns.state === 'forbidden'" class="wr-muted">{{ t("warroom.forbidden") }}</div>
      <div v-else-if="!dns.group" class="wr-muted">{{ t("warroom.no_dns_groups") }}</div>
      <template v-else>
        <div class="svc-dns__g">
          <WrName :text="dns.group.name" class="svc-name" />
          <span v-if="!info" class="wr-tag" :class="`dns-${dns.group.status ?? 'none'}`">{{ t(`warroom.dns_status_${dns.group.status ?? 'none'}`) }}</span>
        </div>
        <div class="svc-srcs" :style="{ gridTemplateColumns: `repeat(${Math.max(dns.group.sources.length, 1)}, minmax(0, 1fr))` }">
          <div v-for="s in dns.group.sources" :key="s.id" class="svc-src" data-testid="wr-dns-source">
            <WrName :text="s.name" class="svc-src__n" />
            <WrMatrix :cells="s.cells" :cols="12" :size="srcSize" :gap="4" />
            <!-- 「每格約 N 筆」跟數量放同一行：另起一行曾超出下方格線（10-22） -->
            <div class="svc-src__c">
              <span v-for="k in SRC_STATES" v-show="s.counts[k]" :key="k">
                <span class="wr-swatch wr-swatch--sm" :class="`s-${k}`" />{{ s.counts[k].toLocaleString() }}
              </span>
              <span v-if="s.unit > 1" class="wr-unit svc-src__u">{{ t("warroom.unit_records", { n: s.unit }) }}</span>
            </div>
          </div>
        </div>
        <div v-if="dns.group.more_sources" class="wr-unit">{{ t("warroom.more_sources", { n: dns.group.more_sources }) }}</div>
      </template>
    </section>
    </Transition>

    <!-- DHCP 集區 -->
    <Transition :name="pageTrans" mode="out-in"><section :key="data.page" class="svc-q svc-pool" data-testid="wr-svc-pool">
      <header class="svc-h">
        <div class="wr-h2"><WrIco :icon="SECTION_ICONS.dhcp" />{{ t("warroom.dhcp_pool") }}</div>
        <span class="wr-index">{{ idx(pool) }}</span>
      </header>
      <div v-if="!pool.item" class="wr-muted">{{ t("warroom.no_pools") }}</div>
      <template v-else>
        <div class="svc-pool__id">
          <WrName :text="pool.item.label" class="svc-name" />
          <div class="svc-pool__r">{{ pool.item.ident }}<span v-if="pool.item.server" class="wr-unit"> · {{ pool.item.server }}</span></div>
        </div>
        <div class="svc-pool__pct">
          <div class="svc-pool__l">{{ t("warroom.lease_usage") }}</div>
          <div class="wr-big svc-pool__v" :class="pool.item.level === 'crit' && !info ? 'tone-coral' : 'tone-accent'">
            {{ pctText(pool.item.pct) }}<span class="svc-pool__pc">%</span>
          </div>
          <span v-if="pool.item.level && pool.item.level !== 'ok'" class="wr-tag" :class="`lv-${pool.item.level}`">{{ t(`warroom.level_${pool.item.level}`) }}</span>
        </div>
        <WrMatrix :cells="pool.item.cells" :cols="20" :size="30" :gap="6" />
        <div class="svc-pool__nums">
          <div><span class="svc-k">{{ t("warroom.used_total") }}</span>
            <span class="svc-v">{{ pool.item.used.toLocaleString() }} / {{ pool.item.capacity.toLocaleString() }}</span></div>
          <div><span class="svc-k">{{ t("warroom.free_leases") }}</span><span class="svc-v">{{ pool.item.free.toLocaleString() }}</span></div>
          <span v-if="pool.item.unit > 1" class="wr-unit">{{ t("warroom.unit_addresses", { n: pool.item.unit }) }}</span>
        </div>
      </template>
    </section>
    </Transition>

    <!-- 資訊展示模式：只寫憑證張數（不寫到期日與剩餘天數），佔滿下面整列 -->
    <section v-if="info" class="svc-q svc-cert svc-cert--count" data-testid="wr-svc-cert-count">
      <header class="svc-h"><div class="wr-h2"><WrIco :icon="SECTION_ICONS.cert" />{{ t("warroom.certificate") }}</div></header>
      <div v-if="certs.state === 'forbidden'" class="wr-muted">{{ t("warroom.forbidden") }}</div>
      <div v-else class="svc-cert__row">
        <div class="svc-k">{{ t("warroom.info_certs_managed") }}</div>
        <div class="wr-big svc-cert__v tone-accent">{{ (certs.total ?? 0).toLocaleString() }}<span class="svc-cert__u">{{ t("warroom.info_unit_certs") }}</span></div>
      </div>
    </section>

    <!-- 最急的憑證 -->
    <Transition v-if="!info" :name="pageTrans" mode="out-in"><section :key="data.page" class="svc-q svc-cert" data-testid="wr-svc-cert">
      <header class="svc-h">
        <div class="wr-h2"><WrIco :icon="SECTION_ICONS.cert" />{{ t("warroom.certificate") }}</div>
        <span class="wr-index">{{ idx(certs) }}</span>
      </header>
      <div v-if="certs.state === 'forbidden'" class="wr-muted">{{ t("warroom.forbidden") }}</div>
      <div v-else-if="!certs.item" class="wr-muted">{{ t("warroom.no_certs") }}</div>
      <template v-else>
        <WrName :text="certs.item.name" class="svc-name" />
        <div class="svc-cert__row">
          <div class="svc-k">{{ certs.item.days < 0 ? t("warroom.expired_for") : t("warroom.days_remaining") }}</div>
          <div class="wr-big svc-cert__v" :class="`tone-${certTone(certs.item.days)}`">
            {{ Math.abs(certs.item.days) }}<span class="svc-cert__u">{{ t("warroom.unit_days") }}</span>
          </div>
          <div class="svc-cert__s" :class="`tone-${certTone(certs.item.days)}`">{{ certStatus(certs.item.days) }}</div>
        </div>
      </template>
    </section>
    </Transition>

    <!-- 到期區間 -->
    <section v-if="!info" class="svc-q svc-buckets" data-testid="wr-svc-buckets">
      <header class="svc-h"><div class="wr-h2"><WrIco :icon="SECTION_ICONS.certExpiry" />{{ t("warroom.cert_expiry") }}</div></header>
      <div v-if="certs.state === 'forbidden'" class="wr-muted">{{ t("warroom.forbidden") }}</div>
      <div v-else-if="!certs.buckets" class="wr-muted">{{ t("warroom.no_certs") }}</div>
      <div v-else class="svc-bk">
        <div v-for="b in BUCKETS" :key="b.key" class="svc-bk__c">
          <div class="svc-bk__l">{{ t(`warroom.bucket_${b.key}`) }}</div>
          <div class="wr-big svc-bk__v" :class="certs.buckets[b.key] ? `tone-${b.tone}` : 'tone-muted'">
            {{ pad2(certs.buckets[b.key] ?? 0) }}
          </div>
          <div class="svc-bk__s">
            <span v-for="(c, i) in bucketCells(certs.buckets[b.key] ?? 0)" :key="i" class="svc-bk__cell"
                  :class="c === 'fill' ? `tone-bg-${b.tone}` : ''" />
            <span v-if="(certs.buckets[b.key] ?? 0) > 8" class="svc-bk__more">+</span>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
/* 列高固定為可用高度（minmax(0, …)）：某一區內容多時曾把整列撐高，另一區的下半部被推出畫面（10-22） */
.svc { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); grid-template-rows: minmax(0, 1.35fr) minmax(0, 1fr); height: 100%; }
.svc-cert--count { grid-column: 1 / -1; }
.svc-q { padding: 10px 40px 14px 0; display: flex; flex-direction: column; gap: 12px; min-width: 0; min-height: 0;
  border-bottom: 1px solid var(--wr-line); overflow: hidden; }
.svc-q:nth-child(2n) { padding-left: 40px; padding-right: 0; border-left: 1px solid var(--wr-line); }
.svc-q:nth-child(n+3) { border-bottom: 0; padding-top: 16px; }
.svc-h { display: flex; justify-content: space-between; align-items: baseline; gap: 16px; }
/* 標題與頁碼不讓、圖例讓：「DNS 比對群組」曾被圖例擠成兩行、「01 / 04」也折行（10-25） */
.svc-h > .wr-h2, .svc-h > .wr-index { flex-shrink: 0; white-space: nowrap; }
.svc-name { font-size: 30px; font-weight: 600; color: var(--wr-ink); }
.svc-dns__g { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 20px; align-items: start; }
.dns-mismatch { color: var(--wr-coral); }
.dns-pending, .dns-incomplete { color: var(--wr-lilac); }
.dns-ok { color: var(--wr-accent); }
.dns-single, .dns-incompatible, .dns-none { color: var(--wr-muted); }
.svc-srcs { display: grid; gap: 28px; margin-top: 4px; }
.svc-src { display: flex; flex-direction: column; gap: 10px; min-width: 0; padding-left: 20px; border-left: 1px solid var(--wr-line); }
.svc-src:first-child { padding-left: 0; border-left: 0; }
.svc-src__n { font-size: 20px; color: var(--wr-muted); font-family: var(--wr-num); }
.svc-src__c { display: flex; gap: 16px; flex-wrap: wrap; font-family: var(--wr-num); font-size: 22px; }
.svc-src__c > span { display: inline-flex; align-items: center; gap: 6px; }
.svc-src__u { margin-left: auto; white-space: nowrap; }
.svc-pool__id { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 20px; align-items: start; }
.svc-pool__r { font-family: var(--wr-num); font-size: 22px; color: var(--wr-muted); white-space: nowrap; }
.svc-pool__pct { display: flex; align-items: baseline; gap: 22px; }
.svc-pool__l { font-size: 24px; white-space: nowrap; }
.svc-pool__v { font-size: 96px; font-weight: 600; }
.svc-pool__pc { font-size: 52px; margin-left: 4px; }
.svc-pool__nums { display: flex; gap: 40px; align-items: baseline; margin-top: auto; }
.svc-k { font-size: 22px; color: var(--wr-muted); margin-right: 16px; }
.svc-v { font-family: var(--wr-num); font-size: 40px; font-variant-numeric: tabular-nums; }
.svc-legend { display: flex; gap: 6px 16px; flex-wrap: wrap; min-width: 0; flex: 0 1 auto; font-size: 18px; color: var(--wr-muted); margin-left: auto; justify-content: flex-end; }
.svc-legend > span { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
.svc-cert__row { display: flex; align-items: baseline; gap: 28px; margin-top: auto; }
.svc-cert__v { font-size: 120px; font-weight: 600; }
.svc-cert__u { font-size: 48px; margin-left: 8px; font-weight: 600; }
.svc-cert__s { font-size: 48px; font-weight: 800; padding-left: 28px; border-left: 1px solid var(--wr-line); }
.svc-bk { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); flex: 1; }
.svc-bk__c { display: flex; flex-direction: column; gap: 12px; padding: 0 20px; border-left: 1px solid var(--wr-line); }
.svc-bk__c:first-child { padding-left: 0; border-left: 0; }
.svc-bk__l { font-size: 24px; font-weight: 700; white-space: nowrap; }
.svc-bk__v { font-size: 104px; font-weight: 600; }
.svc-bk__s { display: flex; gap: 4px; align-items: center; margin-top: auto; }
.svc-bk__cell { width: 18px; height: 18px; background: var(--wr-deep); }
.svc-bk__more { font-size: 22px; color: var(--wr-muted); margin-left: 4px; }
.tone-bg-coral { background: var(--wr-coral); }
.tone-bg-lilac { background: var(--wr-lilac); }
.tone-bg-ink { background: var(--wr-ink); }
</style>
