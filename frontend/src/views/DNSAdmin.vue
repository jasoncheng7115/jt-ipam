<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import ScopeOverlapWarning from "@/components/ScopeOverlapWarning.vue";
import {
  NCard, NDataTable, NSpace, NButton, NTag, NIcon, NTooltip, NAlert,
  NModal, NForm, NFormItem, NInput, NInputNumber, NSelect, NSwitch, NPopconfirm,
  useMessage, type DataTableColumns,
} from "naive-ui";
import {
  listDNSServers, createDNSServer, updateDNSServer, deleteDNSServer, testDNSServer,
  type DNSServer, type DNSServerType,
} from "@/api/integrations";
import { listSubnets } from "@/api/subnets";
import {
  DnsIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, TestIcon, SaveIcon, CancelIcon,
} from "@/icons";
import { autoSort } from "@/composables/useTableSort";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
const { t } = useI18n();

const { visibleKeys: dnsVis, setVisible: dnsSet, reset: dnsReset } = useColumnPrefs(
  "dns_admin",
  ["name", "type", "endpoint", "enabled", "actions"],
  ["name", "type", "endpoint", "enabled", "actions"],
);
const dnsPicker = [
  { key: "name", label: t("cols.name") },
  { key: "type", label: t("cols.type") },
  { key: "endpoint", label: "Endpoint" },
  { key: "enabled", label: t("cols.status") },
  { key: "actions", label: t("cols.actions") },
];

const msg = useMessage();
const rows = ref<DNSServer[]>([]);
import { useTableQuickFilter } from "@/composables/useTableQuickFilter";
const { query: filterQ, filtered: filteredRows } = useTableQuickFilter(rows);
const loading = ref(false);
const show = ref(false);

interface Form {
  name: string;
  type: DNSServerType;
  api_url: string;
  server_address: string;
  enabled: boolean;
  sync_interval_seconds: number;
  api_key: string;
  api_secret: string;
  tsig_key: string;
  password: string;
  username: string;
  verify_tls: boolean;
  scope_subnet_ids: string[];
}

function emptyForm(): Form {
  return {
    name: "", type: "powerdns",
    api_url: "", server_address: "",
    enabled: true, sync_interval_seconds: 300,
    api_key: "", api_secret: "", tsig_key: "", password: "",
    username: "", verify_tls: true,
    scope_subnet_ids: [],
  };
}
const form = ref<Form>(emptyForm());

const subnetOptions = ref<{ label: string; value: string }[]>([]);
async function loadSubnetOptions() {
  try {
    const r = await listSubnets({ page: 1, pageSize: 500 });
    subnetOptions.value = r.items.map((s) => ({
      label: s.description ? `${s.cidr} — ${s.description}` : s.cidr, value: s.id }));
  } catch { /* silent */ }
}

const typeOpts = [
  { label: t("dns_admin.type_powerdns"),         value: "powerdns" },
  { label: t("dns_admin.type_bind9"),            value: "bind9" },
  { label: t("dns_admin.type_unbound_opnsense"), value: "unbound_opnsense" },
  { label: t("dns_admin.type_windows_dns"),      value: "windows_dns" },
  { label: t("dns_admin.type_univention_ucs"),   value: "univention_ucs" },
];

// 不同 type 該顯示哪些憑證欄位
const showApiKey   = computed(() => ["powerdns", "unbound_opnsense"].includes(form.value.type));
const showApiSecret = computed(() => form.value.type === "unbound_opnsense");
const showTsig     = computed(() => form.value.type === "bind9");
const showPassword = computed(() => ["windows_dns", "univention_ucs"].includes(form.value.type));
const showApiUrl   = computed(() => ["powerdns", "unbound_opnsense", "univention_ucs"].includes(form.value.type));
const showServerAddr = computed(() => ["bind9", "windows_dns"].includes(form.value.type));
const showUsername = computed(() => ["windows_dns", "univention_ucs"].includes(form.value.type));
const showVerifyTls = computed(() => form.value.type === "univention_ucs");

async function refresh() {
  loading.value = true;
  try { rows.value = (await listDNSServers()).items ?? []; }
  catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}
