<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NButton, NModal, NForm, NFormItem,
  NInput, NSelect, NInputNumber, NPopconfirm, NTag, NIcon,
  useMessage, type DataTableColumns,
} from "naive-ui";
import {
  listNATs, createNAT, updateNAT, deleteNAT, type NAT,
} from "@/api/phase3";
import { listAddresses } from "@/api/addresses";
import { listDevices } from "@/api/basic";
import {
  NatIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SaveIcon, CancelIcon,
} from "@/icons";

const { t } = useI18n();
const msg = useMessage();
const rows = ref<NAT[]>([]);
const loading = ref(false);
const show = ref(false);
const editing = ref<NAT | null>(null);
const form = ref({
  name: "", type: "many_to_one", protocol: "any",
  src_ip_id: null as string | null,
  dst_ip_id: null as string | null,
  device_id: null as string | null,
  src_port: null as number | null,
  dst_port: null as number | null,
  description: "",
});

const addrOpts = ref<{ label: string; value: string }[]>([]);
const deviceOpts = ref<{ label: string; value: string }[]>([]);

const typeOpts = [
  { label: t("nat.type_one_to_one"),  value: "one_to_one" },
  { label: t("nat.type_many_to_one"), value: "many_to_one" },
  { label: t("nat.type_port_forward"), value: "port_forward" },
];
const protoOpts = ["tcp", "udp", "any"].map((v) => ({ label: v, value: v }));

async function loadOpts() {
  try {
    const [addr, dev] = await Promise.all([
      listAddresses({ pageSize: 500 }),
      listDevices(),
    ]);
    addrOpts.value = addr.items.map((a: any) => ({
      label: `${a.ip}${a.hostname ? " — " + a.hostname : ""}`,
      value: a.id,
    }));
    deviceOpts.value = dev.items.map((d: any) => ({
      label: `${d.name}${d.type ? " (" + d.type + ")" : ""}`,
      value: d.id,
    }));
  } catch { /* 先靜默；refresh 會報網路問題 */ }
}

async function refresh() {
  loading.value = true;
  try { rows.value = (await listNATs()).items; }
  catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}
function openCreate() {
  editing.value = null;
  form.value = {
    name: "", type: "many_to_one", protocol: "any",
    src_ip_id: null, dst_ip_id: null, device_id: null,
    src_port: null, dst_port: null, description: "",
  };
  show.value = true;
}
function openEdit(r: NAT) {
  editing.value = r;
  form.value = {
    name: r.name, type: r.type, protocol: r.protocol,
    src_ip_id: r.src_ip_id, dst_ip_id: r.dst_ip_id, device_id: r.device_id,
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
      src_ip_id: form.value.src_ip_id ?? null,
      dst_ip_id: form.value.dst_ip_id ?? null,
      device_id: form.value.device_id ?? null,
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
    title: t("nat.type"), key: "type",
    render: (r) => h(NTag, { size: "small", type: "info" }, () => r.type),
  },
  { title: t("nat.protocol"), key: "protocol" },
  {
    title: t("nat.src_ip"), key: "src_ip_id",
    render: (r) => addrOpts.value.find((o) => o.value === r.src_ip_id)?.label
      ?? (r.src_ip_id ? r.src_ip_id.slice(0, 8) + "…" : "—"),
  },
  {
    title: t("nat.dst_ip"), key: "dst_ip_id",
    render: (r) => addrOpts.value.find((o) => o.value === r.dst_ip_id)?.label
      ?? (r.dst_ip_id ? r.dst_ip_id.slice(0, 8) + "…" : "—"),
  },
  { title: t("nat.src_port"), key: "src_port", render: (r) => r.src_port ?? "—" },
  { title: t("nat.dst_port"), key: "dst_port", render: (r) => r.dst_port ?? "—" },
  { title: t("sections.description"), key: "description", render: (r) => r.description ?? "" },
  {
    title: t("common.actions"), key: "actions", width: 200,
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
onMounted(() => { void refresh(); void loadOpts(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><NatIcon /></n-icon>
        <span>{{ t("nav.nat") }}</span>
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

    <n-modal v-model:show="show" preset="card" style="width: 540px">
      <template #header>
        <n-space align="center">
          <n-icon :size="20">
            <component :is="editing ? EditIcon : PlusIcon" />
          </n-icon>
          <span>{{ editing ? t("common.edit") : t("common.create") }}</span>
        </n-space>
      </template>
      <n-form>
        <n-form-item :label="t('common.name')"><n-input v-model:value="form.name" /></n-form-item>
        <n-form-item :label="t('nat.type')">
          <n-select v-model:value="form.type" :options="typeOpts" />
        </n-form-item>
        <n-form-item :label="t('nat.src_ip')">
          <n-select v-model:value="form.src_ip_id" :options="addrOpts" filterable clearable
                    :placeholder="t('nat.src_ip_placeholder')" />
        </n-form-item>
        <n-form-item :label="t('nat.dst_ip')">
          <n-select v-model:value="form.dst_ip_id" :options="addrOpts" filterable clearable
                    :placeholder="t('nat.dst_ip_placeholder')" />
        </n-form-item>
        <n-form-item :label="t('nat.device')">
          <n-select v-model:value="form.device_id" :options="deviceOpts" filterable clearable
                    :placeholder="t('nat.device_placeholder')" />
        </n-form-item>
        <n-form-item :label="t('nat.protocol')">
          <n-select v-model:value="form.protocol" :options="protoOpts" />
        </n-form-item>
        <n-form-item :label="t('nat.src_port')">
          <n-input-number v-model:value="form.src_port" :min="1" :max="65535" clearable />
        </n-form-item>
        <n-form-item :label="t('nat.dst_port')">
          <n-input-number v-model:value="form.dst_port" :min="1" :max="65535" clearable />
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
