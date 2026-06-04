/**
 * 自然排序比較器 — 讓含數字的字串（尤其 IP 位址）依數值大小排，
 * 而不是逐字元的字典序。
 *
 * 例：192.168.1.1 < 192.168.1.2 < 192.168.1.102
 * 字典序會把 192.168.1.102 排在 192.168.1.2 前面（"1" < "2"），這是錯的。
 *
 * 作法：把字串切成「數字段」與「非數字段」交錯的 chunk，
 * 兩邊同為數字段就比數值，否則比字串。可同時正確處理 IPv4、主機名、混合字串。
 */

const IPV4_RE = /^\d{1,3}(\.\d{1,3}){3}$/;

/** IPv4 → 32-bit 整數；非 IPv4 回 null */
function ipv4ToInt(s: string): number | null {
  if (!IPV4_RE.test(s)) return null;
  const parts = s.split(".").map(Number);
  if (parts.some((n) => n > 255)) return null;
  return ((parts[0] << 24) >>> 0) + (parts[1] << 16) + (parts[2] << 8) + parts[3];
}

/** 自然序比較：優先 IPv4 數值，其次數字段感知，最後 localeCompare */
export function cmpNatural(a: unknown, b: unknown): number {
  const as = String(a ?? "");
  const bs = String(b ?? "");

  // 兩邊都是 IPv4 → 直接比整數
  const ai = ipv4ToInt(as);
  const bi = ipv4ToInt(bs);
  if (ai != null && bi != null) return ai - bi;

  // 一般自然序：切成數字/非數字 chunk 逐段比
  const ac = as.match(/\d+|\D+/g) ?? [];
  const bc = bs.match(/\d+|\D+/g) ?? [];
  const n = Math.min(ac.length, bc.length);
  for (let i = 0; i < n; i++) {
    const x = ac[i], y = bc[i];
    const xn = /^\d/.test(x), yn = /^\d/.test(y);
    if (xn && yn) {
      const d = Number(x) - Number(y);
      if (d !== 0) return d;
    } else {
      const d = x.localeCompare(y);
      if (d !== 0) return d;
    }
  }
  return ac.length - bc.length;
}
