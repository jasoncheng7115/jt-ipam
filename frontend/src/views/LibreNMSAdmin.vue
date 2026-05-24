<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NButton, NTag, NIcon,
  NModal, NForm, NFormItem, NInput, NInputNumber, NSwitch, NPopconfirm,
  useMessage, type DataTableColumns,
} from "naive-ui";
import {
  listLibreNMS, createLibreNMS, deleteLibreNMS, testLibreNMS, syncLibreNMS,
  type LibreNMSInstance,
} from "@/api/integrations";
import {
  LibreNMSIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SyncIcon, TestIcon, SaveIcon, CancelIcon,
} from "@/icons";

const { t } = useI18n();
const msg = useMessage();
const rows = ref<LibreNMSInstance[]>([]);
const loading = ref(false);
const show = ref(false);
const form = ref({
  name: "", api_url: "", api_token: "",
  enabled: true,
  sync_devices: true, sync_arp: true, sync_fdb: true,
  use_for_status: true, auto_add_devices: false,
  sync_interval_seconds: 300,
});

async function refresh() {
  loading.value = true;
  try { rows.value = (await listLibreNMS()).items; }
  catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}
function openCreate() {
  form.value = {
    name: "", api_url: "", api_token: "",
    enabled: true,
    sync_devices: true, sync_arp: true, sync_fdb: true,
    use_for_status: true, auto_add_devices: false,
    sync_interval_seconds: 300,
  };
  show.value = true;
}
async function submit() {
  if (!form.value.name.trim()) { msg.error(t("librenms_admin.error_name_required")); return; }
  if (form.value.api_token.length < 8) { msg.error(t("librenms_admin.error_token_too_short")); return; }
  try {
    await createLibreNMS(form.value);
    show.value = false;
    msg.success(t("common.ok"));
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function test(id: string) {
  try { await testLibreNMS(id); msg.success(t("librenms_admin.test_ok")); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function sync(id: string) {
  try {
    const res = await syncLibreNMS(id);
    msg.success(t("librenms_admin.sync_summary", { summary: JSON.stringify(res).slice(0, 80) }));
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function del(id: string) {
  try { await deleteLibreNMS(id); msg.success(t("common.ok")); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

const cols = computed<DataTableColumns<LibreNMSInstance>>(() => [
  { title: t("common.name"), key: "name" },
  { title: "API URL", key: "api_url" },
  {
    title: t("common.status"), key: "enabled",
    render: (r) => h(NTag, { type: r.enabled ? "success" : "default", size: "small" },
      () => r.enabled ? t("common.enabled") : t("common.disabled")),
  },
  { title: "interval", key: "sync_interval_seconds", render: (r) => `${r.sync_interval_seconds}s` },
  {
    title: "last sync", key: "last_sync_at",
    render: (r) => r.last_sync_at ? new Date(r.last_sync_at).toLocaleString() : "—",
  },
  { title: "last error", key: "last_error", render: (r) => r.last_error ?? "—" },
  {
    title: t("common.actions"), key: "actions", width: 260,
    render: (r) => h(NSpace, { size: "small" }, () => [
      h(NButton, { size: "small", onClick: () => test(r.id) },
        { default: () => t("common.test"), icon: () => h(NIcon, null, () => h(TestIcon)) }),
      h(NButton, { size: "small", type: "primary", onClick: () => sync(r.id) },
        { default: () => t("common.sync"), icon: () => h(NIcon, null, () => h(SyncIcon)) }),
      h(NPopconfirm, { onPositiveClick: () => del(r.id) }, {
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
        <n-icon :size="22"><LibreNMSIcon /></n-icon>
        <span>{{ t("librenms_admin.title") }}</span>
      </n-space>
    </template>

    <n-space style="margin-bottom: 12px">
      <n-button @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
      <n-button type="primary" @click="openCreate">
        <template #icon><n-icon><PlusIcon /></n-icon></template>
        {{ t("librenms_admin.create") }}
      </n-button>
    </n-space>

    <n-data-table :columns="cols" :data="rows" :loading="loading" :bordered="false">
      <template #empty>
        <n-space justify="center">{{ t("common.no_data") }}</n-space>
      </template>
    </n-data-table>

    <n-modal v-model:show="show" preset="card" style="width: 540px">
      <template #header>
        <n-space align="center">
          <n-icon :size="20"><PlusIcon /></n-icon>
          <span>{{ t("librenms_admin.create") }}</span>
        </n-space>
      </template>
      <n-form>
        <n-form-item :label="t('common.name')">
          <n-input v-model:value="form.name" placeholder="librenms-main" />
        </n-form-item>
        <n-form-item label="API URL">
          <n-input v-model:value="form.api_url"
                   placeholder="https://librenms.example.com（不含結尾 /）" />
        </n-form-item>
        <n-form-item :label="t('librenms_admin.api_token')">
          <n-input v-model:value="form.api_token" type="password" show-password-on="click"
                   :placeholder="t('librenms_admin.api_token_placeholder')" />
        </n-form-item>
        <n-space>
          <n-form-item :label="t('librenms_admin.sync_devices')">
            <n-switch v-model:value="form.sync_devices" />
          </n-form-item>
          <n-form-item :label="t('librenms_admin.sync_arp')">
            <n-switch v-model:value="form.sync_arp" />
          </n-form-item>
          <n-form-item :label="t('librenms_admin.sync_fdb')">
            <n-switch v-model:value="form.sync_fdb" />
          </n-form-item>
          <n-form-item :label="t('librenms_admin.use_for_status')">
            <n-switch v-model:value="form.use_for_status" />
          </n-form-item>
          <n-form-item :label="t('librenms_admin.auto_add_devices')">
            <n-switch v-model:value="form.auto_add_devices" />
          </n-form-item>
        </n-space>
        <n-form-item :label="t('librenms_admin.sync_interval')">
          <n-input-number v-model:value="form.sync_interval_seconds" :min="60" :max="86400" />
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
