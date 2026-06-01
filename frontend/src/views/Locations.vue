<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
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
  LocationsIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SaveIcon, CancelIcon,
} from "@/icons";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";

const { t } = useI18n();
const msg = useMessage();
const rows = ref<Location[]>([]);
import { useTableQuickFilter } from "@/composables/useTableQuickFilter";
const { query: filterQ, filtered: filteredRows } = useTableQuickFilter(rows);
const loading = ref(false);
const show = ref(false);
const editing = ref<Location | null>(null);
const form = ref({
  name: "", address: "", description: "",
  latitude: null as number | null, longitude: null as number | null,
});
const checkedKeys = ref<DataTableRowKey[]>([]);
const bulkBusy = ref(false);

// 地圖供應商：全域系統設定（在「設定 → 系統」由 admin 調整），這裡唯讀套用於地圖預覽
const mapProvider = ref<"osm" | "google">("osm");
const mapSrc = computed(() => {
  const lat = form.value.latitude, lon = form.value.longitude;
  if (lat == null || lon == null) return "";
  if (mapProvider.value === "google") {
    return `https://maps.google.com/maps?q=${lat},${lon}&z=15&output=embed`;
  }
  const d = 0.01;
  return `https://www.openstreetmap.org/export/embed.html`
    + `?bbox=${lon - d}%2C${lat - d}%2C${lon + d}%2C${lat + d}&layer=mapnik&marker=${lat}%2C${lon}`;
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
  form.value = { name: "", address: "", description: "", latitude: null, longitude: null };
  show.value = true;
}
function openEdit(r: Location) {
  editing.value = r;
  form.value = {
    name: r.name, address: r.address ?? "", description: r.description ?? "",
    latitude: r.latitude ?? null, longitude: r.longitude ?? null,
  };
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
  ["name", "address", "coords", "description", "actions"],
  ["name", "address", "coords", "description", "actions"],
);
const columnPickerItems = computed(() => [
  { key: "name", label: t("cols.name") },
  { key: "address", label: t("cols.address") },
  { key: "coords", label: t("cols.coords") },
  { key: "description", label: t("cols.description") },
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
  { title: t("cols.coords"), key: "coords", width: 160,
    render: (r) => (r.latitude != null && r.longitude != null) ? `${r.latitude}, ${r.longitude}` : "—" },
  { title: t("locations.address"), key: "address", minWidth: 200, ellipsis: { tooltip: true }, render: (r) => r.address ?? "—",
    sorter: (a, b) => (a.address ?? "").localeCompare(b.address ?? "") },
  { title: t("common.description"), key: "description", minWidth: 200, ellipsis: { tooltip: true }, render: (r) => r.description ?? "—",
    sorter: (a, b) => (a.description ?? "").localeCompare(b.description ?? "") },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 96,
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openEdit(r)),
      h(NPopconfirm, { onPositiveClick: () => del(r) }, {
        trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]);
const cols = computed<DataTableColumns<Location>>(() =>
  allCols.value.filter((c: any) => c.type === "selection" || visibleKeys.value.includes(c.key)),
);
onMounted(() => { void refresh(); getMapProvider().then((p) => { mapProvider.value = p; }); });
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
      <n-button type="primary" @click="openCreate">
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
    <n-data-table
      :columns="cols" :data="filteredRows" :loading="loading" :bordered="false"
      :scroll-x="876"
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
        <n-form-item v-if="mapSrc" :label="t('locations.map_preview')">
          <iframe :src="mapSrc" style="width: 100%; height: 220px; border: 1px solid var(--n-border-color, #ddd); border-radius: 6px"
                  loading="lazy" referrerpolicy="no-referrer"></iframe>
        </n-form-item>
        <n-form-item :label="t('sections.description')">
          <n-input v-model:value="form.description" type="textarea" :rows="2" />
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
