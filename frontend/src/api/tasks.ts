import { apiClient } from "@/api/client";
import type { Paginated } from "@/types";

export interface BackgroundTask {
  id: string;
  kind: string;
  status: "pending" | "running" | "succeeded" | "failed" | "cancelled";
  trigger: "manual" | "scheduled";
  target_type: string | null;
  target_id: string | null;
  target_label: string | null;
  actor_user_id: string | null;
  progress: number;
  summary: Record<string, unknown> | null;
  error: string | null;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export async function listTasks(params: {
  status_in?: string;
  kind?: string;
  active_only?: boolean;
  page?: number;
  pageSize?: number;
} = {}): Promise<Paginated<BackgroundTask>> {
  const { data } = await apiClient.get<Paginated<BackgroundTask>>("/api/v1/tasks", {
    params: {
      status_in: params.status_in,
      kind: params.kind,
      active_only: params.active_only,
      page: params.page ?? 1,
      page_size: params.pageSize ?? 50,
    },
  });
  return data;
}

export async function getTask(id: string): Promise<BackgroundTask> {
  const { data } = await apiClient.get<BackgroundTask>(`/api/v1/tasks/${id}`);
  return data;
}
