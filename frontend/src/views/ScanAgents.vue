<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NIcon, NButton, NModal, NForm, NFormItem,
  NInput, NSwitch, NPopconfirm, NTag,
  useMessage, type DataTableColumns,
} from "naive-ui";
import {
  ScanAgentsIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SaveIcon, CancelIcon,
} from "@/icons";
import {
  listScanAgents, createScanAgent, updateScanAgent, deleteScanAgent,
  type ScanAgent,
} from "@/api/phase3";

const { t } = useI18n();
const msg = useMessage();
const rows = ref<ScanAgent[]>([]);
const loading = ref(false);
const show = ref(false);
const editing = ref<ScanAgent | null>(null);
const form = ref({
  name: "", agent_url: "https://", api_token: "",
  description: "", enabled: true,
});

async function refresh() {
  loading.value = true;
  try { rows.value = (await listScanAgents()).items; }
  catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}
function openCreate() {
  editing.value = null;
  form.value = { name: "", agent_url: "https://", api_token: "", description: "", enabled: true };
  show.value = true;
}
function openEdit(r: ScanAgent) {
  editing.value = r;
  form.value = {
    name: r.name, agent_url: r.agent_url, api_token: "",
    description: r.description ?? "", enabled: r.enabled,
  };
  show.value = true;
}
async function submit() {
  try {
    if (editing.value) {
      const payload: any = {
        description: form.value.description || undefined,
        agent_url: form.value.agent_url,
        enabled: form.value.enabled,
      };
      if (form.value.api_token) payload.api_token = form.value.api_token;
      await updateScanAgent(editing.value.id, payload);
    } else {
      await createScanAgent({
        name: form.value.name,
        agent_url: form.value.agent_url,
        api_token: form.value.api_token,
        description: form.value.description || undefined,
        enabled: form.value.enabled,
      });
    }
    show.value = false;
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function del(r: ScanAgent) {
  try { await deleteScanAgent(r.id); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

const cols = computed<DataTableColumns<ScanAgent>>(() => [
  { title: t("common.name"), key: "name" },
  { title: "URL", key: "agent_url" },
  {
    title: t("common.status"), key: "enabled",
    render: (r) => h(NTag, { size: "small", type: r.enabled ? "success" : "default" },
      () => r.enabled ? t("common.enabled") : t("common.disabled")),
  },
  {
    title: "last seen", key: "last_seen_at",
    render: (r) => r.last_seen_at ? new Date(r.last_seen_at).toLocaleString() : "—",
  },
  { title: "last error", key: "last_error", render: (r) => r.last_error ?? "—" },
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
        <n-icon :size="22"><ScanAgentsIcon /></n-icon>
        <span>{{ t("nav.scan_agents") }}</span>
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

    <n-modal v-model:show="show" preset="card" style="width: 480px">
      <template #header>
        <n-space align="center">
          <n-icon :size="20"><component :is="editing ? EditIcon : PlusIcon" /></n-icon>
          <span>{{ editing ? t("common.edit") : t("common.create") }}</span>
        </n-space>
      </template>
      <n-form>
        <n-form-item :label="t('common.name')">
          <n-input v-model:value="form.name" :disabled="!!editing" />
        </n-form-item>
        <n-form-item label="agent URL"><n-input v-model:value="form.agent_url" /></n-form-item>
        <n-form-item :label="`API token${editing ? ' (' + t('users.password_blank_unchanged') + ')' : ''}`">
          <n-input v-model:value="form.api_token" type="password" show-password-on="click" />
        </n-form-item>
        <n-form-item :label="t('sections.description')">
          <n-input v-model:value="form.description" type="textarea" :rows="2" />
        </n-form-item>
        <n-form-item :label="t('common.enabled')">
          <n-switch v-model:value="form.enabled" />
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
