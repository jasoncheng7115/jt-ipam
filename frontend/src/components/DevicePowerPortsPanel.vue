<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NSpace, NButton, NIcon, NDataTable, NModal, NForm, NFormItem, NInput,
  NInputNumber, NSelect, NPopconfirm, NTag, useMessage, type DataTableColumns,
} from "naive-ui";
import { Physical, type PowerOutlet } from "@/api/phase3";
import {
  listDevicePowerPorts, createDevicePowerPort, updateDevicePowerPort,
  deleteDevicePowerPort, type DevicePowerPort,
} from "@/api/phase3";
import { PlusIcon, EditIcon, DeleteIcon, SaveIcon, CancelIcon } from "@/icons";

const props = defineProps<{ deviceId: string; deviceName: string; admin: boolean }>();
const { t } = useI18n();
const msg = useMessage();

const ports = ref<DevicePowerPort[]>([]);
const outlets = ref<PowerOutlet[]>([]);
const loading = ref(false);
const outletOpts = computed(() =>
  outlets.value.map((o: any) => ({ label: o.label ?? o.name ?? o.id, value: o.id })));

async function refresh() {
  loading.value = true;
  try {
    ports.value = await listDevicePowerPorts(props.deviceId);
  } catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}
async function loadOutlets() {
  try { outlets.value = await Physical.outlets(); } catch { /* ignore */ }
}

// ── add / edit ──
const showEdit = ref(false);
const editId = ref<string | null>(null);
const form = ref<{ name: string; outlet_id: string | null; max_watts: number | null; description: string }>(
  { name: "", outlet_id: null, max_watts: null, description: "" });

function openCreate() {
  editId.value = null;
  form.value = { name: "", outlet_id: null, max_watts: null, description: "" };
  showEdit.value = true;
}
function openEdit(r: DevicePowerPort) {
  editId.value = r.id;
  form.value = { name: r.name, outlet_id: r.outlet_id, max_watts: r.max_watts, description: r.description ?? "" };
  showEdit.value = true;
}
async function save() {
  if (!form.value.name.trim()) { msg.error(t("ports.name_required")); return; }
  try {
    if (editId.value) {
      await updateDevicePowerPort(editId.value, {
        name: form.value.name.trim(), outlet_id: form.value.outlet_id,
        max_watts: form.value.max_watts, description: form.value.description.trim() || null,
      });
    } else {
      await createDevicePowerPort({
        device_id: props.deviceId, name: form.value.name.trim(),
        outlet_id: form.value.outlet_id, max_watts: form.value.max_watts,
        description: form.value.description.trim() || null,
      });
    }
    showEdit.value = false;
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function del(id: string) {
  try { await deleteDevicePowerPort(id); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

const cols = computed<DataTableColumns<DevicePowerPort>>(() => [
  { title: t("power_ports.name"), key: "name", minWidth: 100 },
  {
    title: t("power_ports.outlet"), key: "outlet_id", minWidth: 160,
    render: (r) => r.outlet_label
      ? h(NTag, { size: "small", type: "success", bordered: false }, { default: () => r.outlet_label })
      : h("span", { style: "opacity:.5" }, t("power_ports.unconnected")),
  },
  { title: t("power_ports.watts"), key: "max_watts", width: 90, render: (r) => r.max_watts ?? "—" },
  { title: t("common.description"), key: "description", minWidth: 140, ellipsis: { tooltip: true }, render: (r) => r.description ?? "—" },
  ...(props.admin ? [{
    title: t("common.actions"), key: "_", width: 96,
    render: (r: DevicePowerPort) => h(NSpace, { size: 2, wrapItem: false }, () => [
      h(NButton, { size: "small", quaternary: true, type: "primary", onClick: () => openEdit(r) },
        { icon: () => h(NIcon, null, () => h(EditIcon)) }),
      h(NPopconfirm, { onPositiveClick: () => del(r.id) }, {
        trigger: () => h(NButton, { size: "small", quaternary: true, type: "error" },
          { icon: () => h(NIcon, null, () => h(DeleteIcon)) }),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  }] : []),
]);

watch(() => props.deviceId, () => { void refresh(); });
onMounted(() => { void refresh(); void loadOutlets(); });
</script>

<template>
  <n-card size="small">
    <template #header>
      <span style="display:inline-flex;align-items:center;gap:8px">
        <n-icon :size="18"><PlusIcon v-if="false" /></n-icon>
        {{ t("power_ports.title") }} ({{ ports.length }})
      </span>
    </template>
    <template #header-extra>
      <n-button v-if="admin" size="small" type="primary" @click="openCreate">
        <template #icon><n-icon><PlusIcon /></n-icon></template>{{ t("power_ports.add") }}
      </n-button>
    </template>
    <n-data-table :columns="cols" :data="ports" :loading="loading" :bordered="false" size="small">
      <template #empty><n-space justify="center" style="opacity:.6">{{ t("power_ports.empty") }}</n-space></template>
    </n-data-table>

    <n-modal v-model:show="showEdit" preset="card" style="width: 460px"
             :title="editId ? t('common.edit') : t('power_ports.add')">
      <n-form label-placement="top">
        <n-form-item :label="t('power_ports.name')">
          <n-input v-model:value="form.name" placeholder="PSU1" />
        </n-form-item>
        <n-form-item :label="t('power_ports.outlet')">
          <n-select v-model:value="form.outlet_id" :options="outletOpts" clearable filterable
                    :placeholder="t('power_ports.unconnected')" />
        </n-form-item>
        <n-form-item :label="t('power_ports.watts')">
          <n-input-number v-model:value="form.max_watts" :min="0" style="width: 160px" />
        </n-form-item>
        <n-form-item :label="t('common.description')">
          <n-input v-model:value="form.description" type="textarea" :autosize="{ minRows: 2 }" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button size="small" @click="showEdit = false">
            <template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}
          </n-button>
          <n-button size="small" type="primary" @click="save">
            <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </n-card>
</template>
