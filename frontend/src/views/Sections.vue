<script setup lang="ts">
import { useAuthStore } from "@/stores/auth";
const _authBtn = useAuthStore();
import { h, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NIcon, NButton, NTag, NPopconfirm, NTooltip,
  NModal, NForm, NFormItem, NInput, NSelect, NInputNumber,
  useMessage, type DataTableColumns, type DataTableRowKey,
} from "naive-ui";
import {
  listSections, bulkDeleteSections,
  createSection, updateSection, deleteSection, type SectionUpdate,
} from "@/api/sections";
import type { Section } from "@/types";
import { SectionsIcon, RefreshIcon, DeleteIcon, PlusIcon, EditIcon, SaveIcon, CancelIcon } from "@/icons";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { useTableQuickFilter } from "@/composables/useTableQuickFilter";
import { useCustomers } from "@/composables/useCustomers";
import { useEntityLinks } from "@/composables/useEntityLinks";
import { computed } from "vue";
import { useTablePagination } from "@/composables/useTablePagination";
const pg = useTablePagination();

const { labelFor: customerLabelFor, ensureLoaded: ensureCustomersLoaded } = useCustomers();

const { t } = useI18n();
const msg = useMessage();
const router = useRouter();
const links = useEntityLinks(router);
const rows = ref<Section[]>([]);
const { query: filterQ, filtered: filteredRows } = useTableQuickFilter(rows);
const loading = ref(false);
const checkedKeys = ref<DataTableRowKey[]>([]);
const bulkBusy = ref(false);

const { options: customerOptions, ensureLoaded: ensureCustomerOptsLoaded } = useCustomers();

const showEdit = ref(false);
const editing = ref<Section | null>(null);
const form = ref({
  name: "", description: "",
  parent_id: null as string | null,
  strict_mode: false, display_order: 0,
  customer_id: null as string | null,
});
const parentOptions = computed(() =>
  rows.value
    .filter((s) => !editing.value || s.id !== editing.value.id)
    .map((s) => ({ label: s.name, value: s.id })),
);

function openCreate() {
  editing.value = null;
  form.value = {
    name: "", description: "", parent_id: null,
    strict_mode: false, display_order: 0, customer_id: null,
  };
  void ensureCustomerOptsLoaded();
  showEdit.value = true;
}
function openEdit(r: Section) {
  editing.value = r;
  form.value = {
    name: r.name, description: r.description ?? "",
    parent_id: r.parent_id, strict_mode: r.strict_mode,
    display_order: r.display_order,
    customer_id: r.customer_id ?? null,
  };
  void ensureCustomerOptsLoaded();
  showEdit.value = true;
}
async function submit() {
  if (!form.value.name.trim()) { msg.error(t("common.name_required")); return; }
  try {
    if (editing.value) {
      const patch: SectionUpdate = {
        name: form.value.name.trim(),
        description: form.value.description.trim() || null,
        parent_id: form.value.parent_id ?? null,
        strict_mode: form.value.strict_mode,
        display_order: form.value.display_order ?? 0,
        customer_id: form.value.customer_id ?? null,
      };
      await updateSection(editing.value.id, patch);
    } else {
      await createSection({
        name: form.value.name.trim(),
        description: form.value.description.trim() || null,
        parent_id: form.value.parent_id ?? null,
        strict_mode: form.value.strict_mode,
        display_order: form.value.display_order ?? 0,
        customer_id: form.value.customer_id ?? null,
      });
    }
    showEdit.value = false;
    await refresh();
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("common.save_failed"));
  }
}
async function delSection(r: Section) {
  try { await deleteSection(r.id); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("common.delete_failed")); }
}

function go(id: string) {
  router.push({ name: "section-detail", params: { id } }).catch(() => {});
}

