<script setup lang="ts">
/**
 * 右下角的「背景作業」面板（客戶 2026-10-10：「設定完要先去看工作跑完了沒，再去看 log 才知道狀態」）。
 * 狀態在 composables/useTaskTracker.ts；這裡只負責畫：執行中轉圈＋經過時間＋進度，跑完顯示結果摘要，
 * 失敗顯示完整錯誤（可以複製）。掛在 MainLayout，所以換頁不會消失。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import { NButton, NCard, NIcon, NProgress, NSpin, NTooltip, useMessage } from "naive-ui";
import { ChevronDownIcon, CopyIcon, DismissIcon, FailIcon, OkIcon, TasksIcon, WarnIcon } from "@/icons";
import { restoreTracked, useTaskTracker, type TrackedTask } from "@/composables/useTaskTracker";
import { taskKindLabel } from "@/utils/taskKind";
import { taskResultText } from "@/utils/taskSummary";

const props = defineProps<{ raised?: boolean }>();
const { t, te } = useI18n();
const router = useRouter();
const msg = useMessage();
const { tracked, dismiss, dismissFinished, isActive } = useTaskTracker();
const collapsed = ref(false);

// 經過時間每秒更新（只在有作業在跑時計時）
const now = ref(Date.now());
let clock: ReturnType<typeof setInterval> | null = null;
const anyActive = computed(() => tracked.value.some(isActive));
watch(anyActive, (on) => {
  if (on && clock === null) clock = setInterval(() => { now.value = Date.now(); }, 1000);
  if (!on && clock !== null) { clearInterval(clock); clock = null; }
}, { immediate: true });
// 有新作業加入就展開，免得收合著看不到
watch(() => tracked.value.length, (n, old) => { if (n > (old ?? 0)) collapsed.value = false; });
onMounted(() => restoreTracked());
onBeforeUnmount(() => { if (clock !== null) clearInterval(clock); });

type View = { id: string; state: "running" | "pending" | "succeeded" | "failed" | "lost"; label: string;
  elapsed: string; progress: number; result: string };

function elapsedText(x: TrackedTask): string {
  const task = x.task;
  const start = task?.started_at ?? task?.queued_at;
  const begin = start ? new Date(start).getTime() : x.addedAt;
  const end = task?.finished_at ? new Date(task.finished_at).getTime() : now.value;
  const sec = Math.max(0, Math.round((end - begin) / 1000));
  return sec < 60 ? t("tasks.tracker.seconds", { n: sec }) : t("tasks.tracker.minutes", { m: Math.floor(sec / 60), s: sec % 60 });
}

const views = computed<View[]>(() => tracked.value.map((x) => {
  const task = x.task;
  const state: View["state"] = x.lost ? "lost"
    : !task ? "pending"
    : task.status === "succeeded" ? "succeeded"
    : task.status === "failed" || task.status === "cancelled" ? "failed"
    : task.status === "running" ? "running" : "pending";
  const label = task
    ? `${taskKindLabel(task.kind, t, te)}${task.target_label ? `：${task.target_label}` : ""}`
    : t("tasks.tracker.loading");
  return {
    id: x.id, state, label, elapsed: elapsedText(x), progress: task?.progress ?? 0,
    result: x.lost ? t("tasks.tracker.lost") : task ? taskResultText(task, t) : "",
  };
}));
const finishedCount = computed(() => views.value.filter((v) => v.state !== "running" && v.state !== "pending").length);

const stateText = (v: View) => ({
  running: t("tasks.status_running"), pending: t("tasks.status_pending"), succeeded: t("tasks.status_succeeded"),
  failed: t("tasks.status_failed"), lost: t("tasks.status_failed"),
}[v.state]);

async function copy(text: string): Promise<void> {
  try { await navigator.clipboard.writeText(text); msg.success(t("tasks.tracker.copied")); }
  catch { msg.error(t("errors.server")); }
}
</script>

<template>
  <n-card v-if="views.length" size="small" class="jt-task-tracker" :class="{ 'jt-tt-raised': props.raised }"
          data-testid="task-tracker"
          :content-style="{ padding: 0 }" :header-style="{ padding: '8px 12px' }">
    <template #header>
      <div class="jt-tt-head">
        <n-icon :size="16"><TasksIcon /></n-icon>
        <span>{{ t("tasks.tracker.title") }}</span>
        <span class="jt-tt-count">{{ views.length }}</span>
      </div>
    </template>
    <template #header-extra>
      <n-button v-if="finishedCount" size="tiny" quaternary @click="dismissFinished" data-testid="task-tracker-clear">
        {{ t("tasks.tracker.clear_done") }}
      </n-button>
      <n-button size="tiny" quaternary :aria-label="collapsed ? t('tasks.tracker.expand') : t('tasks.tracker.collapse')"
                @click="collapsed = !collapsed">
        <template #icon>
          <n-icon :style="{ transform: collapsed ? 'rotate(180deg)' : 'none' }"><ChevronDownIcon /></n-icon>
        </template>
      </n-button>
    </template>
    <div v-show="!collapsed" class="jt-tt-list">
      <div v-for="v in views" :key="v.id" class="jt-tt-item" data-testid="task-tracker-item" :data-state="v.state">
        <div class="jt-tt-row">
          <n-spin v-if="v.state === 'running' || v.state === 'pending'" :size="14" />
          <n-icon v-else-if="v.state === 'succeeded'" :size="16" color="#18a058"><OkIcon /></n-icon>
          <n-icon v-else-if="v.state === 'lost'" :size="16" color="#f0a020"><WarnIcon /></n-icon>
          <n-icon v-else :size="16" color="#d03050"><FailIcon /></n-icon>
          <span class="jt-tt-label" :title="v.label">{{ v.label }}</span>
          <n-tooltip>
            <template #trigger>
              <n-button size="tiny" quaternary :aria-label="t('tasks.tracker.dismiss')" @click="dismiss(v.id)">
                <template #icon><n-icon><DismissIcon /></n-icon></template>
              </n-button>
            </template>
            {{ t("tasks.tracker.dismiss") }}
          </n-tooltip>
        </div>
        <div class="jt-tt-meta" data-testid="task-tracker-status">{{ stateText(v) }} · {{ v.elapsed }}</div>
        <n-progress v-if="v.state === 'running' && v.progress > 0" type="line" :percentage="v.progress"
                    :height="4" :show-indicator="false" style="margin-top: 4px" />
        <div v-if="v.result" class="jt-tt-result" :class="{ 'jt-tt-err': v.state === 'failed' || v.state === 'lost' }"
             data-testid="task-tracker-result">{{ v.result }}</div>
        <div v-if="v.state !== 'running' && v.state !== 'pending'" class="jt-tt-actions">
          <n-button v-if="v.state === 'failed'" size="tiny" quaternary @click="copy(v.result)">
            <template #icon><n-icon><CopyIcon /></n-icon></template>
            {{ t("tasks.tracker.copy") }}
          </n-button>
          <n-button size="tiny" quaternary type="info" @click="router.push('/tasks')">
            <template #icon><n-icon><TasksIcon /></n-icon></template>
            {{ t("tasks.tracker.view_tasks") }}
          </n-button>
        </div>
      </div>
    </div>
  </n-card>
</template>

<style scoped>
.jt-task-tracker {
  position: fixed;
  right: 24px;
  bottom: 24px;
  width: 380px;
  max-width: calc(100vw - 32px);
  z-index: 1850;
  box-shadow: 0 6px 24px rgba(0, 0, 0, .25);
}
/* 有 AI 對話按鈕（右下 24px、56px 高）時放在它上方，不擋住它 */
.jt-task-tracker.jt-tt-raised { bottom: 92px; }
.jt-tt-head { display: flex; align-items: center; gap: 6px; font-size: 14px; font-weight: 600; }
.jt-tt-count { font-size: 12px; opacity: .65; font-weight: 400; }
.jt-tt-list { max-height: min(55vh, 460px); overflow-y: auto; }
.jt-tt-item { padding: 8px 12px; border-top: 1px solid rgba(128, 128, 128, .18); }
.jt-tt-row { display: flex; align-items: center; gap: 8px; }
.jt-tt-label { flex: 1; min-width: 0; font-size: 13px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.jt-tt-meta { margin: 2px 0 0 22px; font-size: 12px; opacity: .7; }
.jt-tt-result {
  margin: 4px 0 0 22px; font-size: 12px; line-height: 1.5; white-space: pre-wrap; word-break: break-word;
  max-height: 140px; overflow-y: auto; user-select: text;
}
.jt-tt-err { color: #d03050; }
.jt-tt-actions { margin: 4px 0 0 16px; display: flex; gap: 4px; }
@media (max-width: 767px) {
  .jt-task-tracker { right: 16px; left: 16px; width: auto; bottom: 16px; }
  .jt-task-tracker.jt-tt-raised { bottom: 88px; }
}
</style>
