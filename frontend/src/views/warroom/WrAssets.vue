<script setup lang="ts">
/**
 * 06 資產與影響：上面兩個虛擬化叢集（一格一台 VM，依狀態排序），下面監控覆蓋與 IP 變更評估。
 * 虛擬化是全域資料：只被指派部分物件的帳號看到「無權限檢視」；監控覆蓋與變更評估是管理員限定。
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

interface Cluster { id: string; name: string; platform: string; standalone: boolean; hosts: number; vms: number;
  counts: Record<string, number>; unit: number; cells: string[] }
interface System { kind: string; covered: number; gap: number; total: number; unit: number; cells: string[] }
interface Coverage { state: string; systems?: System[]; vms?: number; with_ip?: number; no_ip?: number }
interface Change { state: string; counts?: Record<string, number>; open?: number }

const clusters = computed(() => (props.data.clusters as Cluster[]) ?? []);
const clustersState = computed(() => props.data.clusters_state as string);
const cov = computed(() => props.data.coverage as Coverage);
const chg = computed(() => props.data.change as Change);
const VM_STATES = ["running", "other", "stopped", "unknown"] as const;
const PLATFORM: Record<string, string> = { proxmox: "Proxmox VE", vmware: "VMware vSphere", "hyper-v": "Hyper-V", openstack: "OpenStack" };
const SYSTEM: Record<string, string> = { wazuh: "Wazuh", zabbix: "Zabbix", ocs: "OCS Inventory" };
const CHANGE = [
  { key: "blocked", tone: "coral" }, { key: "needs_review", tone: "lilac" }, { key: "incomplete", tone: "ink" },
] as const;
</script>

<template>
  <div class="as" :class="{ 'as--info': data.info_mode }">
    <Transition :name="pageTrans" mode="out-in"><div :key="data.page" class="as-top">
      <div v-if="clustersState === 'forbidden'" class="wr-empty">{{ t("warroom.forbidden") }}</div>
      <div v-else-if="!clusters.length" class="wr-empty">{{ t("warroom.no_clusters") }}</div>
      <section v-for="c in clusters" :key="c.id" class="as-cl" data-testid="wr-cluster">
        <header class="as-cl__h">
          <div class="as-cl__p"><WrIco :icon="SECTION_ICONS.cluster" />{{ PLATFORM[c.platform] ?? c.platform }}</div>
          <WrName :text="c.name" class="as-cl__n" />
          <div class="as-cl__hosts"><span class="as-cl__hn">{{ c.hosts }}</span>{{ t("warroom.hosts_unit") }}</div>
        </header>
        <div class="as-cl__b">
          <div class="as-cl__nums">
            <div class="as-k">{{ t("warroom.virtual_machines") }}</div>
            <div class="wr-big as-cl__v">{{ c.vms.toLocaleString() }}</div>
            <div class="as-cl__counts">
              <div v-for="s in VM_STATES" :key="s" class="as-cnt" :class="{ zero: !c.counts[s] }">
                <span class="wr-swatch wr-swatch--sm" :class="`s-${s}`" />
                <span class="as-cnt__l">{{ t(`warroom.vm_${s}`) }}</span>
                <span class="as-cnt__n">{{ (c.counts[s] ?? 0).toLocaleString() }}</span>
              </div>
            </div>
          </div>
          <div class="as-cl__m">
            <WrMatrix :cells="c.cells" :cols="20" :size="22" :gap="5" />
            <div v-if="c.unit > 1" class="wr-unit">{{ t("warroom.unit_vms", { n: c.unit }) }}</div>
          </div>
        </div>
      </section>
    </div>
    </Transition>

    <!-- 資訊展示模式（11-03）：監控涵蓋（哪些 VM 沒被監控）與變更評估（受阻、待覆核）都是問題，整列不畫 -->
    <div v-if="!data.info_mode" class="as-bottom">
      <section class="as-q" data-testid="wr-coverage">
        <header class="as-h">
          <div class="wr-h2"><WrIco :icon="SECTION_ICONS.coverage" />{{ t("warroom.monitor_coverage") }}</div>
          <span v-if="cov.with_ip != null" class="wr-index">{{ t("warroom.running_vms_n", { n: cov.with_ip }) }}</span>
        </header>
        <div v-if="cov.state === 'forbidden'" class="wr-muted">{{ t("warroom.forbidden") }}</div>
        <div v-else-if="!cov.systems?.length" class="wr-muted">{{ t("warroom.no_monitoring") }}</div>
        <template v-else>
          <div v-for="s in cov.systems" :key="s.kind" class="as-sys">
            <div class="as-sys__n">{{ SYSTEM[s.kind] ?? s.kind }}</div>
            <WrMatrix :cells="s.cells" :cols="20" :size="14" :gap="4" />
            <div class="as-sys__v"><span :class="s.gap ? 'tone-coral' : 'tone-accent'">{{ s.covered.toLocaleString() }}</span><span class="tone-muted"> / {{ s.total.toLocaleString() }}</span></div>
          </div>
          <div class="as-sys__foot">
            <span><span class="wr-swatch wr-swatch--sm s-covered" />{{ t("warroom.covered") }}</span>
            <span><span class="wr-swatch wr-swatch--sm s-gap" />{{ t("warroom.gap") }}</span>
            <span v-if="cov.no_ip" class="wr-unit">{{ t("warroom.no_ip_vms", { n: cov.no_ip }) }}</span>
          </div>
        </template>
      </section>

      <section class="as-q" data-testid="wr-change">
        <header class="as-h">
          <div class="wr-h2"><WrIco :icon="SECTION_ICONS.change" />{{ t("warroom.change_assessment") }}</div>
          <span v-if="chg.open != null" class="wr-index">{{ t("warroom.open_plans_n", { n: chg.open }) }}</span>
        </header>
        <div v-if="chg.state === 'forbidden'" class="wr-muted">{{ t("warroom.forbidden") }}</div>
        <div v-else-if="chg.state === 'disabled'" class="wr-muted">{{ t("warroom.change_disabled") }}</div>
        <div v-else-if="!chg.counts" class="wr-muted">{{ t("warroom.no_open_plans") }}</div>
        <div v-else class="as-chg">
          <div v-for="c in CHANGE" :key="c.key" class="as-chg__c">
            <div class="wr-big as-chg__v" :class="chg.counts[c.key] ? `tone-${c.tone}` : 'tone-muted'">{{ pad2(chg.counts[c.key] ?? 0) }}</div>
            <div class="as-chg__l">{{ t(`warroom.chg_${c.key}`) }}</div>
            <div class="as-chg__d">{{ t(`warroom.chg_${c.key}_d`) }}</div>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.as { display: grid; grid-template-rows: auto minmax(0, 1fr); height: 100%; }
.as--info { grid-template-rows: minmax(0, 1fr); }
.as--info .as-top { border-bottom: 0; }
.as-top { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); border-bottom: 1px solid var(--wr-line); min-height: 0; }
.as-cl { padding: 6px 40px 18px 0; display: flex; flex-direction: column; gap: 14px; min-width: 0; }
.as-cl:nth-child(2) { padding-left: 40px; padding-right: 0; border-left: 1px solid var(--wr-line); }
/* 平台、叢集名稱、主機數三者對齊第一行的基線（頂端對齊時字小的叢集名稱看起來偏上，使用者 10-22 指出） */
.as-cl__h { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 22px; align-items: baseline;
  border-top: 6px solid var(--wr-lilac); padding-top: 12px; }
