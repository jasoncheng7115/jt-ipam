<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { fmtDateTime } from "@/utils/datetime";
import { useI18n } from "vue-i18n";
import { NCard, NDataTable, NSpace, NButton, NIcon, NTag, useMessage, type DataTableColumns } from "naive-ui";
import { RefreshIcon, LockIcon } from "@/icons";
import { getCertAgentStatus, type CertStatusDeployment } from "@/api/certificates";

const { t } = useI18n();
const msg = useMessage();
const loading = ref(false);

interface Row extends CertStatusDeployment {
  agent: string;
  last_seen_at: string | null;
  agent_version: string | null;
}
const rows = ref<Row[]>([]);

async function load() {
  loading.value = true;
  try {
    const data = await getCertAgentStatus();
    const flat: Row[] = [];
    for (const a of data.agents) {
      if (a.deployments.length === 0) {
        flat.push({
          agent: a.agent, last_seen_at: a.last_seen_at, agent_version: a.agent_version,
          cert: null, profile: null, status: null, applied_at: null, dry_run: null,
          reported_fingerprint: null, current_fingerprint: null, up_to_date: false,
          not_before: null, not_after: null, days_remaining: null,
        });
      } else {
        for (const d of a.deployments)
          flat.push({ ...d, agent: a.agent, last_seen_at: a.last_seen_at, agent_version: a.agent_version });
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

function expiryCell(r: Row) {
  if (r.days_remaining === null || r.not_after === null) return "—";
  const d = r.days_remaining;
  const type = d < 0 ? "error" : d <= 21 ? "warning" : "success";
  const label = d < 0 ? t("certStatus.expired") : t("certStatus.days_left", { n: d });
  return h(NTag, { size: "small", type }, () => label);
}

const cols = computed<DataTableColumns<Row>>(() => [
  { title: t("certStatus.col_agent"), key: "agent" },
  { title: t("certStatus.col_cert"), key: "cert", render: (r) => r.cert ?? "—" },
  { title: t("certStatus.col_profile"), key: "profile", width: 90, render: (r) => r.profile ?? "—" },
  { title: t("certStatus.col_status"), key: "status", width: 120, render: (r) => {
    if (!r.cert) return h(NTag, { size: "small" }, () => t("certStatus.no_report"));
    if (r.up_to_date) return h(NTag, { size: "small", type: "success" }, () => t("certStatus.up_to_date"));
    return h(NTag, { size: "small", type: "warning" }, () => t("certStatus.drift"));
  } },
  { title: t("certStatus.col_updated"), key: "last_seen_at",
    render: (r) => r.last_seen_at ? fmtDateTime(r.last_seen_at) : "—" },
  { title: t("certStatus.col_valid_from"), key: "not_before",
    render: (r) => r.not_before ? fmtDateTime(r.not_before).slice(0, 10) : "—" },
  { title: t("certStatus.col_expires"), key: "not_after",
    render: (r) => r.not_after ? fmtDateTime(r.not_after).slice(0, 10) : "—" },
  { title: t("certStatus.col_remaining"), key: "days_remaining", width: 110, render: expiryCell },
]);
</script>

<template>
  <n-card :bordered="false">
    <template #header>
      <n-space align="center" :size="8"><n-icon :component="LockIcon" /> {{ t("nav.cert_status") }}</n-space>
    </template>
    <n-space justify="end" style="margin-bottom: 10px">
      <n-button size="small" quaternary @click="load">
        <template #icon><n-icon :component="RefreshIcon" /></template>{{ t("common.refresh") }}
      </n-button>
    </n-space>
    <n-data-table :columns="cols" :data="rows" :loading="loading" size="small"
                  :row-key="(r:Row) => r.agent + (r.cert ?? '') + (r.profile ?? '')" />
  </n-card>
</template>
