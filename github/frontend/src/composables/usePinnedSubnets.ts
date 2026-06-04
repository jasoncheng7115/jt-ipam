/**
 * Dashboard / SubnetDetail 用的「釘選子網路」狀態共享。
 *
 * 後端存在 user_preferences.pinned_subnet_ids (JSONB list)。
 * 此 composable 全域共享一份 ref，避免每處各自打 API。
 */
import { ref } from "vue";
import { getPreferences, updatePreferences } from "@/api/preferences";

const pinned = ref<string[]>([]);
const loaded = ref(false);
const loading = ref(false);

async function ensureLoaded(): Promise<void> {
  if (loaded.value || loading.value) return;
  loading.value = true;
  try {
    const p = await getPreferences();
    pinned.value = (p.pinned_subnet_ids ?? []).map(String);
    loaded.value = true;
  } finally {
    loading.value = false;
  }
}

async function pin(subnetId: string): Promise<void> {
  await ensureLoaded();
  if (pinned.value.includes(subnetId)) return;
  const next = [...pinned.value, subnetId];
  const p = await updatePreferences({ pinned_subnet_ids: next });
  pinned.value = (p.pinned_subnet_ids ?? []).map(String);
}

async function unpin(subnetId: string): Promise<void> {
  await ensureLoaded();
  if (!pinned.value.includes(subnetId)) return;
  const next = pinned.value.filter((x) => x !== subnetId);
  const p = await updatePreferences({ pinned_subnet_ids: next });
  pinned.value = (p.pinned_subnet_ids ?? []).map(String);
}

async function toggle(subnetId: string): Promise<void> {
  await ensureLoaded();
  if (pinned.value.includes(subnetId)) {
    await unpin(subnetId);
  } else {
    await pin(subnetId);
  }
}

function isPinned(subnetId: string): boolean {
  return pinned.value.includes(subnetId);
}

export function usePinnedSubnets() {
  return { pinned, loaded, loading, ensureLoaded, pin, unpin, toggle, isPinned };
}
