<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NButton, NModal, NForm, NFormItem,
  NInput, NSelect, NInputNumber, NPopconfirm, NTag,
  useMessage, type DataTableColumns,
} from "naive-ui";
import {
  listNATs, createNAT, updateNAT, deleteNAT, type NAT,
} from "@/api/phase3";

const { t } = useI18n();
const msg = useMessage();
const rows = ref<NAT[]>([]);
const loading = ref(false);
const show = ref(false);
const editing = ref<NAT | null>(null);
const form = ref({
  name: "", type: "many_to_one", protocol: "any",
  src_port: null as number | null, dst_port: null as number | null,
  description: "",
});

const typeOpts = [
  { label: "1:1 NAT", value: "one_to_one" },
  { label: "many:1 NAT (PAT)", value: "many_to_one" },
  { label: "Port forward", value: "port_forward" },
];
const protoOpts = ["tcp", "udp", "any"].map((v) => ({ label: v, value: v }));

async function refresh() {
  loading.value = true;
  try { rows.value = (await listNATs()).items; }
  catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}
function openCreate() {
  editing.value = null;
  form.value = { name: "", type: "many_to_one", protocol: "any",
    src_port: null, dst_port: null, description: "" };
  show.value = true;
}
function openEdit(r: NAT) {
  editing.value = r;
  form.value = {
    name: r.name, type: r.type, protocol: r.protocol,
    src_port: r.src_port, dst_port: r.dst_port,
    description: r.description ?? "",
  };
  show.value = true;
}
async function submit() {
  try {
    const payload = {
      name: form.value.name,
      type: form.value.type,
      protocol: form.value.protocol,
      src_port: form.value.src_port ?? null,
      dst_port: form.value.dst_port ?? null,
      description: form.value.description || null,
    } as any;
    if (editing.value) await updateNAT(editing.value.id, payload);
    else await createNAT(payload);
    show.value = false;
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function del(r: NAT) {
  try { await deleteNAT(r.id); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

const cols = computed<DataTableColumns<NAT>>(() => [
  { title: t("common.name"), key: "name" },
  {
    title: "type", key: "type",
    render: (r) => h(NTag, { size: "small", type: "info" }, () => r.type),
  },
  { title: "proto", key: "protocol" },
  { title: "src_port", key: "src_port", render: (r) => r.src_port ?? "—" },
  { title: "dst_port", key: "dst_port", render: (r) => r.dst_port ?? "—" },
  { title: t("sections.description"), key: "description", render: (r) => r.description ?? "" },
  {
    title: t("common.actions"), key: "actions", width: 160,
    render: (r) => h(NSpace, { size: "small" }, () => [
      h(NButton, { size: "small", onClick: () => openEdit(r) }, () => t("common.edit")),
      h(NPopconfirm, { onPositiveClick: () => del(r) }, {
        trigger: () => h(NButton, { size: "small", type: "error" }, () => t("common.delete")),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]);
onMounted(() => { void refresh(); });
</script>

<template>
  <n-card :title="t('nav.nat')">
    <n-space style="margin-bottom: 12px">
      <n-button @click="refresh" :loading="loading">{{ t("common.refresh") }}</n-button>
      <n-button type="primary" @click="openCreate">{{ t("common.create") }}</n-button>
    </n-space>
    <n-data-table :columns="cols" :data="rows" :loading="loading" :bordered="false" />

    <n-modal v-model:show="show" preset="card"
             :title="editing ? t('common.edit') : t('common.create')" style="width: 480px">
      <n-form>
        <n-form-item :label="t('common.name')"><n-input v-model:value="form.name" /></n-form-item>
        <n-form-item label="type">
          <n-select v-model:value="form.type" :options="typeOpts" />
        </n-form-item>
        <n-form-item label="protocol">
          <n-select v-model:value="form.protocol" :options="protoOpts" />
        </n-form-item>
        <n-form-item label="src_port">
          <n-input-number v-model:value="form.src_port" :min="1" :max="65535" clearable />
        </n-form-item>
        <n-form-item label="dst_port">
          <n-input-number v-model:value="form.dst_port" :min="1" :max="65535" clearable />
        </n-form-item>
        <n-form-item :label="t('sections.description')">
          <n-input v-model:value="form.description" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="show = false">{{ t("common.cancel") }}</n-button>
        <n-button type="primary" @click="submit">{{ t("common.save") }}</n-button>
      </n-space>
    </n-modal>
  </n-card>
</template>
