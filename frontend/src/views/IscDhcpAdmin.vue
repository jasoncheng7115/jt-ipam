<script setup lang="ts">
/**
 * 獨立的 ISC DHCP Server（isc-dhcp-server，issue #45）。
 * ISC dhcpd 沒有能列出全部租約的 API，所以由裝在 DHCP 主機上的掃描代理讀本機 dhcpd.conf／dhcpd.leases、
 * 解析後回報。這一頁只管設定（哪台代理、多久回報一次）與顯示回報狀態；沒有「測試連線」「拉取」—— jt-ipam 不連過去。
 */
import { computed, h, onMounted, ref } from "vue";
import { fmtDateTime } from "@/utils/datetime";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import ScopeOverlapWarning from "@/components/ScopeOverlapWarning.vue";
import {
  NCard, NDataTable, NSpace, NButton, NTag, NIcon, NTooltip, NAlert,
  NModal, NForm, NFormItem, NInput, NInputNumber, NSwitch, NSelect, NCheckbox, NPopconfirm,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { listSubnets } from "@/api/subnets";
import { listScanAgents, type ScanAgent } from "@/api/phase3";
import {
  listIsc, createIsc, updateIsc, deleteIsc, type IscDhcpServer, type IscDhcpWrite, type IscFileStatus,
} from "@/api/dhcpStandalone";
import {
  IscDhcpIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SaveIcon, CancelIcon, GoToIcon,
} from "@/icons";
import { autoSort } from "@/composables/useTableSort";
import ColumnPicker from "@/components/ColumnPicker.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { apiErrMsg } from "@/api/client";

const { t } = useI18n();
const msg = useMessage();
const router = useRouter();

// 讀檔回報從代理 1.12.0 開始；舊版代理會自己更新，但更新前不會回報
const MIN_AGENT = [1, 12, 0];
function agentTooOld(v: string | null): boolean {
  if (!v) return false;
  const p = v.split(".").map((x) => Number.parseInt(x, 10) || 0);
  for (let i = 0; i < 3; i++) {
    if ((p[i] ?? 0) !== MIN_AGENT[i]) return (p[i] ?? 0) < MIN_AGENT[i];
  }
  return false;
}

const COLS = ["name", "agent", "enabled", "sync_flags", "files", "last_sync_at", "last_error", "actions"];
const { visibleKeys: vis, setVisible: setVis, reset: resetVis, order, setOrder, orderColumns } =
  useColumnPrefs("isc_dhcp", COLS, COLS);
const picker = computed(() => [
  { key: "name", label: t("cols.name") },
  { key: "agent", label: t("isc_dhcp.agent_col") },
  { key: "enabled", label: t("cols.status") },
  { key: "sync_flags", label: t("cols.sync_items") },
  { key: "files", label: t("isc_dhcp.files") },
  { key: "last_sync_at", label: t("isc_dhcp.last_report") },
  { key: "last_error", label: t("cols.last_error") },
  { key: "actions", label: t("cols.actions") },
]);

const rows = ref<IscDhcpServer[]>([]);
const agents = ref<ScanAgent[]>([]);
const loading = ref(false);
const show = ref(false);
const editing = ref<IscDhcpServer | null>(null);

function blankForm() {
  return {
    name: "", agent_id: null as string | null, enabled: true, sync_scopes: true, sync_leases: true,
    report_interval_seconds: 300, description: "", scope_subnet_ids: [] as string[],
  };
}
const form = ref(blankForm());

// 一台代理只對應一個來源：已經被別的來源用掉的反灰
const agentOptions = computed(() => agents.value.map((a) => {
  const owner = rows.value.find((r) => r.agent_id === a.id && r.id !== editing.value?.id);
  return {
    label: owner ? `${a.name}（${t("isc_dhcp.agent_used_by", { name: owner.name })}）` : a.name,
    value: a.id, disabled: !!owner,
  };
}));

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
    const [srv, ag] = await Promise.all([listIsc(), listScanAgents()]);
    rows.value = srv.items;
    agents.value = ag.items;
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}

