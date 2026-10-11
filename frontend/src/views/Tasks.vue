<script setup lang="ts">
/**
 * 任務頁 — 列出所有背景任務 (進行中 + 歷史)。
 *
 * 進行中區塊每 3 秒 auto-refresh；歷史頁手動。
 */
import { computed, h, onMounted, onUnmounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NIcon, NButton, NTag, NTabs, NTabPane,
  NProgress, NPopover, NInput, NSelect,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { TasksIcon, RefreshIcon, PendingIcon, ListIcon, SearchIcon } from "@/icons";
import { listTaskKinds, listTasks, type BackgroundTask } from "@/api/tasks";
import { autoSort } from "@/composables/useTableSort";
import ColumnPicker from "@/components/ColumnPicker.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { fmtDateTime } from "@/utils/datetime";
import { taskKindLabel } from "@/utils/taskKind";
import { aggregateCounts, formatSummary as fmtSummary, TEXT_SUMMARY_KINDS } from "@/utils/taskSummary";
const { t, te } = useI18n();

const { visibleKeys: tkVis, setVisible: tkSet, reset: tkReset,
  order: tkOrder, setOrder: tkSetOrder, orderColumns: tkOrderCols } = useColumnPrefs(
  "tasks_history",
  ["kind", "target_label", "status", "progress", "queued_at", "duration", "finished_at", "summary"],
  ["kind", "target_label", "status", "progress", "queued_at", "duration", "finished_at", "summary"],
);
const tkPicker = computed(() => [
  { key: "kind", label: t("cols.type") },
  { key: "target_label", label: t("cols.target") },
  { key: "status", label: t("cols.status") },
  { key: "progress", label: t("cols.progress") },
  { key: "queued_at", label: t("cols.queued_at") },
  { key: "duration", label: t("cols.duration") },
  { key: "finished_at", label: t("cols.finished_at") },
  { key: "summary", label: t("cols.result") },
]);

const msg = useMessage();
const active = ref<BackgroundTask[]>([]);
const history = ref<BackgroundTask[]>([]);
const historyTotal = ref(0);
const historyPage = ref(1);
const historyPageSize = ref(50);
const loadingHistory = ref(false);

let pollTimer: ReturnType<typeof setInterval> | null = null;

// 歷史的搜尋與篩選（跟其他清單頁一樣；條件送到後端，分頁與總數才會對）
const q = ref("");
const fKind = ref<string | null>(null);
const fStatus = ref<string | null>(null);
const fTrigger = ref<"manual" | "scheduled" | null>(null);
const taskKinds = ref<string[]>([]);
// computed：換語言時選項跟著換
const kindOptions = computed(() => taskKinds.value
  .map((k) => ({ label: taskKindLabel(k, t, te), value: k }))
  .sort((a, b) => a.label.localeCompare(b.label)));
const statusOptions = computed(() => (["succeeded", "failed", "cancelled"] as const)
  .map((s) => ({ label: t(`tasks.status_${s}`), value: s })));
const triggerOptions = computed(() => [
  { label: t("tasks.trigger_scheduled"), value: "scheduled" },
  { label: t("tasks.trigger_manual"), value: "manual" },
]);
async function fetchKinds() {
  try {
    taskKinds.value = await listTaskKinds();
  } catch {
    // 選項拿不到不影響清單
  }
}
let qTimer: ReturnType<typeof setTimeout> | null = null;
watch(q, () => {
  if (qTimer) clearTimeout(qTimer);
  qTimer = setTimeout(() => { historyPage.value = 1; void fetchHistory(); }, 300);
});
watch([fKind, fStatus, fTrigger], () => { historyPage.value = 1; void fetchHistory(); });

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
      status_in: fStatus.value || "succeeded,failed,cancelled",
      kind: fKind.value || undefined,
      trigger: fTrigger.value || undefined,
      q: q.value.trim() || undefined,
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
  // DB 存 UTC（timestamptz）；顯示轉成觀看者瀏覽器的本地時區（與全站一致），
  // 原本只 strip 掉 T 會直接顯示 UTC → 看起來時區不對。
  return fmtDateTime(s);
}

