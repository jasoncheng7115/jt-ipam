<script setup lang="ts">
/**
 * Checkmk 整合 —— 跟 Zabbix 同一個定位（監控面補充），版面也照 Zabbix 頁。
 * 拉的是主機↔IP 對應、存活狀態、停機排程，以及「監控涵蓋缺口」。
 * 只讀監控端點，內建 guest 角色的自動化帳號就夠；不會更動 Checkmk 任何設定。
 */
import { computed, h, onMounted, ref } from "vue";
import { fmtDateTime } from "@/utils/datetime";
import { useI18n } from "vue-i18n";
import { trackTask } from "@/composables/useTaskTracker";
import ScopeOverlapWarning from "@/components/ScopeOverlapWarning.vue";
import {
  NCard, NDataTable, NSpace, NButton, NTag, NIcon, NTooltip, NAlert, NModal, NForm,
  NFormItem, NInput, NInputNumber, NSwitch, NSelect, NPopconfirm,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { listSubnets } from "@/api/subnets";
import {
  listCheckmk, createCheckmk, updateCheckmk, deleteCheckmk, testCheckmk, syncCheckmk,
  listCheckmkHosts, checkmkCoverageGap,
  type CheckmkInstance, type CheckmkHealth, type CheckmkHost, type CheckmkGapRow,
} from "@/api/checkmk";
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

const COLS = ["name", "api_url", "enabled", "api_user", "last_sync_at", "last_error", "actions"];
const { visibleKeys: vis, setVisible: setVis, reset: resetVis, order, setOrder, orderColumns } =
  useColumnPrefs("checkmk", COLS, COLS);
const picker = computed(() => [
  { key: "name", label: t("cols.name") },
  { key: "api_url", label: t("checkmk.site_url") },
  { key: "enabled", label: t("cols.status") },
  { key: "api_user", label: t("checkmk.automation_user") },
  { key: "last_sync_at", label: t("cols.last_sync") },
  { key: "last_error", label: t("cols.last_error") },
  { key: "actions", label: t("cols.actions") },
]);

const rows = ref<CheckmkInstance[]>([]);
const loading = ref(false);
const show = ref(false);
const saving = ref(false);
const editing = ref<CheckmkInstance | null>(null);

const health = ref<CheckmkHealth | null>(null);
const healthFor = ref("");
const healthOpen = ref(false);
const testingId = ref("");

const hostsOpen = ref(false);
const hostsFor = ref("");
const hosts = ref<CheckmkHost[]>([]);
const hostQuery = ref("");
const hostsLoading = ref(false);
const gapOpen = ref(false);
const gap = ref<CheckmkGapRow[]>([]);
const gapScope = ref<"instance" | "global">("instance");

const hostPager = useTablePagination();
const gapPager = useTablePagination();

function blankForm() {
  return {
    name: "", api_url: "", api_user: "", api_secret: "",
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
      label: s.description ? `${s.cidr} ${s.description}` : s.cidr, value: s.id }));
  } catch { /* silent */ }
}

async function refresh() {
  loading.value = true;
  try { rows.value = (await listCheckmk()).items; }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}

function openCreate() {
  editing.value = null;
  form.value = blankForm();
  show.value = true;
}

function openEdit(r: CheckmkInstance) {
  editing.value = r;
  form.value = {
    ...blankForm(),
    name: r.name, api_url: r.api_url, api_user: r.api_user,
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
    name: f.name, api_url: f.api_url, api_user: f.api_user,
    enabled: f.enabled, verify_tls: f.verify_tls,
    sync_interval_seconds: f.sync_interval_seconds,
    description: f.description ?? "",
    scope_subnet_ids: f.scope_subnet_ids,
  };
  if (f.api_secret) payload.api_secret = f.api_secret;
  saving.value = true;
  try {
    if (editing.value) await updateCheckmk(editing.value.id, payload);
    else await createCheckmk(payload as never);
    show.value = false;
    msg.success(t("common.ok"));
    await refresh();
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { saving.value = false; }
}

async function test(r: CheckmkInstance) {
  testingId.value = r.id;
  try {
    health.value = await testCheckmk(r.id);
    healthFor.value = r.name;
    healthOpen.value = true;
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { testingId.value = ""; }
}

async function sync(r: CheckmkInstance) {
  try {
    const job = await syncCheckmk(r.id);
    trackTask(job.task_id, { onDone: () => void refresh() });
  } catch (e) { msg.error(apiErrMsg(e)); }
}

async function del(id: string) {
  try { await deleteCheckmk(id); await refresh(); }
  catch (e) { msg.error(apiErrMsg(e)); }
}

const currentId = ref("");
async function openHosts(r: CheckmkInstance) {
  currentId.value = r.id;
  hostsFor.value = r.name;
  hostQuery.value = "";
  hostsOpen.value = true;
  await loadHosts();
}
async function loadHosts() {
  hostsLoading.value = true;
  try { hosts.value = (await listCheckmkHosts(currentId.value, hostQuery.value || undefined)).items; }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { hostsLoading.value = false; }
}

async function openGap(r: CheckmkInstance) {
  currentId.value = r.id;
  hostsFor.value = r.name;
  gapScope.value = (r.scope_subnet_ids?.length ?? 0) > 0 ? "instance" : "global";
  gapOpen.value = true;
  try {
    gap.value = (await checkmkCoverageGap(r.id, r.scope_subnet_ids ?? undefined)).items;
  } catch (e) { msg.error(apiErrMsg(e)); }
}

function iconAction(icon: unknown, label: string, onClick: () => void, type?: string, testid?: string,
                    loading = false) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type: type as never, loading,
      disabled: loading, "data-testid": testid,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon as never)) }),
    default: () => label,
  });
}