function openCreate() {
  editing.value = null;
  form.value = blankForm();
  show.value = true;
}

function openEdit(r: IscDhcpServer) {
  editing.value = r;
  form.value = {
    name: r.name, agent_id: r.agent_id, enabled: r.enabled, sync_scopes: r.sync_scopes,
    sync_leases: r.sync_leases, report_interval_seconds: r.report_interval_seconds,
    description: r.description ?? "", scope_subnet_ids: r.scope_subnet_ids ?? [],
  };
  show.value = true;
}

async function submit() {
  const f = form.value;
  const payload: IscDhcpWrite = {
    name: f.name, agent_id: f.agent_id, enabled: f.enabled, sync_scopes: f.sync_scopes,
    sync_leases: f.sync_leases, report_interval_seconds: f.report_interval_seconds,
    description: f.description ?? "", scope_subnet_ids: f.scope_subnet_ids,
  };
  try {
    if (editing.value) await updateIsc(editing.value.id, payload);
    else await createIsc(payload);
    show.value = false;
    msg.success(t("common.ok"));
    await refresh();
  } catch (e) { msg.error(apiErrMsg(e)); }
}

async function del(id: string) {
  try { await deleteIsc(id); await refresh(); }
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

function fileTag(label: string, st: IscFileStatus | undefined) {
  if (!st) return h(NTag, { size: "tiny", bordered: false }, () => `${label} —`);
  return h(NTooltip, null, {
    trigger: () => h(NTag, { size: "tiny", type: st.ok ? "success" : "error", bordered: false,
                             "data-testid": `isc-file-${label}` },
      () => `${label} ${st.ok ? "✓" : "✗"}`),
    default: () => st.ok ? st.path : `${st.path}: ${st.error ?? ""}`,
  });
}

const allCols = computed<DataTableColumns<IscDhcpServer>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 150, ellipsis: { tooltip: true } },
  {
    title: t("isc_dhcp.agent_col"), key: "agent", minWidth: 200,
    render: (r) => {
      if (!r.agent_id) return h("span", { style: "opacity:.6" }, t("isc_dhcp.no_agent"));
      const parts: any[] = [h("span", null, r.agent_name ?? r.agent_id.slice(0, 8))];
      if (agentTooOld(r.agent_version)) {
        parts.push(h(NTag, { size: "tiny", type: "warning", bordered: false, style: "margin-left:6px" },
          () => t("isc_dhcp.agent_old", { v: r.agent_version })));
      }
      return h("span", null, parts);
    },
  },
  {
    title: t("common.status"), key: "enabled", width: 110,
    render: (r) => h(NTag, { type: r.enabled ? "success" : "default", size: "small" },
      () => r.enabled ? t("common.enabled") : t("common.disabled")),
  },
  {
    title: t("common.sync"), key: "sync_flags", width: 200,
    render: (r) => {
      const tags: any[] = [];
      if (r.sync_scopes) tags.push(h(NTag, { size: "tiny", type: "info", bordered: false }, () => t("isc_dhcp.scopes")));
      if (r.sync_leases) tags.push(h(NTag, { size: "tiny", type: "info", bordered: false }, () => t("isc_dhcp.leases")));
      return h(NSpace, { size: 4 }, () => tags);
    },
  },
  {
    title: t("isc_dhcp.files"), key: "files", width: 250,
    render: (r) => h(NSpace, { size: 4 }, () => [
      fileTag("dhcpd.conf", r.file_status?.conf), fileTag("dhcpd.leases", r.file_status?.leases)]),
  },
  { title: t("isc_dhcp.last_report"), key: "last_sync_at", width: 170, render: (r) => fmtDateTime(r.last_sync_at) },
  {
    title: t("cols.last_error"), key: "last_error", minWidth: 180,
    ellipsis: { tooltip: true }, render: (r) => r.last_error ?? "—",
  },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 110,
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openEdit(r)),
      h(NPopconfirm, { onPositiveClick: () => del(r.id) }, {
        trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]));
