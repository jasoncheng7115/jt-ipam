<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { fmtDateTime } from "@/utils/datetime";
import { useI18n } from "vue-i18n";
import {
  NCard,
  NDataTable,
  NSpace,
  NIcon,
  NInput,
  NSelect,
  NButton,
  NTag,
  NModal,
  useMessage,
  type DataTableColumns,
} from "naive-ui";
import { listAudit, verifyAuditChain, type AuditLog } from "@/api/admin";
import { AuditIcon, RefreshIcon, AdminIcon as VerifyIcon } from "@/icons";
import { autoSort } from "@/composables/useTableSort";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { useRouter } from "vue-router";
const { t } = useI18n();

const router = useRouter();

// 把 audit (object_type, object_id) → 可點連結
function renderObjectLink(objectType: string | null, objectId: string | null, label?: string | null) {
  if (!objectId) return "—";
  const short = label || objectId.slice(0, 8) + "…";
  const linkStyle = "color: var(--primary-color, #18a058); text-decoration: none; cursor: pointer;";
  const go = (name: string, params?: any, query?: any) =>
    h("a", {
      href: "#", style: linkStyle,
      onClick: (e: MouseEvent) => { e.preventDefault(); router.push({ name, params, query }); },
    }, short);
  switch (objectType) {
    case "section":      return go("section-detail", { id: objectId });
    case "subnet":       return go("subnet-detail", { id: objectId });
    case "device":       return go("device-detail", { id: objectId });
    case "user":         return go("users");
    case "group":        return go("groups");
    case "customer":     return go("customers");
    case "nat":          return go("nat");
    case "vlan":         return go("vlans");
    case "vrf":          return go("vrfs");
    case "ip_address":
    case "ip":           return go("addresses", undefined, { q: short });
    case "scan_agent":   return go("scan_agents");
    case "webhook":      return go("webhooks");
    case "custom_field": return go("custom_fields");
    case "ip_request":   return go("requests");
    default:             return short;
  }
}

const { visibleKeys: auditVis, setVisible: auditSet, reset: auditReset } = useColumnPrefs(
  "audit",
  ["id", "ts", "actor", "actor_ip", "object_type", "object_link", "action", "diff", "this_hash_hex"],
  // 預設不顯示 ID 與雜湊（要稽核鏈驗證時再自行於「欄位」開）
  ["ts", "actor", "actor_ip", "object_type", "object_link", "action", "diff"],
);
const auditPickerItems = [
  { key: "id", label: "ID" },
  { key: "ts", label: t("cols.time") },
  { key: "actor", label: t("cols.actor") },
  { key: "actor_ip", label: "IP" },
  { key: "object_type", label: t("cols.object_type") },
  { key: "object_link", label: t("cols.target") },
  { key: "action", label: t("cols.action") },
  { key: "diff", label: t("cols.diff") },
  { key: "this_hash_hex", label: t("cols.hash") },
];

const msg = useMessage();
const rows = ref<AuditLog[]>([]);
const total = ref(0);
const loading = ref(false);
const verifying = ref(false);
const filterObjType = ref<string | null>(null);

// 常見的 object_type 值 (與 backend 寫 audit 時用的名字對齊)
const objTypeOptions = [
  "user", "group", "section", "subnet", "ip_address", "device", "rack", "location",
  "vlan", "vlan_domain", "vrf", "nat",
  "auth", "api_token", "anomaly",
  "dns_server", "librenms_instance", "opnsense_firewall", "opnsense_alias_mapping",
  "wazuh_instance", "scan_agent", "webhook",
  "phpipam_migration", "ip_request", "custom_field",
].map((v) => ({ label: v, value: v }));
const filterAction = ref("");
const limit = ref(50);
const offset = ref(0);

