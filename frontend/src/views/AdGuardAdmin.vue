<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { fmtDateTime } from "@/utils/datetime";
import { useI18n } from "vue-i18n";
import ScopeOverlapWarning from "@/components/ScopeOverlapWarning.vue";
import {
  NCard, NDataTable, NSpace, NButton, NTag, NIcon, NTooltip,
  NModal, NForm, NFormItem, NInput, NInputNumber, NSwitch, NSelect, NCheckbox, NPopconfirm,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { listSubnets } from "@/api/subnets";
import {
  listAdGuard, createAdGuard, updateAdGuard, deleteAdGuard,
  testAdGuard, syncAdGuard, type AdGuardInstance,
} from "@/api/integrations";
import {
  DnsIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SyncIcon, TestIcon, SaveIcon, CancelIcon,
} from "@/icons";
import { autoSort } from "@/composables/useTableSort";
import ColumnPicker from "@/components/ColumnPicker.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
const { t } = useI18n();

const { visibleKeys: agVis, setVisible: agSet, reset: agReset } = useColumnPrefs(
  "adguard",
  ["name", "api_url", "enabled", "sync_flags", "last_sync_at", "last_error", "actions"],
  ["name", "api_url", "enabled", "sync_flags", "last_sync_at", "last_error", "actions"],
);
const agPicker = [
  { key: "name", label: t("cols.name") },
  { key: "api_url", label: "API URL" },
  { key: "enabled", label: t("cols.status") },
  { key: "sync_flags", label: t("cols.sync_items") },
  { key: "last_sync_at", label: t("cols.last_sync") },
  { key: "last_error", label: t("cols.last_error") },
  { key: "actions", label: t("cols.actions") },
];

const msg = useMessage();
const rows = ref<AdGuardInstance[]>([]);
const loading = ref(false);
const show = ref(false);
const editing = ref<AdGuardInstance | null>(null);

const form = ref({
  name: "", api_url: "", api_user: "", api_password: "",
  enabled: true, verify_tls: true,
  sync_clients: true, sync_rewrites: true,
  sync_interval_seconds: 300,
  description: "",
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
  try {
    const r = await listAdGuard();
    rows.value = r.items;
  } catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}

function openCreate() {
  editing.value = null;
  form.value = {
    name: "", api_url: "", api_user: "", api_password: "",
    enabled: true, verify_tls: true,
    sync_clients: true, sync_rewrites: true,
    sync_interval_seconds: 300, description: "",
    scope_subnet_ids: [],
  };
  show.value = true;
}

function openEdit(r: AdGuardInstance) {
  editing.value = r;
  form.value = {
    name: r.name, api_url: r.api_url, api_user: r.api_user, api_password: "",
    enabled: r.enabled, verify_tls: r.verify_tls,
    sync_clients: r.sync_clients, sync_rewrites: r.sync_rewrites,
    sync_interval_seconds: r.sync_interval_seconds,
    description: r.description ?? "",
    scope_subnet_ids: r.scope_subnet_ids ?? [],
  };
  show.value = true;
}

async function submit() {
  try {
    if (editing.value) {
      const payload: any = {
        name: form.value.name,
        api_url: form.value.api_url,
        api_user: form.value.api_user,
        enabled: form.value.enabled,
        verify_tls: form.value.verify_tls,
        sync_clients: form.value.sync_clients,
        sync_rewrites: form.value.sync_rewrites,
        sync_interval_seconds: form.value.sync_interval_seconds,
        description: form.value.description || undefined,
        scope_subnet_ids: form.value.scope_subnet_ids,
      };
      if (form.value.api_password) payload.api_password = form.value.api_password;
      await updateAdGuard(editing.value.id, payload);
    } else {
      await createAdGuard({
        name: form.value.name,
        api_url: form.value.api_url,
        api_user: form.value.api_user,
        api_password: form.value.api_password,
        enabled: form.value.enabled,
        verify_tls: form.value.verify_tls,
        sync_clients: form.value.sync_clients,
        sync_rewrites: form.value.sync_rewrites,
        sync_interval_seconds: form.value.sync_interval_seconds,
        description: form.value.description || undefined,
        scope_subnet_ids: form.value.scope_subnet_ids,
      });
    }
    show.value = false;
    msg.success(t("common.ok"));
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

async function test(id: string) {
  try {
    const r: any = await testAdGuard(id);
    msg.success(r?.version ? `AdGuard ${r.version}` : t("common.ok"));
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

async function sync(id: string) {
  const row = rows.value.find((r) => r.id === id);
  const targetName = row?.name ?? id.slice(0, 8);
  try {
    await syncAdGuard(id);
    msg.success(t("tasks.queued_toast", { kind: "AdGuard sync", target: targetName }));
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

async function del(id: string) {
  try { await deleteAdGuard(id); await refresh(); }
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
const allCols = computed<DataTableColumns<AdGuardInstance>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 160, ellipsis: { tooltip: true } },
  { title: "API URL", key: "api_url", minWidth: 200, ellipsis: { tooltip: true } },
  {
    title: t("common.status"), key: "enabled", width: 110,
    render: (r) => h(NTag, { type: r.enabled ? "success" : "default", size: "small" },
      () => r.enabled ? t("common.enabled") : t("common.disabled")),
  },
  {
    title: t("common.sync"), key: "sync_flags", width: 150,
    render: (r) => {
      const tags: any[] = [];
      if (r.sync_clients) tags.push(h(NTag, { size: "tiny", type: "info", bordered: false }, () => "clients"));
      if (r.sync_rewrites) tags.push(h(NTag, { size: "tiny", type: "info", bordered: false }, () => "rewrites"));
      return h(NSpace, { size: 4 }, () => tags);
    },
  },
  {
    title: t("cols.last_sync"), key: "last_sync_at", width: 170,
    render: (r) => fmtDateTime(r.last_sync_at),
  },
  { title: t("cols.last_error"), key: "last_error", minWidth: 160, ellipsis: { tooltip: true }, render: (r) => r.last_error ?? "—" },
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

const cols = computed<DataTableColumns<AdGuardInstance>>(() =>
  allCols.value.filter((c: any) => agVis.value.includes(c.key)),
);

onMounted(() => { void refresh(); void loadSubnetOptions(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><DnsIcon /></n-icon>
        <span>AdGuard Home</span>
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
      <ColumnPicker :all="agPicker" :visible="agVis"
                    @update:visible="agSet" @reset="agReset" />
    </n-space>
    <n-data-table :columns="cols" :data="rows" :loading="loading" :bordered="false" :scroll-x="1126" />

    <n-modal v-model:show="show" preset="card"
             :title="editing ? t('common.edit') : `${t('common.create')} AdGuard`"
             style="width: 520px">
      <n-form>
        <n-form-item :label="t('common.name')"><n-input v-model:value="form.name" /></n-form-item>
        <n-form-item label="API URL">
          <n-input v-model:value="form.api_url" placeholder="http://adguard.example.com:3000" />
        </n-form-item>
        <n-form-item :label="t('adguard_admin.api_user')">
          <n-input v-model:value="form.api_user" />
        </n-form-item>
        <n-form-item :label="editing ? t('adguard_admin.password_keep') : t('adguard_admin.password')">
          <n-input v-model:value="form.api_password" type="password" show-password-on="click" />
        </n-form-item>
        <n-form-item :label="t('firewall_admin.verify_tls')">
          <n-switch v-model:value="form.verify_tls" />
        </n-form-item>
        <n-form-item :label="t('adguard_admin.pull_what')">
          <n-space :size="20">
            <n-checkbox v-model:checked="form.sync_clients">clients(IP + MAC + hostname)</n-checkbox>
            <n-checkbox v-model:checked="form.sync_rewrites">DNS rewrites</n-checkbox>
          </n-space>
        </n-form-item>
        <n-form-item :label="t('adguard_admin.sync_interval')">
          <n-input-number v-model:value="form.sync_interval_seconds" :min="30" :max="86400" />
        </n-form-item>
        <n-form-item :label="t('common.enable')">
          <n-switch v-model:value="form.enabled" />
        </n-form-item>
        <n-form-item :label="t('adguard_admin.scope_subnets')">
          <n-select v-model:value="form.scope_subnet_ids" :options="subnetOptions"
                    multiple filterable clearable :placeholder="t('adguard_admin.scope_all')" />
          <ScopeOverlapWarning :scope-empty="!form.scope_subnet_ids?.length" />
        </n-form-item>
        <div style="margin: -8px 0 4px">
          <span style="font-size: 11px; opacity: .7">{{ t("adguard_admin.scope_hint") }}</span>
        </div>
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
