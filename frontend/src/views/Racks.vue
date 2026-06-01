<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard,
  NDataTable,
  NSpace,
  NSelect,
  NSpin,
  NButton,
  NPopconfirm,
  NModal,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NTooltip,
  useMessage,
  type DataTableColumns,
  type DataTableRowKey,
} from "naive-ui";
import { NIcon } from "naive-ui";
import { RacksIcon, DeleteIcon, PlusIcon, EditIcon, SaveIcon, CancelIcon, LocationsIcon } from "@/icons";
import { useRouter } from "vue-router";
import { apiClient } from "@/api/client";
import RackDiagram from "@/components/RackDiagram.vue";
import { RACK_DEVICE_TYPES, rackTypeColor } from "@/utils/rackColors";
import RackFloorPlan from "@/components/RackFloorPlan.vue";
import { getRackDiagram, type RackDiagram as RD } from "@/api/racks";
import { bulkDeleteRacks, listLocations, type Location } from "@/api/basic";
import { useAuthStore } from "@/stores/auth";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";

interface Rack {
  id: string;
  name: string;
  u_height: number;
  location_id: string | null;
  description: string | null;
  numbering?: "top-down" | "bottom-up";
  face?: "front" | "rear";
  pos_x?: number | null;
  pos_y?: number | null;
}

const { t } = useI18n();
const msg = useMessage();
const auth = useAuthStore();
const router = useRouter();
function goRooms() { router.push({ name: "locations" }); }
// 釘選機房（存 localStorage，每瀏覽器）：進機櫃頁時預設先看釘選的機房
const PINNED_ROOM_KEY = "jt_pinned_room";
const pinnedRoom = ref<string | null>(localStorage.getItem(PINNED_ROOM_KEY));
function togglePinRoom() {
  if (!roomId.value) return;
  if (pinnedRoom.value === roomId.value) {
    pinnedRoom.value = null; localStorage.removeItem(PINNED_ROOM_KEY);
  } else {
    pinnedRoom.value = roomId.value; localStorage.setItem(PINNED_ROOM_KEY, roomId.value);
  }
}
const isAdmin = computed(() => !!auth.me?.is_admin);
const roomFocus = ref<RD | null>(null);   // 在平面圖上點選的機櫃 → 顯示其 U 位
async function onRoomRackSelect(rackId: string) {
  try { roomFocus.value = await getRackDiagram(rackId); }
  catch { msg.error(t("errors.network")); }
}
const rows = ref<Rack[]>([]);
const loading = ref(false);
const selected = ref<string | null>(null);
const diagram = ref<RD | null>(null);
const diagramLoading = ref(false);

// 機房（= location）：選一間機房可一次把該機房所有機櫃並排成一排
const locations = ref<Location[]>([]);
const roomId = ref<string | null>(null);
import { useTableQuickFilter } from "@/composables/useTableQuickFilter";
const { query: filterQ, filtered: filteredRows } = useTableQuickFilter(rows);
const roomDiagrams = ref<RD[]>([]);
const roomLoading = ref(false);
const locationOptions = computed(() =>
  locations.value.map((l) => ({ label: l.name, value: l.id })));

async function loadRoom(locId: string) {
  roomLoading.value = true;
  try {
    // 並排順序依機房平面圖相對位置（pos_x → pos_y）；未擺放的排最後
    const racksHere = rows.value
      .filter((r) => r.location_id === locId)
      .sort((a, b) => {
        const ax = a.pos_x ?? 99, bx = b.pos_x ?? 99;
        if (ax !== bx) return ax - bx;
        return (a.pos_y ?? 99) - (b.pos_y ?? 99);
      });
    roomDiagrams.value = (await Promise.all(
      racksHere.map((r) => getRackDiagram(r.id).catch(() => null)),
    )).filter((d): d is RD => d !== null);
  } catch {
    msg.error(t("errors.network"));
    roomDiagrams.value = [];
  } finally {
    roomLoading.value = false;
  }
}

watch(roomId, (v) => {
  roomFocus.value = null;
  if (v) { selected.value = null; void loadRoom(v); }
  else roomDiagrams.value = [];
});

