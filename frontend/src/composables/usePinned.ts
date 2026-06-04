/**
 * 通用「釘選常用項目」composable — 以 localStorage 存一組 ID（每個 namespace 一份）。
 * 用於機房 / 機櫃等的「釘選常用」，把釘選的排到清單最前面。
 * （子網路另有 usePinnedSubnets，存後端 prefs；這裡是輕量、純前端的常用釘選。）
 */
import { ref } from "vue";

const cache: Record<string, ReturnType<typeof make>> = {};

function make(namespace: string) {
  const storeKey = `jtipam.pinned.${namespace}`;
  let initial: string[] = [];
  try { initial = JSON.parse(localStorage.getItem(storeKey) || "[]"); } catch { /* ignore */ }
  const ids = ref<string[]>(Array.isArray(initial) ? initial : []);
  // 直接在 toggle 內同步寫 localStorage（不靠 watch；watch 會綁到第一個呼叫的元件
  // 的 effect scope，該元件卸除後就停止，導致釘選不再被保存）。
  function persist(): void {
    try { localStorage.setItem(storeKey, JSON.stringify(ids.value)); } catch { /* ignore */ }
  }

  function isPinned(id: string): boolean { return ids.value.includes(id); }
  function toggle(id: string): void {
    const i = ids.value.indexOf(id);
    if (i >= 0) ids.value.splice(i, 1);
    else ids.value.push(id);
    persist();
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
