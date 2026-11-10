import { ref } from "vue";
import { getDevice, listDevices } from "@/api/basic";

/**
 * 「選一台裝置」的下拉選項：搜尋走後端（`q`），跟 useIpOptions 同一個作法。
 *
 * 由來（GitHub issue #58 連帶）：NAT 頁的裝置選單用 `listDevices()` 只拿到第一頁（預設 200 台），
 * 再靠 `filterable` 在已載入的那幾百台裡找 —— 站台裝置一多，後面的就選不到、打字也搜不到。
 * 編輯既有資料時，已選的那台若不在第一批，`ensure()` 會補抓，選單才不會顯示 UUID。
 */
export function useDeviceOptions(pageSize = 50, debounceMs = 250) {
  const options = ref<{ label: string; value: string }[]>([]);
  const loading = ref(false);
  const labels = new Map<string, string>();

  const label = (name: string, type?: string | null) => (type ? `${name} (${type})` : name);

  async function search(q?: string) {
    loading.value = true;
    try {
      const res = await listDevices({ page: 1, pageSize, q: q || undefined });
      options.value = res.items.map((d) => {
        const l = label(d.name, d.type);
        labels.set(d.id, l);
        return { label: l, value: d.id };
      });
    } catch {
      options.value = [];
    } finally {
      loading.value = false;
    }
  }

  let timer: ReturnType<typeof setTimeout> | undefined;
  function onSearch(q: string) {
    clearTimeout(timer);
    timer = setTimeout(() => void search(q), debounceMs);
  }

  async function ensure(id?: string | null) {
    if (!id || labels.has(id)) return;
    try {
      const d = await getDevice(id);
      const l = label(d.name, d.type);
      labels.set(id, l);
      if (!options.value.some((o) => o.value === id)) options.value = [{ label: l, value: id }, ...options.value];
    } catch { /* 被刪掉了就顯示空白，不要壞掉 */ }
  }

  return { options, loading, search, onSearch, ensure };
}
