<script setup lang="ts">
/**
 * 獨立的 Kea DHCP Server（issue #45）。
 * jt-ipam 直接打 Kea 的 JSON 控制 API（控制代理，或 Kea 3.0 起 DHCP 伺服器自己的 HTTP 控制通道），
 * 唯讀拉範圍、保留與租約。版面照 Windows DHCP 那一頁。
 */
import { computed, h, onMounted, ref } from "vue";
import { fmtDateTime } from "@/utils/datetime";
import { useI18n } from "vue-i18n";
import { trackTask } from "@/composables/useTaskTracker";
import ScopeOverlapWarning from "@/components/ScopeOverlapWarning.vue";
import {
  NCard, NDataTable, NSpace, NButton, NTag, NIcon, NTooltip, NAlert,
  NModal, NForm, NFormItem, NInput, NInputNumber, NSwitch, NSelect, NCheckbox, NPopconfirm,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { listSubnets } from "@/api/subnets";
import {
  listKea, createKea, updateKea, deleteKea, testKea, syncKea, type KeaDhcpServer, type KeaDhcpWrite,
} from "@/api/dhcpStandalone";
import {
  KeaDhcpIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SyncIcon, TestIcon, SaveIcon, CancelIcon,
} from "@/icons";
import { autoSort } from "@/composables/useTableSort";
import ColumnPicker from "@/components/ColumnPicker.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { apiErrMsg } from "@/api/client";

const { t } = useI18n();
const msg = useMessage();

const COLS = ["name", "api_url", "enabled", "sync_flags", "last_sync_at", "last_error", "actions"];
const { visibleKeys: vis, setVisible: setVis, reset: resetVis, order, setOrder, orderColumns } =
  useColumnPrefs("kea_dhcp", COLS, COLS);
const picker = computed(() => [
  { key: "name", label: t("cols.name") },
  { key: "api_url", label: t("kea_dhcp.api_url") },
  { key: "enabled", label: t("cols.status") },
  { key: "sync_flags", label: t("cols.sync_items") },
  { key: "last_sync_at", label: t("cols.last_sync") },
  { key: "last_error", label: t("cols.last_error") },
  { key: "actions", label: t("cols.actions") },
]);

const rows = ref<KeaDhcpServer[]>([]);
const loading = ref(false);
const show = ref(false);
const editing = ref<KeaDhcpServer | null>(null);

function blankForm() {
  return {
    name: "", api_url: "", username: "", password: "", verify_tls: true, enabled: true,
    sync_scopes: true, sync_leases: true, sync_interval_seconds: 300, description: "",
    scope_subnet_ids: [] as string[],
  };
}
const form = ref(blankForm());

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
  try { rows.value = (await listKea()).items; }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}

function openCreate() {
  editing.value = null;
  form.value = blankForm();
  show.value = true;
}

function openEdit(r: KeaDhcpServer) {
  editing.value = r;
  form.value = {
    name: r.name, api_url: r.api_url, username: r.username ?? "", password: "", verify_tls: r.verify_tls,
    enabled: r.enabled, sync_scopes: r.sync_scopes, sync_leases: r.sync_leases,
    sync_interval_seconds: r.sync_interval_seconds, description: r.description ?? "",
    scope_subnet_ids: r.scope_subnet_ids ?? [],
  };
  show.value = true;
}

async function submit() {
  const f = form.value;
  const payload: KeaDhcpWrite = {
    name: f.name, api_url: f.api_url.trim(), verify_tls: f.verify_tls, username: f.username.trim() || null,
    enabled: f.enabled, sync_scopes: f.sync_scopes, sync_leases: f.sync_leases,
    sync_interval_seconds: f.sync_interval_seconds, description: f.description || undefined,
    scope_subnet_ids: f.scope_subnet_ids,
  };
  if (f.password) payload.password = f.password;
  try {
    if (editing.value) await updateKea(editing.value.id, payload);
    else await createKea(payload);
    show.value = false;
    msg.success(t("common.ok"));
    await refresh();
  } catch (e) { msg.error(apiErrMsg(e)); }
}

async function test(id: string) {
  try {
    const r = await testKea(id);
    const how = t(`kea_dhcp.mode_${r.mode}`);
    msg.success(t("kea_dhcp.test_ok", { version: r.version ?? "?", how, subnets: r.subnets, pools: r.pools }));
    if (!r.leases_supported) msg.warning(t("kea_dhcp.leases_unsupported"), { duration: 8000 });
  } catch (e) {
    // 連不上的原因是這一步最需要看清楚的：預設 3 秒一閃就沒了
    msg.error(apiErrMsg(e), { duration: 10_000, closable: true });
  }
}

async function sync(id: string) {
  try {
    const r = await syncKea(id);
    trackTask(r.task_id, { onDone: () => void refresh() });
  } catch (e) { msg.error(apiErrMsg(e)); }
}

async function del(id: string) {
  try { await deleteKea(id); await refresh(); }
  catch (e) { msg.error(apiErrMsg(e)); }
}

function iconAction(icon: any, label: string, onClick: () => void, type?: any) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type, "aria-label": label,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}

