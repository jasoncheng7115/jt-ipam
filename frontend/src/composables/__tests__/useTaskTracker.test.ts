/**
 * 背景作業即時回報（客戶 2026-10-10：「設定完要先去看工作跑完了沒，再去看 log 才知道狀態」）。
 * 按下拉取／同步／匯入之後，右下角直接顯示進度與結果，不用再去作業頁、日誌找。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BackgroundTask } from "@/api/tasks";

const state: Record<string, Partial<BackgroundTask> | "404"> = {};
vi.mock("@/api/tasks", () => ({
  getTask: vi.fn(async (id: string) => {
    const s = state[id];
    if (s === "404" || s === undefined) {
      const err = Object.assign(new Error("not found"), { response: { status: 404 } });
      throw err;
    }
    return { id, kind: "dns.sync", status: "pending", trigger: "manual", target_type: null, target_id: null,
             target_label: "ns1", actor_user_id: null, progress: 0, summary: null, error: null,
             queued_at: "2026-10-10T00:00:00Z", started_at: null, finished_at: null, ...s } as BackgroundTask;
  }),
}));

async function fresh() {
  vi.resetModules();
  return await import("@/composables/useTaskTracker");
}

describe("useTaskTracker", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    for (const k of Object.keys(state)) delete state[k];
    sessionStorage.clear();
  });
  afterEach(() => { vi.useRealTimers(); });

  it("追蹤到完成：顯示結果、只呼叫一次 onDone、之後不再輪詢", async () => {
    const m = await fresh();
    const { getTask } = await import("@/api/tasks");
    const onDone = vi.fn();
    state.a = { status: "running", progress: 40 };
    m.trackTask("a", { onDone });
    await vi.advanceTimersByTimeAsync(0);
    const { tracked } = m.useTaskTracker();
    expect(tracked.value).toHaveLength(1);
    expect(tracked.value[0].task?.status).toBe("running");
    state.a = { status: "succeeded", progress: 100, summary: { pulled_zones: 2, pulled_records: 10 } };
    await vi.advanceTimersByTimeAsync(m.POLL_MS);
    expect(tracked.value[0].task?.status).toBe("succeeded");
    expect(onDone).toHaveBeenCalledTimes(1);
    const calls = (getTask as any).mock.calls.length;
    await vi.advanceTimersByTimeAsync(m.POLL_MS * 3);
    expect((getTask as any).mock.calls.length).toBe(calls);
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("成功的過一陣子自動收起；失敗的留著，直到使用者關掉", async () => {
    const m = await fresh();
    state.ok = { status: "succeeded" };
    state.bad = { status: "failed", error: "DNSAdapterError: UCS UDM REST 401" };
    m.trackTask("ok");
    m.trackTask("bad");
    await vi.advanceTimersByTimeAsync(0);
    const { tracked, dismiss } = m.useTaskTracker();
    expect(tracked.value.map((x) => x.id).sort()).toEqual(["bad", "ok"]);
    await vi.advanceTimersByTimeAsync(m.SUCCESS_TTL_MS + m.POLL_MS);
    expect(tracked.value.map((x) => x.id)).toEqual(["bad"]);
    dismiss("bad");
    expect(tracked.value).toHaveLength(0);
  });

  it("同一個作業追蹤兩次不重複；找不到的作業標成遺失、不再輪詢", async () => {
    const m = await fresh();
    state.x = { status: "running" };
    m.trackTask("x");
    m.trackTask("x");
    m.trackTask("gone");
    await vi.advanceTimersByTimeAsync(0);
    const { tracked } = m.useTaskTracker();
    expect(tracked.value.filter((t) => t.id === "x")).toHaveLength(1);
    expect(tracked.value.find((t) => t.id === "gone")?.lost).toBe(true);
  });

  it("重新整理頁面後接著追：還沒跑完的作業記在 sessionStorage", async () => {
    let m = await fresh();
    state.r = { status: "running" };
    m.trackTask("r");
    await vi.advanceTimersByTimeAsync(0);
    expect(sessionStorage.getItem(m.STORAGE_KEY)).toContain("r");
    m = await fresh();                     // 模擬重新載入
    m.restoreTracked();
    await vi.advanceTimersByTimeAsync(0);
    expect(m.useTaskTracker().tracked.value.map((t) => t.id)).toEqual(["r"]);
  });
});
