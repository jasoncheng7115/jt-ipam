/**
 * 通用「釘選常用項目」composable — 跟著帳號存（後端 user_preferences.pinned，
 * 結構為 {namespace: [id,...]}），換瀏覽器 / 裝置都保留。
 * 用於機房 / 地點 / 機櫃等的「釘選常用」，把釘選的排到清單最前面。
 * （子網路另有 usePinnedSubnets，存 prefs.pinned_subnet_ids。）
 *
 * 為相容舊版 localStorage，首次載入若後端沒有但本機有，會自動搬上去一次。
 */
import { ref } from "vue";
import { getPreferences, updatePreferences } from "@/api/preferences";

const cache: Record<string, ReturnType<typeof make>> = {};

// 全 namespace 共用一份後端 pinned map 快取，避免互相覆蓋
const allPinned = ref<Record<string, string[]>>({});
let loaded = false;
let loadingPromise: Promise<void> | null = null;

async function ensureLoaded(): Promise<void> {
  if (loaded) return;
  if (loadingPromise) { await loadingPromise; return; }
  loadingPromise = (async () => {
    try {
      const p = await getPreferences();
      allPinned.value = { ...(p.pinned ?? {}) };
    } catch { /* 未登入或失敗 → 空 */ }
    loaded = true;
  })();
  await loadingPromise;
}

async function persistAll(): Promise<void> {
  try { await updatePreferences({ pinned: allPinned.value }); } catch { /* ignore */ }
}

function make(namespace: string) {
  const ids = ref<string[]>([]);
  const lsKey = `jtipam.pinned.${namespace}`;

  void ensureLoaded().then(() => {
    let arr = allPinned.value[namespace];
    // 舊版 localStorage 遷移：後端沒這個 namespace 但本機有 → 搬上去一次
    if (!arr) {
      try {
        const legacy = JSON.parse(localStorage.getItem(lsKey) || "[]");
        if (Array.isArray(legacy) && legacy.length) {
          arr = legacy.map(String);
          allPinned.value[namespace] = arr;
          void persistAll();
          localStorage.removeItem(lsKey);
        }
      } catch { /* ignore */ }
    }
    ids.value = arr ? [...arr] : [];
  });

  function isPinned(id: string): boolean { return ids.value.includes(id); }
  function toggle(id: string): void {
    const i = ids.value.indexOf(id);
    if (i >= 0) ids.value.splice(i, 1);
    else ids.value.push(id);
    allPinned.value[namespace] = [...ids.value];
    void persistAll();
  }
  /** 把釘選的排到最前面（穩定排序，其餘維持原順序） */
  function sortPinnedFirst<T extends { id: string }>(rows: T[]): T[] {
    return [...rows].sort((a, b) => Number(isPinned(b.id)) - Number(isPinned(a.id)));
  }
  return { ids, isPinned, toggle, sortPinnedFirst };
}

export function usePinned(namespace: string) {
  return (cache[namespace] ??= make(namespace));
}