const STATE_TAG: Record<string, "success" | "error" | "warning" | "default"> = {
  up: "success", down: "error", unreachable: "warning",
};

const allCols = computed<DataTableColumns<CheckmkInstance>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 150, ellipsis: { tooltip: true } },
  { title: t("checkmk.site_url"), key: "api_url", minWidth: 220, ellipsis: { tooltip: true } },
  {
    title: t("common.status"), key: "enabled", width: 100,
    render: (r) => h(NTag, { type: r.enabled ? "success" : "default", size: "small" },
      () => r.enabled ? t("common.enabled") : t("common.disabled")),
  },
  { title: t("checkmk.automation_user"), key: "api_user", width: 150, ellipsis: { tooltip: true } },
  { title: t("cols.last_sync"), key: "last_sync_at", width: 165,
    render: (r) => fmtDateTime(r.last_sync_at) },
  {
    title: t("cols.last_error"), key: "last_error", minWidth: 150,
    ellipsis: { tooltip: true }, render: (r) => r.last_error ?? "—",
  },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 210,
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openEdit(r), "success", "cmk-edit"),
      iconAction(TestIcon, t("checkmk.test"), () => test(r), undefined, "cmk-test", testingId.value === r.id),
      iconAction(SearchIcon, t("checkmk.hosts"), () => openHosts(r), undefined, "cmk-hosts"),
      iconAction(LibreNMSIcon, t("checkmk.gap"), () => openGap(r), undefined, "cmk-gap"),
      iconAction(SyncIcon, t("common.pull"), () => sync(r), "primary", "cmk-sync"),
      h(NPopconfirm, { onPositiveClick: () => del(r.id) }, {
        trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error", "cmk-delete"),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]));
const cols = computed<DataTableColumns<CheckmkInstance>>(() =>
  orderColumns(allCols.value.filter((c) => vis.value.includes(String((c as { key?: string }).key)))),
);

const hostCols = computed<DataTableColumns<CheckmkHost>>(() => autoSort([
  { title: t("checkmk.host_name"), key: "host_name", minWidth: 160, ellipsis: { tooltip: true } },
  { title: t("checkmk.alias"), key: "alias", minWidth: 140, ellipsis: { tooltip: true },
    render: (r) => r.alias && r.alias !== r.host_name ? r.alias : "—" },
  { title: "IP", key: "ip", width: 150, ellipsis: { tooltip: true },
    render: (r) => (r.addresses?.length ? r.addresses.join(", ") : null) ?? r.ip ?? "—" },
  {
    title: t("checkmk.state"), key: "state", width: 110,
    render: (r) => h(NTag, { size: "small", bordered: false, type: STATE_TAG[r.state ?? ""] ?? "default" },
      () => t(`checkmk.state_${r.state ?? "unknown"}`)),
  },
  {
    title: t("checkmk.downtime"), key: "downtime", width: 100,
    render: (r) => r.downtime ? t("common.yes") : "—",
  },
  {
    title: t("checkmk.linked"), key: "ip_address_id", width: 100,
    render: (r) => r.ip_address_id ? t("common.yes") : "—",
  },
  { title: t("checkmk.folder"), key: "folder", width: 120, ellipsis: { tooltip: true },
    render: (r) => r.folder ?? "—" },
  { title: t("checkmk.site"), key: "site", width: 100, render: (r) => r.site ?? "—" },
  { title: t("checkmk.last_check"), key: "last_check", width: 165,
    render: (r) => fmtDateTime(r.last_check) },
  { title: t("checkmk.output"), key: "output", minWidth: 200, ellipsis: { tooltip: true },
    render: (r) => r.output ?? "—" },
]));

const gapCols = computed<DataTableColumns<CheckmkGapRow>>(() => autoSort([
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
        <span>{{ t("checkmk.title") }}</span>
      </n-space>
    </template>

    <n-alert type="info" :bordered="false" style="margin-bottom: 12px">
      {{ t("checkmk.intro") }}
    </n-alert>

    <n-space style="margin-bottom: 12px">
      <n-button @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
      <n-button type="primary" data-testid="cmk-create" @click="openCreate">
        <template #icon><n-icon><PlusIcon /></n-icon></template>
        {{ t("common.create") }}
      </n-button>
      <ColumnPicker :all="picker" :visible="vis" @update:visible="setVis" @reset="resetVis"
                    :order="order" @update:order="setOrder" />
    </n-space>

    <n-data-table :columns="cols" :data="rows" :loading="loading" :bordered="false"
                  :scroll-x="1200" :row-key="(r: CheckmkInstance) => r.id" data-testid="cmk-table" />

    <!-- 新增 / 編輯 -->
    <n-modal v-model:show="show" preset="card"
             :title="editing ? t('common.edit') : `${t('common.create')} ${t('checkmk.title')}`"
             style="width: 580px">
      <n-form>
        <n-form-item :label="t('common.name')"><n-input v-model:value="form.name" data-testid="cmk-name" /></n-form-item>
        <n-form-item :label="t('checkmk.site_url')">
          <n-input v-model:value="form.api_url" placeholder="https://checkmk.example.net/mysite" data-testid="cmk-url" />
        </n-form-item>
        <div style="margin: -8px 0 12px">
          <span style="font-size: 11px; opacity: .7">{{ t("checkmk.url_hint") }}</span>
        </div>
        <n-form-item :label="t('checkmk.automation_user')">
          <n-input v-model:value="form.api_user" placeholder="jt-ipam" data-testid="cmk-user" />
        </n-form-item>
        <n-form-item :label="editing ? t('checkmk.secret_keep') : t('checkmk.secret')">
          <n-input v-model:value="form.api_secret" type="password" show-password-on="click"
                   :placeholder="editing ? t('checkmk.secret_keep_ph') : ''" data-testid="cmk-secret" />
        </n-form-item>
        <div style="margin: -8px 0 12px">
          <span style="font-size: 11px; opacity: .7">{{ t("checkmk.user_hint") }}</span>
        </div>
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
        <n-button type="primary" :loading="saving" :disabled="saving" data-testid="cmk-save" @click="submit">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </n-space>
    </n-modal>

    <!-- 測試連線 -->
    <n-modal v-model:show="healthOpen" preset="card"
             :title="`${t('checkmk.test')} ${healthFor}`" style="width: 460px">
      <template v-if="health">
        <n-space vertical :size="10" data-testid="cmk-health">
          <div><strong>{{ t("checkmk.version") }}：</strong>{{ health.version ?? "—" }}
            <span v-if="health.edition">（{{ health.edition }}）</span></div>
          <div><strong>{{ t("checkmk.site") }}：</strong>{{ health.site ?? "—" }}</div>
          <n-alert :type="health.hosts_readable && health.host_count ? 'success' : 'warning'" :bordered="false">
            <template v-if="!health.hosts_readable">{{ health.error ?? t("errors.server") }}</template>
            <template v-else-if="health.host_count">{{ t("checkmk.hosts_readable", { n: health.host_count }) }}</template>
            <template v-else>{{ t("checkmk.hosts_none") }}</template>
          </n-alert>
        </n-space>
      </template>
    </n-modal>

    <!-- 主機清單 -->
    <n-modal v-model:show="hostsOpen" preset="card"
             :title="`${t('checkmk.hosts')} ${hostsFor}`" style="width: 1000px">
      <n-space style="margin-bottom: 10px">
        <n-input v-model:value="hostQuery" clearable style="width: 240px"
                 :placeholder="t('common.search')" @keyup.enter="loadHosts" />
        <n-button @click="loadHosts" :loading="hostsLoading">
          <template #icon><n-icon><SearchIcon /></n-icon></template>
          {{ t("common.search") }}
        </n-button>
      </n-space>
      <n-data-table :columns="hostCols" :data="hosts" :loading="hostsLoading" :bordered="false"
                    :pagination="hostPager" :scroll-x="1400"
                    :row-key="(r: CheckmkHost) => r.id" data-testid="cmk-hosts-table" />
    </n-modal>

    <!-- 監控涵蓋缺口 -->
    <n-modal v-model:show="gapOpen" preset="card"
             :title="`${t('checkmk.gap')} ${hostsFor}`" style="width: 720px">
      <n-alert :type="gap.length ? 'warning' : 'success'" :bordered="false" style="margin-bottom: 10px">
        {{ gapScope === "instance" ? t("checkmk.gap_scoped", { n: gap.length })
                                   : t("checkmk.gap_global", { n: gap.length }) }}
      </n-alert>
      <n-data-table :columns="gapCols" :data="gap" :bordered="false"
                    :pagination="gapPager"
                    :row-key="(r: CheckmkGapRow) => r.ip_address_id" />
    </n-modal>
  </n-card>
</template>
