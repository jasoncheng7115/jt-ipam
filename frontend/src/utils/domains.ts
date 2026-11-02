/** 網域畫面共用的顯示規則（清單與詳細資料一致）。 */
import type { Domain } from "@/api/domains";

type T = (k: string, p?: Record<string, unknown>) => string;
type Te = (k: string) => boolean;

/** 到期標籤：已到期紅、30 天內黃、其他灰；沒有到期日不顯示 */
export function expiryTag(d: Domain, t: T): { type: "error" | "warning" | "default"; text: string } | null {
  if (d.days_left == null) return null;
  if (d.days_left < 0) return { type: "error", text: t("domains.expired_days", { n: -d.days_left }) };
  if (d.days_left === 0) return { type: "error", text: t("domains.expires_today") };
  return { type: d.days_left <= 30 ? "warning" : "default", text: t("domains.days_left", { n: d.days_left }) };
}

/** RDAP 查詢的錯誤代碼 → 一句話（翻不到就顯示代碼） */
export function rdapErrorText(code: string | null, t: T, te: Te): string {
  if (!code) return "";
  const k = `domains.rdap_err.${code}`;
  return te(k) ? t(k) : code;
}
