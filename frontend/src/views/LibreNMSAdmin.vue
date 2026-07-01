<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { fmtDateTime } from "@/utils/datetime";
import { useI18n } from "vue-i18n";
import ScopeOverlapWarning from "@/components/ScopeOverlapWarning.vue";
import {
  NCard, NDataTable, NSpace, NButton, NTag, NIcon, NTooltip,
  NModal, NForm, NFormItem, NInput, NInputNumber, NSwitch, NPopconfirm, NSelect,
  useMessage, type DataTableColumns,
} from "naive-ui";
import {
  listLibreNMS, createLibreNMS, updateLibreNMS, deleteLibreNMS, testLibreNMS, syncLibreNMS,
  linkLibreNMSDevices,
  type LibreNMSInstance,
} from "@/api/integrations";
import {
  LibreNMSIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SyncIcon, TestIcon, SaveIcon, CancelIcon,
} from "@/icons";
import { Link as LinkDevicesIcon } from "@iconoir/vue";
import { autoSort } from "@/composables/useTableSort";
import { listSubnets } from "@/api/subnets";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
const { t } = useI18n();

const { visibleKeys: lnVis, setVisible: lnSet, reset: lnReset } = useColumnPrefs(
  "librenms",
  ["name", "api_url", "enabled", "sync_interval_seconds", "last_sync_at", "last_error", "actions"],
  ["name", "api_url", "enabled", "sync_interval_seconds", "last_sync_at", "last_error", "actions"],
);
const lnPicker = computed(() => [
  { key: "name", label: t("cols.name") },
  { key: "api_url", label: "API URL" },
  { key: "enabled", label: t("cols.status") },
  { key: "sync_interval_seconds", label: t("cols.interval") },
  { key: "last_sync_at", label: t("cols.last_sync") },
  { key: "last_error", label: t("cols.last_error") },
  { key: "actions", label: t("cols.actions") },
]);

const msg = useMessage();
const rows = ref<LibreNMSInstance[]>([]);
import { useTableQuickFilter } from "@/composables/useTableQuickFilter";
const { query: filterQ, filtered: filteredRows } = useTableQuickFilter(rows);
const loading = ref(false);
const show = ref(false);
const editing = ref<LibreNMSInstance | null>(null);
const form = ref({
  name: "", api_url: "", api_token: "",
  enabled: true,
  sync_devices: true, sync_arp: true, sync_fdb: true, sync_vlans: true,
  use_for_status: true, auto_add_devices: true, auto_create_ips: true,
  sync_interval_seconds: 300,
  scope_subnet_ids: [] as string[],
});
const subnetOptions = ref<{ label: string; value: string }[]>([]);
async function loadSubnetOptions() {
  try {
    const r = await listSubnets({ page: 1, pageSize: 500 });
    subnetOptions.value = r.items.map((s) => ({
      label: s.description ? `${s.cidr} — ${s.description}` : s.cidr, value: s.id }));
  } catch { /* silent */ }
}

async function refresh() {
  loading.value = true;
  try { rows.value = (await listLibreNMS()).items; }
  catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}
