<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { fmtDateTime } from "@/utils/datetime";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NButton, NIcon, NTag, NInput, NTooltip,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { RefreshIcon, LockIcon, SearchIcon, WarnIcon, UpgradeIcon } from "@/icons";
import { autoSort } from "@/composables/useTableSort";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { useTablePagination } from "@/composables/useTablePagination";
import ColumnPicker from "@/components/ColumnPicker.vue";
import { getCertAgentStatus, type CertStatusDeployment } from "@/api/certificates";

const { t } = useI18n();
const msg = useMessage();
const loading = ref(false);
const filter = ref("");
const pg = useTablePagination();

interface Row extends CertStatusDeployment {
  agent: string;
  last_seen_at: string | null;
  last_source_ip: string | null;
  recent_source_ips: string[];
  multi_source_recent: boolean;
  agent_version: string | null;
  server_agent_version: string | null;
}
const rows = ref<Row[]>([]);

async function load() {
  loading.value = true;
  try {
    const data = await getCertAgentStatus();
    const flat: Row[] = [];
    for (const a of data.agents) {
      const base = {
        agent: a.agent, last_seen_at: a.last_seen_at, last_source_ip: a.last_source_ip,
        recent_source_ips: a.recent_source_ips ?? [], multi_source_recent: a.multi_source_recent ?? false,
        agent_version: a.agent_version, server_agent_version: a.server_agent_version,
      };
      if (a.deployments.length === 0) {
        flat.push({
          ...base,
          cert: null, profile: null, status: null, applied_at: null, dry_run: null,
          reported_fingerprint: null, current_fingerprint: null, up_to_date: false,
          not_before: null, not_after: null, days_remaining: null,
        });
      } else {
        for (const d of a.deployments) flat.push({ ...d, ...base });
      }
    }
    rows.value = flat;
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.server"));
  } finally {
    loading.value = false;
  }
}
onMounted(load);

const rowsFiltered = computed(() => {
  const q = filter.value.trim().toLowerCase();
  if (!q) return rows.value;
  return rows.value.filter((r) =>
    r.agent.toLowerCase().includes(q)
    || (r.cert ?? "").toLowerCase().includes(q)
    || (r.profile ?? "").toLowerCase().includes(q)
    || (r.last_source_ip ?? "").includes(q));
});

function expiryCell(r: Row) {
  if (r.days_remaining === null || r.not_after === null) return "—";
  const d = r.days_remaining;
  const type = d < 0 ? "error" : d <= 21 ? "warning" : "success";
  const label = d < 0 ? t("certStatus.expired") : t("certStatus.days_left", { n: d });
  return h(NTag, { size: "small", type }, () => label);
}

const STATUS_KEYS = [
  "agent", "source_ip", "version", "cert", "profile", "status",
  "updated", "valid_from", "expires", "remaining",
];
const prefs = useColumnPrefs("cert_status", STATUS_KEYS, STATUS_KEYS);
const pickerItems = computed(() => [
  { key: "agent", label: t("certStatus.col_agent") },
  { key: "source_ip", label: t("cols.source_ip") },
  { key: "version", label: t("cols.version") },
  { key: "cert", label: t("certStatus.col_cert") },
  { key: "profile", label: t("certStatus.col_profile") },
  { key: "status", label: t("certStatus.col_status") },
  { key: "updated", label: t("certStatus.col_updated") },
  { key: "valid_from", label: t("certStatus.col_valid_from") },
  { key: "expires", label: t("certStatus.col_expires") },
  { key: "remaining", label: t("certStatus.col_remaining") },
]);