async function doBulkDelete() {
  if (!checkedKeys.value.length) return;
  bulkBusy.value = true;
  try {
    const res = await bulkDeleteSections(checkedKeys.value.map(String));
    if (res.failed) msg.warning(t("common.deleted_failed", { deleted: res.deleted, failed: res.failed }));
    else msg.success(t("common.deleted_n", { n: res.deleted }));
    checkedKeys.value = [];
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.network")); }
  finally { bulkBusy.value = false; }
}

const { visibleKeys, setVisible, reset } = useColumnPrefs(
  "sections",
  ["name", "description", "subnet_count", "customer_id", "actions"],
  ["name", "description", "subnet_count", "customer_id", "actions"],
);
const columnPickerItems = computed(() => [
  { key: "name", label: t("cols.name") },
  { key: "description", label: t("cols.description") },
  { key: "subnet_count", label: t("cols.subnet_count") },
  { key: "customer_id", label: t("cols.unit") },
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
const allColumns: DataTableColumns<Section> = [
  { type: "selection" },
  { title: () => t("sections.name"), key: "name", minWidth: 180, ellipsis: { tooltip: true },
    render: (r) => links.section(r.id, r.name),
    sorter: (a, b) => a.name.localeCompare(b.name) },
  { title: () => t("common.description"), key: "description", minWidth: 200, ellipsis: { tooltip: true },
    render: (r) => r.description ?? "—",
    sorter: (a, b) => (a.description ?? "").localeCompare(b.description ?? "") },
  {
    title: () => t("common.subnet_count"),
    key: "subnet_count", width: 110,
    render: (r) => h(NTag, { type: r.subnet_count > 0 ? "success" : "default", size: "small" }, () => String(r.subnet_count ?? 0)),
    sorter: (a, b) => (a.subnet_count ?? 0) - (b.subnet_count ?? 0),
  },
  {
    title: () => t("nav.customers"),
    key: "customer_id", width: 160, ellipsis: { tooltip: true },
    render: (r) => links.customer(r.customer_id, customerLabelFor(r.customer_id)),
    sorter: (a, b) => customerLabelFor(a.customer_id).localeCompare(customerLabelFor(b.customer_id)),
  },
  {
    title: () => t("common.actions"), key: "actions", className: "col-actions", width: 96,
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openEdit(r)),
      h(NPopconfirm, { onPositiveClick: () => delSection(r) }, {
        trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
];

const columns = computed<DataTableColumns<Section>>(() =>
  allColumns.filter((c: any) => c.type === "selection" || visibleKeys.value.includes(c.key)),
);

async function refresh() {
  loading.value = true;
  try {
    const res = await listSections(1, 50);
    rows.value = res.items;
  } catch {
    msg.error(t("errors.network"));
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  void refresh();
  void ensureCustomersLoaded();
});
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><SectionsIcon /></n-icon>
        <span>{{ t("sections.title") }}</span>
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
      <ExportButton :columns="columns" :rows="rows" filename="sections" :title="t('nav.sections')" />
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
      :pagination="pg"
      :bordered="false"
      :scroll-x="896"
      :row-key="(row: Section) => row.id"
      :checked-row-keys="checkedKeys"
      @update:checked-row-keys="(keys: DataTableRowKey[]) => checkedKeys = keys"
      :row-props="(row: Section) => ({
        style: 'cursor: pointer',
        onClick: (e: MouseEvent) => {
          const target = e.target as HTMLElement;
          if (target.closest('.n-checkbox') || target.closest('.n-button') || target.closest('a')) return;
          go(row.id);
        },
      })"
    >
      <template #empty>
        <n-space justify="center">{{ t("common.no_data") }}</n-space>
      </template>
    </n-data-table>

    <n-modal v-model:show="showEdit" preset="card" style="width: 540px">
      <template #header>
        <n-space align="center">
          <n-icon :size="20">
            <component :is="editing ? EditIcon : PlusIcon" />
          </n-icon>
          <span>{{ (editing ? t("common.edit") : t("common.create")) + " " + t("sections.title") }}</span>
        </n-space>
      </template>
      <n-form label-placement="left" label-width="120">
        <n-form-item :label="t('common.name')" required>
          <n-input v-model:value="form.name" />
        </n-form-item>
        <n-form-item :label="t('common.description')">
          <n-input v-model:value="form.description" type="textarea" :rows="2" />
        </n-form-item>
        <n-form-item :label="t('sections.parent')">
          <n-select v-model:value="form.parent_id" :options="parentOptions"
                    :placeholder="t('sections.no_parent')" clearable filterable />
        </n-form-item>
        <n-form-item :label="t('cols.unit')">
          <n-select v-model:value="form.customer_id" :options="customerOptions"
                    :placeholder="t('common.not_specified')" clearable filterable />
        </n-form-item>
        <n-form-item :label="t('sections.order')">
          <n-input-number v-model:value="form.display_order" :min="0" :max="10000" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="showEdit = false">
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