// 點列 → 開明細 modal（把 diff 的 JSON 整理成好讀的表格）
const detailRow = ref<AuditLog | null>(null);
function rowProps(row: AuditLog) {
  return { style: "cursor: pointer", onClick: () => { detailRow.value = row; } };
}
function fmtVal(v: unknown, field?: string): string {
  if (v == null) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  const s = String(v);
  // switch_port 與其他頁一致：交換器 / 連接埠 顯示為 device@port
  if (field === "switch_port") return s.replace(" / ", "@");
  return s;
}
const detailDiff = computed<{ mode: string; rows: any[] }>(() => {
  const d = detailRow.value?.diff as any;
  if (!d || typeof d !== "object") return { mode: "none", rows: [] };
  if (d.before && d.changes) {
    return {
      mode: "update",
      rows: Object.keys(d.changes).map((f) => ({
        field: f, before: fmtVal(d.before?.[f], f), after: fmtVal(d.changes[f], f),
      })),
    };
  }
  const obj = d.changes ?? d.after ?? d;
  return { mode: "single", rows: Object.entries(obj).map(([k, v]) => ({ field: k, value: fmtVal(v, k) })) };
});

const allColumns = computed<DataTableColumns<AuditLog>>(() => autoSort([
  { title: t("audit.id"), key: "id", width: 70 },
  {
    title: t("audit.ts"), key: "ts", width: 180,
    render: (r) => fmtDateTime(r.ts),
  },
  {
    title: t("audit.actor"), key: "actor", width: 130,
    render: (r) => r.actor_name || (r.actor_user_id ? `${r.actor_user_id.slice(0, 8)}…` : "(system)"),
  },
  { title: "IP", key: "actor_ip", width: 130, render: (r) => r.actor_ip ?? "—" },
  {
    title: t("audit.object_type"), key: "object_type", width: 150,
    render: (r) => h_tag(r.object_type),
  },
  {
    title: t("cols.target"), key: "object_link", width: 140,
    render: (r) => renderObjectLink(r.object_type, r.object_id, r.object_label),
  },
  {
    title: t("audit.action"), key: "action", width: 120,
    render: (r) => h_tag(r.action, action_color(r.action)),
  },
  {
    title: t("audit.diff"), key: "diff", minWidth: 220, ellipsis: { tooltip: true },
    render: (r) => r.diff ? diffSummary(r.diff) : "—",
  },
  {
    title: t("audit.this_hash"), key: "this_hash_hex", width: 120,
    render: (r) => `${r.this_hash_hex.slice(0, 10)}…`,
  },
]));

const columns = computed<DataTableColumns<AuditLog>>(() =>
  allColumns.value.filter((c: any) => auditVis.value.includes(c.key)),
);

// 匯出全部：用相同篩選分頁抓完整資料集
async function fetchAllForExport(): Promise<AuditLog[]> {
  const all: AuditLog[] = [];
  const big = 500;   // 後端 limit 上限
  let off = 0;
  for (;;) {
    const res = await listAudit({
      object_type: filterObjType.value || undefined,
      action: filterAction.value || undefined,
      limit: big, offset: off,
    });
    all.push(...res.items);
    if (res.items.length === 0 || all.length >= res.total) break;
    off += big;
  }
  return all;
}

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

import { h } from "vue";

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

// 差異欄：整理成好讀文字（field: 舊 → 新；或 field: 值），不直接吐 JSON。
// 欄位窄，靠 column 的 ellipsis tooltip 顯示完整；點該列開明細看完整 before/after 表。
function diffSummary(diff: Record<string, unknown>): string {
  if (!diff || typeof diff !== "object") return "—";
  const d = diff as any;
  if (d.before && d.changes) {
    return Object.keys(d.changes)
      .map((f) => `${f}: ${fmtVal(d.before?.[f])} → ${fmtVal(d.changes[f])}`)
      .join("；");
  }
  const obj = d.changes ?? d.after ?? d;
  return Object.entries(obj).map(([k, v]) => `${k}: ${fmtVal(v)}`).join("；");
}

