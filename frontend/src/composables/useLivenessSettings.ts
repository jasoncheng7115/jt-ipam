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
const LS_SRC = "jt-ipam:liveness_sources";

/**
 * 哪些證據算「上線」（全域系統設定）。**ARP 預設不勾**：它證明的是「某個 MAC↔IP
 * 對應被學到過」，不是機器現在活著，而且 LibreNMS 的 ARP API 連時間都不回 ——
 * 來源設備的快取不老化，關機的機器也會一直看起來剛剛才出現。
 */
export const livenessSources = ref<string[]>(
  JSON.parse(localStorage.getItem(LS_SRC) || '["scanner","librenms"]'),
);

export const onlineGraceMinutes = ref<number>(
  Number(localStorage.getItem(LS_KEY) || "30") || 30,
);

let loaded = false;
async function loadOnce() {
  if (loaded) return;
  loaded = true;
  try {
    // 上線判定閾值改為「全域系統設定」（管理員設），不再是個人偏好
    const { data } = await apiClient.get<{ minutes?: number; sources?: string[] }>(
      "/api/v1/system/online-grace");
    if (data?.minutes && data.minutes > 0) {
      onlineGraceMinutes.value = data.minutes;
      localStorage.setItem(LS_KEY, String(data.minutes));
    }
    if (Array.isArray(data?.sources)) {
      livenessSources.value = data.sources;
      localStorage.setItem(LS_SRC, JSON.stringify(data.sources));
    }
  } catch {}
}
void loadOnce();

// 由管理員在「系統設定」調整（全域）。
export async function setOnlineGraceMinutes(n: number): Promise<void> {
  // 參數防呆，不是給使用者看的文案（目前沒有呼叫端；真要上畫面時，訊息要由呼叫端翻譯）
  if (n < 1 || n > 43200) throw new Error("onlineGraceMinutes out of range (1-43200)");
  onlineGraceMinutes.value = n;
  localStorage.setItem(LS_KEY, String(n));
  await apiClient.put("/api/v1/system/online-grace",
    { minutes: n, sources: livenessSources.value });
}

