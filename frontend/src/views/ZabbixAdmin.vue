<script setup lang="ts">
/**
 * Zabbix 整合 —— 監控面補充，不是 LibreNMS 的替代。
 * 拉的是主機↔IP 對應、存活狀態、維護狀態，以及「監控涵蓋缺口」。
 * 全程唯讀（只呼叫 host.get 之類的查詢方法），不會更動 Zabbix 任何設定。
 */
import { computed, h, onMounted, ref } from "vue";
import { fmtDateTime } from "@/utils/datetime";
import { useI18n } from "vue-i18n";
import { trackTask } from "@/composables/useTaskTracker";
import ScopeOverlapWarning from "@/components/ScopeOverlapWarning.vue";
import {
  NCard, NDataTable, NSpace, NButton, NTag, NIcon, NTooltip, NAlert, NModal, NForm,
  NFormItem, NInput, NInputNumber, NSwitch, NSelect, NPopconfirm, NRadioGroup, NRadio,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { listSubnets } from "@/api/subnets";
import {
  listZabbix, createZabbix, updateZabbix, deleteZabbix, testZabbix, syncZabbix,
  listZabbixHosts, zabbixCoverageGap,
  type ZabbixInstance, type ZabbixHealth, type ZabbixHost, type ZabbixGapRow,
} from "@/api/zabbix";
import {
  LibreNMSIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SyncIcon, TestIcon,
  SaveIcon, CancelIcon, SearchIcon,
} from "@/icons";
import { autoSort } from "@/composables/useTableSort";
import ColumnPicker from "@/components/ColumnPicker.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { useTablePagination } from "@/composables/useTablePagination";
import { apiErrMsg } from "@/api/client";

const { t } = useI18n();
const msg = useMessage();

const COLS = ["name", "api_url", "enabled", "auth", "last_sync_at", "last_error", "actions"];
const { visibleKeys: vis, setVisible: setVis, reset: resetVis, order, setOrder, orderColumns } =
  useColumnPrefs("zabbix", COLS, COLS);
const picker = computed(() => [
  { key: "name", label: t("cols.name") },
  { key: "api_url", label: "API URL" },
  { key: "enabled", label: t("cols.status") },
  { key: "auth", label: t("zabbix.auth") },
  { key: "last_sync_at", label: t("cols.last_sync") },
  { key: "last_error", label: t("cols.last_error") },
  { key: "actions", label: t("cols.actions") },
]);

const rows = ref<ZabbixInstance[]>([]);
const loading = ref(false);
const show = ref(false);
const editing = ref<ZabbixInstance | null>(null);

const health = ref<ZabbixHealth | null>(null);
const healthFor = ref("");
const healthOpen = ref(false);

// 主機清單與涵蓋缺口（同一個對話框兩個分頁的簡化版：各自一個對話框）
const hostsOpen = ref(false);
const hostsFor = ref("");
const hosts = ref<ZabbixHost[]>([]);
const hostQuery = ref("");
const hostsLoading = ref(false);
const gapOpen = ref(false);
const gap = ref<ZabbixGapRow[]>([]);
const gapScope = ref<"instance" | "global">("instance");

const hostPager = useTablePagination();
const gapPager = useTablePagination();

function blankForm() {
  return {
    name: "", api_url: "", authMode: "token" as "token" | "password",
    api_token: "", api_user: "", api_password: "",
    enabled: true, verify_tls: true,
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
  try { rows.value = (await listZabbix()).items; }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}

function openCreate() {
  editing.value = null;
  form.value = blankForm();
  show.value = true;
}

function openEdit(r: ZabbixInstance) {
  editing.value = r;
  form.value = {
    ...blankForm(),
    name: r.name, api_url: r.api_url,
    authMode: r.has_api_token || !r.api_user ? "token" : "password",
    api_user: r.api_user ?? "",
    enabled: r.enabled, verify_tls: r.verify_tls,
    sync_interval_seconds: r.sync_interval_seconds,
    description: r.description ?? "",
    scope_subnet_ids: r.scope_subnet_ids ?? [],
  };
  show.value = true;
}

async function submit() {
  const f = form.value;
  const payload: Record<string, unknown> = {
    name: f.name, api_url: f.api_url,
    enabled: f.enabled, verify_tls: f.verify_tls,
    sync_interval_seconds: f.sync_interval_seconds,
    description: f.description || undefined,
    scope_subnet_ids: f.scope_subnet_ids,
  };
  if (f.authMode === "token") {
    if (f.api_token) payload.api_token = f.api_token;
  } else {
    payload.api_user = f.api_user;
    if (f.api_password) payload.api_password = f.api_password;
  }
  try {
    if (editing.value) await updateZabbix(editing.value.id, payload);
    else await createZabbix(payload as never);
    show.value = false;
    msg.success(t("common.ok"));
    await refresh();
  } catch (e) { msg.error(apiErrMsg(e)); }
}

async function test(r: ZabbixInstance) {
  try {
    health.value = await testZabbix(r.id);
    healthFor.value = r.name;
    healthOpen.value = true;
  } catch (e) { msg.error(apiErrMsg(e)); }
}

async function sync(r: ZabbixInstance) {
  try {
    const job = await syncZabbix(r.id);
    trackTask(job.task_id, { onDone: () => void refresh() });
  } catch (e) { msg.error(apiErrMsg(e)); }
}

async function del(id: string) {
  try { await deleteZabbix(id); await refresh(); }
  catch (e) { msg.error(apiErrMsg(e)); }
}

const currentId = ref("");
async function openHosts(r: ZabbixInstance) {
  currentId.value = r.id;
  hostsFor.value = r.name;
  hostQuery.value = "";
  hostsOpen.value = true;
  await loadHosts();
}
async function loadHosts() {
  hostsLoading.value = true;
  try { hosts.value = (await listZabbixHosts(currentId.value, hostQuery.value || undefined)).items; }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { hostsLoading.value = false; }
}

async function openGap(r: ZabbixInstance) {
  currentId.value = r.id;
  hostsFor.value = r.name;
  gapScope.value = (r.scope_subnet_ids?.length ?? 0) > 0 ? "instance" : "global";
  gapOpen.value = true;
  try {
    // 有設限定範圍就只問那些網段，否則是全站（與後端的 scope 語意一致）
    gap.value = (await zabbixCoverageGap(r.id, r.scope_subnet_ids ?? undefined)).items;
  } catch (e) { msg.error(apiErrMsg(e)); }
}

function iconAction(icon: unknown, label: string, onClick: () => void, type?: string) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type: type as never,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon as never)) }),
    default: () => label,
  });
}

