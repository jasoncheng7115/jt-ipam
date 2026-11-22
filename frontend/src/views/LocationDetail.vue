<script setup lang="ts">
/**
 * 機房詳情（交接包 physical-v1.2 設計圖 03、07）：把機房當成查看機櫃、裝置、IP 與規劃電力的入口。
 *
 * - 裝置用「有效位置」：在機櫃裡就算機櫃的機房，沒入櫃才算裝置自己的；記錄不一致的列成位置衝突，不自動改
 * - 數字全部後端算、只算看得到的；電力與佈線是全域資料，沒有全域讀取會明講，不顯示空白
 * - 平面配置沿用機櫃頁的同一個元件（兩個入口讀寫同一份座標）
 * - 只呈現既有資料：不放溫濕度、即時功率這類沒有來源的數字
 */
import { computed, h, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import {
  NAlert, NButton, NCard, NDataTable, NEmpty, NIcon, NInput, NRadioButton, NRadioGroup, NSpace, NSpin, NTabPane,
  NTabs, NTag, type DataTableColumns,
} from "naive-ui";
import {
  getLocation, getLocationCabling, getLocationDevices, getLocationPower, getLocationSummary,
  type Location, type LocationCabling, type LocationDevice, type LocationDeviceFilter, type LocationDeviceSort,
  type LocationSummary,
} from "@/api/basic";
import { getRackDiagram, type RackDiagram as RD } from "@/api/racks";
import { apiErrMsg } from "@/api/client";
import { useAuthStore } from "@/stores/auth";
import { useEntityLinks } from "@/composables/useEntityLinks";
import { useTablePagination } from "@/composables/useTablePagination";
import { autoSort } from "@/composables/useTableSort";
import { cmpNatural } from "@/utils/sort";
import ExportButton from "@/components/ExportButton.vue";
import SwitchPortLabel from "@/components/SwitchPortLabel.vue";
import RackFloorPlan from "@/components/RackFloorPlan.vue";
import RackDiagram from "@/components/RackDiagram.vue";
import { ArrowLeft as ArrowLeftIcon } from "@iconoir/vue";
import { EditIcon, LocationsIcon, PhysicalIcon, PowerIcon, RacksIcon, RefreshIcon } from "@/icons";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const links = useEntityLinks(router);
const pg = useTablePagination();
const id = computed(() => String(route.params.id ?? ""));
const loc = ref<Location | null>(null);
const sum = ref<LocationSummary | null>(null);
const loading = ref(false);
const error = ref("");
const tab = ref("overview");
const W = (n: number | null | undefined) => (n == null ? "—" : `${n.toLocaleString()} W`);

async function load() {
  if (!id.value) return;
  loading.value = true; error.value = "";
  try {
    [loc.value, sum.value] = await Promise.all([getLocation(id.value), getLocationSummary(id.value)]);
  } catch (e) { error.value = apiErrMsg(e); }
  finally { loading.value = false; }
  devPage.value = 1;
  void loadDevices();
  if (tab.value === "power") void loadPower();
  if (tab.value === "cabling") void loadCabling();
}
watch(id, () => { selectedRack.value = null; power.value = null; cabling.value = null; void load(); });
onMounted(load);

// ── 總覽：選中的機櫃預覽 ──
const selectedRack = ref<string | null>(null);
const rackDiagram = ref<RD | null>(null);
async function pickRack(rid: string) {
  selectedRack.value = rid;
  try { rackDiagram.value = await getRackDiagram(rid); } catch { rackDiagram.value = null; }
}
const selRow = computed(() => sum.value?.rack_list.find((r) => r.id === selectedRack.value) ?? null);
const openRack = (rid: string) => router.push({ name: "racks", query: { rack: rid } });

const pendingItems = computed(() => {
  const p = sum.value?.pending;
  if (!p) return [];
  return [
    { key: "unracked", n: p.unracked, go: () => showDevices("unracked") },
    { key: "load_unknown", n: p.load_unknown, go: () => { tab.value = "power"; void loadPower(); } },
    { key: "location_conflicts", n: p.location_conflicts, go: () => showDevices("conflict") },
    { key: "rack_location_unset", n: p.rack_location_unset, go: () => showDevices("rack_location_unset") },
  ].filter((x) => x.n > 0);
});

type RackRow = LocationSummary["rack_list"][number];
type CabRow = LocationCabling["items"][number];
const ratio = (c: { known: number; included: number } | null) => (c && c.included ? c.known / c.included : null);
const rackCols = computed<DataTableColumns<LocationSummary["rack_list"][number]>>(() => autoSort([
  { title: t("location_detail.col_rack"), key: "name", minWidth: 150,
    render: (r) => h("a", { class: "ld-link", onClick: () => openRack(r.id) }, r.name) },
  { title: t("location_detail.col_devices"), key: "devices", width: 100 },
  { title: t("location_detail.col_load"), key: "known_total_w", width: 130, render: (r) => W(r.known_total_w) },
  { title: t("location_detail.col_completeness"), key: "completeness", width: 140,
    render: (r: RackRow) => (r.completeness ? `${r.completeness.known} / ${r.completeness.included}` : "—"),
    sorter: (a: RackRow, b: RackRow) => (ratio(a.completeness) ?? -1) - (ratio(b.completeness) ?? -1),
    exportValue: (r: any) => (r.completeness ? `${r.completeness.known} / ${r.completeness.included}` : "") } as any,
  { title: t("common.actions"), key: "_", width: 92, className: "col-actions",
    render: (r) => h(NButton, { size: "small", quaternary: true, type: "primary", title: t("location_detail.open_rack"),
                                onClick: () => openRack(r.id) }, { icon: () => h(NIcon, null, () => h(RacksIcon)) }) },
]));

// ── 裝置與 IP ──
const devFilter = ref<LocationDeviceFilter>("all");
const devQ = ref("");
const devPage = ref(1);
const devRows = ref<LocationDevice[]>([]);
const devTotal = ref(0);
const devLoading = ref(false);
// 後端分頁：排序交給後端（分頁前排），不能只排畫面上那一頁
const devSort = ref<{ key: LocationDeviceSort; order: "ascend" | "descend" } | null>(null);
function devParams(page: number, size: number) {
  return { filter: devFilter.value, q: devQ.value.trim() || undefined, page, page_size: size,
           sort: devSort.value?.key, order: devSort.value?.order === "descend" ? "desc" as const : "asc" as const };
}
function onDevSorter(s: { columnKey: string | number; order: "ascend" | "descend" | false } | null) {
  devSort.value = s && s.order ? { key: String(s.columnKey) as LocationDeviceSort, order: s.order } : null;
  devPage.value = 1; void loadDevices();
}
async function fetchAllDevices(): Promise<LocationDevice[]> {
  const out: LocationDevice[] = [];
  for (let page = 1; page <= 200; page++) {
    const r = await getLocationDevices(id.value, devParams(page, 500));
    out.push(...r.items);
    if (out.length >= r.total || !r.items.length) break;
  }
  return out;
}
async function loadDevices() {
  devLoading.value = true;
  try {
    const r = await getLocationDevices(id.value, devParams(devPage.value, 50));
    devRows.value = r.items; devTotal.value = r.total;
  } catch (e) { devRows.value = []; devTotal.value = 0; error.value = apiErrMsg(e); }
  finally { devLoading.value = false; }
}
function showDevices(f: LocationDeviceFilter) { devFilter.value = f; devPage.value = 1; tab.value = "devices"; void loadDevices(); }
let qTimer: ReturnType<typeof setTimeout> | undefined;
watch(devQ, () => { clearTimeout(qTimer); qTimer = setTimeout(() => { devPage.value = 1; void loadDevices(); }, 300); });
watch(devFilter, () => { devPage.value = 1; void loadDevices(); });
const devPagination = computed(() => ({
  page: devPage.value, pageSize: 50, itemCount: devTotal.value, remote: true,
  onUpdatePage: (p: number) => { devPage.value = p; void loadDevices(); },
}));
const STATUS_TYPE: Record<string, "success" | "default" | "warning" | "error"> = {
  racked: "success", unracked: "default", conflict: "warning", rack_location_unset: "error",
};
function statusNote(r: LocationDevice): string {
  if (r.status === "conflict") {
    return r.counted_location
      ? t("location_detail.conflict_elsewhere", { loc: r.counted_location })
      : t("location_detail.conflict_here", { loc: r.recorded_location ?? "" });
  }
  if (r.status === "rack_location_unset") return t("location_detail.rack_unset_note", { rack: r.rack ?? "" });
  return "";
}
const sortOrder = (k: string) => (devSort.value?.key === k ? devSort.value.order : false);
const devCols = computed<DataTableColumns<LocationDevice>>(() => [
  { title: t("location_detail.col_device"), key: "name", minWidth: 160, render: (r) => links.device(r.id, r.name),
    sorter: true, sortOrder: sortOrder("name") },
  { title: t("location_detail.col_rack"), key: "rack", width: 140,
    render: (r) => (r.rack_id ? h("a", { class: "ld-link", onClick: () => openRack(r.rack_id!) }, r.rack ?? "") : t("location_detail.unracked")),
    sorter: true, sortOrder: sortOrder("rack") },
  { title: t("location_detail.col_status"), key: "status", width: 120,
    render: (r: LocationDevice) => h(NTag, { size: "small", bordered: false, type: STATUS_TYPE[r.status] }, () => t(`location_detail.status_${r.status}`)),
    sorter: true, sortOrder: sortOrder("status"), exportValue: (r: LocationDevice) => t(`location_detail.status_${r.status}`) } as any,
  { title: t("location_detail.col_ips"), key: "ips", minWidth: 200,
    render: (r: LocationDevice) => (r.ips.length ? h(NSpace, { size: 4 }, () => r.ips.map((x: { id: string; ip: string }) => links.ipById(x.id, x.ip.split("/")[0]))) : "—"),
    exportValue: (r: LocationDevice) => r.ips.map((x) => x.ip).join(", "), sorter: true, sortOrder: sortOrder("ips") } as any,
  { title: t("location_detail.col_note"), key: "note", minWidth: 200, render: statusNote, exportValue: statusNote } as any,
]);

// ── 電力、佈線（全域資料：要全域讀取；沒有就明講）──
const power = ref<any | null>(null);
const powerErr = ref("");
const cabling = ref<LocationCabling | null>(null);
const cablingErr = ref("");
async function loadPower() {
  powerErr.value = "";
  try { power.value = await getLocationPower(id.value); } catch (e) { power.value = null; powerErr.value = apiErrMsg(e); }
}
async function loadCabling() {
  cablingErr.value = "";
  try { cabling.value = await getLocationCabling(id.value); } catch (e) { cabling.value = null; cablingErr.value = apiErrMsg(e); }
}
watch(tab, (v) => {
  if (v === "power" && !power.value) void loadPower();
  if (v === "cabling" && !cabling.value) void loadCabling();
});
const feedCols = computed<DataTableColumns<any>>(() => autoSort([
  { title: t("rack_power.col_feed"), key: "name", minWidth: 150, render: (r: any) => `${r.panel} / ${r.name}`,
    sorter: (a: any, b: any) => cmpNatural(`${a.panel} / ${a.name}`, `${b.panel} / ${b.name}`), exportValue: (r: any) => `${r.panel} / ${r.name}` } as any,
  { title: t("location_detail.col_location_share"), key: "location_allocated_w", width: 130, render: (r) => W(r.location_allocated_w) },
  { title: t("rack_power.col_total"), key: "full_allocated_w", width: 130,
    render: (r) => (r.partial ? t("rack_power.at_least_w", { w: W(r.full_allocated_w) }) : W(r.full_allocated_w)) },
  { title: t("rack_power.col_capacity"), key: "planning_capacity_w", width: 120, render: (r) => W(r.planning_capacity_w) },
  { title: t("rack_power.col_usage"), key: "utilization_pct", width: 160, exportValue: (r: any) => (r.utilization_pct == null ? t(`power_status.${r.status}`) : `${r.utilization_pct}% ${t(`power_status.${r.status}`)}`), render: (r) => h(NSpace, { size: 6, align: "center" }, () => [
    r.utilization_pct == null ? "" : (r.partial ? t("rack_power.at_least", { pct: r.utilization_pct }) : `${r.utilization_pct}%`),
    h(NTag, { size: "tiny", bordered: false, type: r.status === "over" ? "error" : r.status === "warn" ? "warning" : r.status === "ok" ? "success" : "default" },
      () => t(`power_status.${r.status}`)),
  ]) },
  { title: t("location_detail.col_cross"), key: "cross_location", width: 110, render: (r) => (r.cross_location ? t("location_detail.cross_yes") : "—") },
]));
const issueText = (r: any) => (!r.included ? t("rack_power.excluded", { reason: r.exclude_reason === "passive" ? t("rack_power.passive") : (r.exclude_reason ?? "") })
  : r.issues.map((i: string) => t(`power_issue.${i}`)).join("、") || "—");
const powerDevCols = computed<DataTableColumns<any>>(() => autoSort([
  { title: t("location_detail.col_device"), key: "name", minWidth: 160, render: (r) => links.device(r.id, r.name) },
  { title: t("rack_power.col_load"), key: "load_w", width: 120, render: (r) => (r.included ? W(r.load_w) : "—") },
  { title: t("rack_power.col_note"), key: "issues", minWidth: 200, render: issueText,
    sorter: (a: any, b: any) => cmpNatural(issueText(a), issueText(b)), exportValue: issueText } as any,
]));
const KIND_TYPE = { in_room: "success", cross_room: "info", unknown: "warning" } as const;
// 兩端照全站寫法「裝置@埠」（SwitchPortLabel：@ 用品牌綠、裝置名稱是連結）
function endCell(id: string | null, dev: string | null, port: string | null, text: string | null) {
  if (!dev) return text ?? t("location_detail.end_unknown");
  return h(SwitchPortLabel, { value: `${dev} / ${port ?? ""}`, to: id ? { name: "device-detail", params: { id } } : null });
}
const cableCols = computed<DataTableColumns<LocationCabling["items"][number]>>(() => autoSort([
  { title: t("location_detail.col_cable"), key: "label", width: 140, render: (r) => r.label || t("physical_check.no_label") },
  { title: t("location_detail.col_kind"), key: "kind", width: 120,
    render: (r: CabRow) => h(NTag, { size: "small", bordered: false, type: KIND_TYPE[r.kind] }, () => t(`location_detail.kind_${r.kind}`)),
    exportValue: (r: any) => t(`location_detail.kind_${r.kind}`) } as any,
  { title: t("location_detail.col_a"), key: "a", minWidth: 180, render: (r: CabRow) => endCell(r.a_device_id, r.a_device, r.a_port, r.a),
    exportValue: (r: any) => r.a ?? "" } as any,
  { title: t("location_detail.col_b"), key: "b", minWidth: 180, render: (r: CabRow) => endCell(r.b_device_id, r.b_device, r.b_port, r.b),
    exportValue: (r: any) => r.b ?? "" } as any,
  { title: t("location_detail.col_other_location"), key: "other_location", width: 140, render: (r) => r.other_location ?? "—" },
]));
const canEditPlan = computed(() => !!auth.me?.is_admin || auth.canCreate("location"));
</script>

<template>
  <n-spin :show="loading">
    <n-card data-testid="location-detail">
      <template #header>
        <n-space align="center" :wrap-item="false">
          <n-icon :size="22"><LocationsIcon /></n-icon>
          <span>{{ loc?.name ?? "" }}</span>
        </n-space>
      </template>
      <n-space justify="end" style="margin-bottom: 10px">
        <n-button size="small" @click="load">
          <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}
        </n-button>
        <n-button size="small" type="primary" data-testid="location-edit"
                  @click="router.push({ name: 'locations', query: { edit: id } })">
          <template #icon><n-icon><EditIcon /></n-icon></template>{{ t("common.edit") }}
        </n-button>
        <n-button size="small" @click="router.push({ name: 'locations' })">
          <template #icon><n-icon><ArrowLeftIcon /></n-icon></template>{{ t("common.back") }}
        </n-button>
      </n-space>
      <n-alert v-if="error" type="error" :bordered="false" style="margin-bottom: 10px">{{ error }}</n-alert>

      <n-tabs v-model:value="tab" type="line">
        <!-- 總覽 -->
        <n-tab-pane name="overview" :tab="t('location_detail.tab_overview')">
          <div v-if="sum" class="ld-tiles">
            <div class="ld-tile" data-testid="ld-racks"><div class="ld-k">{{ t("location_detail.racks") }}</div>
              <div class="ld-v">{{ sum.racks }}</div></div>
            <div class="ld-tile" data-testid="ld-devices"><div class="ld-k">{{ t("location_detail.devices") }}</div>
              <div class="ld-v">{{ sum.devices.total }}</div>
              <div class="hint">{{ t("location_detail.unracked_n", { n: sum.devices.unracked }) }}</div></div>
            <div class="ld-tile" data-testid="ld-ips"><div class="ld-k">{{ t("location_detail.ips") }}</div>
              <div class="ld-v">{{ sum.ips }}</div><div class="hint">{{ t("location_detail.ips_hint") }}</div></div>
            <div class="ld-tile" data-testid="ld-power"><div class="ld-k">{{ t("rack_power.known_total") }}</div>
              <div class="ld-v">{{ W(sum.power.known_total_w) }}</div>
              <div class="hint">{{ sum.power.completeness ? t("location_detail.filled_n", { n: sum.power.completeness.known, m: sum.power.completeness.included }) : t("rack_power.no_devices") }}</div></div>
          </div>

          <div class="ld-grid">
            <div>
              <div class="ld-sec">{{ t("location_detail.floor_plan") }}</div>
              <RackFloorPlan :location-id="id" :can-edit="false" @select="pickRack" />
              <div class="hint">{{ t("location_detail.floor_hint") }}</div>
            </div>
            <div>
              <div class="ld-sec">{{ t("location_detail.selected_rack") }}</div>
              <template v-if="selRow">
                <div class="ld-sel" data-testid="ld-selected-rack">
                  <div style="max-width: 200px"><RackDiagram v-if="rackDiagram" :diagram="rackDiagram" compact bare :controls="false" :show-legend="false" /></div>
                  <div style="flex: 1">
                    <div class="ld-name">{{ selRow.name }}</div>
                    <div>{{ t("location_detail.devices") }}：{{ selRow.devices }}</div>
                    <div>{{ t("rack_power.known_total") }}：{{ W(selRow.known_total_w) }}</div>
                    <div>{{ t("location_detail.col_completeness") }}：{{ selRow.completeness ? `${selRow.completeness.known} / ${selRow.completeness.included}` : "—" }}</div>
                    <n-space style="margin-top: 8px">
                      <n-button size="small" type="primary" @click="openRack(selRow.id)">
                        <template #icon><n-icon><RacksIcon /></n-icon></template>{{ t("location_detail.open_rack") }}
                      </n-button>
                      <n-button size="small" @click="tab = 'power'">
                        <template #icon><n-icon><PowerIcon /></n-icon></template>{{ t("location_detail.power_detail") }}
                      </n-button>
                    </n-space>
                  </div>
                </div>
              </template>
              <div v-else class="hint">{{ t("location_detail.pick_rack") }}</div>

              <div class="ld-sec" style="margin-top: 14px">{{ t("location_detail.pending") }}</div>
              <div v-if="!pendingItems.length" class="hint">{{ t("location_detail.pending_none") }}</div>
              <div v-for="p in pendingItems" :key="p.key" class="ld-pend" :data-testid="`ld-pending-${p.key}`" @click="p.go()">
                <span>{{ t(`location_detail.pending_${p.key}`, { n: p.n }) }}</span><span>›</span>
              </div>
            </div>
          </div>

          <div class="ld-sec" style="margin-top: 14px">{{ t("location_detail.racks_in_room") }}</div>
          <n-data-table :columns="rackCols" :data="sum?.rack_list ?? []" size="small" :bordered="false" :pagination="pg"
                        :scroll-x="600" data-testid="ld-rack-table" />
          <div class="hint" style="margin-top: 6px">{{ t("location_detail.visible_note") }}</div>
        </n-tab-pane>

        <!-- 平面配置 -->
        <n-tab-pane name="plan" :tab="t('location_detail.tab_plan')" display-directive="show:lazy">
          <RackFloorPlan :location-id="id" :can-edit="canEditPlan" @select="openRack" />
        </n-tab-pane>

        <!-- 機櫃 -->
        <n-tab-pane name="racks" :tab="t('location_detail.tab_racks')">
          <n-space justify="end" style="margin-bottom: 8px">
            <ExportButton :columns="rackCols" :rows="sum?.rack_list ?? []" :filename="`${loc?.name ?? 'location'}-racks`" :title="t('location_detail.tab_racks')" />
          </n-space>
          <n-data-table :columns="rackCols" :data="sum?.rack_list ?? []" size="small" :bordered="false" :pagination="pg" :scroll-x="600" />
        </n-tab-pane>

        <!-- 裝置與 IP -->
        <n-tab-pane name="devices" :tab="t('location_detail.tab_devices')">
          <n-space align="center" style="margin-bottom: 8px">
            <n-radio-group v-model:value="devFilter" size="small" data-testid="ld-dev-filter">
              <n-radio-button v-for="f in (['all', 'racked', 'unracked', 'conflict', 'rack_location_unset'] as const)" :key="f" :value="f">
                {{ t(`location_detail.filter_${f}`) }}
              </n-radio-button>
            </n-radio-group>
            <n-input v-model:value="devQ" size="small" clearable :placeholder="t('common.filter')" style="width: 200px" />
            <ExportButton :columns="devCols" :rows="devRows" :fetch-all="fetchAllDevices"
                          :filename="`${loc?.name ?? 'location'}-devices`" :title="t('location_detail.tab_devices')" />
          </n-space>
          <n-data-table remote :columns="devCols" :data="devRows" :loading="devLoading" :pagination="devPagination"
                        @update:sorter="onDevSorter"
                        size="small" :bordered="false" :scroll-x="800" :row-key="(r: LocationDevice) => r.id" data-testid="ld-dev-table" />
          <div class="hint" style="margin-top: 6px">{{ t("location_detail.effective_note") }}</div>
        </n-tab-pane>

        <!-- 電力 -->
        <n-tab-pane name="power" :tab="t('location_detail.tab_power')">
          <n-alert v-if="powerErr" type="warning" :bordered="false">{{ powerErr }}</n-alert>
          <template v-else-if="power">
            <div class="ld-tiles">
              <div class="ld-tile"><div class="ld-k">{{ t("rack_power.known_total") }}</div><div class="ld-v">{{ W(power.known_total_w) }}</div>
                <div class="hint">{{ t("rack_power.known_total_hint") }}</div></div>
              <div class="ld-tile"><div class="ld-k">{{ t("rack_power.completeness") }}</div>
                <div class="ld-v">{{ power.completeness ? t("rack_power.n_of_m", { n: power.completeness.known, m: power.completeness.included }) : "—" }}</div></div>
              <div class="ld-tile"><div class="ld-k">{{ t("rack_power.unassigned") }}</div><div class="ld-v">{{ W(power.unassigned_w) }}</div></div>
            </div>
            <n-alert v-if="power.partial" type="warning" :bordered="false" style="margin: 10px 0">{{ t("rack_power.partial") }}</n-alert>
            <n-space align="center" justify="space-between" style="margin-bottom: 8px">
              <div class="ld-sec" style="margin: 0">{{ t("rack_power.feeds_title") }}</div>
              <ExportButton :columns="feedCols" :rows="power.feeds" :filename="`${loc?.name ?? 'location'}-feeds`" :title="t('rack_power.feeds_title')" />
            </n-space>
            <n-data-table :columns="feedCols" :data="power.feeds" size="small" :bordered="false" :scroll-x="820" data-testid="ld-power-feeds" />
            <div class="hint" style="margin: 6px 0 12px">{{ t("location_detail.power_feeds_hint") }}</div>
            <n-space align="center" justify="space-between" style="margin-bottom: 8px">
              <div class="ld-sec" style="margin: 0">{{ t("rack_power.devices_title") }}</div>
              <ExportButton :columns="powerDevCols" :rows="power.devices" :filename="`${loc?.name ?? 'location'}-power`" :title="t('rack_power.devices_title')" />
            </n-space>
            <n-data-table :columns="powerDevCols" :data="power.devices" size="small" :bordered="false" :pagination="pg" :scroll-x="520" />
            <div class="hint" style="margin-top: 6px">{{ t("rack_power.planning_note") }}</div>
          </template>
        </n-tab-pane>

        <!-- 佈線 -->
        <n-tab-pane name="cabling" :tab="t('location_detail.tab_cabling')">
          <n-alert v-if="cablingErr" type="warning" :bordered="false">{{ cablingErr }}</n-alert>
          <template v-else-if="cabling">
            <div class="ld-tiles">
              <div class="ld-tile" data-testid="ld-cab-total"><div class="ld-k">{{ t("location_detail.cab_total") }}</div><div class="ld-v">{{ cabling.total }}</div></div>
              <div class="ld-tile" data-testid="ld-cab-in"><div class="ld-k">{{ t("location_detail.kind_in_room") }}</div><div class="ld-v">{{ cabling.in_room }}</div></div>
              <div class="ld-tile" data-testid="ld-cab-cross"><div class="ld-k">{{ t("location_detail.kind_cross_room") }}</div><div class="ld-v">{{ cabling.cross_room }}</div></div>
              <div class="ld-tile" data-testid="ld-cab-unknown"><div class="ld-k">{{ t("location_detail.kind_unknown") }}</div><div class="ld-v">{{ cabling.unknown }}</div></div>
            </div>
            <div class="hint" style="margin: 6px 0 12px">{{ t("location_detail.cab_hint") }}</div>
            <n-empty v-if="!cabling.items.length" :description="t('location_detail.cab_none')" />
            <template v-else>
              <n-space justify="end" style="margin-bottom: 8px">
                <ExportButton :columns="cableCols" :rows="cabling.items" :filename="`${loc?.name ?? 'location'}-cabling`" :title="t('location_detail.tab_cabling')" />
              </n-space>
              <n-data-table :columns="cableCols" :data="cabling.items" size="small" :bordered="false" :pagination="pg"
                            :scroll-x="760" data-testid="ld-cab-table" />
              <div class="ld-sec" style="margin-top: 12px">{{ t("location_detail.cab_by_rack") }}</div>
              <n-space>
                <n-tag v-for="r in cabling.by_rack" :key="r.id" size="small" :bordered="false">
                  <n-icon style="vertical-align:-2px;margin-right:4px"><PhysicalIcon /></n-icon>{{ r.rack }}：{{ r.cables }}
                </n-tag>
              </n-space>
              <div class="hint" style="margin-top: 6px">{{ t("location_detail.cab_by_rack_hint") }}</div>
            </template>
          </template>
        </n-tab-pane>
      </n-tabs>
    </n-card>
  </n-spin>
</template>

<style scoped>
.hint { font-size: 12px; opacity: .7; line-height: 1.5; }
.ld-tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 10px; margin-bottom: 12px; }
.ld-tile { padding: 10px 12px; border-radius: 6px; background: rgba(127, 127, 127, .08); }
.ld-k { font-size: 12.5px; opacity: .8; }
.ld-v { font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; }
.ld-sec { font-weight: 600; margin-bottom: 8px; }
.ld-grid { display: grid; grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); gap: 16px; }
@media (max-width: 900px) { .ld-grid { grid-template-columns: 1fr; } }
.ld-sel { display: flex; gap: 12px; align-items: flex-start; }
.ld-name { font-weight: 600; font-size: 16px; margin-bottom: 4px; }
.ld-pend { display: flex; justify-content: space-between; padding: 7px 10px; margin-bottom: 4px; border-radius: 6px;
           background: rgba(127, 127, 127, .08); cursor: pointer; }
.ld-pend:hover { background: rgba(127, 127, 127, .16); }
:deep(.ld-link) { cursor: pointer; color: var(--n-color-target, #18a058); }
</style>
