import { apiClient } from "@/api/client";
import type { Paginated } from "@/types";

export interface Notification {
  id: string;
  severity: string;
  title: string;
  body: string | null;
  title_key: string | null;
  body_key: string | null;
  params: Record<string, unknown> | null;
  link: string | null;
  object_type: string | null;
  object_id: string | null;
  read_at: string | null;
  created_at: string;
}

export async function listNotifications(
  unreadOnly = false,
  page = 1,
  pageSize = 50,
): Promise<Paginated<Notification>> {
  const { data } = await apiClient.get<Paginated<Notification>>("/api/v1/notifications", {
    params: { unread_only: unreadOnly, page, page_size: pageSize },
  });
  return data;
}

export async function markRead(id: string): Promise<void> {
  await apiClient.post(`/api/v1/notifications/${id}/read`);
}

export async function markAllRead(): Promise<void> {
  await apiClient.post("/api/v1/notifications/read-all");
}
