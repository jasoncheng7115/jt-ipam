<template>
  <n-card size="small" style="margin-top:16px" data-testid="rack-power-card">
    <template #header>
      <n-space align="center" :wrap-item="false" :size="8">
        <n-icon :size="18"><PowerIcon /></n-icon><span>{{ t("rack_power.title") }}</span>
      </n-space>
    </template>
    <n-spin :show="loading">
      <div v-if="data && !data.feeds.length && !data.devices.length" class="hint" data-testid="rack-power-empty">
        {{ t("rack_power.empty") }}
      </div>
      <template v-else-if="data">
        <div class="rp-tiles">
          <div class="rp-tile" data-testid="rack-power-known">
            <div class="rp-k">{{ t("rack_power.known_total") }}</div>
            <div class="rp-v">{{ W(data.known_total_w) }}</div>
            <div class="hint">{{ t("rack_power.known_total_hint") }}</div>
          </div>
          <div class="rp-tile" data-testid="rack-power-completeness">
            <div class="rp-k">{{ t("rack_power.completeness") }}</div>
            <div class="rp-v">{{ data.completeness ? t("rack_power.n_of_m", { n: data.completeness.known, m: data.completeness.included }) : "—" }}</div>
            <div class="hint">{{ data.completeness ? (data.unknown_devices ? t("rack_power.n_unknown", { n: data.unknown_devices }) : t("rack_power.all_filled")) : t("rack_power.no_devices") }}</div>
          </div>
          <div class="rp-tile" data-testid="rack-power-unassigned">
            <div class="rp-k">{{ t("rack_power.unassigned") }}</div>
            <div class="rp-v">{{ W(data.unassigned_w) }}</div>
            <div class="hint">
              {{ t("rack_power.unassigned_hint") }}<template v-if="data.allocation_unknown_w">；{{ t("rack_power.alloc_unknown", { w: W(data.allocation_unknown_w) }) }}</template>
            </div>
          </div>
        </div>
        <n-alert v-if="data.partial" type="warning" :bordered="false" style="margin:10px 0" data-testid="rack-power-partial">
          {{ t("rack_power.partial") }}
        </n-alert>

        <n-space align="center" justify="space-between" style="margin: 12px 0 6px">
          <div class="rp-sec" style="margin: 0">{{ t("rack_power.feeds_title") }}</div>
          <ExportButton v-if="data.feeds.length" :columns="feedCols" :rows="data.feeds" filename="rack-power-feeds" :title="t('rack_power.feeds_title')" />
        </n-space>
        <n-data-table v-if="data.feeds.length" :columns="feedCols" :data="data.feeds" size="small" :bordered="false"
                      :scroll-x="880" :row-key="(r: RackPowerFeed) => r.id" data-testid="rack-power-feeds" />
        <div v-else class="hint">{{ t("rack_power.no_feeds") }}</div>
        <div class="hint" style="margin-top:6px">{{ t("rack_power.feeds_hint") }}</div>

        <n-space align="center" justify="space-between" style="margin-top:14px">
          <div class="rp-sec" style="margin:0">{{ t("rack_power.devices_title") }}</div>
          <n-space align="center" :size="12">
            <n-checkbox v-model:checked="onlyUnknown" data-testid="rack-power-only-unknown">
              {{ t("rack_power.only_unknown", { n: countUnknown }) }}
            </n-checkbox>
            <n-checkbox v-model:checked="onlyWiring" data-testid="rack-power-only-wiring">
              {{ t("rack_power.only_wiring", { n: countWiring }) }}
            </n-checkbox>
            <ExportButton :columns="devCols" :rows="devRows" filename="rack-power" :title="t('rack_power.title')" />
          </n-space>
        </n-space>
        <n-data-table :columns="devCols" :data="devRows" size="small" :bordered="false" :scroll-x="720"
                      :pagination="pg" :row-key="(r: RackPowerDevice) => r.id" data-testid="rack-power-devices"
                      style="margin-top:6px" />
        <div class="hint" style="margin-top:6px">{{ t("rack_power.planning_note") }}</div>
      </template>
    </n-spin>
    <PowerFailureDrawer ref="failure" />
  </n-card>