export async function setLivenessSources(list: string[]): Promise<void> {
  livenessSources.value = list;
  localStorage.setItem(LS_SRC, JSON.stringify(list));
  await apiClient.put("/api/v1/system/online-grace",
    { minutes: onlineGraceMinutes.value, sources: list });
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
 * 有 scanner/LibreNMS/DNS/ARP last_seen 就照時間分級；完全沒有任何線上記錄時：
 *   - exclude_from_ping(刻意不偵測)→ 未知 (灰)
 *   - 所屬 subnet 沒啟用掃描 (subnet_scan_enabled === false) → 未知 (灰)
 *       根本沒主動偵測，標離線紅燈會誤導
 *   - 其餘 → 離線 (紅)  ← 已登記、有掃描、但閾值內無任何上線記錄＝離線
 */
export function classifyAddressLiveness(addr: {
  last_seen_scanner?: string | null;
  last_seen_librenms?: string | null;
  last_seen_arp?: string | null;
  last_seen_dns?: string | null;
  last_seen_wazuh?: string | null;
  last_seen_zabbix?: string | null;
  last_seen_checkmk?: string | null;
  /** 防火牆給的逐來源觀測時間（`arp:opnsense` / `lease:pfsense` …） */
  arp_seen?: Record<string, string> | null;
  exclude_from_ping?: boolean | null;
  subnet_scan_enabled?: boolean | null;
  liveness_from_device?: boolean | null;
  device_id?: string | null;
}): LivenessKind {
  // 刻意不偵測（exclude_from_ping）或所屬 subnet 沒啟用掃描時：根本沒主動探測，
  // 不論有沒有舊的 last_seen，都不該顯示「離線(紅)」——過期最多降為未知(灰)。
  // 勾了「上線狀態跟著設備」卻沒掛設備（2026-10-14，多 LAN 路由器的閘道）：沒有設備可以跟，同樣不算離線
  const noProbe = !!addr.exclude_from_ping || addr.subnet_scan_enabled === false
    || (!!addr.liveness_from_device && !addr.device_id);
  const ts = countedTimes(addr);
  if (ts.length) {
    const kind = classifyLiveness(Math.max(...ts));
    if (kind === "offline" && noProbe) return "unknown";
    return kind;
  }
  return noProbe ? "unknown" : "offline";
}

type EvidenceFields = {
  last_seen_scanner?: string | null;
  last_seen_librenms?: string | null;
  last_seen_arp?: string | null;
  last_seen_dns?: string | null;
  last_seen_wazuh?: string | null;
  last_seen_zabbix?: string | null;
  last_seen_checkmk?: string | null;
  arp_seen?: Record<string, string> | null;
};

/** 會算數的上線證據時間（ms），依「上線判定」勾選的來源 */
function countedTimes(addr: EvidenceFields): number[] {
  const use = livenessSources.value;
  return [
    use.includes("scanner") ? addr.last_seen_scanner : null,
    use.includes("librenms") ? addr.last_seen_librenms : null,
    use.includes("wazuh") ? addr.last_seen_wazuh : null,
    use.includes("zabbix") ? addr.last_seen_zabbix : null,
    use.includes("checkmk") ? addr.last_seen_checkmk : null,
    // last_seen_dns（AdGuard）刻意不算：它只代表「AdGuard 的設定裡有這個 IP」（固定用戶端、
    // DNS 改寫），每輪同步都蓋成現在 —— 關機的機器也一直亮綠燈（2026-09-26 稽核）。後端也不採用。
    use.includes("arp") || use.includes("arp:librenms") ? addr.last_seen_arp : null,
    // 防火牆逐來源：只算被勾選的（`lease:*` 這種不會過期的預設沒被勾）
    ...Object.entries(addr.arp_seen || {})
      .filter(([k]) => use.includes(k))
      .map(([, v]) => v),
  ]
    .filter(Boolean)
    .map((s) => new Date(s as string).getTime());
}

// 兩邊時間的容許誤差：掃描代理／ARP 快取在機器換址後還會殘留幾個小時
const MOVE_SLACK_MS = 24 * 3600 * 1000;

/**
 * 同一個 MAC 的另一個位址：這台機器是不是「搬過去了」（而不是同一台的另一個介面）。
 *
 * 判準：這裡曾經有會算數的證據，而那個 MAC 是在這裡最後一次出現之後（容許一天）才開始出現在那裡。
 * 正式環境 2026-10-20：主機重灌換了位址，舊位址的頁面卻建議「上線狀態跟著設備」，照做的話舊位址會一直顯示上線。
 * 多 LAN 路由器的閘道：要嘛這裡從來不回探測（沒有證據），要嘛兩邊長期同時在 —— 都不算搬走。
 */
/** 會算數的證據最後一次出現的時間（ms）；沒有就 null */
export function lastCountedAt(addr: EvidenceFields): number | null {
  const ts = countedTimes(addr);
  return ts.length ? Math.max(...ts) : null;
}

export function looksMoved(addr: EvidenceFields, sibling: { mac_since?: string | null }): boolean {
  const ts = countedTimes(addr);
  if (!ts.length || !sibling.mac_since) return false;
  return new Date(sibling.mac_since).getTime() >= Math.max(...ts) - MOVE_SLACK_MS;
}

/**
 * 這個 IP 的「上線」是不是**只**靠 ARP 撐著。
 *
 * ARP 證據沒有時間概念：LibreNMS 的 ARP API 不回任何時間欄位，我們只能因為
 * 「這筆還在清單裡」就蓋上同步當下的時間。來源設備（例如 AP／路由器）的 ARP 快取
 * 不老化的話，機器早就關了也會一直看起來「剛剛才看到」。所以要標示出來，
 * 讓看的人知道這個綠燈的可信度和實際探測到的不一樣。
 */
export function isArpOnlyEvidence(addr: {
  last_seen_scanner?: string | null;
  last_seen_librenms?: string | null;
  last_seen_dns?: string | null;
  last_seen_arp?: string | null;
  last_seen_wazuh?: string | null;
  last_seen_zabbix?: string | null;
  last_seen_checkmk?: string | null;
  arp_seen?: Record<string, string> | null;
}): boolean {
  if (!addr.last_seen_arp) return false;
  // 防火牆自己的 ARP 表／VPN 連線會逾時淘汰，那是有時間概念的證據 —— 有它撐著
  // 就不算「只靠 ARP」。DHCP 租約（lease:*）不算，租期比開機時間長得多。
  const fw = Object.keys(addr.arp_seen || {}).some((k) => !k.startsWith("lease:"));
  return !addr.last_seen_scanner && !addr.last_seen_librenms
    && !addr.last_seen_wazuh && !addr.last_seen_zabbix && !addr.last_seen_checkmk && !fw;
}