const commonCols = computed<DataTableColumns<BackgroundTask>>(() => autoSort([
  // 顯示翻好的名稱，內部名稱放下面當小字（查日誌、對 API 時用得到）
  { title: t("tasks.col_kind"), key: "kind", width: 240,
    render: (r) => {
      const label = taskKindLabel(r.kind, t, te);
      return label === r.kind ? r.kind : h("div", null, [
        h("div", null, label),
        h("div", { style: "font-size: 11.5px; opacity: .55; font-family: var(--n-font-family-mono, monospace)" }, r.kind),
      ]);
    } },
  {
    title: t("tasks.col_trigger"), key: "trigger", width: 96,
    render: (r) => h(
      NTag,
      { size: "small", round: true, type: r.trigger === "scheduled" ? "info" : "default" },
      () => r.trigger === "scheduled" ? t("tasks.trigger_scheduled") : t("tasks.trigger_manual"),
    ),
  },
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

const allHistoryCols = computed<DataTableColumns<BackgroundTask>>(() => autoSort([
  ...commonCols.value,
  { title: t("tasks.col_finished"), key: "finished_at", width: 170, render: (r) => fmtTs(r.finished_at) },
  {
    title: t("tasks.col_summary"), key: "summary", width: 280,
    render: (r) => {
      const isErr = r.status === "failed" && r.error;
      // 失敗卻沒有錯誤訊息（例如探測代理沒說原因）：講清楚是失敗，不要顯示成四個 0
      if (r.status === "failed" && !r.error) {
        return h("span", { style: "color: var(--err-color, #e88080); font-size: 12px;" }, t("tasks.summary.failed_no_detail"));
      }
      // 探測、代理回報與資料庫更新的結果不是「新增／更新幾筆」，四個數字永遠是 0（使用者回報）→ 直接顯示結論
      if (!isErr && TEXT_SUMMARY_KINDS.has(r.kind)) {
        const text = fmtSummary(r.kind, r.summary, t);
        return h("span", { style: "font-size: 12px;", title: text, "data-testid": "task-summary-text" }, text);
      }
      const c = aggregateCounts(r.summary);
      const detailTxt = isErr
        ? r.error!
        : fmtSummary(r.kind, r.summary, t);
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
  // 觸發方式（排程／手動）永遠顯示，不受欄位選擇隱藏
  tkOrderCols(allHistoryCols.value.filter((c: any) => c.key === "trigger" || tkVis.value.includes(c.key))),
);

onMounted(() => {
  void fetchActive();
  void fetchHistory();
  void fetchKinds();
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
      <!-- 控制列：自標題列搬到內文最上方 -->
      <n-space align="center" justify="end" style="margin-bottom: 10px">
        <n-button size="small" @click="() => { fetchActive(); fetchHistory(); fetchKinds(); }">
          <template #icon><n-icon><RefreshIcon /></n-icon></template>
          {{ t("common.refresh") }}
        </n-button>
      </n-space>

      <n-tabs type="line" animated>
        <n-tab-pane name="active">
          <template #tab>
            <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><PendingIcon /></n-icon>{{ `${t('tasks.tab_active')} (${active.length})` }}</span>
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
          <n-space justify="space-between" style="margin-bottom: 8px">
            <n-space :size="8">
              <n-input v-model:value="q" clearable size="small" :placeholder="t('tasks.search')"
                       style="width: 240px" data-testid="tasks-search">
                <template #prefix><n-icon :component="SearchIcon" /></template>
              </n-input>
              <n-select v-model:value="fKind" :options="kindOptions" clearable filterable size="small"
                        :placeholder="t('tasks.filter_kind')" style="width: 180px" data-testid="tasks-filter-kind" />
              <n-select v-model:value="fStatus" :options="statusOptions" clearable size="small"
                        :placeholder="t('tasks.filter_status')" style="width: 120px" data-testid="tasks-filter-status" />
              <n-select v-model:value="fTrigger" :options="triggerOptions" clearable size="small"
                        :placeholder="t('tasks.filter_trigger')" style="width: 120px" data-testid="tasks-filter-trigger" />
            </n-space>
            <ColumnPicker :all="tkPicker" :visible="tkVis"
                          @update:visible="tkSet" @reset="tkReset"
                          :order="tkOrder" @update:order="tkSetOrder" />
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
              prefix: ({ itemCount }) => t('common.total_rows', { n: itemCount ?? 0 }),
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
