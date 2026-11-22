<script setup lang="ts">
/**
 * 迴路失效模擬（交接包 physical-v1.2 SPEC.md 第 6 節、P3，2026-11-21）。
 *
 * 唯讀計算：假設選的迴路整條斷電，列出每台受影響裝置的結果（可轉移 / 失去供電 / 剩餘容量不足 / 無法判斷）
 * 與存活迴路的新負載。規則都在後端；這裡只顯示，不自己加總。電力頁的迴路清單與機櫃電力卡共用。
 */
import { computed, h, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import {
  NAlert, NButton, NDataTable, NDrawer, NDrawerContent, NEmpty, NIcon, NSelect, NSpace, NSpin, NTag, useMessage,
  type DataTableColumns,
} from "naive-ui";
import { Physical, type FailureOutcome, type PowerFailurePreview, type PowerStatus } from "@/api/phase3";
import { apiErrMsg } from "@/api/client";
import { useEntityLinks } from "@/composables/useEntityLinks";
import ExportButton from "@/components/ExportButton.vue";
import { useTablePagination } from "@/composables/useTablePagination";
import { autoSort } from "@/composables/useTableSort";
import { cmpNatural } from "@/utils/sort";
import { PowerFailureIcon } from "@/icons";

const { t } = useI18n();
const msg = useMessage();
const links = useEntityLinks(useRouter());
const pg = useTablePagination();
const show = ref(false);
const loading = ref(false);
const picked = ref<string[]>([]);
const feedOpts = ref<{ label: string; value: string }[]>([]);
const data = ref<PowerFailurePreview | null>(null);

async function loadFeeds() {
  if (feedOpts.value.length) return;
  const [panels, feeds] = await Promise.all([Physical.panels(), Physical.feeds()]);
  const pn = new Map(panels.map((p) => [p.id, p.name]));
  feedOpts.value = feeds.map((f) => ({ label: `${pn.get(f.panel_id) ?? "—"} / ${f.name}`, value: f.id }))
    .sort((a, b) => a.label.localeCompare(b.label));
}
async function run() {
  if (!picked.value.length) { data.value = null; return; }
  loading.value = true;
  try { data.value = await Physical.previewFailure(picked.value); }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}
async function open(feedIds: string[] = []) {
  show.value = true;
  picked.value = [...feedIds];
  data.value = null;
  try { await loadFeeds(); } catch (e) { msg.error(apiErrMsg(e)); }
  await run();
}
defineExpose({ open });

const W = (n: number | null | undefined) => (n == null ? "—" : `${n.toLocaleString()} W`);
const OUTCOME_TAG: Record<FailureOutcome, "error" | "warning" | "default" | "success"> = {
  loses_power: "error", insufficient_capacity: "warning", undetermined: "default", transferable: "success",
};
const ORDER: FailureOutcome[] = ["loses_power", "insufficient_capacity", "undetermined", "transferable"];
const STATUS_TAG: Record<PowerStatus, "error" | "warning" | "default" | "success"> = {
  over: "error", warn: "warning", incomplete: "default", ok: "success", no_capacity: "default",
};
type Dev = PowerFailurePreview["devices"][number];
type Feed = PowerFailurePreview["feeds"][number];
const devRows = computed(() => (data.value?.devices ?? []).slice()
  .sort((a, b) => ORDER.indexOf(a.outcome) - ORDER.indexOf(b.outcome) || a.name.localeCompare(b.name)));

const reasonText = (d: Dev) => (d.reason ? t(`power_failure.reason.${d.reason}`) : t("power_failure.reason.confirmed"));
const toText = (r: Dev) => r.to_feeds.map((f) => f.name).join("、");
const devCols = computed<DataTableColumns<Dev>>(() => autoSort<Dev>([
  { title: t("power_failure.col_device"), key: "name", minWidth: 160, render: (r: Dev) => links.device(r.id, r.name) },
  { title: t("power_failure.col_outcome"), key: "outcome", width: 130,
    render: (r: Dev) => h(NTag, { size: "small", bordered: false, type: OUTCOME_TAG[r.outcome] }, () => t(`power_failure.outcome.${r.outcome}`)),
    sorter: (a: Dev, b: Dev) => ORDER.indexOf(a.outcome) - ORDER.indexOf(b.outcome),
    exportValue: (r: Dev) => t(`power_failure.outcome.${r.outcome}`) } as any,
  { title: t("power_failure.col_reason"), key: "reason", minWidth: 220, render: reasonText, exportValue: reasonText,
    sorter: (a: Dev, b: Dev) => cmpNatural(reasonText(a), reasonText(b)) } as any,
  { title: t("power_failure.col_load"), key: "load_w", width: 100,
    render: (r: Dev) => (r.included ? W(r.load_w) : t("power_failure.excluded")) },
  { title: t("power_failure.col_to"), key: "to_feeds", minWidth: 120,
    render: (r: Dev) => toText(r) || "—", exportValue: toText, sorter: (a: Dev, b: Dev) => cmpNatural(toText(a), toText(b)) } as any,
]));
function usage(r: Feed) {
  if (r.utilization_pct == null) return h(NTag, { size: "tiny", bordered: false }, () => t("power_status.no_capacity"));
  return h(NSpace, { size: 6, align: "center", wrapItem: false }, () => [
    h("span", null, r.partial ? t("rack_power.at_least", { pct: r.utilization_pct }) : `${r.utilization_pct}%`),
    h(NTag, { size: "tiny", bordered: false, type: STATUS_TAG[r.status] }, () => t(`power_status.${r.status}`)),
  ]);
}
const fName = (r: Feed) => `${r.panel} / ${r.name}`;
const feedCols = computed<DataTableColumns<Feed>>(() => autoSort<Feed>([
  { title: t("power_failure.col_feed"), key: "name", minWidth: 140, render: fName, exportValue: fName,
    sorter: (a: Feed, b: Feed) => cmpNatural(fName(a), fName(b)) } as any,
  { title: t("power_failure.col_before"), key: "before_w", width: 110,
    render: (r: Feed) => (r.before_partial ? t("rack_power.at_least_w", { w: W(r.before_w) }) : W(r.before_w)) },
  { title: t("power_failure.col_after"), key: "after_w", width: 120,
    render: (r: Feed) => (r.partial ? t("rack_power.at_least_w", { w: W(r.after_w) }) : W(r.after_w)) },
  { title: t("power_failure.col_capacity"), key: "planning_capacity_w", width: 110, render: (r: Feed) => W(r.planning_capacity_w) },
  { title: t("power_failure.col_usage"), key: "utilization_pct", minWidth: 170, render: usage,
    exportValue: (r: Feed) => (r.utilization_pct == null ? t(`power_status.${r.status}`) : `${r.utilization_pct}% ${t(`power_status.${r.status}`)}`) } as any,
]));
</script>

<template>
  <n-drawer v-model:show="show" :width="760" placement="right" style="max-width: 100vw">
    <n-drawer-content closable data-testid="power-failure-drawer">
      <template #header>
        <span style="display:inline-flex;align-items:center;gap:8px">
          <n-icon><PowerFailureIcon /></n-icon>{{ t("power_failure.title") }}
        </span>
      </template>
      <div class="hint">{{ t("power_failure.hint") }}</div>
      <n-space align="center" :wrap-item="false" style="margin: 10px 0 12px">
        <n-select v-model:value="picked" multiple filterable :options="feedOpts" style="min-width: 280px; max-width: 480px"
                  :placeholder="t('power_failure.pick_feeds')" data-testid="power-failure-feeds" />
        <n-button type="primary" :loading="loading" :disabled="!picked.length" data-testid="power-failure-run" @click="run">
          <template #icon><n-icon><PowerFailureIcon /></n-icon></template>{{ t("power_failure.run") }}
        </n-button>
      </n-space>
      <n-spin :show="loading">
        <template v-if="data">
          <n-alert type="info" :bordered="false" style="margin-bottom: 12px">
            <div v-for="a in data.assumptions" :key="a">{{ t(`power_failure.assumption.${a}`) }}</div>
          </n-alert>
          <div class="pf-tiles" data-testid="power-failure-counts">
            <div v-for="k in ORDER" :key="k" class="pf-tile" :data-testid="`pf-count-${k}`">
              <div class="pf-k">{{ t(`power_failure.outcome.${k}`) }}</div>
              <div class="pf-v">{{ data.counts[k] }}</div>
            </div>
            <div class="pf-tile" data-testid="pf-lost">
              <div class="pf-k">{{ t("power_failure.lost") }}</div>
              <div class="pf-v">{{ W(data.lost_load_w) }}</div>
              <div v-if="data.lost_unknown_devices" class="hint">{{ t("power_failure.lost_unknown", { n: data.lost_unknown_devices }) }}</div>
            </div>
          </div>

          <n-space align="center" justify="space-between">
            <div class="pf-sec">{{ t("power_failure.survivors_title") }}</div>
            <ExportButton v-if="data.feeds.length" :columns="feedCols" :rows="data.feeds" filename="power-failure-feeds" :title="t('power_failure.survivors_title')" />
          </n-space>
          <n-data-table v-if="data.feeds.length" :columns="feedCols" :data="data.feeds" size="small" :bordered="false"
                        :scroll-x="660" :row-key="(r: Feed) => r.id" data-testid="power-failure-survivors" />
          <div v-else class="hint">{{ t("power_failure.no_survivors") }}</div>

          <n-space align="center" justify="space-between" style="margin-top: 14px">
            <div class="pf-sec" style="margin: 0">{{ t("power_failure.devices_title") }}</div>
            <ExportButton :columns="devCols" :rows="devRows" filename="power-failure" :title="t('power_failure.title')" />
          </n-space>
          <n-data-table v-if="devRows.length" :columns="devCols" :data="devRows" size="small" :bordered="false"
                        :scroll-x="700" :pagination="pg" :row-key="(r: Dev) => r.id"
                        style="margin-top: 6px" data-testid="power-failure-devices" />
          <n-empty v-else :description="t('power_failure.none_affected')" style="margin-top: 12px" />
        </template>
      </n-spin>
    </n-drawer-content>
  </n-drawer>
</template>

<style scoped>
.hint { font-size: 12px; opacity: .7; }
.pf-tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 10px; margin-bottom: 12px; }
.pf-tile { padding: 10px 12px; border-radius: 6px; background: rgba(127, 127, 127, .08); }
.pf-k { font-size: 12.5px; opacity: .8; }
.pf-v { font-size: 22px; font-weight: 700; margin: 2px 0; font-variant-numeric: tabular-nums; }
.pf-sec { font-weight: 600; margin: 12px 0 6px; }
</style>
