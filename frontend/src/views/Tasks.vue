<script setup lang="ts">
/**
 * 任務頁 — 列出所有背景任務 (進行中 + 歷史)。
 *
 * 進行中區塊每 3 秒 auto-refresh；歷史頁手動。
 */
import { computed, h, onMounted, onUnmounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NIcon, NButton, NTag, NTabs, NTabPane,
  NProgress, NPopover,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { TasksIcon, RefreshIcon, PendingIcon, ListIcon } from "@/icons";
import { listTasks, type BackgroundTask } from "@/api/tasks";
import { autoSort } from "@/composables/useTableSort";
import ColumnPicker from "@/components/ColumnPicker.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
const { t } = useI18n();

const { visibleKeys: tkVis, setVisible: tkSet, reset: tkReset } = useColumnPrefs(
  "tasks_history",
  ["kind", "target_label", "status", "progress", "queued_at", "duration", "finished_at", "summary"],
  ["kind", "target_label", "status", "progress", "queued_at", "duration", "finished_at", "summary"],
);
const tkPicker = [
  { key: "kind", label: t("cols.type") },
  { key: "target_label", label: t("cols.target") },
  { key: "status", label: t("cols.status") },
  { key: "progress", label: t("cols.progress") },
  { key: "queued_at", label: t("cols.queued_at") },
  { key: "duration", label: t("cols.duration") },
  { key: "finished_at", label: t("cols.finished_at") },
  { key: "summary", label: t("cols.result") },
];

const msg = useMessage();
const active = ref<BackgroundTask[]>([]);
const history = ref<BackgroundTask[]>([]);
const historyTotal = ref(0);
const historyPage = ref(1);
const historyPageSize = ref(50);
const loadingHistory = ref(false);

let pollTimer: ReturnType<typeof setInterval> | null = null;

async function fetchActive() {
  try {
    const res = await listTasks({ active_only: true, page: 1, pageSize: 200 });
    active.value = res.items;
  } catch {
    // 不要每 3 秒跳一次錯誤 toast 太吵
  }
}

async function fetchHistory() {
  loadingHistory.value = true;
  try {
    const res = await listTasks({
      status_in: "succeeded,failed,cancelled",
      page: historyPage.value,
      pageSize: historyPageSize.value,
    });
    history.value = res.items;
    historyTotal.value = res.total;
  } catch {
    msg.error(t("errors.network"));
  } finally {
    loadingHistory.value = false;
  }
}

function statusTag(s: BackgroundTask["status"]) {
  const map = {
    pending: { type: "default", text: t("tasks.status_pending") },
    running: { type: "info", text: t("tasks.status_running") },
    succeeded: { type: "success", text: t("tasks.status_succeeded") },
    failed: { type: "error", text: t("tasks.status_failed") },
    cancelled: { type: "warning", text: t("tasks.status_cancelled") },
  } as const;
  const m = map[s] ?? map.pending;
  return h(NTag, { type: m.type, size: "small" }, () => m.text);
}

function duration(r: BackgroundTask): string {
  const start = r.started_at ?? r.queued_at;
  const end = r.finished_at ?? new Date().toISOString();
  const sec = Math.max(0, Math.floor((new Date(end).getTime() - new Date(start).getTime()) / 1000));
  if (sec < 60) return `${sec}s`;
  return `${Math.floor(sec / 60)}m ${sec % 60}s`;
}

function fmtTs(s: string | null): string {
  if (!s) return "—";
  return s.replace("T", " ").split(".")[0];
}

const commonCols = computed<DataTableColumns<BackgroundTask>>(() => autoSort([
  { title: t("tasks.col_kind"), key: "kind", width: 180 },
  {
    title: t("tasks.col_target"), key: "target_label",
    width: 200, ellipsis: { tooltip: true },
    render: (r) => r.target_label ?? (r.target_id ? r.target_id.slice(0, 8) : "—"),
  },
  { title: t("common.status"), key: "status", width: 100, render: (r) => statusTag(r.status) },
  {
    title: t("tasks.col_progress"), key: "progress", width: 140,
    render: (r) => h(NProgress, {
      type: "line", percentage: r.progress,
      showIndicator: true, status: r.status === "failed" ? "error" : r.status === "running" ? "info" : "success",
    }),
  },
  { title: t("tasks.col_queued"), key: "queued_at", width: 170, render: (r) => fmtTs(r.queued_at) },
  { title: t("tasks.col_duration"), key: "duration", width: 100, render: (r) => duration(r) },
]));

const activeCols = computed<DataTableColumns<BackgroundTask>>(() => commonCols.value);

// 聚合各種 summary 形狀 → 四個數字
function aggregateCounts(summary: any): { ins: number; upd: number; err: number; total: number } {
  const out = { ins: 0, upd: 0, err: 0, total: 0 };
  if (!summary || typeof summary !== "object") return out;
  const n = (v: any) => (typeof v === "number" ? v : Number(v ?? 0)) || 0;

  // 1) phpipam.migration: {tables: {x: {inserted, updated, skipped, errored}}}
  if (summary.tables && typeof summary.tables === "object") {
    for (const v of Object.values(summary.tables) as any[]) {
      out.ins += n(v.inserted);
      out.upd += n(v.updated);
      out.err += n(v.errored);
      out.total += n(v.inserted) + n(v.updated) + n(v.skipped) + n(v.errored);
    }
    return out;
  }

  // 2) opnsense.sync: {details: [{task, seen, matched, inserted, updated, removed, error?}]}
  if (Array.isArray(summary.details)) {
    for (const d of summary.details) {
      out.ins += n(d.inserted);
      out.upd += n(d.updated) || n(d.matched);
      out.err += n(d.errored) + (d.error ? 1 : 0);
      out.total += n(d.seen);
    }
    return out;
  }

  // 3) 通用 top-level：librenms / wazuh / adguard 等
  for (const [k, v] of Object.entries(summary)) {
    if (typeof v !== "number") continue;
    if (k.endsWith("_inserted") || k === "inserted") out.ins += v;
    else if (k.endsWith("_updated") || k === "updated" || k.endsWith("_matched") || k.endsWith("_filled")) out.upd += v;
    else if (k.endsWith("_errored") || k === "errored" || k.endsWith("_failed") || k === "missing_agents") out.err += v;
    else if (k.endsWith("_seen") || k.endsWith("_count")) out.total += v;
  }
  return out;
}

// 把 task summary 翻譯成人話
function formatSummary(kind: string, summary: any): string {
  if (!summary || typeof summary !== "object") return "—";

  // 通用統計欄位 (migration / sync 都用)
  const lines: string[] = [];
  const num = (v: any) => (typeof v === "number" ? v : Number(v ?? 0));

  // 1) phpipam migration 風格：{tables: {sections: {inserted, updated, skipped, errored}, ...}, error}
  if (summary.tables && typeof summary.tables === "object") {
    if (summary.error) lines.push(t("tasks.summary.error", { msg: summary.error }));
    for (const [tname, t] of Object.entries(summary.tables) as [string, any][]) {
      const ins = num(t.inserted), upd = num(t.updated), err = num(t.errored);
      // 只顯示「有變動或失敗」的；只 skip 的不列
      if (ins + upd + err === 0) continue;
      const parts: string[] = [];
      if (ins) parts.push(t("common.added_n", { n: ins }));
      if (upd) parts.push(t("common.updated_n", { n: upd }));
      if (err) parts.push(t("common.failed_n", { n: err }));
      lines.push(`${tname}：${parts.join("、")}`);
    }
    if (!lines.length) return t("tasks.summary.no_change");
    return lines.join("；");
  }

  // 2) OPNsense sync 風格：{firewall, tasks, details: [{task, seen, matched}, ...]}
  if (Array.isArray(summary.details)) {
    for (const d of summary.details) {
      const name = d.task ?? d.alias ?? "?";
      if (d.error) { lines.push(t("tasks.summary.named_error", { name, msg: d.error })); continue; }
      const seen = num(d.seen), matched = num(d.matched);
      // 全 0 的子任務也跳過
      if (seen === 0 && matched === 0) continue;
      lines.push(`${name}：${matched}/${seen}`);
    }
    if (!lines.length) return t("tasks.summary.no_change");
    return lines.join("；");
  }

  // 3) LibreNMS sync 風格：{instance, devices_seen, devices_inserted, ...}
  const k = (key: string, label: string) => {
    if (typeof summary[key] === "number" && summary[key] !== 0) lines.push(`${label} ${summary[key]}`);
  };
  k("devices_seen", t("tasks.summary.devices_seen"));
  k("devices_inserted", t("tasks.summary.devices_inserted"));
  k("devices_updated", t("tasks.summary.devices_updated"));
  k("arp_seen", "ARP");
  k("arp_inserted", t("tasks.summary.arp_inserted"));
  k("fdb_seen", "FDB");
  k("ip_mac_filled", t("tasks.summary.ip_mac_filled"));

  // 4) AdGuard 風格：{clients_result: {clients, ips_seen, ips_matched}, ...}
  if (summary.clients_result) {
    const r = summary.clients_result;
    lines.push(`clients ${num(r.clients)}(IP ${num(r.ips_matched)}/${num(r.ips_seen)})`);
  }
  if (summary.rewrites_result) {
    const r = summary.rewrites_result;
    lines.push(t("tasks.summary.rewrites", { n: num(r.rewrites), matched: num(r.rewrites_matched) }));
  }

  // 5) Wazuh 風格：{instance, agents_seen, agents_inserted, ...}
  k("agents_seen", t("tasks.summary.agents_seen"));
  k("agents_inserted", t("tasks.summary.agents_inserted"));
  k("agents_updated", t("tasks.summary.agents_updated"));
  k("missing_agents", "missing");

  if (!lines.length) return t("tasks.summary.done");
  return lines.join("；");
}

const allHistoryCols = computed<DataTableColumns<BackgroundTask>>(() => autoSort([
  ...commonCols.value,
  { title: t("tasks.col_finished"), key: "finished_at", width: 170, render: (r) => fmtTs(r.finished_at) },
  {
    title: t("tasks.col_summary"), key: "summary", width: 280,
    render: (r) => {
      const isErr = r.status === "failed" && r.error;
      const c = aggregateCounts(r.summary);
      const detailTxt = isErr
        ? r.error!
        : formatSummary(r.kind, r.summary);
      const rawJson = r.summary ? JSON.stringify(r.summary, null, 2) : "";

      const tag = (label: string, val: number, type: "default" | "info" | "warning" | "success" | "error") =>
        h(NTag, { size: "small", type, bordered: false, style: { cursor: "pointer" } }, () => `${label} ${val}`);

      const trigger = isErr
        ? h("span", { style: "color: var(--err-color, #e88080); font-size: 12px; cursor: pointer;" }, t("tasks.summary.failed_click"))
        : h(NSpace, { size: 4, wrap: false, style: "cursor: pointer;" }, () => [
            tag(t("tasks.summary.tag_add"), c.ins, "info"),
            tag(t("tasks.summary.tag_update"), c.upd, "warning"),
            tag(t("tasks.summary.tag_fail"), c.err, c.err > 0 ? "error" : "default"),
            tag(t("tasks.summary.tag_total"), c.total, "default"),
          ]);

      return h(
        NPopover,
        { trigger: "click", placement: "left-start", style: { maxWidth: "640px" } },
        {
          trigger: () => trigger,
          default: () => h("div", { style: "font-size: 12px;" }, [
            h("div", { style: "margin-bottom: 8px; white-space: pre-wrap; word-break: break-word;" }, detailTxt),
            rawJson
              ? h("details", null, [
                  h("summary", { style: "cursor: pointer; opacity: 0.7;" }, t("tasks.summary.raw_json")),
                  h("pre", {
                    style: "white-space: pre-wrap; max-height: 360px; overflow: auto; margin: 4px 0 0; font-size: 11px;",
                  }, rawJson),
                ])
              : null,
          ]),
        },
      );
    },
  },
]));

const historyCols = computed<DataTableColumns<BackgroundTask>>(() =>
  allHistoryCols.value.filter((c: any) => tkVis.value.includes(c.key)),
);

onMounted(() => {
  void fetchActive();
  void fetchHistory();
  pollTimer = setInterval(() => { void fetchActive(); }, 3000);
});

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer);
});
</script>

