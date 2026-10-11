/**
 * 背景作業即時回報（客戶 2026-10-10：「設定完要先去看工作跑完了沒，再去看 log 才知道狀態」）。
 *
 * 按下拉取／同步／匯入之後呼叫 `trackTask(task_id, { onDone })`：右下角的「背景作業」面板
 * （components/TaskTrackerPanel.vue）馬上出現一筆，執行中顯示進度與經過時間，跑完直接顯示結果摘要，
 * 失敗顯示完整的錯誤訊息。全站共用一份狀態：換頁不會消失；還沒跑完的記在 sessionStorage，
 * 重新整理後接著追。成功的過一陣子自動收起，失敗的留到使用者自己關。
 */
import { ref } from "vue";
import { getTask, type BackgroundTask } from "@/api/tasks";

export const POLL_MS = 1500;
/** 成功的作業顯示多久後自動收起（失敗的不會自動收起） */
export const SUCCESS_TTL_MS = 20_000;
export const STORAGE_KEY = "jt_tracked_tasks";

export interface TrackedTask {
  id: string;
  task: BackgroundTask | null;
  addedAt: number;
  /** 前端看到它跑完的時間（自動收起從這時算） */
  doneAt?: number;
  /** 作業不存在或沒有權限看（404） */
  lost?: boolean;
}

type OnDone = (task: BackgroundTask) => void;

const tracked = ref<TrackedTask[]>([]);
const callbacks = new Map<string, OnDone[]>();
let timer: ReturnType<typeof setInterval> | null = null;
let polling = false;
/** 輪詢途中又有新作業加入：這輪結束馬上再跑一輪，不用等下一個間隔 */
let again = false;

const isActive = (x: TrackedTask) => !x.lost && (!x.task || x.task.status === "pending" || x.task.status === "running");

function persist(): void {
  try {
    const ids = tracked.value.filter(isActive).map((x) => x.id);
    if (ids.length) sessionStorage.setItem(STORAGE_KEY, JSON.stringify(ids));
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch { /* 私密視窗等情況拿不到 storage：只是重新整理後不會接著追 */ }
}

async function pollOnce(): Promise<void> {
  if (polling) { again = true; return; }
  polling = true;
  try {
    const now = Date.now();
    // 成功的到時間就收起
    tracked.value = tracked.value.filter((x) =>
      !(x.task?.status === "succeeded" && x.doneAt !== undefined && now - x.doneAt >= SUCCESS_TTL_MS));
    for (const item of tracked.value.filter(isActive)) {
      try {
        const task = await getTask(item.id);
        const cur = tracked.value.find((x) => x.id === item.id);
        if (!cur) continue;            // 輪詢途中被關掉
        cur.task = task;
        if (!isActive(cur)) {
          cur.doneAt = Date.now();
          for (const cb of callbacks.get(item.id) ?? []) {
            try { cb(task); } catch { /* 頁面的重新整理失敗不影響面板 */ }
          }
          callbacks.delete(item.id);
        }
      } catch (e) {
        const status = (e as { response?: { status?: number } })?.response?.status;
        if (status === 404 || status === 403) {
          const cur = tracked.value.find((x) => x.id === item.id);
          if (cur) { cur.lost = true; cur.doneAt = Date.now(); }
          callbacks.delete(item.id);
        }
        // 其他錯誤（網路斷線、伺服器重啟）下一輪再試
      }
    }
    persist();
    const pendingTtl = tracked.value.some((x) => x.task?.status === "succeeded");
    if (!tracked.value.some(isActive) && !pendingTtl) stop();
  } finally {
    polling = false;
  }
  if (again) { again = false; await pollOnce(); }
}

function start(): void {
  if (timer === null) timer = setInterval(() => { void pollOnce(); }, POLL_MS);
}

function stop(): void {
  if (timer !== null) { clearInterval(timer); timer = null; }
}

/** 開始追蹤一個背景作業。同一個 id 追蹤兩次只會出現一筆（onDone 都會被呼叫）。 */
export function trackTask(id: string, opts: { onDone?: OnDone } = {}): void {
  if (!id) return;
  if (opts.onDone) callbacks.set(id, [...(callbacks.get(id) ?? []), opts.onDone]);
  const existing = tracked.value.find((x) => x.id === id);
  if (existing) {
    // 已經跑完、使用者又按了一次（極少見）：重新追
    if (!isActive(existing)) { existing.task = null; existing.doneAt = undefined; existing.lost = false; }
  } else {
    tracked.value = [{ id, task: null, addedAt: Date.now() }, ...tracked.value];
  }
  persist();
  start();
  void pollOnce();
}

/** 重新整理後接著追 sessionStorage 裡還沒跑完的作業（面板掛載時呼叫） */
export function restoreTracked(): void {
  let ids: string[] = [];
  try { ids = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "[]"); } catch { ids = []; }
  for (const id of Array.isArray(ids) ? ids : []) {
    if (typeof id === "string" && !tracked.value.some((x) => x.id === id)) {
      tracked.value = [...tracked.value, { id, task: null, addedAt: Date.now() }];
    }
  }
  if (tracked.value.length) { start(); void pollOnce(); }
}

/** 登出時清空（同一個分頁換人登入，不該看到前一個人的作業） */
export function clearTracked(): void {
  tracked.value = [];
  callbacks.clear();
  stop();
  persist();
}

export function useTaskTracker() {
  function dismiss(id: string): void {
    tracked.value = tracked.value.filter((x) => x.id !== id);
    callbacks.delete(id);
    persist();
  }
  function dismissFinished(): void {
    tracked.value = tracked.value.filter(isActive);
    persist();
  }
  return { tracked, dismiss, dismissFinished, isActive };
}