</template>

<script setup lang="ts">
/**
 * 機櫃的規劃電力（issue #54；交接包 physical-v1.2 設計圖 01）。
 *
 * 規劃值、不是即時量測：整機規劃負載整台只算一次（PSU 額定容量不是負載），依各顆電源的分攤比例分到迴路。
 * 未知不當 0：顯示已知小計與完整度；資料不完整時使用率標「至少」，不顯示安全的綠色。
 */
import { computed, h, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { NAlert, NButton, NCard, NCheckbox, NDataTable, NIcon, NProgress, NSpace, NSpin, NTag, type DataTableColumns } from "naive-ui";
import { useI18n } from "vue-i18n";
import { Physical, type PowerStatus, type RackPower, type RackPowerDevice, type RackPowerFeed } from "@/api/phase3";
import ExportButton from "@/components/ExportButton.vue";
import { autoSort } from "@/composables/useTableSort";
import { cmpNatural } from "@/utils/sort";
import { useEntityLinks } from "@/composables/useEntityLinks";
import { useTablePagination } from "@/composables/useTablePagination";
import PowerFailureDrawer from "@/components/PowerFailureDrawer.vue";
import { PowerFailureIcon, PowerIcon } from "@/icons";

const props = defineProps<{ rackId: string }>();
const { t } = useI18n();
const links = useEntityLinks(useRouter());
const pg = useTablePagination();
const data = ref<RackPower | null>(null);
const loading = ref(false);
const onlyUnknown = ref(false);
const onlyWiring = ref(false);
const failure = ref<InstanceType<typeof PowerFailureDrawer> | null>(null);

async function load() {
  if (!props.rackId) return;
  loading.value = true;
  try { data.value = await Physical.rackPower(props.rackId); }
  catch { data.value = null; }
  finally { loading.value = false; }
}
onMounted(load);
watch(() => props.rackId, () => void load());
defineExpose({ reload: load });

const W = (n: number | null | undefined) => (n == null ? "—" : `${n.toLocaleString()} W`);
const WIRING = new Set(["unconnected", "no_power_ports", "single_feed", "share_unknown"]);
const isUnknown = (d: RackPowerDevice) => d.included && d.load_w == null;
const isWiring = (d: RackPowerDevice) => d.included && d.issues.some((i) => WIRING.has(i));
const countUnknown = computed(() => data.value?.devices.filter(isUnknown).length ?? 0);
const countWiring = computed(() => data.value?.devices.filter(isWiring).length ?? 0);
const devRows = computed(() => (data.value?.devices ?? []).filter((d) =>
  (!onlyUnknown.value || isUnknown(d)) && (!onlyWiring.value || isWiring(d))));

const STATUS_TAG: Record<PowerStatus, "error" | "warning" | "default" | "success" | "info"> = {
  over: "error", warn: "warning", incomplete: "default", ok: "success", no_capacity: "default",
};
function usage(r: RackPowerFeed) {
  if (r.utilization_pct == null) return h("span", { class: "hint" }, t("power_status.no_capacity"));
  const pct = r.utilization_pct;
  // 不完整的不用綠色：顏色只在超過或達警戒時出現，其餘灰色，並寫出文字狀態
  const color = r.status === "over" ? "error" : r.status === "warn" ? "warning" : r.status === "ok" ? "success" : "default";
  return h("span", { style: "display:inline-flex;align-items:center;gap:8px" }, [
    h(NProgress, { type: "line", percentage: Math.min(pct, 100), status: color as any, showIndicator: false, height: 8,
                   style: "width:70px" }),
    h("span", null, r.partial ? t("rack_power.at_least", { pct }) : `${pct}%`),
    h(NTag, { size: "tiny", bordered: false, type: STATUS_TAG[r.status] }, () => t(`power_status.${r.status}`)),
  ]);
}
const feedName = (r: RackPowerFeed) => `${r.panel} / ${r.name}`;
const feedCols = computed<DataTableColumns<RackPowerFeed>>(() => autoSort<RackPowerFeed>([
  { title: t("rack_power.col_feed"), key: "name", minWidth: 150, render: feedName,
    sorter: (a: RackPowerFeed, b: RackPowerFeed) => cmpNatural(feedName(a), feedName(b)), exportValue: feedName } as any,
  { title: t("rack_power.col_rack"), key: "rack_allocated_w", width: 110, render: (r) => W(r.rack_allocated_w) },
  { title: t("rack_power.col_total"), key: "full_allocated_w", width: 120,
    render: (r) => (r.partial ? t("rack_power.at_least_w", { w: W(r.full_allocated_w) }) : W(r.full_allocated_w)) },
  { title: t("rack_power.col_capacity"), key: "planning_capacity_w", width: 120, render: (r) => W(r.planning_capacity_w) },
  { title: t("rack_power.col_usage"), key: "utilization_pct", minWidth: 240, render: usage,
    exportValue: (r: RackPowerFeed) => (r.utilization_pct == null ? t(`power_status.${r.status}`) : `${r.utilization_pct}% ${t(`power_status.${r.status}`)}`) } as any,
  { title: t("common.actions"), key: "actions", width: 120,
    render: (r) => h(NButton, { size: "tiny", type: "warning", secondary: true, "data-testid": `rack-power-simulate-${r.name}`,
                                onClick: () => failure.value?.open([r.id]) },
      { icon: () => h(NIcon, null, () => h(PowerFailureIcon)), default: () => t("power_failure.simulate") }) },
]));

function issueText(d: RackPowerDevice): string {
  if (!d.included) return t("rack_power.excluded", { reason: d.exclude_reason === "passive" ? t("rack_power.passive") : (d.exclude_reason ?? "") });
  return d.issues.map((i) => t(`power_issue.${i}`)).join("、") || "—";
}
function allocText(d: RackPowerDevice): string {
  if (!d.included) return "—";
  if (d.load_w == null) return t("power_issue.load_unknown");
  if (d.allocation_unknown_w) return t("power_issue.share_unknown");
  const parts = d.allocations.map((a) => `${a.feed ?? t("rack_power.unassigned_short")} ${W(a.watts)}${a.cross_rack ? `（${t("rack_power.cross_rack")}）` : ""}`);
  if (!d.allocations.length && d.unassigned_w) parts.push(`${t("rack_power.unassigned_short")} ${W(d.unassigned_w)}`);
  return parts.join(" / ");
}
const devCols = computed<DataTableColumns<RackPowerDevice>>(() => autoSort<RackPowerDevice>([
  { title: t("rack_power.col_device"), key: "name", minWidth: 160, render: (r: RackPowerDevice) => links.device(r.id, r.name) },
  { title: t("rack_power.col_load"), key: "load_w", width: 110, render: (r: RackPowerDevice) => (r.included ? W(r.load_w) : "—"),
    exportValue: (r: RackPowerDevice) => (r.included ? r.load_w : null) },
  { title: t("rack_power.col_alloc"), key: "allocations", minWidth: 220, render: allocText, exportValue: allocText,
    sorter: (a: RackPowerDevice, b: RackPowerDevice) => cmpNatural(allocText(a), allocText(b)) },
  { title: t("rack_power.col_note"), key: "issues", minWidth: 180, render: issueText, exportValue: issueText,
    sorter: (a: RackPowerDevice, b: RackPowerDevice) => cmpNatural(issueText(a), issueText(b)) },
] as DataTableColumns<RackPowerDevice>));
</script>

<style scoped>
.hint { font-size: 12px; opacity: .7; }
.rp-tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; }
.rp-tile { padding: 10px 12px; border-radius: 6px; background: rgba(127, 127, 127, .08); }
.rp-k { font-size: 12.5px; opacity: .8; }
.rp-v { font-size: 22px; font-weight: 700; margin: 2px 0; font-variant-numeric: tabular-nums; }
.rp-sec { font-weight: 600; margin: 12px 0 6px; }
</style>
