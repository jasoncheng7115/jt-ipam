<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { fmtDateTime } from "@/utils/datetime";
import { useI18n } from "vue-i18n";
import { trackTask } from "@/composables/useTaskTracker";
import ScopeOverlapWarning from "@/components/ScopeOverlapWarning.vue";
import {
  NCard, NDataTable, NSpace, NButton, NTag, NIcon, NTooltip,
  NModal, NForm, NFormItem, NInput, NInputNumber, NSwitch, NPopconfirm, NSelect, NAlert, NCheckbox,
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

const { visibleKeys: lnVis, setVisible: lnSet, reset: lnReset,
  order: lnOrder, setOrder: lnSetOrder, orderColumns: lnOrderCols } = useColumnPrefs(
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
import { apiErrMsg } from "@/api/client";
const { query: filterQ, filtered: filteredRows } = useTableQuickFilter(rows);
const loading = ref(false);
const show = ref(false);
const editing = ref<LibreNMSInstance | null>(null);
const form = ref({
  name: "", api_url: "", api_token: "",
  enabled: true,
  verify_tls: true,
  sync_devices: true, sync_arp: true, sync_fdb: true, sync_vlans: true, sync_links: true,
  use_for_status: true, auto_add_devices: true, auto_create_ips: true,
  auto_create_from_arp: false, arp_create_require_fdb: true, arp_create_skip_dhcp: true,
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
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}
function openCreate() {
  editing.value = null;
  form.value = {
    name: "", api_url: "", api_token: "",
    enabled: true,
    verify_tls: true,
    sync_devices: true, sync_arp: true, sync_fdb: true, sync_vlans: true, sync_links: true,
    use_for_status: true, auto_add_devices: true, auto_create_ips: true,
    auto_create_from_arp: false, arp_create_require_fdb: true, arp_create_skip_dhcp: true,
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
    verify_tls: r.verify_tls,
    sync_devices: r.sync_devices,
    sync_arp: r.sync_arp,
    sync_fdb: r.sync_fdb,
    sync_vlans: r.sync_vlans,
    sync_links: r.sync_links ?? true,
    use_for_status: r.use_for_status,
    auto_add_devices: r.auto_add_devices,
    auto_create_ips: r.auto_create_ips,
    auto_create_from_arp: r.auto_create_from_arp ?? false,
    arp_create_require_fdb: r.arp_create_require_fdb ?? true,
    arp_create_skip_dhcp: r.arp_create_skip_dhcp ?? true,
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
        verify_tls: form.value.verify_tls,
        sync_devices: form.value.sync_devices,
        sync_arp: form.value.sync_arp,
        sync_fdb: form.value.sync_fdb,
        sync_vlans: form.value.sync_vlans,
        sync_links: form.value.sync_links,
        use_for_status: form.value.use_for_status,
        auto_add_devices: form.value.auto_add_devices,
        auto_create_ips: form.value.auto_create_ips,
        auto_create_from_arp: form.value.auto_create_from_arp,
        arp_create_require_fdb: form.value.arp_create_require_fdb,
        arp_create_skip_dhcp: form.value.arp_create_skip_dhcp,
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
  try {
    const r = await syncLibreNMS(id);
    // 背景跑：右下角的「背景作業」面板顯示進度與結果，跑完重新整理清單
    trackTask(r.task_id, { onDone: () => void refresh() });
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
  lnOrderCols(allCols.value.filter((c: any) => lnVis.value.includes(c.key))),
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
                    @update:visible="lnSet" @reset="lnReset"
                    :order="lnOrder" @update:order="lnSetOrder" />
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
        <n-form-item :label="t('librenms_admin.verify_tls')">
          <n-space vertical :size="2" style="width:100%">
            <n-switch v-model:value="form.verify_tls" />
            <span class="hint">{{ t('librenms_admin.verify_tls_hint') }}</span>
          </n-space>
        </n-form-item>
        <div class="sync-toggles">
          <div class="row"><span>{{ t('librenms_admin.sync_devices') }}</span><n-switch size="small" v-model:value="form.sync_devices" /></div>
          <div class="row"><span>{{ t('librenms_admin.sync_arp') }}</span><n-switch size="small" v-model:value="form.sync_arp" /></div>
          <div class="row"><span>{{ t('librenms_admin.sync_fdb') }}</span><n-switch size="small" v-model:value="form.sync_fdb" /></div>
          <div class="row"><span>{{ t('librenms_admin.sync_vlans') }}</span><n-switch size="small" v-model:value="form.sync_vlans" /></div>
          <div class="row">
            <span>{{ t('librenms_admin.sync_links') }}</span>
            <n-switch size="small" v-model:value="form.sync_links" />
          </div>
          <!-- 說明橫跨兩欄：塞在右欄那格的話寬度只有一半，長句子會被擠成一團 -->
          <p class="row-hint">{{ t("librenms_admin.sync_links_hint") }}</p>
          <div class="row"><span>{{ t('librenms_admin.use_for_status') }}</span><n-switch size="small" v-model:value="form.use_for_status" /></div>
          <div class="row"><span>{{ t('librenms_admin.auto_add_devices') }}</span><n-switch size="small" v-model:value="form.auto_add_devices" /></div>
          <div class="row"><span>{{ t('librenms_admin.auto_create_ips') }}</span><n-switch size="small" v-model:value="form.auto_create_ips" /></div>
        </div>
        <p class="hint">{{ t('librenms_admin.auto_create_ips_hint') }}</p>
        <!-- 依 ARP 表自動建立 IP（#48）：預設關；打開才顯示代價與把關選項 -->
        <div class="arp-create" data-testid="lnms-arp-create">
          <div class="row">
            <span>{{ t('librenms_admin.arp_create') }}</span>
            <n-switch size="small" v-model:value="form.auto_create_from_arp" data-testid="lnms-arp-create-switch" />
          </div>
          <p class="row-hint">{{ t('librenms_admin.arp_create_hint') }}</p>
          <template v-if="form.auto_create_from_arp">
            <n-alert type="warning" :show-icon="false" :bordered="false" class="arp-warn">
              <span class="arp-warn-text">{{ t('librenms_admin.arp_create_warn') }}</span>
            </n-alert>
            <n-checkbox v-model:checked="form.arp_create_require_fdb" data-testid="lnms-arp-require-fdb">
              {{ t('librenms_admin.arp_create_require_fdb') }}
            </n-checkbox>
            <p class="row-hint indent">{{ t(form.arp_create_require_fdb
              ? 'librenms_admin.arp_create_require_fdb_hint' : 'librenms_admin.arp_create_no_fdb_warn') }}</p>
            <n-checkbox v-model:checked="form.arp_create_skip_dhcp">
              {{ t('librenms_admin.arp_create_skip_dhcp') }}
            </n-checkbox>
            <p class="row-hint indent">{{ t('librenms_admin.arp_create_skip_dhcp_hint') }}</p>
          </template>
        </div>
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
/* 開關格是兩欄，但說明要占滿整列 —— 不然它會掉進右邊那半格，
   一行只放得下五六個字，讀起來像被切碎的 */
.sync-toggles .row-hint {
  grid-column: 1 / -1;
  margin: 0 0 6px; padding: 6px 10px;
  font-size: 12px; line-height: 1.75;
  color: var(--n-text-color-3, #888);
  background: var(--n-color-embedded, rgba(128, 128, 128, .06));
  border-radius: 4px;
}
.arp-create {
  border: 1px solid var(--n-border-color, rgba(128,128,128,0.25));
  border-radius: 8px;
  padding: 10px 14px;
  margin-bottom: 18px;
  display: flex; flex-direction: column; gap: 6px;
}
.arp-create .row { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.arp-create .row span { font-size: 13px; }
.arp-create .row-hint { font-size: 12px; opacity: .7; margin: 0; line-height: 1.5; }
.arp-create .row-hint.indent { margin-left: 26px; }
.arp-create .arp-warn-text { font-size: 12.5px; line-height: 1.6; }
.hint { font-size: 12px; color: var(--n-text-color-disabled, #888); margin: -8px 0 14px; line-height: 1.5; }
</style>