const editingId = ref<string | null>(null);
function openCreate() {
  editingId.value = null;
  form.value = emptyForm();
  show.value = true;
}
function openEdit(r: DNSServer) {
  editingId.value = r.id;
  const f = emptyForm();
  f.name = r.name;
  f.type = r.type as DNSServerType;
  f.enabled = r.enabled;
  f.sync_interval_seconds = r.sync_interval_seconds ?? 300;
  // api_url / server_address 依類型回填（祕密欄留空＝不變）
  f.api_url = r.api_url ?? "";
  f.server_address = r.server_address ?? "";
  f.scope_subnet_ids = r.scope_subnet_ids ?? [];
  // extra_config（JSON）回填 username / verify_tls，否則重開會跑回預設值
  if (r.extra_config) {
    try {
      const extra = JSON.parse(r.extra_config) as { username?: string; verify_tls?: boolean };
      if (typeof extra.username === "string") f.username = extra.username;
      if (typeof extra.verify_tls === "boolean") f.verify_tls = extra.verify_tls;
    } catch { /* ignore malformed */ }
  }
  form.value = f;
  show.value = true;
}
async function submit() {
  if (!form.value.name.trim()) { msg.error(t("dns_admin.error_name_required")); return; }
  const payload: any = {
    name: form.value.name,
    type: form.value.type,
    enabled: form.value.enabled,
    sync_interval_seconds: form.value.sync_interval_seconds,
    scope_subnet_ids: form.value.scope_subnet_ids,
  };
  if (showApiUrl.value && form.value.api_url) payload.api_url = form.value.api_url;
  if (showServerAddr.value && form.value.server_address) payload.server_address = form.value.server_address;
  if (showApiKey.value && form.value.api_key) payload.api_key = form.value.api_key;
  if (showApiSecret.value && form.value.api_secret) payload.api_secret = form.value.api_secret;
  if (showTsig.value && form.value.tsig_key) payload.tsig_key = form.value.tsig_key;
  if (showPassword.value && form.value.password) payload.password = form.value.password;
  // username / verify_tls 走 extra_config（windows_dns / univention_ucs）
  if (showUsername.value || showVerifyTls.value) {
    const extra: Record<string, unknown> = {};
    if (showUsername.value && form.value.username) extra.username = form.value.username;
    if (showVerifyTls.value) extra.verify_tls = form.value.verify_tls;
    if (Object.keys(extra).length) payload.extra_config = JSON.stringify(extra);
  }
  try {
    if (editingId.value) await updateDNSServer(editingId.value, payload);
    else await createDNSServer(payload);
    show.value = false;
    msg.success(t("common.ok"));
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function test(id: string) {
  try { await testDNSServer(id); msg.success(t("librenms_admin.test_ok")); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function del(id: string) {
  try { await deleteDNSServer(id); msg.success(t("common.ok")); await refresh(); }
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
const allCols = computed<DataTableColumns<DNSServer>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 160, ellipsis: { tooltip: true } },
  {
    title: t("dns_admin.type"), key: "type", width: 110,
    render: (r) => h(NTag, { size: "small", type: "info" }, () => r.type),
  },
  { title: t("dns_admin.endpoint"), key: "endpoint", minWidth: 200, ellipsis: { tooltip: true },
    render: (r) => r.api_url ?? r.server_address ?? "—" },
  {
    title: t("common.status"), key: "enabled", width: 110,
    render: (r) => h(NTag, { type: r.enabled ? "success" : "default", size: "small" },
      () => r.enabled ? t("common.enabled") : t("common.disabled")),
  },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 124,
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openEdit(r)),
      iconAction(TestIcon, t("common.test"), () => test(r.id)),
      h(NPopconfirm, { onPositiveClick: () => del(r.id) }, {
        trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]));

const cols = computed<DataTableColumns<DNSServer>>(() =>
  allCols.value.filter((c: any) => dnsVis.value.includes(c.key)),
);

onMounted(() => { void refresh(); void loadSubnetOptions(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><DnsIcon /></n-icon>
        <span>{{ t("dns_admin.title") }}</span>
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
        {{ t("dns_admin.create") }}
      </n-button>
      <ColumnPicker :all="dnsPicker" :visible="dnsVis"
                    @update:visible="dnsSet" @reset="dnsReset" />
      <ExportButton :columns="cols" :rows="rows" filename="dns-servers" :title="t('dns_admin.title')" />
    </n-space>

    <n-data-table :columns="cols" :data="filteredRows" :loading="loading" :bordered="false" :scroll-x="766">
      <template #empty>
        <n-space justify="center">{{ t("common.no_data") }}</n-space>
      </template>
    </n-data-table>

    <n-modal v-model:show="show" preset="card" style="width: 560px">
      <template #header>
        <n-space align="center">
          <n-icon :size="20"><component :is="editingId ? EditIcon : PlusIcon" /></n-icon>
          <span>{{ editingId ? t("dns_admin.edit") : t("dns_admin.create") }}</span>
        </n-space>
      </template>
      <n-form>
        <n-form-item :label="t('common.name')">
          <n-input v-model:value="form.name" placeholder="dns-edge" />
        </n-form-item>
        <n-form-item :label="t('dns_admin.type')">
          <n-select v-model:value="form.type" :options="typeOpts" />
        </n-form-item>

        <!-- 各類型設定說明 -->
        <n-alert v-if="form.type === 'univention_ucs'" type="info" :bordered="false"
                 :show-icon="true" style="margin-bottom: 12px">
          {{ t("dns_admin.help_ucs") }}
        </n-alert>
        <n-alert v-else type="default" :bordered="false" :show-icon="true" style="margin-bottom: 12px">
          {{ t("dns_admin.help_" + form.type) }}
        </n-alert>

        <n-form-item v-if="showApiUrl" label="API URL">
          <n-input v-model:value="form.api_url"
                   :placeholder="form.type === 'powerdns'
                     ? 'https://powerdns.example.com:8081'
                     : form.type === 'univention_ucs'
                       ? 'https://ucs.example.com'
                       : 'https://opnsense.example.com'" />
        </n-form-item>
        <n-form-item v-if="showUsername" :label="t('dns_admin.username')">
          <n-input v-model:value="form.username"
                   :placeholder="form.type === 'univention_ucs' ? 'Administrator' : 'DOMAIN\\\\svc-dns'" />
        </n-form-item>
        <n-form-item v-if="showServerAddr" :label="t('dns_admin.server_address')">
          <n-input v-model:value="form.server_address"
                   :placeholder="form.type === 'bind9' ? 'ns1.example.com' : 'dc01.example.com'" />
        </n-form-item>

        <n-form-item v-if="showApiKey"
                     :label="form.type === 'powerdns' ? 'X-API-Key' : 'OPNsense API key'">
          <n-input v-model:value="form.api_key" type="password" show-password-on="click" />
        </n-form-item>
        <n-form-item v-if="showApiSecret" label="OPNsense API secret">
          <n-input v-model:value="form.api_secret" type="password" show-password-on="click" />
        </n-form-item>
        <n-form-item v-if="showTsig" label="TSIG key (BIND9)">
          <n-input v-model:value="form.tsig_key" type="password" show-password-on="click"
                   placeholder="hmac-sha256:keyname:base64key" />
        </n-form-item>
        <n-form-item v-if="showPassword"
                     :label="form.type === 'univention_ucs' ? t('dns_admin.password') : t('dns_admin.winrm_password')">
          <n-input v-model:value="form.password" type="password" show-password-on="click" />
        </n-form-item>
        <n-form-item v-if="showVerifyTls" :label="t('dns_admin.verify_tls')">
          <n-switch v-model:value="form.verify_tls" />
        </n-form-item>

        <n-form-item :label="t('common.enabled')">
          <n-switch v-model:value="form.enabled" />
        </n-form-item>
        <n-form-item :label="t('librenms_admin.sync_interval')">
          <n-input-number v-model:value="form.sync_interval_seconds" :min="60" :max="86400" />
        </n-form-item>
        <n-form-item :label="t('dns_admin.scope_subnets')">
          <n-select v-model:value="form.scope_subnet_ids" :options="subnetOptions"
                    multiple filterable clearable :placeholder="t('dns_admin.scope_all')" />
          <ScopeOverlapWarning :scope-empty="!form.scope_subnet_ids?.length" />
        </n-form-item>
        <div style="margin: -8px 0 4px">
          <span style="font-size: 11px; opacity: .7">{{ t("dns_admin.scope_hint") }}</span>
        </div>
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