.as-cl__p { font-size: 30px; font-weight: 800; white-space: nowrap; }
.as-cl__n { font-size: 24px; color: var(--wr-muted); font-family: var(--wr-num); }
.as-cl__hosts { font-size: 22px; color: var(--wr-muted); white-space: nowrap; }
.as-cl__hn { font-family: var(--wr-num); font-size: 34px; color: var(--wr-ink); margin-right: 8px; }
.as-cl__b { display: grid; grid-template-columns: 300px minmax(0, 1fr); gap: 24px; align-items: start; }
.as-k { font-size: 22px; color: var(--wr-muted); }
.as-cl__v { font-size: 80px; margin: 2px 0 8px; }
.as-cl__counts { display: grid; gap: 4px; }
.as-cnt { display: grid; grid-template-columns: 20px 1fr auto; gap: 10px; align-items: center; font-size: 21px; white-space: nowrap; }
.as-cnt.zero { opacity: .45; }
.as-cnt__n { font-family: var(--wr-num); font-size: 24px; font-variant-numeric: tabular-nums; }
.as-cl__m { display: flex; flex-direction: column; gap: 10px; padding-top: 6px; }
.as-bottom { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); min-height: 0; }
.as-q { padding: 18px 40px 6px 0; display: flex; flex-direction: column; gap: 14px; min-width: 0; min-height: 0; overflow: hidden; }
.as-q:nth-child(2) { padding-left: 40px; padding-right: 0; border-left: 1px solid var(--wr-line); }
.as-h { display: flex; justify-content: space-between; align-items: baseline; gap: 16px; }
.as-sys { display: grid; grid-template-columns: 190px minmax(0, 1fr) auto; gap: 20px; align-items: center; }
.as-sys__n { font-size: 24px; font-weight: 700; white-space: nowrap; }
.as-sys__v { font-family: var(--wr-num); font-size: 30px; text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.as-sys__foot { display: flex; gap: 24px; align-items: center; font-size: 20px; margin-top: auto; }
.as-sys__foot > span { display: inline-flex; align-items: center; gap: 8px; }
.as-chg { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); flex: 1; }
.as-chg__c { padding: 0 24px; border-left: 1px solid var(--wr-line); display: flex; flex-direction: column; gap: 6px; }
.as-chg__c:first-child { padding-left: 0; border-left: 0; }
.as-chg__v { font-size: 84px; font-weight: 600; }
.as-chg__l { font-size: 28px; font-weight: 700; margin-top: 4px; line-height: 1.15; }
.as-chg__d { font-size: 20px; color: var(--wr-muted); line-height: 1.3; }
</style>
