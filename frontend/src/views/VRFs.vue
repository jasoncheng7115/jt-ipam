<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NIcon, NButton, NModal, NForm, NFormItem,
  NInput, NSwitch, NPopconfirm,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { listVRFs, createVRF, updateVRF, deleteVRF, type VRF } from "@/api/basic";
import {
  VrfsIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SaveIcon, CancelIcon,
} from "@/icons";

const { t } = useI18n();
const msg = useMessage();
const rows = ref<VRF[]>([]);
const loading = ref(false);
const show = ref(false);
const editing = ref<VRF | null>(null);
const form = ref({ name: "", rd: "", description: "", allow_overlap: true });

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

const cols = computed<DataTableColumns<VRF>>(() => [
  { title: t("common.name"), key: "name" },
  { title: "RD", key: "rd", render: (r) => r.rd ?? "—" },
  { title: t("sections.description"), key: "description", render: (r) => r.description ?? "" },
  { title: "allow overlap", key: "allow_overlap",
    render: (r) => r.allow_overlap ? "✓" : "—" },
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
        <n-icon :size="22"><VrfsIcon /></n-icon>
        <span>{{ t("nav.vrfs") }}</span>
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
