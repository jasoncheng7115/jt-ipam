<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NIcon, NButton, NModal, NForm, NFormItem,
  NInput, NPopconfirm,
  useMessage, type DataTableColumns,
} from "naive-ui";
import {
  listLocations, createLocation, updateLocation, deleteLocation, type Location,
} from "@/api/basic";
import {
  LocationsIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SaveIcon, CancelIcon,
} from "@/icons";

const { t } = useI18n();
const msg = useMessage();
const rows = ref<Location[]>([]);
const loading = ref(false);
const show = ref(false);
const editing = ref<Location | null>(null);
const form = ref({ name: "", site: "", address: "", description: "" });

async function refresh() {
  loading.value = true;
  try { rows.value = (await listLocations()).items; }
  catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}
function openCreate() {
  editing.value = null;
  form.value = { name: "", site: "", address: "", description: "" };
  show.value = true;
}
function openEdit(r: Location) {
  editing.value = r;
  form.value = {
    name: r.name, site: r.site ?? "", address: r.address ?? "",
    description: r.description ?? "",
  };
  show.value = true;
}
async function submit() {
  try {
    const payload = {
      name: form.value.name,
      site: form.value.site || undefined,
      address: form.value.address || undefined,
      description: form.value.description || undefined,
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

const cols = computed<DataTableColumns<Location>>(() => [
  { title: t("common.name"), key: "name" },
  { title: t("locations.site"), key: "site", render: (r) => r.site ?? "—" },
  { title: t("locations.address"), key: "address", render: (r) => r.address ?? "—" },
  { title: t("sections.description"), key: "description", render: (r) => r.description ?? "" },
  {
    title: t("common.actions"), key: "actions", width: 160,
    render: (r) => h(NSpace, { size: "small" }, () => [
      h(NButton, { size: "small", onClick: () => openEdit(r) },
        { default: () => t("common.edit"), icon: () => h(NIcon, null, () => h(EditIcon)) }),
      h(NPopconfirm, { onPositiveClick: () => del(r) }, {
        trigger: () => h(NButton, { size: "small", type: "error" },
          { default: () => t("common.delete"), icon: () => h(NIcon, null, () => h(DeleteIcon)) }),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]);
onMounted(() => { void refresh(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><LocationsIcon /></n-icon>
        <span>{{ t("nav.locations") }}</span>
      </n-space>
    </template>
    <n-space style="margin-bottom: 12px">
      <n-button @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
      <n-button type="primary" @click="openCreate">
        <template #icon><n-icon><PlusIcon /></n-icon></template>
        {{ t("common.create") }}
      </n-button>
    </n-space>
    <n-data-table :columns="cols" :data="rows" :loading="loading" :bordered="false" />
    <n-modal v-model:show="show" preset="card" style="width: 460px">
      <template #header>
        <n-space align="center">
          <n-icon :size="20"><component :is="editing ? EditIcon : PlusIcon" /></n-icon>
          <span>{{ editing ? t("common.edit") : t("common.create") }}</span>
        </n-space>
      </template>
      <n-form>
        <n-form-item :label="t('common.name')"><n-input v-model:value="form.name" /></n-form-item>
        <n-form-item :label="t('locations.site')"><n-input v-model:value="form.site" /></n-form-item>
        <n-form-item :label="t('locations.address')"><n-input v-model:value="form.address" /></n-form-item>
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