const colsAll = computed<DataTableColumns<Row>>(() => autoSort([
  { title: t("certStatus.col_agent"), key: "agent", minWidth: 130 },
  { title: t("cols.source_ip"), key: "source_ip", minWidth: 150,
    render: (r) => r.last_source_ip
      ? h("div", { style: "display:flex;align-items:center;gap:4px;flex-wrap:wrap" }, [
          h("span", { style: "font-family:monospace" }, r.last_source_ip),
          r.multi_source_recent
            ? h(NTooltip, null, {
                trigger: () => h(NTag, { size: "tiny", type: "warning", round: true, bordered: false },
                  { default: () => t("certs.multi_ip_badge"), icon: () => h(NIcon, { component: WarnIcon }) }),
                default: () => t("certs.multi_ip_hint", { ips: r.recent_source_ips.join("、") }),
              })
            : null,
        ])
      : "—" },
  { title: t("cols.version"), key: "version", width: 120,
    render: (r) => {
      if (!r.agent_version) return "—";
      const outdated = !!r.server_agent_version && r.agent_version !== r.server_agent_version;
      const tag = h(NTag, { size: "small", type: outdated ? "warning" : "success", bordered: false },
        () => `v${r.agent_version}`);
      if (!outdated) return tag;
      return h("div", { style: "display:flex;align-items:center;gap:4px;white-space:nowrap" }, [
        tag,
        h(NTooltip, null, {
          trigger: () => h(NIcon, { component: UpgradeIcon, size: 16,
            style: "color:var(--warning-color,#f0a020);cursor:help;flex-shrink:0" }),
          default: () => t("scan_agent.outdated_hint", { v: r.server_agent_version }),
        }),
      ]);
    } },
  { title: t("certStatus.col_cert"), key: "cert", minWidth: 120, render: (r) => r.cert ?? "—" },
  { title: t("certStatus.col_profile"), key: "profile", width: 90, render: (r) => r.profile ?? "—" },
  { title: t("certStatus.col_status"), key: "status", width: 120, render: (r) => {
    if (!r.cert) return h(NTag, { size: "small" }, () => t("certStatus.no_report"));
    if (r.up_to_date) return h(NTag, { size: "small", type: "success" }, () => t("certStatus.up_to_date"));
    return h(NTag, { size: "small", type: "warning" }, () => t("certStatus.drift"));
  } },
  { title: t("certStatus.col_updated"), key: "updated", minWidth: 160,
    sorter: (a, b) => (a.last_seen_at ?? "").localeCompare(b.last_seen_at ?? ""),
    render: (r) => r.last_seen_at ? fmtDateTime(r.last_seen_at) : "—" },
  { title: t("certStatus.col_valid_from"), key: "valid_from", width: 110,
    render: (r) => r.not_before ? fmtDateTime(r.not_before).slice(0, 10) : "—" },
  { title: t("certStatus.col_expires"), key: "expires", width: 110,
    render: (r) => r.not_after ? fmtDateTime(r.not_after).slice(0, 10) : "—" },
  { title: t("certStatus.col_remaining"), key: "remaining", width: 110,
    sorter: (a, b) => (a.days_remaining ?? 1e9) - (b.days_remaining ?? 1e9), render: expiryCell },
]));
const cols = computed<DataTableColumns<Row>>(() =>
  colsAll.value.filter((c: any) => prefs.visibleKeys.value.includes(c.key)));
</script>

<template>
  <n-card :bordered="false">
    <template #header>
      <n-space align="center" :size="8"><n-icon :component="LockIcon" /> {{ t("nav.cert_status") }}</n-space>
    </template>
    <n-space justify="space-between" style="margin-bottom: 10px">
      <n-input v-model:value="filter" clearable
               :placeholder="t('certStatus.filter')" style="width: 240px">
        <template #prefix><n-icon :component="SearchIcon" /></template>
      </n-input>
      <n-space :size="8">
        <ColumnPicker :all="pickerItems" :visible="prefs.visibleKeys.value"
                      @update:visible="prefs.setVisible" @reset="prefs.reset" />
        <n-button size="small" quaternary @click="load">
          <template #icon><n-icon :component="RefreshIcon" /></template>{{ t("common.refresh") }}
        </n-button>
      </n-space>
    </n-space>
    <n-data-table :columns="cols" :data="rowsFiltered" :loading="loading" size="small" :scroll-x="1100"
                  :pagination="pg" :row-key="(r:Row) => r.agent + (r.cert ?? '') + (r.profile ?? '')" />
  </n-card>
</template>
