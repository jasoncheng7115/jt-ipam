/** 維護排程的時間文字（清單、詳細資料、物件頁按鈕共用，說法一致）。 */
import type { Window } from "@/api/maintenance";
import { fmtDate, fmtDateTime } from "@/utils/datetime";

type T = (k: string, p?: Record<string, unknown>) => string;

/** 星期的顯示順序：日、一、二…六（值是 0＝週一…6＝週日，跟後端一致） */
export const WEEKDAY_ORDER = [6, 0, 1, 2, 3, 4, 5];
export function sortWeekdays(days: number[]): number[] {
  return [...days].sort((a, b) => WEEKDAY_ORDER.indexOf(a) - WEEKDAY_ORDER.indexOf(b));
}

/** 時長的單位：分鐘、小時、天 */
export type DurationUnit = "minute" | "hour" | "day";
export const UNIT_MINUTES: Record<DurationUnit, number> = { minute: 1, hour: 60, day: 1440 };

/** 一鍵維護的選項文字：30 分鐘、1 小時、2 小時… */
export function durationLabel(m: number, t: T): string {
  return m < 60 ? t("maint.minutes_opt", { n: m }) : t("maint.hours_opt", { n: m / 60 });
}

/** 剩多久：「1 小時 20 分」「35 分」 */
export function remaining(until: string | null | undefined, t: T): string {
  if (!until) return "";
  const min = Math.max(0, Math.round((new Date(until).getTime() - Date.now()) / 60000));
  const h = Math.floor(min / 60);
  const m = min % 60;
  return h ? t("maint.remain_hm", { h, m }) : t("maint.remain_m", { m });
}

/** 維護時間精確到分就夠：「2026-11-03 02:00」；同一天的結束只寫「04:00」 */
function hm(s: string | null | undefined): string { return fmtDateTime(s).slice(0, 16); }
function span(a: string | null | undefined, b: string | null | undefined): string {
  const x = hm(a), y = hm(b);
  return x.slice(0, 10) === y.slice(0, 10) ? `${x} - ${y.slice(11)}` : `${x} - ${y}`;
}

/** 「每週二、四 02:00 - 04:00」或「11-03 02:00 - 11-03 04:00」 */
export function scheduleText(w: Window, t: T): string {
  if (w.schedule_type === "weekly") {
    const days = sortWeekdays(w.weekdays ?? []).map((d) => t(`maint.wd_${d}`)).join("、");
    const until = w.valid_until ? t("maint.until_date", { date: fmtDate(w.valid_until) }) : "";
    return `${t("maint.weekly_prefix", { days })} ${w.start_time} - ${w.end_time}${until ? `（${until}）` : ""}`;
  }
  return span(w.starts_at, w.ends_at);
}

/** 狀態那一行：進行中「到 14:30（剩 1 小時 20 分）」、即將開始「11-03 02:00 開始」 */
export function statusLine(w: Window, t: T): string {
  if (w.status === "active") return t("maint.active_until", { until: hm(w.current_end), left: remaining(w.current_end, t) });
  if (w.status === "upcoming") return t("maint.starts_at", { at: hm(w.next_start) });
  return t("maint.ended");
}
