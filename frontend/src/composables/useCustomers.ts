/**
 * Customer 清單共用 composable。
 *
 * - 第一次呼叫會打 API；之後共享同一份資料
 * - reload() 強制重新拉 (新增 customer 後可呼叫)
 * - 提供 options(NSelect 用)、labelById、map
 */
import { computed, ref } from "vue";
import { listCustomers, type Customer } from "@/api/customers";

const all = ref<Customer[]>([]);
const loading = ref(false);
const loaded = ref(false);

async function reload(): Promise<void> {
  loading.value = true;
  try {
    const res = await listCustomers({ pageSize: 500 });
    all.value = res.items;
    loaded.value = true;
  } finally {
    loading.value = false;
  }
}

async function ensureLoaded(): Promise<void> {
  if (loaded.value || loading.value) return;
  await reload();
}

export function useCustomers() {
  const options = computed(() =>
    all.value.map((c) => ({
      label: (c.title && c.title !== c.name) ? `${c.name} (${c.title})` : c.name,
      value: c.id,
    })),
  );

  const map = computed<Record<string, Customer>>(() =>
    Object.fromEntries(all.value.map((c) => [c.id, c])),
  );

  function labelFor(id: string | null | undefined): string {
    if (!id) return "—";
    const c = map.value[id];
    if (!c) return id.slice(0, 8) + "…";
    return c.title || c.name;
  }

  return {
    customers: all,
    loading,
    options,
    map,
    labelFor,
    ensureLoaded,
    reload,
  };
}