onMounted(() => { void refresh(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><AuditIcon /></n-icon>
        <span>{{ t("audit.title") }}</span>
      </n-space>
    </template>
    <n-space style="margin-bottom: 12px" align="center">
      <n-select v-model:value="filterObjType" :options="objTypeOptions" filterable clearable
                :placeholder="t('audit.filter_object_type')"
                @update:value="refresh"
                style="width: 240px" />
      <n-input v-model:value="filterAction" :placeholder="t('audit.filter_action')"
               style="width: 220px" clearable />
      <n-button @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
      <n-button type="primary" :loading="verifying" @click="verify">
        <template #icon><n-icon><VerifyIcon /></n-icon></template>
        {{ t("audit.verify_chain") }}
      </n-button>
      <ColumnPicker :all="auditPickerItems" :visible="auditVis"
                    @update:visible="auditSet" @reset="auditReset" />
      <ExportButton :columns="columns" :rows="rows" :fetch-all="fetchAllForExport"
                    filename="audit" :title="t('audit.title')" />
      <span style="opacity: 0.6">{{ t("common.total_n", { n: total }) }}</span>
    </n-space>
    <n-data-table
      :columns="columns" :data="rows" :loading="loading"
      :pagination="{
        page: Math.floor(offset / limit) + 1,
        pageSize: limit,
        itemCount: total,
        prefix: ({ itemCount }) => t('common.total_rows', { n: itemCount ?? 0 }),
        onUpdatePage: (p) => { offset = (p - 1) * limit; void refresh(); },
      }"
      remote :bordered="false" :scroll-x="1240" :row-props="rowProps"
    >
      <template #empty>
        <n-space justify="center">{{ t("common.no_data") }}</n-space>
      </template>
    </n-data-table>

    <n-modal :show="!!detailRow" preset="card" style="width: 760px; max-width: 94vw"
             :title="t('audit.detail_title')" @update:show="(v: boolean) => { if (!v) detailRow = null; }">
      <div v-if="detailRow">
        <table class="audit-meta">
          <tbody>
            <tr><th>{{ t("audit.ts") }}</th><td>{{ fmtDateTime(detailRow.ts) }}</td></tr>
            <tr><th>{{ t("audit.actor") }}</th><td>{{ detailRow.actor_name || detailRow.actor_user_id || "(system)" }}<span v-if="detailRow.actor_ip"> · {{ detailRow.actor_ip }}</span></td></tr>
            <tr><th>{{ t("audit.object_type") }}</th><td>{{ detailRow.object_type }} <span v-if="detailRow.object_label">— {{ detailRow.object_label }}</span><span v-if="detailRow.object_id" style="opacity:.6"> ({{ detailRow.object_id }})</span></td></tr>
            <tr><th>{{ t("audit.action") }}</th><td>{{ detailRow.action }}</td></tr>
            <tr><th>{{ t("audit.this_hash") }}</th><td style="word-break:break-all; font-family:monospace; font-size:11px">{{ detailRow.this_hash_hex }}</td></tr>
          </tbody>
        </table>

        <div style="margin-top: 14px; font-weight: 600">{{ t("audit.diff") }}</div>
        <n-space v-if="!detailDiff.rows.length" justify="center" style="padding: 14px; opacity:.6">—</n-space>
        <table v-else class="audit-diff">
          <thead v-if="detailDiff.mode === 'update'">
            <tr><th>{{ t("cols.key") }}</th><th>{{ t("audit.before") }}</th><th>{{ t("audit.after") }}</th></tr>
          </thead>
          <thead v-else>
            <tr><th>{{ t("cols.key") }}</th><th>{{ t("audit.value") }}</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in detailDiff.rows" :key="row.field">
              <td class="k">{{ row.field }}</td>
              <template v-if="detailDiff.mode === 'update'">
                <td class="old">{{ row.before }}</td>
                <td class="new">{{ row.after }}</td>
              </template>
              <td v-else>{{ row.value }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </n-modal>
  </n-card>
</template>

<style scoped>
.audit-meta { width: 100%; border-collapse: collapse; font-size: 13px; }
.audit-meta th { text-align: left; white-space: nowrap; padding: 3px 12px 3px 0; color: var(--n-text-color-3, #888); font-weight: 500; vertical-align: top; width: 1%; }
.audit-meta td { padding: 3px 0; word-break: break-all; }
.audit-diff { width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 6px; }
.audit-diff th { text-align: left; padding: 4px 8px; border-bottom: 1px solid rgba(127,127,127,0.25); color: var(--n-text-color-3, #888); font-weight: 500; }
.audit-diff td { padding: 4px 8px; border-bottom: 1px solid rgba(127,127,127,0.12); word-break: break-all; vertical-align: top; }
.audit-diff td.k { font-family: monospace; white-space: nowrap; }
.audit-diff td.old { color: #c0392b; text-decoration: line-through; opacity: 0.8; }
.audit-diff td.new { color: #18a058; }
</style>
