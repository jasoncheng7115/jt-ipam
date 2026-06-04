/**
 * 全站時間 / 日期格式化 helper。
 *
 * 後端統一給 ISO 8601 (UTC, e.g. "2026-05-27T05:19:41.328975Z")。
 * 前端統一用瀏覽器 locale 顯示，避免每個 view 自己拼字串。
 */

const PAD = (n: number) => n.toString().padStart(2, "0");

function _toDate(s: string | number | Date | null | undefined): Date | null {
  if (s == null || s === "") return null;
  const d = s instanceof Date ? s : new Date(s);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "2026-05-27 13:15:30"(本地時區) */
export function fmtDateTime(
  s: string | number | Date | null | undefined,
  fallback = "—",
): string {
  const d = _toDate(s);
  if (!d) return fallback;
  return `${d.getFullYear()}-${PAD(d.getMonth() + 1)}-${PAD(d.getDate())} ` +
         `${PAD(d.getHours())}:${PAD(d.getMinutes())}:${PAD(d.getSeconds())}`;
}

/** "2026-05-27"(本地時區) */
export function fmtDate(
  s: string | number | Date | null | undefined,
  fallback = "—",
): string {
  const d = _toDate(s);
  if (!d) return fallback;
  return `${d.getFullYear()}-${PAD(d.getMonth() + 1)}-${PAD(d.getDate())}`;
}

/** "5 分鐘前" / "2 小時前" / "3 天前"；超過 30 天回 fmtDate */
export function fmtRelative(
  s: string | number | Date | null | undefined,
  fallback = "—",
): string {
  const d = _toDate(s);
  if (!d) return fallback;
  const diff = Date.now() - d.getTime();
  if (diff < 0) return fmtDateTime(d);                            // 未來時間
  const sec = Math.floor(diff / 1000);
  if (sec < 60)     return `${sec} 秒前`;
  const min = Math.floor(sec / 60);
  if (min < 60)     return `${min} 分鐘前`;
  const hr  = Math.floor(min / 60);
  if (hr < 24)      return `${hr} 小時前`;
  const day = Math.floor(hr / 24);
  if (day < 30)     return `${day} 天前`;
  return fmtDate(d);
}

/** 秒數差人話：65 → "1m 5s" */
export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const sec = Math.max(0, Math.floor(seconds));
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  if (m < 60) return `${m}m ${s}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}