const allCols = computed<DataTableColumns<ZabbixInstance>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 150, ellipsis: { tooltip: true } },
  { title: "API URL", key: "api_url", minWidth: 200, ellipsis: { tooltip: true } },
  {
    title: t("common.status"), key: "enabled", width: 100,
    render: (r) => h(NTag, { type: r.enabled ? "success" : "default", size: "small" },
      () => r.enabled ? t("common.enabled") : t("common.disabled")),
  },
  {
    title: t("zabbix.auth"), key: "auth", width: 120,
    render: (r) => h(NTag, { size: "small", type: "info", bordered: false },
      () => r.has_api_token ? t("zabbix.auth_token") : t("zabbix.auth_password")),
  },
  { title: t("cols.last_sync"), key: "last_sync_at", width: 165,
    render: (r) => fmtDateTime(r.last_sync_at) },
  {
    title: t("cols.last_error"), key: "last_error", minWidth: 150,
    ellipsis: { tooltip: true }, render: (r) => r.last_error ?? "—",
  },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 210,
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openEdit(r)),
      iconAction(TestIcon, t("zabbix.test"), () => test(r)),
      iconAction(SearchIcon, t("zabbix.hosts"), () => openHosts(r)),
      iconAction(LibreNMSIcon, t("zabbix.gap"), () => openGap(r)),
      iconAction(SyncIcon, t("common.pull"), () => sync(r), "primary"),
      h(NPopconfirm, { onPositiveClick: () => del(r.id) }, {
        trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]));
const cols = computed<DataTableColumns<ZabbixInstance>>(() =>
  orderColumns(allCols.value.filter((c) => vis.value.includes(String((c as { key?: string }).key)))),
);

const hostCols = computed<DataTableColumns<ZabbixHost>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 160, ellipsis: { tooltip: true },
    render: (r) => r.name || r.host },
  { title: "IP", key: "ip", width: 140, render: (r) => r.ip ?? r.dns ?? "—" },
  {
    title: t("common.status"), key: "status", width: 110,
    render: (r) => h(NTag, {
      size: "small", bordered: false,
      type: r.status === "monitored" ? "success" : "default",
    }, () => r.status === "monitored" ? t("zabbix.monitored") : t("zabbix.unmonitored")),
  },
  {
    title: t("zabbix.available"), key: "available", width: 110,
    render: (r) => h(NTag, {
      size: "small", bordered: false,
      type: r.available === "up" ? "success" : r.available === "down" ? "error" : "default",
    }, () => r.available ?? "—"),
  },
  {
    title: t("zabbix.maintenance"), key: "maintenance", width: 100,
    render: (r) => r.maintenance ? t("common.yes") : "—",
  },
  {
    title: t("zabbix.linked"), key: "ip_address_id", width: 100,
    render: (r) => r.ip_address_id ? t("common.yes") : "—",
  },
  { title: t("zabbix.groups"), key: "groups", minWidth: 160, ellipsis: { tooltip: true },
    render: (r) => (r.groups ?? []).join(", ") || "—" },
]));

