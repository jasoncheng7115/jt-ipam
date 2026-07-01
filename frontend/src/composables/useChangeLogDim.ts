/**
 * 異動記錄淡化：系統設定「超過 N 天的項目以淡色顯示」（管理→系統設定，預設 30 天）。
 * 全站共用、模組層快取一次（避免每個元件各打一次 API）。0 = 不淡化。
 */
import { ref } from "vue";
import { getUiDisplay } from "@/api/system";

const dimDays = ref<number>(30);
let loaded = false;
let inflight: Promise<void> | null = null;

async function ensureLoaded(): Promise<void> {
  if (loaded) return;
  if (!inflight) {
    inflight = getUiDisplay()
      .then((d) => { dimDays.value = d.change_log_dim_days; loaded = true; })
      .catch(() => { /* 靜默：維持預設 30 */ })
      .finally(() => { inflight = null; });
  }
  await inflight;
}

export function useChangeLogDim() {
  void ensureLoaded();
  // 該時間是否「超過 N 天」→ 需淡化。dimDays=0 視為關閉。
  function isOld(ts: string | number | Date | null | undefined): boolean {
    if (!ts || dimDays.value <= 0) return false;
    const t = new Date(ts).getTime();
    if (Number.isNaN(t)) return false;
    return (Date.now() - t) > dimDays.value * 86400_000;
  }
  return { dimDays, isOld };
}