const cols = computed<DataTableColumns<IscDhcpServer>>(() =>
  orderColumns(allCols.value.filter((c: any) => vis.value.includes(c.key))),
);

onMounted(() => { void refresh(); void loadSubnetOptions(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><IscDhcpIcon /></n-icon>
        <span>{{ t("isc_dhcp.title") }}</span>
      </n-space>
    </template>

    <n-alert type="info" :bordered="false" style="margin-bottom: 12px">
      <div>{{ t("isc_dhcp.intro") }}</div>
      <ol class="isc-steps">
        <li>
          {{ t("isc_dhcp.step_agent") }}
          <n-button text type="primary" size="small" @click="router.push({ name: 'scan_agents' })"><template #icon><n-icon><GoToIcon /></n-icon></template>
            {{ t("isc_dhcp.go_agents") }}
          </n-button>
        </li>
        <li>{{ t("isc_dhcp.step_read") }} <code>/etc/dhcp/dhcpd.conf</code>、<code>/var/lib/dhcp/dhcpd.leases</code></li>
        <li>{{ t("isc_dhcp.step_paths") }} <code>JT_IPAM_DHCPD_CONF</code> / <code>JT_IPAM_DHCPD_LEASES</code></li>
        <li>{{ t("isc_dhcp.step_create") }}</li>
      </ol>
      <div class="isc-note">{{ t("isc_dhcp.privacy") }}</div>
    </n-alert>

    <n-space style="margin-bottom: 12px">
      <n-button @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
      <n-button type="primary" data-testid="isc-create" @click="openCreate">
        <template #icon><n-icon><PlusIcon /></n-icon></template>
        {{ t("common.create") }}
      </n-button>
      <ColumnPicker :all="picker" :visible="vis" @update:visible="setVis" @reset="resetVis"
                    :order="order" @update:order="setOrder" />
    </n-space>

    <n-data-table :columns="cols" :data="rows" :loading="loading" :bordered="false" :scroll-x="1360" />

    <n-modal v-model:show="show" preset="card"
             :title="editing ? t('common.edit') : `${t('common.create')} — ${t('isc_dhcp.title')}`"
             style="width: min(580px, 100%)">
      <n-form>
        <n-form-item :label="t('common.name')"><n-input v-model:value="form.name" data-testid="isc-name" /></n-form-item>
        <n-form-item :label="t('isc_dhcp.agent')">
          <div style="width: 100%">
            <n-select v-model:value="form.agent_id" :options="agentOptions" filterable clearable
                      :placeholder="t('isc_dhcp.agent_pick')" data-testid="isc-agent" />
            <div class="form-hint">{{ t("isc_dhcp.agent_hint") }}</div>
          </div>
        </n-form-item>
        <n-form-item :label="t('isc_dhcp.pull_what')">
          <n-space :size="20">
            <n-checkbox v-model:checked="form.sync_scopes">{{ t("isc_dhcp.scopes") }}</n-checkbox>
            <n-checkbox v-model:checked="form.sync_leases">{{ t("isc_dhcp.leases") }}</n-checkbox>
          </n-space>
        </n-form-item>
        <n-form-item :label="t('isc_dhcp.interval')">
          <n-input-number v-model:value="form.report_interval_seconds" :min="60" :max="86400" />
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
        <n-button type="primary" data-testid="isc-save" @click="submit">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </n-space>
    </n-modal>
  </n-card>
</template>

<style scoped>
.form-hint { font-size: 11.5px; opacity: .7; margin-top: 4px; line-height: 1.5; }
.isc-steps { margin: 6px 0 4px; padding-left: 20px; line-height: 1.8; }
.isc-steps code { font-size: 12px; }
.isc-note { font-size: 12px; opacity: .8; }
</style>