const gapCols = computed<DataTableColumns<ZabbixGapRow>>(() => autoSort([
  { title: "IP", key: "ip", width: 160 },
  { title: t("cols.hostname"), key: "hostname", minWidth: 200, ellipsis: { tooltip: true } },
]));

onMounted(() => { void refresh(); void loadSubnetOptions(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><LibreNMSIcon /></n-icon>
        <span>{{ t("zabbix.title") }}</span>
      </n-space>
    </template>

    <n-alert type="info" :bordered="false" style="margin-bottom: 12px">
      {{ t("zabbix.intro") }}
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
      <ColumnPicker :all="picker" :visible="vis" @update:visible="setVis" @reset="resetVis"
                    :order="order" @update:order="setOrder" />
    </n-space>

    <n-data-table :columns="cols" :data="rows" :loading="loading" :bordered="false"
                  :scroll-x="1200" :row-key="(r: ZabbixInstance) => r.id" />

    <!-- 新增 / 編輯 -->
    <n-modal v-model:show="show" preset="card"
             :title="editing ? t('common.edit') : `${t('common.create')} — ${t('zabbix.title')}`"
             style="width: 580px">
      <n-form>
        <n-form-item :label="t('common.name')"><n-input v-model:value="form.name" /></n-form-item>
        <n-form-item label="API URL">
          <n-input v-model:value="form.api_url" placeholder="https://zabbix.example.net" />
        </n-form-item>
        <div style="margin: -8px 0 12px">
          <span style="font-size: 11px; opacity: .7">{{ t("zabbix.url_hint") }}</span>
        </div>
        <n-form-item :label="t('zabbix.auth')">
          <n-radio-group v-model:value="form.authMode">
            <n-radio value="token">{{ t("zabbix.auth_token") }}</n-radio>
            <n-radio value="password">{{ t("zabbix.auth_password") }}</n-radio>
          </n-radio-group>
        </n-form-item>
        <n-form-item v-if="form.authMode === 'token'"
                     :label="editing ? t('zabbix.token_keep') : t('zabbix.token')">
          <n-input v-model:value="form.api_token" type="password" show-password-on="click" />
        </n-form-item>
        <template v-else>
          <n-form-item :label="t('common.username')">
            <n-input v-model:value="form.api_user" />
          </n-form-item>
          <n-form-item :label="editing ? t('zabbix.password_keep') : t('common.password')">
            <n-input v-model:value="form.api_password" type="password" show-password-on="click" />
          </n-form-item>
        </template>
        <n-form-item :label="t('firewall_admin.verify_tls')">
          <n-switch v-model:value="form.verify_tls" />
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

    <!-- 測試連線 -->
    <n-modal v-model:show="healthOpen" preset="card"
             :title="`${t('zabbix.test')} — ${healthFor}`" style="width: 460px">
      <template v-if="health">
        <n-space vertical :size="10">
          <div><strong>{{ t("zabbix.version") }}：</strong>{{ health.version ?? "—" }}</div>
          <n-alert :type="health.hosts_readable ? 'success' : 'warning'" :bordered="false">
            <template v-if="health.hosts_readable">
              {{ t("zabbix.hosts_readable", { n: health.host_count ?? 0 }) }}
            </template>
            <template v-else>{{ health.error ?? t("errors.server") }}</template>
          </n-alert>
        </n-space>
      </template>
    </n-modal>

    <!-- 主機清單 -->
    <n-modal v-model:show="hostsOpen" preset="card"
             :title="`${t('zabbix.hosts')} — ${hostsFor}`" style="width: 900px">
      <n-space style="margin-bottom: 10px">
        <n-input v-model:value="hostQuery" clearable style="width: 240px"
                 :placeholder="t('common.search')" @keyup.enter="loadHosts" />
        <n-button @click="loadHosts" :loading="hostsLoading">
          <template #icon><n-icon><SearchIcon /></n-icon></template>
          {{ t("common.search") }}
        </n-button>
      </n-space>
      <n-data-table :columns="hostCols" :data="hosts" :loading="hostsLoading" :bordered="false"
                    :pagination="hostPager" :scroll-x="900"
                    :row-key="(r: ZabbixHost) => r.id" />
    </n-modal>

    <!-- 監控涵蓋缺口 -->
    <n-modal v-model:show="gapOpen" preset="card"
             :title="`${t('zabbix.gap')} — ${hostsFor}`" style="width: 720px">
      <n-alert :type="gap.length ? 'warning' : 'success'" :bordered="false" style="margin-bottom: 10px">
        {{ gapScope === "instance" ? t("zabbix.gap_scoped", { n: gap.length })
                                   : t("zabbix.gap_global", { n: gap.length }) }}
      </n-alert>
      <n-data-table :columns="gapCols" :data="gap" :bordered="false"
                    :pagination="gapPager"
                    :row-key="(r: ZabbixGapRow) => r.ip_address_id" />
    </n-modal>
  </n-card>
</template>
