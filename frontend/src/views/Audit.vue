<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard,
  NDataTable,
  NSpace,
  NInput,
  NButton,
  NTag,
  NPopover,
  useMessage,
  type DataTableColumns,
} from "naive-ui";
import { listAudit, verifyAuditChain, type AuditLog } from "@/api/admin";

const { t } = useI18n();
const msg = useMessage();
const rows = ref<AuditLog[]>([]);
const total = ref(0);
const loading = ref(false);
const verifying = ref(false);
const filterObjType = ref("");
const filterAction = ref("");
const limit = ref(50);
const offset = ref(0);

const columns = computed<DataTableColumns<AuditLog>>(() => [
  { title: t("audit.id"), key: "id", width: 70 },
  {
    title: t("audit.ts"), key: "ts", width: 180,
    render: (r) => new Date(r.ts).toLocaleString(),
  },
  {
    title: t("audit.actor"), key: "actor",
    render: (r) => r.actor_user_id ? `${r.actor_user_id.slice(0, 8)}…` : "(system)",
  },
  { title: "IP", key: "actor_ip", width: 130, render: (r) => r.actor_ip ?? "—" },
  {
    title: t("audit.object_type"), key: "object_type",
    render: (r) => h_tag(r.object_type),
  },
  {
    title: t("audit.action"), key: "action",
    render: (r) => h_tag(r.action, action_color(r.action)),
  },
  {
    title: t("audit.diff"), key: "diff",
    render: (r) => r.diff
      ? renderDiffPopover(r.diff)
      : "—",
  },
  {
    title: t("audit.this_hash"), key: "this_hash_hex", width: 120,
    render: (r) => `${r.this_hash_hex.slice(0, 10)}…`,
  },
]);

async function refresh() {
  loading.value = true;
  try {
    const res = await listAudit({
      object_type: filterObjType.value || undefined,
      action: filterAction.value || undefined,
      limit: limit.value, offset: offset.value,
    });
    rows.value = res.items;
    total.value = res.total;
  } catch {
    msg.error(t("errors.network"));
  } finally {
    loading.value = false;
  }
}

async function verify() {
  verifying.value = true;
  try {
    const res = await verifyAuditChain();
    if (res.ok) {
      msg.success(t("audit.chain_ok", { n: res.checked }));
    } else {
      msg.error(t("audit.chain_broken", { id: String(res.broken_at_id) }));
    }
  } catch {
    msg.error(t("errors.network"));
  } finally {
    verifying.value = false;
  }
}

import { h, defineComponent } from "vue";

function action_color(action: string): "default" | "success" | "warning" | "error" | "info" {
  if (action.includes("login_success")) return "success";
  if (action.includes("login_failed") || action === "delete") return "error";
  if (action === "create") return "info";
  if (action === "update" || action === "sync") return "warning";
  return "default";
}

function h_tag(text: string, type: "default" | "success" | "warning" | "error" | "info" = "default") {
  return h(NTag, { type, size: "small", bordered: false }, () => text);
}

function renderDiffPopover(diff: Record<string, unknown>) {
  const summary = JSON.stringify(diff).slice(0, 60);
  return h(
    NPopover,
    { trigger: "hover", style: { maxWidth: "480px" } },
    {
      trigger: () => h("code", { style: "font-size: 12px; cursor: help" }, summary + "…"),
      default: () =>
        h("pre", { style: "white-space: pre-wrap; max-height: 400px; overflow: auto" },
          JSON.stringify(diff, null, 2)),
    },
  );
}

onMounted(() => { void refresh(); });
</script>

<template>
  <n-card :title="t('audit.title')">
    <n-space style="margin-bottom: 12px" align="center">
      <n-input v-model:value="filterObjType" :placeholder="t('audit.filter_object_type')"
               style="width: 220px" clearable />
      <n-input v-model:value="filterAction" :placeholder="t('audit.filter_action')"
               style="width: 220px" clearable />
      <n-button @click="refresh" :loading="loading">{{ t("common.refresh") }}</n-button>
      <n-button type="primary" :loading="verifying" @click="verify">
        {{ t("audit.verify_chain") }}
      </n-button>
      <span style="opacity: 0.6">total: {{ total }}</span>
    </n-space>
    <n-data-table
      :columns="columns" :data="rows" :loading="loading"
      :pagination="{
        page: Math.floor(offset / limit) + 1,
        pageSize: limit,
        itemCount: total,
        onUpdatePage: (p) => { offset = (p - 1) * limit; void refresh(); },
      }"
      remote :bordered="false"
    >
      <template #empty>
        <n-space justify="center">{{ t("common.no_data") }}</n-space>
      </template>
    </n-data-table>
  </n-card>
</template>
