import { apiClient } from "@/api/client";

export interface UserPreferences {
  locale: "zh-TW" | "en-US";
  theme: "light" | "dark" | "auto";
  timezone: string;
  calendar: "gregorian" | "minguo";
  page_size: number;
  default_section_id: string | null;
  dashboard_layout: Record<string, unknown> | null;
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
