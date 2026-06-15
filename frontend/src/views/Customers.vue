<script setup lang="ts">
/**
 * Customers / 管理單位 — CRUD。
 *
 * phpIPAM 對齊：每個 section / subnet / IP / device 都可掛一個 customer，
 * 用於 MSP 多客戶情境。
 */
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import {
  NCard, NDataTable, NSpace, NIcon, NInput, NButton, NPopconfirm, NTooltip,
  NModal, NForm, NFormItem,
  useMessage, type DataTableColumns,
} from "naive-ui";
import {
  listCustomers, createCustomer, updateCustomer, deleteCustomer,
  type Customer,
} from "@/api/customers";
import {
  UsersIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SaveIcon, CancelIcon,
} from "@/icons";
import { autoSort } from "@/composables/useTableSort";
import { fmtDateTime } from "@/utils/datetime";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { useTablePagination } from "@/composables/useTablePagination";
const pg = useTablePagination();
const { t } = useI18n();
const router = useRouter();

const { visibleKeys, setVisible, reset } = useColumnPrefs(
  "customers",
  ["name", "subnet_count", "contact", "email", "phone", "description", "created_at", "actions"],
  ["name", "subnet_count", "contact", "email", "phone", "description", "created_at", "actions"],
);
const columnPickerItems = computed(() => [
  { key: "name", label: t("cols.name") },
  { key: "subnet_count", label: t("cols.subnet") },
  { key: "contact", label: t("cols.contact") },
  { key: "email", label: "Email" },
  { key: "phone", label: t("cols.phone") },
  { key: "description", label: t("cols.description") },
  { key: "created_at", label: t("cols.created_at") },
  { key: "actions", label: t("cols.actions") },
]);

const msg = useMessage();

const rows = ref<Customer[]>([]);
const total = ref(0);
const loading = ref(false);
const q = ref("");

const showEdit = ref(false);
const editing = ref<Customer | null>(null);
const form = ref({
  name: "", title: "", description: "",
  contact: "", email: "", phone: "", address: "",
});

async function refresh() {
  loading.value = true;
  try {
    const res = await listCustomers({ q: q.value || undefined, pageSize: 500 });
    rows.value = res.items;
    total.value = res.total;
  } catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}

function openCreate() {
  editing.value = null;
  form.value = {
    name: "", title: "", description: "",
    contact: "", email: "", phone: "", address: "",
  };
  showEdit.value = true;
}

function openEdit(r: Customer) {
  editing.value = r;
  form.value = {
    name: r.name ?? "",
    title: r.title ?? "",
    description: r.description ?? "",
    contact: r.contact ?? "",
    email: r.email ?? "",
    phone: r.phone ?? "",
    address: r.address ?? "",
  };
  showEdit.value = true;
}

async function submit() {
  try {
    const payload = {
      name: form.value.name.trim(),
      title: form.value.title.trim() || null,
      description: form.value.description.trim() || null,
      contact: form.value.contact.trim() || null,
      email: form.value.email.trim() || null,
      phone: form.value.phone.trim() || null,
      address: form.value.address.trim() || null,
    };
    if (!payload.name) {
      msg.error(t("common.name_required"));
      return;
    }
    if (editing.value) {
      await updateCustomer(editing.value.id, payload);
    } else {
      await createCustomer(payload);
    }
    showEdit.value = false;
    await refresh();
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.server"));
  }
}

async function remove(r: Customer) {
  try {
    await deleteCustomer(r.id);
    await refresh();
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.server"));
  }
}

function iconAction(icon: any, label: string, onClick: () => void, type?: any) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}

const allCols = computed<DataTableColumns<Customer>>(() => autoSort([
  { title: t("cols.name"), key: "name", minWidth: 160, ellipsis: { tooltip: true } },
  { title: t("cols.subnet"), key: "subnet_count", width: 104,
    sorter: (a: any, b: any) => (a.subnet_count ?? 0) - (b.subnet_count ?? 0),
    render: (r: any) => {
      const n = r.subnet_count ?? 0;
      if (!n) return 0;
      return h("a", {
        href: "#", class: "entity-link",
        onClick: (e: MouseEvent) => { e.preventDefault(); router.push({ name: "subnets", query: { customer: r.id } }); },
      }, n);
    } },
  { title: t("cols.contact"), key: "contact", width: 120,
    ellipsis: { tooltip: true }, render: (r) => r.contact ?? "—" },
  { title: t("cols.email"), key: "email", minWidth: 150,
    ellipsis: { tooltip: true }, render: (r) => r.email ?? "—" },
  { title: t("cols.phone"), key: "phone", width: 110, render: (r) => r.phone ?? "—" },
  { title: t("cols.description"), key: "description", minWidth: 150,
    ellipsis: { tooltip: true }, render: (r) => r.description ?? "—" },
  { title: t("cols.created_at"), key: "created_at", width: 160, render: (r) => fmtDateTime(r.created_at) },
  {
    title: t("cols.actions"), key: "actions", className: "col-actions", width: 96,
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openEdit(r)),
      h(NPopconfirm, { onPositiveClick: () => remove(r) }, {
        trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
        default: () => t("common.confirm_delete_short"),
      }),
    ]),
  },
]));

const cols = computed<DataTableColumns<Customer>>(() =>
  allCols.value.filter((c: any) => visibleKeys.value.includes(c.key)),
);

onMounted(() => { void refresh(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><UsersIcon /></n-icon>
        <span>{{ t("customers.title") }}</span>
      </n-space>
    </template>
    <template #header-extra>
      <n-space>
        <n-input v-model:value="q" :placeholder="t('common.search_name')" clearable
                 style="width: 240px" @keyup.enter="refresh" />
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
        <ExportButton :columns="cols" :rows="rows" filename="customers" :title="t('nav.customers')" />
      </n-space>
    </template>
    <n-data-table
      :columns="cols" :data="rows" :loading="loading"
      :bordered="false" :scroll-x="1054"
      :pagination="pg"
    >
      <template #empty>
        <n-space justify="center">{{ t("common.no_data") }}</n-space>
      </template>
    </n-data-table>

    <n-modal v-model:show="showEdit" preset="card" style="width: 600px">
      <template #header>
        <n-space align="center">
          <n-icon :size="20">
            <component :is="editing ? EditIcon : PlusIcon" />
          </n-icon>
          <span>{{ editing ? t("customers.edit") : t("customers.create") }}</span>
        </n-space>
      </template>
      <n-form label-placement="left" label-width="100">
        <n-form-item :label="t('common.name')" required>
          <n-input v-model:value="form.name" :placeholder="t('customers.name_ph')" />
        </n-form-item>
        <n-form-item :label="t('common.description')">
          <n-input v-model:value="form.description" type="textarea" :rows="2" />
        </n-form-item>
        <n-form-item :label="t('cols.contact')">
          <n-input v-model:value="form.contact" />
        </n-form-item>
        <n-form-item label="Email">
          <n-input v-model:value="form.email" />
        </n-form-item>
        <n-form-item :label="t('cols.phone')">
          <n-input v-model:value="form.phone" />
        </n-form-item>
        <n-form-item :label="t('locations.address')">
          <n-input v-model:value="form.address" type="textarea" :rows="2" />
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
