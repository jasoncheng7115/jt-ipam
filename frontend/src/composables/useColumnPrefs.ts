/**
 * Per-user column visibility preference.
 *
 * 從 /api/v1/me/preferences 拉 table_columns，本機 localStorage 快取一份。
 * 切換可見欄位時馬上更新 UI + 後台 patch；patch 失敗回滾。
 *
 * 用法 (在 view 內)：
 *
 *   const { visibleKeys, setVisible, allColumns, isVisible } = useColumnPrefs(
 *     "addresses",
 *     ["live", "ip", "hostname", "mac", "state", "discovery_source"],   // 全部選用欄位 key
 *     ["live", "ip", "hostname", "state"],                              // 預設可見
 *   );
 *
 *   const filteredCols = computed(() => allColumns.filter((c) => isVisible(c.key)));
 */

import { ref, watch } from "vue";
import { apiClient } from "@/api/client";

const LS_KEY = "jt-ipam:table_columns";
// 記錄「使用者曾被提供過的欄位集合」（每表一份，localStorage）。用來區分
// 「新加的預設欄位（從沒被提供過 → 應自動顯示）」與「使用者刻意隱藏的欄位」。
const SEEN_KEY = "jt-ipam:table_columns_seen";

// 全局 cache(一個 SPA session 內共用一份)
const cache = ref<Record<string, string[]>>({});
let loaded = false;

function loadSeen(): Record<string, string[]> {
  try { return JSON.parse(localStorage.getItem(SEEN_KEY) || "{}") ?? {}; } catch { return {}; }
}
function markSeen(tableKey: string, allKeys: string[]): void {
  const s = loadSeen();
  s[tableKey] = [...new Set([...(s[tableKey] ?? []), ...allKeys])];
  try { localStorage.setItem(SEEN_KEY, JSON.stringify(s)); } catch { /* ignore */ }
}
// 既有偏好 + 自動補上「新加的預設欄位」（在 defaultVisible/allKeys、但使用者從沒被提供過）。
// 使用者一旦在欄位選單做過選擇（setVisible→markSeen），其隱藏選擇就會被尊重、不再自動補。
function withNewDefaults(
  saved: string[], allKeys: string[], defaultVisible: string[], tableKey: string,
): string[] {
  const seen = loadSeen()[tableKey] ?? [];
  const adds = defaultVisible.filter(
    (k) => allKeys.includes(k) && !seen.includes(k) && !saved.includes(k),
  );
  return adds.length ? [...saved, ...adds] : saved;
}

async function loadCache(): Promise<void> {
  if (loaded) return;
  loaded = true;
  // 1. 先讀 localStorage(避免登入後第一次拉時的閃爍)
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (raw) cache.value = JSON.parse(raw) ?? {};
  } catch {}
  // 2. 再拉後端覆寫 (authoritative)
  try {
    const { data } = await apiClient.get<{ table_columns: Record<string, string[]> | null }>(
      "/api/v1/me/preferences",
    );
    if (data?.table_columns) {
      cache.value = data.table_columns;
      localStorage.setItem(LS_KEY, JSON.stringify(cache.value));
    }
  } catch {
    // 沒登入 / API 失敗 → 用 localStorage 版本就好
  }
}

let saveTimer: ReturnType<typeof setTimeout> | null = null;
async function persist(): Promise<void> {
  localStorage.setItem(LS_KEY, JSON.stringify(cache.value));
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    try {
      await apiClient.patch("/api/v1/me/preferences", { table_columns: cache.value });
    } catch {
      // 後端失敗不回滾 UI；下次重整時會以後端為準 (loadCache)
    }
  }, 400);
}

export function useColumnPrefs(
  tableKey: string,
  allKeys: string[],
  defaultVisible: string[],
) {
  void loadCache();
  const saved0 = cache.value[tableKey];
  const initial = saved0
    ? withNewDefaults(saved0, allKeys, defaultVisible, tableKey)
    : [...defaultVisible];
  const visibleKeys = ref<string[]>([...initial]);

  // 同步 cache → local（後端載入 / 其他 view 改了）；同樣補上未曾提供過的新預設欄位
  watch(
    () => cache.value[tableKey],
    (v) => { if (v) visibleKeys.value = withNewDefaults(v, allKeys, defaultVisible, tableKey); },
  );

  function isVisible(key: string): boolean {
    return visibleKeys.value.includes(key);
  }

  function setVisible(keys: string[]) {
    // 過濾掉不在 allKeys 裡的 (防 stale)
    const filtered = keys.filter((k) => allKeys.includes(k));
    visibleKeys.value = filtered;
    cache.value[tableKey] = filtered;
    markSeen(tableKey, allKeys);   // 使用者已做過明確選擇 → 之後尊重其隱藏、不再自動補
    void persist();
  }

  function reset() {
    setVisible([...defaultVisible]);
  }

  return { visibleKeys, isVisible, setVisible, reset, allKeys };
}
