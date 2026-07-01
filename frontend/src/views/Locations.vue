<script setup lang="ts">
import { useAuthStore } from "@/stores/auth";
const _authBtn = useAuthStore();
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useTablePagination } from "@/composables/useTablePagination";
import {
  NCard, NDataTable, NSpace, NIcon, NButton, NModal, NForm, NFormItem,
  NInput, NPopconfirm, NInputNumber, NTooltip,
  useMessage, type DataTableColumns, type DataTableRowKey,
} from "naive-ui";
import {
  listLocations, createLocation, updateLocation, deleteLocation, bulkDeleteLocations,
  getMapProvider, type Location,
} from "@/api/basic";
import {
  LocationsIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SaveIcon, CancelIcon, PinIcon,
} from "@/icons";
import { usePinned } from "@/composables/usePinned";
import LocationsMap from "@/components/LocationsMap.vue";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { useCustomers } from "@/composables/useCustomers";
import { uploadFloorplan, getFloorplanObjectURL, deleteFloorplan } from "@/api/racks";

// ── 機房平面圖（在編輯既有機房時可上傳/檢視/移除；完整定位編輯在「機櫃」頁）──
const fpUrl = ref<string | null>(null);
const fpHas = ref(false);
const fpBusy = ref(false);
const fpInput = ref<HTMLInputElement | null>(null);
function revokeFp() { if (fpUrl.value) { URL.revokeObjectURL(fpUrl.value); fpUrl.value = null; } }
async function loadFp(id: string) {
  revokeFp(); fpHas.value = false;
  try { fpUrl.value = await getFloorplanObjectURL(id); fpHas.value = true; } catch { fpHas.value = false; }
}
function pickFp() { fpInput.value?.click(); }
async function onFpFile(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0];
  if (!f || !editing.value) return;
  fpBusy.value = true;
  try { await uploadFloorplan(editing.value.id, f); await loadFp(editing.value.id); msg.success(t("common.ok")); }
  catch (err: any) { msg.error(err?.response?.data?.detail ?? t("errors.server")); }
  finally { fpBusy.value = false; if (fpInput.value) fpInput.value.value = ""; }
}
async function removeFp() {
  if (!editing.value) return;
  try { await deleteFloorplan(editing.value.id); revokeFp(); fpHas.value = false; }
  catch (err: any) { msg.error(err?.response?.data?.detail ?? t("errors.server")); }
}

const { t } = useI18n();
const pg = useTablePagination();
const { options: customerOptions, ensureLoaded: ensureCustomerOptsLoaded } = useCustomers();
const msg = useMessage();
const rows = ref<Location[]>([]);
import { useTableQuickFilter } from "@/composables/useTableQuickFilter";
const { query: filterQ, filtered: filteredRows } = useTableQuickFilter(rows);
const pin = usePinned("locations");
const displayRows = computed(() => pin.sortPinnedFirst(filteredRows.value));

// 世界地圖標記：有經緯度的地點
const mapPoints = computed(() => rows.value
  .filter((r) => r.latitude != null && r.longitude != null)
  .map((r) => ({ id: r.id, name: r.name, lat: Number(r.latitude), lng: Number(r.longitude) })));
function onMapSelect(id: string) {
  const r = rows.value.find((x) => x.id === id);
  if (r) openEdit(r);
}
const loading = ref(false);
const show = ref(false);
const editing = ref<Location | null>(null);
const form = ref({
  name: "", address: "", description: "",
  latitude: null as number | null, longitude: null as number | null,
  customer_id: null as string | null,
});
const checkedKeys = ref<DataTableRowKey[]>([]);
const bulkBusy = ref(false);

// 地圖供應商：全域系統設定（在「設定 → 系統」由 admin 調整），這裡唯讀套用於地圖預覽
const mapProvider = ref<"builtin" | "osm" | "google">("builtin");
// 不內嵌第三方地圖 iframe（會把 Google/OSM 的頁面與其 JS 一起載進來 → 隱私外洩 + 安全掃描誤報
// 跨網域 JS／SRI）。改成「在新分頁開啟地圖」連結，使用者點了才連到第三方。
const mapLink = computed(() => {
  const lat = form.value.latitude, lon = form.value.longitude;
  if (lat == null || lon == null) return "";
  if (mapProvider.value === "google") {
    return `https://www.google.com/maps?q=${lat},${lon}&z=15`;
  }
  return `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=15/${lat}/${lon}`;
});

