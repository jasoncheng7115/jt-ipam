/**
 * 全域 reactive 的「上線判定」設定 — 從 user_preferences 拉，可全 SPA 共享。
 *
 * online_grace_minutes：last_seen 超過這數值 (分鐘) 就視為離線。
 *
 * 規則：
 *   - 0 ~ grace          → 上線 (綠)
 *   - grace ~ grace*48   → 近期出現 (橘)
 *   - 其它 / 超過         → 離線 (紅)
 *   - 完全沒 last_seen    → 未知 (灰)
 */
import { ref } from "vue";
import { apiClient } from "@/api/client";

const LS_KEY = "jt-ipam:online_grace_minutes";

export const onlineGraceMinutes = ref<number>(
  Number(localStorage.getItem(LS_KEY) || "30") || 30,
);

let loaded = false;
async function loadOnce() {
  if (loaded) return;
  loaded = true;
  try {
    // 上線判定閾值改為「全域系統設定」（管理員設），不再是個人偏好
    const { data } = await apiClient.get<{ minutes?: number }>("/api/v1/system/online-grace");
    if (data?.minutes && data.minutes > 0) {
      onlineGraceMinutes.value = data.minutes;
      localStorage.setItem(LS_KEY, String(data.minutes));
    }
  } catch {}
}
void loadOnce();

// 由管理員在「系統設定」調整（全域）。
export async function setOnlineGraceMinutes(n: number): Promise<void> {
  if (n < 1 || n > 43200) throw new Error("超出範圍 (1 ~ 43200 分鐘)");
  onlineGraceMinutes.value = n;
  localStorage.setItem(LS_KEY, String(n));
  await apiClient.put("/api/v1/system/online-grace", { minutes: n });
}

/** 根據最新 last_seen 時戳 (ms) 算狀態。 */
export type LivenessKind = "online" | "stale" | "offline" | "unknown";

// 近期出現窗口 = 上線閾值的倍數（過了上線閾值、但還在這個窗口內＝可能剛漏掃/抖動）。
// 超過就視為離線（避免「3 小時沒回應」還被當近期出現）。
const STALE_FACTOR = 4;

export function classifyLiveness(newestMs: number | null): LivenessKind {
  if (!newestMs) return "unknown";
  const grace = onlineGraceMinutes.value || 30;
  const ageMin = (Date.now() - newestMs) / 60000;
  if (ageMin <= grace) return "online";
  if (ageMin <= grace * STALE_FACTOR) return "stale";   // 例：30min 閾值 → 近期出現 = 30min~2h
  return "offline";
}

/**
 * 已登記 IP 的存活判定 (給指示計 / 狀態燈用)。
 *
 * 有 scanner/LibreNMS/DNS last_seen 就照時間分級；完全沒有任何線上記錄時：
 *   - exclude_from_ping(刻意不偵測)→ 未知 (灰)
 *   - 所屬 subnet 沒啟用掃描 (subnet_scan_enabled === false) → 未知 (灰)
 *       根本沒主動偵測，標離線紅燈會誤導
 *   - 其餘 → 離線 (紅)  ← 已登記、有掃描、但閾值內無任何上線記錄＝離線
 */
export function classifyAddressLiveness(addr: {
  last_seen_scanner?: string | null;
  last_seen_librenms?: string | null;
  last_seen_dns?: string | null;
  exclude_from_ping?: boolean | null;
  subnet_scan_enabled?: boolean | null;
}): LivenessKind {
  // 刻意不偵測（exclude_from_ping）或所屬 subnet 沒啟用掃描時：根本沒主動探測，
  // 不論有沒有舊的 last_seen，都不該顯示「離線(紅)」——過期最多降為未知(灰)。
  const noProbe = !!addr.exclude_from_ping || addr.subnet_scan_enabled === false;
  const ts = [addr.last_seen_scanner, addr.last_seen_librenms, addr.last_seen_dns]
    .filter(Boolean)
    .map((s) => new Date(s as string).getTime());
  if (ts.length) {
    const kind = classifyLiveness(Math.max(...ts));
    if (kind === "offline" && noProbe) return "unknown";
    return kind;
  }
  return noProbe ? "unknown" : "offline";
}