<template>
  <n-space vertical :size="16">
    <n-card>
      <template #header>
        <n-space align="center" :wrap-item="false">
          <n-icon :size="22"><TasksIcon /></n-icon>
          <span>{{ t("nav.tasks") }}</span>
        </n-space>
      </template>
      <template #header-extra>
        <n-button size="small" @click="() => { fetchActive(); fetchHistory(); }">
          <template #icon><n-icon><RefreshIcon /></n-icon></template>
          {{ t("common.refresh") }}
        </n-button>
      </template>

      <n-tabs type="line" animated>
        <n-tab-pane name="active">
          <template #tab>
            <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><PendingIcon /></n-icon>{{ `${t('tasks.tab_active')}(${active.length})` }}</span>
          </template>
          <n-data-table
            :columns="activeCols"
            :data="active"
            :bordered="false"
            size="small"
            :scroll-x="890"
          >
            <template #empty>
              <n-space justify="center" style="padding: 24px; opacity: 0.7;">
                {{ t("tasks.empty_active") }}
              </n-space>
            </template>
          </n-data-table>
        </n-tab-pane>

        <n-tab-pane name="history">
          <template #tab>
            <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><ListIcon /></n-icon>{{ t('tasks.tab_history') }}</span>
          </template>
          <n-space style="margin-bottom: 8px">
            <ColumnPicker :all="tkPicker" :visible="tkVis"
                          @update:visible="tkSet" @reset="tkReset" />
          </n-space>
          <n-data-table
            :columns="historyCols"
            :data="history"
            :loading="loadingHistory"
            :bordered="false"
            size="small"
            :scroll-x="1340"
            remote
            :pagination="{
              page: historyPage,
              pageSize: historyPageSize,
              itemCount: historyTotal,
              showSizePicker: true,
              pageSizes: [20, 50, 100, 200],
              onUpdatePage: (p) => { historyPage = p; void fetchHistory(); },
              onUpdatePageSize: (ps) => { historyPageSize = ps; historyPage = 1; void fetchHistory(); },
            }"
          >
            <template #empty>
              <n-space justify="center" style="padding: 24px; opacity: 0.7;">
                {{ t("tasks.empty_history") }}
              </n-space>
            </template>
          </n-data-table>
        </n-tab-pane>
      </n-tabs>
    </n-card>
  </n-space>
</template>