const checkedKeys = ref<DataTableRowKey[]>([]);
const bulkBusy = ref(false);

async function doBulkDelete() {
  if (!checkedKeys.value.length) return;
  bulkBusy.value = true;
  try {
    const res = await bulkDeleteRacks(checkedKeys.value.map(String));
    if (res.failed) msg.warning(t("common.deleted_failed", { deleted: res.deleted, failed: res.failed }));
    else msg.success(t("common.deleted_n", { n: res.deleted }));
    checkedKeys.value = [];
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.network")); }
  finally { bulkBusy.value = false; }
}

const { visibleKeys, setVisible, reset } = useColumnPrefs(
  "racks",
  ["name", "u_height", "description"],
  ["name", "u_height", "description"],
);
const columnPickerItems = [
  { key: "name", label: t("cols.name") },
  { key: "u_height", label: t("cols.u_height") },
  { key: "description", label: t("cols.description") },
];
function iconBtn(icon: any, label: string, onClick: () => void, type?: any) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, {
      size: "small", quaternary: true, type,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); },
    }, { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}
const allColumns = computed<DataTableColumns<Rack>>(() => [
  { type: "selection" },
  { title: t("common.name"), key: "name", sorter: (a, b) => a.name.localeCompare(b.name) },
  { title: t("racks.u_height"), key: "u_height", width: 100, sorter: (a, b) => a.u_height - b.u_height },
  { title: t("common.description"), key: "description", render: (r) => r.description ?? "—",
    sorter: (a, b) => (a.description ?? "").localeCompare(b.description ?? "") },
  {
    title: t("common.actions"), key: "actions", width: 96, className: "col-actions",
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconBtn(EditIcon, t("common.edit"), () => openEdit(r)),
      h(NPopconfirm, { onPositiveClick: () => removeRack(r) }, {
        trigger: () => iconBtn(DeleteIcon, t("common.delete"), () => {}, "error"),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]);
const columns = computed<DataTableColumns<Rack>>(() =>
  allColumns.value.filter((c: any) =>
    c.type === "selection" || c.key === "actions" || visibleKeys.value.includes(c.key)),
);

// ── 新增 / 編輯 / 刪除機櫃 ──
const showEdit = ref(false);
const editing = ref<Rack | null>(null);
const form = ref({
  name: "", u_height: 42, location_id: null as string | null, description: "",
  numbering: "top-down" as "top-down" | "bottom-up", face: "front" as "front" | "rear",
});
function openCreate() {
  editing.value = null;
  form.value = { name: "", u_height: 42, location_id: roomId.value, description: "", numbering: "top-down", face: "front" };
  showEdit.value = true;
}
function openEdit(r: Rack) {
  editing.value = r;
  form.value = {
    name: r.name, u_height: r.u_height, location_id: r.location_id, description: r.description ?? "",
    numbering: r.numbering ?? "top-down", face: r.face ?? "front",
  };
  showEdit.value = true;
}
const numberingOpts = [
  { label: t("racks.numbering_top_down"), value: "top-down" },
  { label: t("racks.numbering_bottom_up"), value: "bottom-up" },
];
const faceOpts = [
  { label: t("racks.face_front"), value: "front" },
  { label: t("racks.face_rear"), value: "rear" },
];
async function submitRack() {
  if (!form.value.name.trim()) { msg.error(t("common.name_required")); return; }
  const payload = {
    name: form.value.name.trim(),
    u_height: form.value.u_height,
    location_id: form.value.location_id ?? null,
    description: form.value.description.trim() || null,
    numbering: form.value.numbering,
    face: form.value.face,
  };
  try {
    if (editing.value) await apiClient.patch(`/api/v1/racks/${editing.value.id}`, payload);
    else await apiClient.post("/api/v1/racks", payload);
    showEdit.value = false;
    msg.success(t("common.ok"));
    await refresh();
    if (roomId.value) await loadRoom(roomId.value);
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function removeRack(r: Rack) {
  try {
    await apiClient.delete(`/api/v1/racks/${r.id}`);
    msg.success(t("common.ok"));
    if (selected.value === r.id) selected.value = null;
    await refresh();
    if (roomId.value) await loadRoom(roomId.value);
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

async function refresh() {
  loading.value = true;
  try {
    const { data } = await apiClient.get<{ items: Rack[] }>("/api/v1/racks", {
      params: { page: 1, page_size: 200 },
    });
    rows.value = data.items;
    if (!selected.value && rows.value.length) {
      selected.value = rows.value[0].id;
    }
  } catch {
    msg.error(t("errors.network"));
  } finally {
    loading.value = false;
  }
}

async function loadDiagram(id: string) {
  diagramLoading.value = true;
  try {
    diagram.value = await getRackDiagram(id);
  } catch {
    msg.error(t("errors.network"));
    diagram.value = null;
  } finally {
    diagramLoading.value = false;
  }
}

watch(selected, (v) => {
  if (v) { roomId.value = null; void loadDiagram(v); }
  else diagram.value = null;
});

onMounted(() => {
  void refresh();
  listLocations().then((r) => { locations.value = r.items; }).catch(() => { /* silent */ });
  // 有釘選機房 → 進來預設先看它（watch(roomId) 會載入該機房整排機櫃）
  if (pinnedRoom.value && !roomId.value && !selected.value) roomId.value = pinnedRoom.value;
});
</script>

<template>
  <n-space vertical :size="16">
    <n-card>
      <template #header>
        <n-space align="center" :wrap-item="false">
          <n-icon :size="22"><RacksIcon /></n-icon>
          <span>{{ t("nav.racks") }}</span>
        </n-space>
      </template>
      <n-space align="center">
        <n-select
          v-model:value="roomId"
          :options="locationOptions"
          :placeholder="t('racks.room_placeholder')"
          style="width: 260px"
          clearable
        />
        <n-button v-if="roomId" size="small" quaternary
                  :type="pinnedRoom === roomId ? 'warning' : 'default'"
                  @click="togglePinRoom"
                  :title="pinnedRoom === roomId ? t('racks.unpin_room') : t('racks.pin_room')">
          {{ pinnedRoom === roomId ? '★' : '☆' }}
        </n-button>
        <span style="opacity: .4">{{ t("racks.or") }}</span>
        <n-select
          v-model:value="selected"
          :options="rows.map((r) => ({ label: `${r.name} (${r.u_height}U)`, value: r.id }))"
          :placeholder="t('racks.select_placeholder')"
          style="width: 280px"
          clearable
        />
        <n-button quaternary size="small" @click="goRooms" :title="t('racks.manage_rooms_hint')">
          <template #icon><n-icon><LocationsIcon /></n-icon></template>
          {{ t("racks.manage_rooms") }}
        </n-button>
      </n-space>
    </n-card>

    <!-- 機房模式：平面圖 + 一整排機櫃並排 -->
    <template v-if="roomId">
      <n-card style="margin-bottom: 16px">
        <rack-floor-plan :location-id="roomId" :can-edit="isAdmin" @select="onRoomRackSelect" />
      </n-card>

      <!-- 在平面圖上點選的機櫃 → 顯示其 U 位 -->
      <n-card v-if="roomFocus" style="margin-bottom: 16px" :bordered="false" content-style="padding:0">
        <n-space justify="end" style="margin-bottom: 6px">
          <n-button size="tiny" quaternary @click="roomFocus = null">
            {{ t("common.cancel") }}
          </n-button>
        </n-space>
        <rack-diagram :diagram="roomFocus" />
      </n-card>

      <!-- 點選聚焦時隱藏整排總覽，避免同一機櫃顯示兩次 -->
      <n-spin v-if="!roomFocus" :show="roomLoading">
        <div v-if="roomDiagrams.length" class="rack-row">
          <rack-diagram v-for="d in roomDiagrams" :key="d.rack_id" :diagram="d" :show-legend="false" />
        </div>
        <!-- 整排機櫃共用一個圖例（不用每櫃都重複） -->
        <div v-if="roomDiagrams.length" class="rack-legend-shared">
          <span v-for="ty in RACK_DEVICE_TYPES" :key="ty" class="legend-item"
                :style="{ background: rackTypeColor(ty) }">{{ ty }}</span>
        </div>
        <n-card v-else-if="!roomLoading" :title="t('racks.diagram_title')">
          <p style="opacity: 0.7">{{ t("racks.room_empty") }}</p>
        </n-card>
      </n-spin>
    </template>

    <!-- 單一機櫃模式 -->
    <n-spin v-else :show="diagramLoading">
      <rack-diagram v-if="diagram" :diagram="diagram" />
      <n-card v-else-if="!selected" :title="t('racks.diagram_title')">
        <p style="opacity: 0.7">{{ t("racks.diagram_empty") }}</p>
      </n-card>
    </n-spin>

    <n-card :title="t('racks.all_title')">
      <n-space style="margin-bottom: 8px" align="center">
        <n-input v-model:value="filterQ" :placeholder="t('common.filter')" clearable style="width: 180px" />
        <n-button type="primary" @click="openCreate">
          <template #icon><n-icon><PlusIcon /></n-icon></template>
          {{ t("racks.add") }}
        </n-button>
        <ColumnPicker :all="columnPickerItems" :visible="visibleKeys"
                      @update:visible="setVisible" @reset="reset" />
        <ExportButton :columns="columns" :rows="rows" filename="racks" :title="t('nav.racks')" />
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
        :columns="columns"
        :data="filteredRows"
        :loading="loading"
        :pagination="{ pageSize: 50 }"
        :bordered="false"
        :row-key="(row: Rack) => row.id"
        :checked-row-keys="checkedKeys"
        @update:checked-row-keys="(keys: DataTableRowKey[]) => checkedKeys = keys"
        :row-props="(row: Rack) => ({
          style: 'cursor: pointer',
          onClick: (e: MouseEvent) => {
            const target = e.target as HTMLElement;
            if (target.closest('.n-checkbox')) return;
            selected = row.id;
          },
        })"
      />
    </n-card>

    <n-modal v-model:show="showEdit" preset="card" style="width: 460px"
             :title="editing ? t('common.edit') : t('racks.add')">
      <n-form label-placement="left" label-width="90">
        <n-form-item :label="t('common.name')" required>
          <n-input v-model:value="form.name" />
        </n-form-item>
        <n-form-item :label="t('racks.u_height')">
          <n-input-number v-model:value="form.u_height" :min="1" :max="99" style="width: 100%" />
        </n-form-item>
        <n-form-item :label="t('racks.numbering')">
          <n-select v-model:value="form.numbering" :options="numberingOpts" />
        </n-form-item>
        <n-form-item :label="t('racks.face')">
          <n-select v-model:value="form.face" :options="faceOpts" />
        </n-form-item>
        <n-form-item :label="t('nav.locations')">
          <n-select v-model:value="form.location_id" :options="locationOptions"
                    clearable :placeholder="t('racks.room_placeholder')" />
        </n-form-item>
        <n-form-item :label="t('common.description')">
          <n-input v-model:value="form.description" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="showEdit = false">
          <template #icon><n-icon><CancelIcon /></n-icon></template>
          {{ t("common.cancel") }}
        </n-button>
        <n-button type="primary" @click="submitRack">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </n-space>
    </n-modal>
  </n-space>
</template>

<style scoped>
/* 機房內機櫃並排成一橫排（依平面圖相對位置排序）；超出寬度橫向捲動，不上下堆疊 */
.rack-row {
  display: flex;
  flex-wrap: nowrap;
  gap: 16px;
  align-items: flex-start;
  overflow-x: auto;
  padding-bottom: 8px;
}
.rack-row > * { flex: 0 0 auto; }
.rack-row :deep(.n-card) { width: auto; }
/* 整排機櫃共用的圖例 */
.rack-legend-shared {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 11px;
  margin-top: 4px;
}
.rack-legend-shared .legend-item {
  padding: 2px 8px;
  border-radius: 3px;
  color: white;
  font-family: monospace;
}
</style>
