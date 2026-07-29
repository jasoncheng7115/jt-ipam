<script setup lang="ts">
/**
 * Windows DHCP Server 整合（Beta）。
 * 與 OPNsense / pfSense 的 DHCP 各自獨立設定；這裡只管 Windows DHCP 自己的連線與同步。
 * 走 WinRM + PowerShell 唯讀拉取 scope（發放範圍）與租約，不會更動 Windows 端任何設定。
 */
import { computed, h, onMounted, ref } from "vue";
import { fmtDateTime } from "@/utils/datetime";
import { useI18n } from "vue-i18n";
import ScopeOverlapWarning from "@/components/ScopeOverlapWarning.vue";
import {
  NCard, NDataTable, NSpace, NButton, NTag, NIcon, NTooltip, NAlert,
  NModal, NForm, NFormItem, NInput, NInputNumber, NSwitch, NSelect, NCheckbox, NPopconfirm,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { listSubnets } from "@/api/subnets";
import {
  listWindowsDhcp, createWindowsDhcp, updateWindowsDhcp, deleteWindowsDhcp,
  testWindowsDhcp, syncWindowsDhcp, type WindowsDhcpServer,
} from "@/api/windowsDhcp";
import {
  DhcpServerIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SyncIcon, TestIcon, SaveIcon, CancelIcon,
} from "@/icons";
import { autoSort } from "@/composables/useTableSort";
import ColumnPicker from "@/components/ColumnPicker.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";

const { t } = useI18n();
const msg = useMessage();

const COLS = ["name", "host", "enabled", "sync_flags", "last_sync_at", "last_error", "actions"];
const { visibleKeys: vis, setVisible: setVis, reset: resetVis } = useColumnPrefs(
  "windows_dhcp", COLS, COLS,
);
const picker = computed(() => [
  { key: "name", label: t("cols.name") },
  { key: "host", label: t("windows_dhcp.host") },
  { key: "enabled", label: t("cols.status") },
  { key: "sync_flags", label: t("cols.sync_items") },
  { key: "last_sync_at", label: t("cols.last_sync") },
  { key: "last_error", label: t("cols.last_error") },
  { key: "actions", label: t("cols.actions") },
]);

const rows = ref<WindowsDhcpServer[]>([]);
const loading = ref(false);
const show = ref(false);
const editing = ref<WindowsDhcpServer | null>(null);

function blankForm() {
  return {
    name: "", host: "", username: "", password: "",
    port: 5986, use_ssl: true, verify_tls: true, enabled: true,
    sync_scopes: true, sync_leases: true,
    sync_interval_seconds: 300, description: "",
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
  try { rows.value = (await listWindowsDhcp()).items; }
  catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}

function openCreate() {
  editing.value = null;
  form.value = blankForm();
  show.value = true;
}

function openEdit(r: WindowsDhcpServer) {
  editing.value = r;
  form.value = {
    name: r.name, host: r.host, username: r.username, password: "",
    port: r.port, use_ssl: r.use_ssl, verify_tls: r.verify_tls, enabled: r.enabled,
    sync_scopes: r.sync_scopes, sync_leases: r.sync_leases,
    sync_interval_seconds: r.sync_interval_seconds,
    description: r.description ?? "",
    scope_subnet_ids: r.scope_subnet_ids ?? [],
  };
  show.value = true;
}

async function submit() {
  const payload: any = {
    name: form.value.name, host: form.value.host, username: form.value.username,
    port: form.value.port, use_ssl: form.value.use_ssl, verify_tls: form.value.verify_tls,
    enabled: form.value.enabled,
    sync_scopes: form.value.sync_scopes, sync_leases: form.value.sync_leases,
    sync_interval_seconds: form.value.sync_interval_seconds,
    description: form.value.description || undefined,
    scope_subnet_ids: form.value.scope_subnet_ids,
  };
  try {
    if (editing.value) {
      if (form.value.password) payload.password = form.value.password;
      await updateWindowsDhcp(editing.value.id, payload);
    } else {
      payload.password = form.value.password;
      await createWindowsDhcp(payload);
    }
    show.value = false;
    msg.success(t("common.ok"));
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

async function test(id: string) {
  try {
    const r = await testWindowsDhcp(id);
    msg.success(t("windows_dhcp.test_ok", { n: r.scopes }));
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

async function sync(id: string) {
  const name = rows.value.find((r) => r.id === id)?.name ?? id.slice(0, 8);
  try {
    await syncWindowsDhcp(id);
    msg.success(t("tasks.queued_toast", { kind: "Windows DHCP sync", target: name }));
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

async function del(id: string) {
  try { await deleteWindowsDhcp(id); await refresh(); }
  catch { msg.error(t("errors.server")); }
}

function iconAction(icon: any, label: string, onClick: () => void, type?: any) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}

const allCols = computed<DataTableColumns<WindowsDhcpServer>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 160, ellipsis: { tooltip: true } },
  {
    title: t("windows_dhcp.host"), key: "host", minWidth: 200, ellipsis: { tooltip: true },
    render: (r) => `${r.host}:${r.port}`,
  },
  {
    title: t("common.status"), key: "enabled", width: 110,
    render: (r) => h(NTag, { type: r.enabled ? "success" : "default", size: "small" },
      () => r.enabled ? t("common.enabled") : t("common.disabled")),
  },
  {
    title: t("common.sync"), key: "sync_flags", width: 170,
    render: (r) => {
      const tags: any[] = [];
      if (r.sync_scopes) tags.push(h(NTag, { size: "tiny", type: "info", bordered: false }, () => t("windows_dhcp.scopes")));
      if (r.sync_leases) tags.push(h(NTag, { size: "tiny", type: "info", bordered: false }, () => t("windows_dhcp.leases")));
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
const cols = computed<DataTableColumns<WindowsDhcpServer>>(() =>
  allCols.value.filter((c: any) => vis.value.includes(c.key)),
);

onMounted(() => { void refresh(); void loadSubnetOptions(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><DhcpServerIcon /></n-icon>
        <span>{{ t("windows_dhcp.title") }}</span>
        <n-tag type="warning" size="small" :bordered="false">Beta</n-tag>
      </n-space>
    </template>

    <n-alert type="info" :bordered="false" style="margin-bottom: 12px">
      {{ t("windows_dhcp.intro") }}
    </n-alert>

    <n-space style="margin-bottom: 12px">
      <n-button @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
      <n-button type="primary" @click="openCreate">
        <template #icon><n-icon><PlusIcon /></n-icon></template>
        {{ t("common.create") }}
      </n-button>
      <ColumnPicker :all="picker" :visible="vis" @update:visible="setVis" @reset="resetVis" />
    </n-space>

    <n-data-table :columns="cols" :data="rows" :loading="loading" :bordered="false" :scroll-x="1126" />

    <n-modal v-model:show="show" preset="card"
             :title="editing ? t('common.edit') : `${t('common.create')} — ${t('windows_dhcp.title')}`"
             style="width: 560px">
      <n-form>
        <n-form-item :label="t('common.name')"><n-input v-model:value="form.name" /></n-form-item>
        <n-form-item :label="t('windows_dhcp.host')">
          <n-input v-model:value="form.host" placeholder="dhcp01.corp.example.com" />
        </n-form-item>
        <n-form-item :label="t('windows_dhcp.username')">
          <n-input v-model:value="form.username" placeholder="CORP\\svc-ipam" />
        </n-form-item>
        <n-form-item :label="editing ? t('windows_dhcp.password_keep') : t('windows_dhcp.password')">
          <n-input v-model:value="form.password" type="password" show-password-on="click" />
        </n-form-item>
        <n-form-item :label="t('windows_dhcp.winrm')">
          <n-space align="center" :size="16">
            <n-input-number v-model:value="form.port" :min="1" :max="65535" style="width: 120px" />
            <span><n-switch v-model:value="form.use_ssl" size="small" /> HTTPS</span>
            <span><n-switch v-model:value="form.verify_tls" size="small" /> {{ t("firewall_admin.verify_tls") }}</span>
          </n-space>
        </n-form-item>
        <div style="margin: -8px 0 12px">
          <span style="font-size: 11px; opacity: .7">{{ t("windows_dhcp.winrm_hint") }}</span>
        </div>
        <n-form-item :label="t('windows_dhcp.pull_what')">
          <n-space :size="20">
            <n-checkbox v-model:checked="form.sync_scopes">{{ t("windows_dhcp.scopes") }}</n-checkbox>
            <n-checkbox v-model:checked="form.sync_leases">{{ t("windows_dhcp.leases") }}</n-checkbox>
          </n-space>
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
        <n-button type="primary" @click="submit">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </n-space>
    </n-modal>
  </n-card>
</template>
