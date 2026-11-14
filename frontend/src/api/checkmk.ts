import { apiClient } from "@/api/client";
import type { Paginated } from "@/types";

// Checkmk 整合。路徑帶 /api/v1 前綴（baseURL 為 /）。

export interface CheckmkInstance {
  id: string;
  name: string;
  api_url: string;
  api_user: string;
  enabled: boolean;
  verify_tls: boolean;
  has_api_secret: boolean;
  scope_subnet_ids: string[] | null;
  sync_interval_seconds: number;
  description: string | null;
  last_sync_at: string | null;
  last_error: string | null;
}

export interface CheckmkWrite {
  name: string;
  api_url: string;
  api_user: string;
  api_secret?: string;
  enabled?: boolean;
  verify_tls?: boolean;
  scope_subnet_ids?: string[];
  sync_interval_seconds?: number;
  description?: string;
}

export interface CheckmkHealth {
  version?: string | null;
  edition?: string | null;
  site?: string | null;
  hosts_readable?: boolean;
  host_count?: number | null;
  error?: string;
}

export interface CheckmkHost {
  id: string;
  host_name: string;
  alias: string | null;
  site: string | null;
  /** up | down | unreachable | pending | unknown */
  state: string | null;
  checks_enabled: boolean;
  downtime: boolean;
  ip: string | null;
  addresses: string[] | null;
  folder: string | null;
  groups: string[] | null;
  labels: Record<string, string> | null;
  output: string | null;
  last_check: string | null;
  ip_address_id: string | null;
  synced_at: string | null;
}

export interface CheckmkGapRow {
  ip_address_id: string;
  ip: string | null;
  hostname: string | null;
}

export async function listCheckmk(): Promise<Paginated<CheckmkInstance>> {
  const { data } = await apiClient.get("/api/v1/checkmk", { params: { page_size: 200 } });
  return data;
}

export async function createCheckmk(payload: CheckmkWrite): Promise<CheckmkInstance> {
  const { data } = await apiClient.post("/api/v1/checkmk", payload);
  return data;
}

export async function updateCheckmk(id: string, payload: Partial<CheckmkWrite>): Promise<CheckmkInstance> {
  const { data } = await apiClient.patch(`/api/v1/checkmk/${id}`, payload);
  return data;
}

export async function deleteCheckmk(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/checkmk/${id}`);
}

export async function testCheckmk(id: string): Promise<CheckmkHealth> {
  const { data } = await apiClient.post(`/api/v1/checkmk/${id}/test`);
  return data;
}

export async function syncCheckmk(id: string): Promise<{ task_id: string }> {
  const { data } = await apiClient.post(`/api/v1/checkmk/${id}/sync`);
  return data;
}

export async function listCheckmkHosts(id: string, q?: string): Promise<{ items: CheckmkHost[] }> {
  const { data } = await apiClient.get(`/api/v1/checkmk/${id}/hosts`, { params: q ? { q } : {} });
  return data;
}

export async function checkmkCoverageGap(
  id: string, subnetIds?: string[],
): Promise<{ items: CheckmkGapRow[]; count: number; scope: string }> {
  const { data } = await apiClient.get(`/api/v1/checkmk/${id}/coverage-gap`, {
    params: subnetIds?.length ? { subnet_ids: subnetIds } : {},
  });
  return data;
}
