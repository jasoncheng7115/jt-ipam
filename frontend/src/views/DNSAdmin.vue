<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NButton, NTag, NIcon,
  NModal, NForm, NFormItem, NInput, NInputNumber, NSelect, NSwitch, NPopconfirm,
  useMessage, type DataTableColumns,
} from "naive-ui";
import {
  listDNSServers, createDNSServer, deleteDNSServer, testDNSServer,
  type DNSServer, type DNSServerType,
} from "@/api/integrations";
import {
  DnsIcon, PlusIcon, DeleteIcon, RefreshIcon, TestIcon, SaveIcon, CancelIcon,
} from "@/icons";

const { t } = useI18n();
const msg = useMessage();
const rows = ref<DNSServer[]>([]);
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
}

const form = ref<Form>({
  name: "", type: "powerdns",
  api_url: "https://", server_address: "",
  enabled: true, sync_interval_seconds: 300,
  api_key: "", api_secret: "", tsig_key: "", password: "",
});

const typeOpts = [
  { label: t("dns_admin.type_powerdns"),         value: "powerdns" },
  { label: t("dns_admin.type_bind9"),            value: "bind9" },
  { label: t("dns_admin.type_unbound_opnsense"), value: "unbound_opnsense" },
  { label: t("dns_admin.type_windows_dns"),      value: "windows_dns" },
];

// 不同 type 該顯示哪些憑證欄位
const showApiKey   = computed(() => ["powerdns", "unbound_opnsense"].includes(form.value.type));
const showApiSecret = computed(() => form.value.type === "unbound_opnsense");
const showTsig     = computed(() => form.value.type === "bind9");
const showPassword = computed(() => form.value.type === "windows_dns");
const showApiUrl   = computed(() => ["powerdns", "unbound_opnsense"].includes(form.value.type));
const showServerAddr = computed(() => ["bind9", "windows_dns"].includes(form.value.type));

async function refresh() {
  loading.value = true;
  try { rows.value = (await listDNSServers()).items ?? []; }
  catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}
function openCreate() {
  form.value = {
    name: "", type: "powerdns",
    api_url: "https://", server_address: "",
    enabled: true, sync_interval_seconds: 300,
    api_key: "", api_secret: "", tsig_key: "", password: "",
  };
  show.value = true;
}
async function submit() {
  if (!form.value.name.trim()) { msg.error(t("dns_admin.error_name_required")); return; }
  const payload: any = {
    name: form.value.name,
    type: form.value.type,
    enabled: form.value.enabled,
    sync_interval_seconds: form.value.sync_interval_seconds,
  };
  if (showApiUrl.value && form.value.api_url) payload.api_url = form.value.api_url;
  if (showServerAddr.value && form.value.server_address) payload.server_address = form.value.server_address;
  if (showApiKey.value && form.value.api_key) payload.api_key = form.value.api_key;
  if (showApiSecret.value && form.value.api_secret) payload.api_secret = form.value.api_secret;
  if (showTsig.value && form.value.tsig_key) payload.tsig_key = form.value.tsig_key;
  if (showPassword.value && form.value.password) payload.password = form.value.password;
  try {
    await createDNSServer(payload);
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

const cols = computed<DataTableColumns<DNSServer>>(() => [
  { title: t("common.name"), key: "name" },
  {
    title: t("dns_admin.type"), key: "type",
    render: (r) => h(NTag, { size: "small", type: "info" }, () => r.type),
  },
  { title: t("dns_admin.endpoint"), key: "endpoint" },
  {
    title: t("common.status"), key: "enabled",
    render: (r) => h(NTag, { type: r.enabled ? "success" : "default", size: "small" },
      () => r.enabled ? t("common.enabled") : t("common.disabled")),
  },
  { title: "auth", key: "is_authoritative", render: (r) => r.is_authoritative ? "✓" : "—" },
  {
    title: t("common.actions"), key: "actions", width: 200,
    render: (r) => h(NSpace, { size: "small" }, () => [
      h(NButton, { size: "small", onClick: () => test(r.id) },
        { default: () => t("common.test"), icon: () => h(NIcon, null, () => h(TestIcon)) }),
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
        <n-icon :size="22"><DnsIcon /></n-icon>
        <span>{{ t("dns_admin.title") }}</span>
      </n-space>
    </template>

    <n-space style="margin-bottom: 12px">
      <n-button @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
      <n-button type="primary" @click="openCreate">
        <template #icon><n-icon><PlusIcon /></n-icon></template>
        {{ t("dns_admin.create") }}
      </n-button>
    </n-space>

    <n-data-table :columns="cols" :data="rows" :loading="loading" :bordered="false">
      <template #empty>
        <n-space justify="center">{{ t("common.no_data") }}</n-space>
      </template>
    </n-data-table>

    <n-modal v-model:show="show" preset="card" style="width: 560px">
      <template #header>
        <n-space align="center">
          <n-icon :size="20"><PlusIcon /></n-icon>
          <span>{{ t("dns_admin.create") }}</span>
        </n-space>
      </template>
      <n-form>
        <n-form-item :label="t('common.name')">
          <n-input v-model:value="form.name" placeholder="dns-edge" />
        </n-form-item>
        <n-form-item :label="t('dns_admin.type')">
          <n-select v-model:value="form.type" :options="typeOpts" />
        </n-form-item>

        <n-form-item v-if="showApiUrl" label="API URL">
          <n-input v-model:value="form.api_url"
                   :placeholder="form.type === 'powerdns' ? 'https://powerdns:8081' : 'https://opnsense'" />
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
        <n-form-item v-if="showPassword" :label="t('dns_admin.winrm_password')">
          <n-input v-model:value="form.password" type="password" show-password-on="click" />
        </n-form-item>

        <n-form-item :label="t('common.enabled')">
          <n-switch v-model:value="form.enabled" />
        </n-form-item>
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
