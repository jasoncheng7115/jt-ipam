import { apiClient } from "@/api/client";

export interface UserPreferences {
  locale: "zh-TW" | "en-US";
  theme: "light" | "dark" | "auto";
  timezone: string;
  calendar: "gregorian" | "minguo";
  page_size: number;
  // 每張表要顯示哪些欄位 (key 由 view 自訂，e.g. "addresses")
  table_columns: Record<string, string[]> | null;
  // Dashboard 常用子網路 UUID 清單
  pinned_subnet_ids: string[] | null;
}

export async function getPreferences(): Promise<UserPreferences> {
  const { data } = await apiClient.get<UserPreferences>("/api/v1/me/preferences");
  return data;
}

export async function updatePreferences(
  patch: Partial<UserPreferences>,
): Promise<UserPreferences> {
  const { data } = await apiClient.patch<UserPreferences>("/api/v1/me/preferences", patch);
  return data;
}