const allCols = computed<DataTableColumns<KeaDhcpServer>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 150, ellipsis: { tooltip: true } },
  { title: t("kea_dhcp.api_url"), key: "api_url", minWidth: 220, ellipsis: { tooltip: true } },
  {
    title: t("common.status"), key: "enabled", width: 110,
    render: (r) => h(NTag, { type: r.enabled ? "success" : "default", size: "small" },
      () => r.enabled ? t("common.enabled") : t("common.disabled")),
  },
  {
    title: t("common.sync"), key: "sync_flags", width: 210,
    render: (r) => {
      const tags: any[] = [];
      if (r.sync_scopes) tags.push(h(NTag, { size: "tiny", type: "info", bordered: false }, () => t("kea_dhcp.scopes")));
      if (r.sync_leases) {
        const off = r.last_summary?.leases_unsupported;
        tags.push(h(NTooltip, { disabled: !off }, {
          trigger: () => h(NTag, { size: "tiny", type: off ? "warning" : "info", bordered: false,
                                   "data-testid": off ? "kea-leases-unsupported" : undefined },
            () => t("kea_dhcp.leases")),
          default: () => t("kea_dhcp.leases_unsupported"),
        }));
      }
      return h(NSpace, { size: 4 }, () => tags);
    },
  },
  { title: t("cols.last_sync"), key: "last_sync_at", width: 170, render: (r) => fmtDateTime(r.last_sync_at) },
  {
    title: t("cols.last_error"), key: "last_error", minWidth: 160,
    ellipsis: { tooltip: true }, render: (r) => r.last_error ?? "—",
  },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 176,
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openEdit(r)),
      iconAction(TestIcon, t("common.test"), () => test(r.id)),
      iconAction(SyncIcon, t("common.pull"), () => sync(r.id), "primary"),
      h(NPopconfirm, { onPositiveClick: () => del(r.id) }, {
        trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]));
const cols = computed<DataTableColumns<KeaDhcpServer>>(() =>
  orderColumns(allCols.value.filter((c: any) => vis.value.includes(c.key))),
);

onMounted(() => { void refresh(); void loadSubnetOptions(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><KeaDhcpIcon /></n-icon>
        <span>{{ t("kea_dhcp.title") }}</span>
      </n-space>
    </template>

    <n-alert type="info" :bordered="false" style="margin-bottom: 12px">
      {{ t("kea_dhcp.intro") }}
    </n-alert>

    <n-space style="margin-bottom: 12px">
      <n-button @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
      <n-button type="primary" data-testid="kea-create" @click="openCreate">
        <template #icon><n-icon><PlusIcon /></n-icon></template>
        {{ t("common.create") }}
      </n-button>
      <ColumnPicker :all="picker" :visible="vis" @update:visible="setVis" @reset="resetVis"
                    :order="order" @update:order="setOrder" />
    </n-space>

    <n-data-table :columns="cols" :data="rows" :loading="loading" :bordered="false" :scroll-x="1150" />

    <n-modal v-model:show="show" preset="card"
             :title="editing ? t('common.edit') : `${t('common.create')} — ${t('kea_dhcp.title')}`"
             style="width: min(580px, 100%)">
      <n-form>
        <n-form-item :label="t('common.name')"><n-input v-model:value="form.name" data-testid="kea-name" /></n-form-item>
        <n-form-item :label="t('kea_dhcp.api_url')">
          <div style="width: 100%">
            <n-input v-model:value="form.api_url" placeholder="https://kea.example.test:8000/" data-testid="kea-url" />
            <div class="form-hint">{{ t("kea_dhcp.api_url_hint") }}</div>
          </div>
        </n-form-item>
        <n-form-item :label="t('kea_dhcp.username')">
          <n-input v-model:value="form.username" :placeholder="t('kea_dhcp.username_hint')" />
        </n-form-item>
        <n-form-item :label="editing?.has_password ? t('kea_dhcp.password_keep') : t('kea_dhcp.password')">
          <n-input v-model:value="form.password" type="password" show-password-on="click"
                   :disabled="!form.username" />
        </n-form-item>
        <n-form-item :label="t('firewall_admin.verify_tls')">
          <n-switch v-model:value="form.verify_tls" />
        </n-form-item>
        <n-form-item :label="t('kea_dhcp.pull_what')">
          <div>
            <n-space :size="20">
              <n-checkbox v-model:checked="form.sync_scopes">{{ t("kea_dhcp.scopes") }}</n-checkbox>
              <n-checkbox v-model:checked="form.sync_leases">{{ t("kea_dhcp.leases") }}</n-checkbox>
            </n-space>
            <div class="form-hint">{{ t("kea_dhcp.hooks_hint") }}</div>
          </div>
        </n-form-item>
        <n-form-item :label="t('adguard_admin.sync_interval')">
          <n-input-number v-model:value="form.sync_interval_seconds" :min="30" :max="86400" />
        </n-form-item>
        <n-form-item :label="t('common.enable')">
          <n-switch v-model:value="form.enabled" />
        </n-form-item>
        <n-form-item :label="t('adguard_admin.scope_subnets')">
          <div style="width: 100%">
            <n-select v-model:value="form.scope_subnet_ids" :options="subnetOptions"
                      multiple filterable clearable :placeholder="t('adguard_admin.scope_all')" />
            <ScopeOverlapWarning :scope-empty="!form.scope_subnet_ids?.length" />
          </div>
        </n-form-item>
        <n-form-item :label="t('common.description')">
          <n-input v-model:value="form.description" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="show = false">
          <template #icon><n-icon><CancelIcon /></n-icon></template>
          {{ t("common.cancel") }}
        </n-button>
        <n-button type="primary" data-testid="kea-save" @click="submit">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </n-space>
    </n-modal>
  </n-card>
</template>

<style scoped>
.form-hint { font-size: 11.5px; opacity: .7; margin-top: 4px; line-height: 1.5; }
</style>