async function doBulkDelete() {
  if (!checkedKeys.value.length) return;
  bulkBusy.value = true;
  try {
    const res = await bulkDeleteLocations(checkedKeys.value.map(String));
    if (res.failed) msg.warning(t("common.deleted_failed", { deleted: res.deleted, failed: res.failed }));
    else msg.success(t("common.deleted_n", { n: res.deleted }));
    checkedKeys.value = [];
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.network")); }
  finally { bulkBusy.value = false; }
}

async function refresh() {
  loading.value = true;
  try { rows.value = (await listLocations()).items; }
  catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}
function openCreate() {
  editing.value = null;
  form.value = { name: "", address: "", description: "", latitude: null, longitude: null, customer_id: null };
  revokeFp(); fpHas.value = false;
  show.value = true;
}
function openEdit(r: Location) {
  editing.value = r;
  form.value = {
    name: r.name, address: r.address ?? "", description: r.description ?? "",
    latitude: r.latitude ?? null, longitude: r.longitude ?? null,
    customer_id: (r as any).customer_id ?? null,
  };
  void loadFp(r.id);
  show.value = true;
}
async function submit() {
  try {
    const payload = {
      name: form.value.name,
      address: form.value.address || undefined,
      description: form.value.description || undefined,
      latitude: form.value.latitude,
      longitude: form.value.longitude,
      customer_id: form.value.customer_id ?? null,
    };
    if (editing.value) await updateLocation(editing.value.id, payload);
    else await createLocation(payload);
    show.value = false;
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function del(r: Location) {
  try { await deleteLocation(r.id); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

const { visibleKeys, setVisible, reset } = useColumnPrefs(
  "locations",
  ["name", "customer_name", "address", "coords", "description", "rack_count", "device_count", "actions"],
  ["name", "customer_name", "address", "coords", "description", "rack_count", "device_count", "actions"],
);
const columnPickerItems = computed(() => [
  { key: "name", label: t("cols.name") },
  { key: "customer_name", label: t("cols.unit") },
  { key: "address", label: t("cols.address") },
  { key: "coords", label: t("cols.coords") },
  { key: "description", label: t("cols.description") },
  { key: "rack_count", label: t("cols.rack_count") },
  { key: "device_count", label: t("cols.device_count") },
  { key: "actions", label: t("cols.actions") },
]);
function iconAction(icon: any, label: string, onClick: () => void, type?: any) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}
const allCols = computed<DataTableColumns<Location>>(() => [
  { type: "selection" },
  { title: t("common.name"), key: "name", minWidth: 180, ellipsis: { tooltip: true }, sorter: (a, b) => a.name.localeCompare(b.name) },
  { title: t("cols.unit"), key: "customer_name", width: 140, ellipsis: { tooltip: true },
    render: (r) => (r as any).customer_name ?? "—" },
  { title: t("cols.coords"), key: "coords", width: 150,
    render: (r) => (r.latitude != null && r.longitude != null) ? `${r.latitude}, ${r.longitude}` : "—" },
  { title: t("locations.address"), key: "address", minWidth: 150, ellipsis: { tooltip: true }, render: (r) => r.address ?? "—",
    sorter: (a, b) => (a.address ?? "").localeCompare(b.address ?? "") },
  { title: t("common.description"), key: "description", minWidth: 150, ellipsis: { tooltip: true }, render: (r) => r.description ?? "—",
    sorter: (a, b) => (a.description ?? "").localeCompare(b.description ?? "") },
  { title: t("cols.rack_count"), key: "rack_count", width: 90, render: (r) => r.rack_count ?? 0,
    sorter: (a, b) => (a.rack_count ?? 0) - (b.rack_count ?? 0) },
  { title: t("cols.device_count"), key: "device_count", width: 90, render: (r) => r.device_count ?? 0,
    sorter: (a, b) => (a.device_count ?? 0) - (b.device_count ?? 0) },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 128,
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      h(NButton, {
        size: "small", quaternary: true,
        type: pin.isPinned(r.id) ? "warning" : "default", title: t("common.pin"),
        onClick: (e: MouseEvent) => { e.stopPropagation(); pin.toggle(r.id); },
      }, { icon: () => h(NIcon, { color: pin.isPinned(r.id) ? "#f0a020" : undefined }, () => h(PinIcon)) }),
      iconAction(EditIcon, t("common.edit"), () => openEdit(r)),
      h(NPopconfirm, { onPositiveClick: () => del(r) }, {
        trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]);
const cols = computed<DataTableColumns<Location>>(() =>
  allCols.value.filter((c: any) => c.type === "selection" || c.key === "actions" || visibleKeys.value.includes(c.key)),
);
onMounted(() => {
  void refresh();
  void ensureCustomerOptsLoaded();
  getMapProvider().then((p) => { mapProvider.value = p; });
});
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><LocationsIcon /></n-icon>
        <span>{{ t("nav.locations") }}</span>
      </n-space>
    </template>
    <n-space style="margin-bottom: 12px" align="center">
      <n-input v-model:value="filterQ" :placeholder="t('common.filter')" clearable style="width: 180px" />
      <n-button @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
      <n-button type="primary" :disabled="_authBtn.me?.can_edit === false" @click="openCreate">
        <template #icon><n-icon><PlusIcon /></n-icon></template>
        {{ t("common.create") }}
      </n-button>
      <ColumnPicker :all="columnPickerItems" :visible="visibleKeys"
                    @update:visible="setVisible" @reset="reset" />
      <ExportButton :columns="cols" :rows="rows" filename="locations" :title="t('nav.locations')" />
    </n-space>
    <n-space v-if="checkedKeys.length" align="center" style="margin-bottom: 8px; padding: 8px 12px; background: rgba(127,127,127,0.08); border-radius: 6px;">
      <span>{{ t("common.selected_n", { n: checkedKeys.length }) }}</span>
      <n-popconfirm @positive-click="doBulkDelete">
        <template #trigger>
          <n-button type="error" size="small" :loading="bulkBusy">
            <template #icon><n-icon><DeleteIcon /></n-icon></template>
            {{ t("common.bulk_delete") }}
          </n-button>
        </template>
        {{ t("common.confirm_delete_n", { n: checkedKeys.length }) }}
      </n-popconfirm>
      <n-button size="small" @click="checkedKeys = []">{{ t("common.clear_selection") }}</n-button>
    </n-space>
    <locations-map v-if="mapPoints.length" :points="mapPoints" :provider="mapProvider" style="margin-bottom: 12px" @select="onMapSelect" />
    <n-data-table
      :columns="cols" :data="displayRows" :loading="loading" :bordered="false"
      :scroll-x="1048" :pagination="pg"
      :row-key="(row: Location) => row.id"
      :checked-row-keys="checkedKeys"
      @update:checked-row-keys="(keys: DataTableRowKey[]) => checkedKeys = keys"
    />
    <n-modal v-model:show="show" preset="card" style="width: 460px">
      <template #header>
        <n-space align="center">
          <n-icon :size="20"><component :is="editing ? EditIcon : PlusIcon" /></n-icon>
          <span>{{ editing ? t("common.edit") : t("common.create") }}</span>
        </n-space>
      </template>
      <n-form>
        <n-form-item :label="t('common.name')"><n-input v-model:value="form.name" /></n-form-item>
        <n-form-item :label="t('cols.unit')">
          <n-select v-model:value="form.customer_id" :options="customerOptions"
                    :placeholder="t('common.not_specified')" clearable filterable />
        </n-form-item>
        <n-form-item :label="t('locations.address')"><n-input v-model:value="form.address" /></n-form-item>
        <n-space :size="12">
          <n-form-item :label="t('locations.latitude')">
            <n-input-number v-model:value="form.latitude" :min="-90" :max="90" :step="0.0001"
                            placeholder="23.9037" style="width: 180px" />
          </n-form-item>
          <n-form-item :label="t('locations.longitude')">
            <n-input-number v-model:value="form.longitude" :min="-180" :max="180" :step="0.0001"
                            placeholder="120.6869" style="width: 180px" />
          </n-form-item>
        </n-space>
        <n-form-item v-if="mapLink" :label="t('locations.map_preview')">
          <n-button tag="a" :href="mapLink" target="_blank" rel="noopener noreferrer" secondary>
            <template #icon><n-icon :component="LocationsIcon" /></template>
            {{ t("locations.open_in_map") }}
          </n-button>
        </n-form-item>
        <n-form-item :label="t('sections.description')">
          <n-input v-model:value="form.description" type="textarea" :rows="2" />
        </n-form-item>
        <n-form-item v-if="editing" :label="t('racks.floor_plan')">
          <div style="width: 100%">
            <input ref="fpInput" type="file" accept="image/png,image/jpeg,image/gif,image/webp"
                   style="display:none" @change="onFpFile" />
            <img v-if="fpHas && fpUrl" :src="fpUrl" alt="floor plan"
                 style="display:block; width:100%; max-height:220px; object-fit:contain;
                        border:1px solid var(--n-border-color,#ddd); border-radius:6px; background:rgba(127,127,127,.05)" />
            <n-space style="margin-top: 8px">
              <n-button size="small" :loading="fpBusy" @click="pickFp">
                <template #icon><n-icon><PlusIcon /></n-icon></template>
                {{ fpHas ? t("racks.fp_replace") : t("racks.fp_upload") }}
              </n-button>
              <n-popconfirm v-if="fpHas" @positive-click="removeFp">
                <template #trigger>
                  <n-button size="small" type="error" quaternary>
                    <template #icon><n-icon><DeleteIcon /></n-icon></template>
                    {{ t("racks.fp_remove") }}
                  </n-button>
                </template>
                {{ t("racks.fp_remove_confirm") }}
              </n-popconfirm>
            </n-space>
            <div style="font-size:12px; opacity:0.6; margin-top:4px">{{ t("racks.fp_location_hint") }}</div>
          </div>
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="show = false">
          <template #icon><n-icon><CancelIcon /></n-icon></template>
          {{ t("common.cancel") }}
        </n-button>
        <n-button type="primary" @click="submit">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </n-space>
    </n-modal>
  </n-card>
</template>

<style scoped>
/* 寬表用內部水平捲動吸收寬度，卡片不被撐爆溢出 */
:deep(.n-card) { min-width: 0; }
:deep(.n-data-table) { max-width: 100%; }
</style>
