<script setup lang="ts">
import { useAuthStore } from "@/stores/auth";
const _authBtn = useAuthStore();
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NIcon, NButton, NModal, NForm, NFormItem,
  NInput, NSwitch, NPopconfirm, NTooltip,
  useMessage, type DataTableColumns, type DataTableRowKey,
} from "naive-ui";
import { listVRFs, createVRF, updateVRF, deleteVRF, bulkDeleteVRFs, type VRF } from "@/api/basic";
import {
  VrfsIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SaveIcon, CancelIcon,
} from "@/icons";
import ColumnPicker from "@/components/ColumnPicker.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";

const { t } = useI18n();
const msg = useMessage();
const rows = ref<VRF[]>([]);
import { useTableQuickFilter } from "@/composables/useTableQuickFilter";
const { query: filterQ, filtered: filteredRows } = useTableQuickFilter(rows);
const loading = ref(false);
const show = ref(false);
const editing = ref<VRF | null>(null);
const form = ref({ name: "", rd: "", description: "", allow_overlap: true });
const checkedKeys = ref<DataTableRowKey[]>([]);
const bulkBusy = ref(false);

async function doBulkDelete() {
  if (!checkedKeys.value.length) return;
  bulkBusy.value = true;
  try {
    const res = await bulkDeleteVRFs(checkedKeys.value.map(String));
    if (res.failed) msg.warning(t("common.deleted_failed", { deleted: res.deleted, failed: res.failed }));
    else msg.success(t("common.deleted_n", { n: res.deleted }));
    checkedKeys.value = [];
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.network")); }
  finally { bulkBusy.value = false; }
}

async function refresh() {
  loading.value = true;
  try { rows.value = (await listVRFs()).items; }
  catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}
function openCreate() {
  editing.value = null;
  form.value = { name: "", rd: "", description: "", allow_overlap: true };
  show.value = true;
}
function openEdit(r: VRF) {
  editing.value = r;
  form.value = {
    name: r.name, rd: r.rd ?? "", description: r.description ?? "",
    allow_overlap: r.allow_overlap,
  };
  show.value = true;
}
async function submit() {
  try {
    const payload = {
      name: form.value.name,
      rd: form.value.rd || undefined,
      description: form.value.description || undefined,
      allow_overlap: form.value.allow_overlap,
    };
    if (editing.value) await updateVRF(editing.value.id, payload);
    else await createVRF(payload);
    show.value = false;
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function del(r: VRF) {
  try { await deleteVRF(r.id); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

const { visibleKeys, setVisible, reset } = useColumnPrefs(
  "vrfs",
  ["name", "rd", "description", "allow_overlap", "actions"],
  ["name", "rd", "description", "allow_overlap", "actions"],
);
const columnPickerItems = computed(() => [
  { key: "name", label: t("cols.name") },
  { key: "rd", label: "RD" },
  { key: "description", label: t("cols.description") },
  { key: "allow_overlap", label: "allow overlap" },
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
const allCols = computed<DataTableColumns<VRF>>(() => [
  { type: "selection" },
  { title: t("common.name"), key: "name", minWidth: 180, ellipsis: { tooltip: true }, sorter: (a, b) => a.name.localeCompare(b.name) },
  { title: t("cols.rd"), key: "rd", width: 140, render: (r) => r.rd ?? "—",
    sorter: (a, b) => (a.rd ?? "").localeCompare(b.rd ?? "") },
  { title: t("common.description"), key: "description", minWidth: 220, ellipsis: { tooltip: true },
    render: (r) => r.description ?? "—",
    sorter: (a, b) => (a.description ?? "").localeCompare(b.description ?? "") },
  { title: t("cols.allow_overlap"), key: "allow_overlap", width: 120,
    render: (r) => r.allow_overlap ? "✓" : "—" },
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
const cols = computed<DataTableColumns<VRF>>(() =>
  allCols.value.filter((c: any) => c.type === "selection" || visibleKeys.value.includes(c.key)),
);
onMounted(() => { void refresh(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><VrfsIcon /></n-icon>
        <span>{{ t("nav.vrfs") }}</span>
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
      :scroll-x="796"
      :row-key="(row: VRF) => row.id"
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
        <n-form-item label="RD"><n-input v-model:value="form.rd" placeholder="65000:1" /></n-form-item>
        <n-form-item :label="t('sections.description')">
          <n-input v-model:value="form.description" type="textarea" :rows="2" />
        </n-form-item>
        <n-form-item label="Allow overlap">
          <n-switch v-model:value="form.allow_overlap" />
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
