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
import ExportButton from "@/components/ExportButton.vue";
import { getCertAgentStatus, type CertStatusDeployment } from "@/api/certificates";

const { t } = useI18n();
const msg = useMessage();
const loading = ref(false);
const filter = ref("");
const pg = useTablePagination();

// 一個代理一列；它部署的多個憑證 / 服務彙整在欄位內（不再每個 deployment 各一列）。
interface Row {
  agent: string;
  last_seen_at: string | null;
  last_source_ip: string | null;
  recent_source_ips: string[];
  multi_source_recent: boolean;
  agent_version: string | null;
  server_agent_version: string | null;
  certs: string;
  profiles: string;
  status: "up_to_date" | "drift" | "no_report";
  not_before: string | null;
  not_after: string | null;
  days_remaining: number | null;
  deployments: CertStatusDeployment[];
}
const rows = ref<Row[]>([]);

async function load() {
  loading.value = true;
  try {
    const data = await getCertAgentStatus();
    rows.value = data.agents.map((a) => {
      const deps = (a.deployments ?? []).filter((d) => d.cert);
      let status: Row["status"] = "no_report";
      let certs = "", profiles = "";
      let not_after: string | null = null, not_before: string | null = null, days: number | null = null;
      if (deps.length) {
        // 健康＝指紋相符且回報 ok；任一失敗/未套用 → drift
        status = deps.every((d) => d.up_to_date && d.status === "ok") ? "up_to_date" : "drift";
        certs = [...new Set(deps.map((d) => d.cert).filter(Boolean))].join("、");
        profiles = [...new Set(deps.map((d) => d.profile).filter(Boolean))].join(", ");
        const withExp = deps.filter((d) => d.not_after).sort((x, y) => (x.not_after! < y.not_after! ? -1 : 1));
        if (withExp.length) {
          not_after = withExp[0].not_after; not_before = withExp[0].not_before; days = withExp[0].days_remaining;
        }
      }
      return {
        agent: a.agent, last_seen_at: a.last_seen_at, last_source_ip: a.last_source_ip,
        recent_source_ips: a.recent_source_ips ?? [], multi_source_recent: a.multi_source_recent ?? false,
        agent_version: a.agent_version, server_agent_version: a.server_agent_version,
        certs, profiles, status, not_after, not_before, days_remaining: days,
        deployments: a.deployments ?? [],
      };
    });
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
    r.agent.toLowerCase().includes(q) || r.certs.toLowerCase().includes(q)
    || r.profiles.toLowerCase().includes(q) || (r.last_source_ip ?? "").includes(q));
});

function statusLabel(s: Row["status"]) {
  return s === "up_to_date" ? t("certStatus.up_to_date") : s === "drift" ? t("certStatus.drift") : t("certStatus.no_report");
}
function depDetail(r: Row) {
  // tooltip：逐一列出 cert / profile / 是否最新 / 剩餘天數
  return h("div", { style: "display:flex;flex-direction:column;gap:2px" },
    r.deployments.filter((d) => d.cert).map((d) =>
      h("div", `${d.cert} / ${d.profile} — ${d.up_to_date ? t("certStatus.up_to_date") : t("certStatus.drift")}`
        + (d.days_remaining != null ? `（${t("certStatus.days_left", { n: d.days_remaining })}）` : ""))));
}
function expiryCell(r: Row) {
  if (r.days_remaining === null || r.not_after === null) return "—";
  const d = r.days_remaining;
  const type = d < 0 ? "error" : d <= 21 ? "warning" : "success";
  const label = d < 0 ? t("certStatus.expired") : t("certStatus.days_left", { n: d });
  return h(NTag, { size: "small", type }, () => label);
}

const STATUS_KEYS = [
  "agent", "source_ip", "version", "certs", "profiles", "status",
  "updated", "valid_from", "expires", "remaining",
];
const prefs = useColumnPrefs("cert_status", STATUS_KEYS, STATUS_KEYS);
const pickerItems = computed(() => [
  { key: "agent", label: t("certStatus.col_agent") },
  { key: "source_ip", label: t("cols.source_ip") },
  { key: "version", label: t("cols.version") },
  { key: "certs", label: t("certStatus.col_cert") },
  { key: "profiles", label: t("certStatus.col_profile") },
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
  { title: t("certStatus.col_cert"), key: "certs", minWidth: 130, render: (r) => r.certs || "—" },
  { title: t("certStatus.col_profile"), key: "profiles", minWidth: 110, render: (r) => r.profiles || "—" },
  { title: t("certStatus.col_status"), key: "status", width: 120, render: (r) => {
    const type = r.status === "up_to_date" ? "success" : r.status === "drift" ? "warning" : "default";
    const tag = h(NTag, { size: "small", type }, () => statusLabel(r.status));
    if (r.status === "no_report") return tag;
    return h(NTooltip, null, {
      trigger: () => h("span", { style: "cursor:help" }, tag),
      default: () => depDetail(r),
    });
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

// 匯出：純字串欄位（避免匯出 render 出來的物件）
const exportCols = computed(() => pickerItems.value);
const exportRows = computed(() => rowsFiltered.value.map((r) => ({
  agent: r.agent, source_ip: r.last_source_ip ?? "",
  version: r.agent_version ? `v${r.agent_version}` : "",
  certs: r.certs, profiles: r.profiles, status: statusLabel(r.status),
  updated: r.last_seen_at ? fmtDateTime(r.last_seen_at) : "",
  valid_from: r.not_before ? fmtDateTime(r.not_before).slice(0, 10) : "",
  expires: r.not_after ? fmtDateTime(r.not_after).slice(0, 10) : "",
  remaining: r.days_remaining != null ? String(r.days_remaining) : "",
})));
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
        <ExportButton :columns="exportCols" :rows="exportRows" filename="cert-distribution-status"
                      :title="t('nav.cert_status')" />
        <ColumnPicker :all="pickerItems" :visible="prefs.visibleKeys.value"
                      @update:visible="prefs.setVisible" @reset="prefs.reset" />
        <n-button size="small" quaternary @click="load">
          <template #icon><n-icon :component="RefreshIcon" /></template>{{ t("common.refresh") }}
        </n-button>
      </n-space>
    </n-space>
    <n-data-table :columns="cols" :data="rowsFiltered" :loading="loading" size="small" :scroll-x="1100"
                  :pagination="pg" :row-key="(r:Row) => r.agent" />
  </n-card>
</template>