function openCreate() {
  editing.value = null;
  form.value = {
    name: "", api_url: "", api_token: "",
    enabled: true,
    sync_devices: true, sync_arp: true, sync_fdb: true, sync_vlans: true,
    use_for_status: true, auto_add_devices: true, auto_create_ips: true,
    sync_interval_seconds: 300, scope_subnet_ids: [],
  };
  show.value = true;
}
function openEdit(r: LibreNMSInstance) {
  editing.value = r;
  form.value = {
    name: r.name,
    api_url: r.api_url,
    api_token: "",  // 留空表示不變
    enabled: r.enabled,
    sync_devices: r.sync_devices,
    sync_arp: r.sync_arp,
    sync_fdb: r.sync_fdb,
    sync_vlans: r.sync_vlans,
    use_for_status: r.use_for_status,
    auto_add_devices: r.auto_add_devices,
    auto_create_ips: r.auto_create_ips,
    sync_interval_seconds: r.sync_interval_seconds,
    scope_subnet_ids: r.scope_subnet_ids ?? [],
  };
  show.value = true;
}
async function submit() {
  if (!editing.value) {
    if (!form.value.name.trim()) { msg.error(t("librenms_admin.error_name_required")); return; }
    if (form.value.api_token.length < 8) { msg.error(t("librenms_admin.error_token_too_short")); return; }
  } else if (form.value.api_token && form.value.api_token.length < 8) {
    msg.error(t("librenms_admin.error_token_too_short"));
    return;
  }
  try {
    if (editing.value) {
      const payload: Record<string, unknown> = {
        api_url: form.value.api_url,
        enabled: form.value.enabled,
        sync_devices: form.value.sync_devices,
        sync_arp: form.value.sync_arp,
        sync_fdb: form.value.sync_fdb,
        sync_vlans: form.value.sync_vlans,
        use_for_status: form.value.use_for_status,
        auto_add_devices: form.value.auto_add_devices,
        auto_create_ips: form.value.auto_create_ips,
        sync_interval_seconds: form.value.sync_interval_seconds,
        scope_subnet_ids: form.value.scope_subnet_ids,
      };
      if (form.value.api_token) payload.api_token = form.value.api_token;
      await updateLibreNMS(editing.value.id, payload);
    } else {
      await createLibreNMS(form.value);
    }
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
  const row = rows.value.find((r) => r.id === id);
  const targetName = row?.name ?? id.slice(0, 8);
  try {
    await syncLibreNMS(id);
    // 後端現在立刻回 task_id，實際 sync 在背景跑。前端跳訊息引導去任務頁。
    msg.success(t("tasks.queued_toast", { kind: "LibreNMS sync", target: targetName }));
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function linkDevices(id: string) {
  try {
    const r = await linkLibreNMSDevices(id);
    msg.success(t("librenms_admin.link_done", { linked: r.linked, created: r.created }));
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function del(id: string) {
  try { await deleteLibreNMS(id); msg.success(t("common.ok")); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

function iconAction(icon: any, label: string, onClick: () => void, type?: any) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}
const allCols = computed<DataTableColumns<LibreNMSInstance>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 160, ellipsis: { tooltip: true } },
  { title: "API URL", key: "api_url", minWidth: 200, ellipsis: { tooltip: true } },
  {
    title: t("common.status"), key: "enabled", width: 110,
    render: (r) => h(NTag, { type: r.enabled ? "success" : "default", size: "small" },
      () => r.enabled ? t("common.enabled") : t("common.disabled")),
  },
  { title: t("cols.interval"), key: "sync_interval_seconds", width: 100, render: (r) => `${r.sync_interval_seconds}s` },
  {
    title: t("cols.last_sync"), key: "last_sync_at", width: 170,
    render: (r) => fmtDateTime(r.last_sync_at),
  },
  { title: t("cols.last_error"), key: "last_error", minWidth: 160, ellipsis: { tooltip: true }, render: (r) => r.last_error ?? "—" },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 216,
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openEdit(r)),
      iconAction(TestIcon, t("common.test"), () => test(r.id)),
      iconAction(SyncIcon, t("common.pull"), () => sync(r.id), "primary"),
      iconAction(LinkDevicesIcon, t("librenms_admin.link_devices"), () => linkDevices(r.id)),
      h(NPopconfirm, { onPositiveClick: () => del(r.id) }, {
        trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]));

const cols = computed<DataTableColumns<LibreNMSInstance>>(() =>
  allCols.value.filter((c: any) => lnVis.value.includes(c.key)),
);

onMounted(() => { void refresh(); void loadSubnetOptions(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><LibreNMSIcon /></n-icon>
        <span>{{ t("librenms_admin.title") }}</span>
      </n-space>
    </template>

    <n-space style="margin-bottom: 12px" align="center">
      <n-input v-model:value="filterQ" :placeholder="t('common.filter')" clearable style="width: 160px" />
      <n-button @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
      <n-button type="primary" @click="openCreate">
        <template #icon><n-icon><PlusIcon /></n-icon></template>
        {{ t("librenms_admin.create") }}
      </n-button>
      <ColumnPicker :all="lnPicker" :visible="lnVis"
                    @update:visible="lnSet" @reset="lnReset" />
      <ExportButton :columns="cols" :rows="rows" filename="librenms" :title="t('librenms_admin.title')" />
    </n-space>

    <n-data-table :columns="cols" :data="filteredRows" :loading="loading" :bordered="false" :scroll-x="1116">
      <template #empty>
        <n-space justify="center">{{ t("common.no_data") }}</n-space>
      </template>
    </n-data-table>

    <n-modal v-model:show="show" preset="card" style="width: 540px">
      <template #header>
        <n-space align="center">
          <n-icon :size="20"><component :is="editing ? EditIcon : PlusIcon" /></n-icon>
          <span>{{ editing ? `${t("common.edit")} ${editing.name}` : t("librenms_admin.create") }}</span>
        </n-space>
      </template>
      <n-form>
        <n-form-item :label="t('common.name')">
          <n-input v-model:value="form.name" placeholder="librenms-main" :disabled="!!editing" />
          <template #feedback v-if="editing">
            <span style="opacity: 0.7">{{ t("common.name_readonly_hint") }}</span>
          </template>
        </n-form-item>
        <n-form-item label="API URL">
          <n-input v-model:value="form.api_url"
                   :placeholder="t('librenms_admin.url_ph')" />
        </n-form-item>
        <n-form-item :label="editing
                       ? `${t('librenms_admin.api_token')} (${t('users.password_blank_unchanged')})`
                       : t('librenms_admin.api_token')">
          <n-input v-model:value="form.api_token" type="password" show-password-on="click"
                   :placeholder="editing ? t('users.password_blank_unchanged') : t('librenms_admin.api_token_placeholder')" />
        </n-form-item>
        <n-form-item :label="t('common.enabled')">
          <n-switch v-model:value="form.enabled" />
        </n-form-item>
        <div class="sync-toggles">
          <div class="row"><span>{{ t('librenms_admin.sync_devices') }}</span><n-switch size="small" v-model:value="form.sync_devices" /></div>
          <div class="row"><span>{{ t('librenms_admin.sync_arp') }}</span><n-switch size="small" v-model:value="form.sync_arp" /></div>
          <div class="row"><span>{{ t('librenms_admin.sync_fdb') }}</span><n-switch size="small" v-model:value="form.sync_fdb" /></div>
          <div class="row"><span>{{ t('librenms_admin.sync_vlans') }}</span><n-switch size="small" v-model:value="form.sync_vlans" /></div>
          <div class="row"><span>{{ t('librenms_admin.use_for_status') }}</span><n-switch size="small" v-model:value="form.use_for_status" /></div>
          <div class="row"><span>{{ t('librenms_admin.auto_add_devices') }}</span><n-switch size="small" v-model:value="form.auto_add_devices" /></div>
          <div class="row"><span>{{ t('librenms_admin.auto_create_ips') }}</span><n-switch size="small" v-model:value="form.auto_create_ips" /></div>
        </div>
        <p class="hint">{{ t('librenms_admin.auto_create_ips_hint') }}</p>
        <n-form-item :label="t('librenms_admin.sync_interval')">
          <n-input-number v-model:value="form.sync_interval_seconds" :min="60" :max="86400" />
        </n-form-item>
        <n-form-item :label="t('librenms_admin.scope_subnets')">
          <div style="width: 100%">
            <n-select v-model:value="form.scope_subnet_ids" :options="subnetOptions"
                      multiple filterable clearable :placeholder="t('librenms_admin.scope_all')" />
            <ScopeOverlapWarning :scope-empty="!form.scope_subnet_ids?.length" />
          </div>
          <template #feedback>
            <span style="font-size: 11px; opacity: .7">{{ t("librenms_admin.scope_hint") }}</span>
          </template>
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

<style scoped>
.sync-toggles {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 4px 16px;
  border: 1px solid var(--n-border-color, rgba(128,128,128,0.25));
  border-radius: 8px;
  padding: 10px 14px;
  margin-bottom: 18px;
}
.sync-toggles .row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 4px 0;
}
.sync-toggles .row span { font-size: 13px; }
.hint { font-size: 12px; color: var(--n-text-color-disabled, #888); margin: -8px 0 14px; line-height: 1.5; }
</style>
