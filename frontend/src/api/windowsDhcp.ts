import { apiClient } from "@/api/client";
import type { Paginated } from "@/types";

// Windows DHCP Server 整合（Beta）。路徑帶 /api/v1 前綴（baseURL 為 /）。

export interface WindowsDhcpServer {
  id: string;
  name: string;
  host: string;
  username: string;
  port: number;
  use_ssl: boolean;
  verify_tls: boolean;
  enabled: boolean;
  sync_scopes: boolean;
  sync_leases: boolean;
  sync_interval_seconds: number;
  description: string | null;
  scope_subnet_ids: string[] | null;
  last_sync_at: string | null;
  last_error: string | null;
}

export interface WindowsDhcpWrite {
  name: string;
  host: string;
  username: string;
  password?: string;
  port?: number;
  use_ssl?: boolean;
  verify_tls?: boolean;
  enabled?: boolean;
  sync_scopes?: boolean;
  sync_leases?: boolean;
  sync_interval_seconds?: number;
  description?: string;
  scope_subnet_ids?: string[];
}

export async function listWindowsDhcp(): Promise<Paginated<WindowsDhcpServer>> {
  const { data } = await apiClient.get<Paginated<WindowsDhcpServer>>(
    "/api/v1/windows-dhcp/servers", { params: { page: 1, page_size: 200 } },
  );
  return data;
}

export async function createWindowsDhcp(p: WindowsDhcpWrite): Promise<WindowsDhcpServer> {
  const { data } = await apiClient.post<WindowsDhcpServer>("/api/v1/windows-dhcp/servers", p);
  return data;
}

export async function updateWindowsDhcp(id: string, p: Partial<WindowsDhcpWrite>): Promise<WindowsDhcpServer> {
  const { data } = await apiClient.patch<WindowsDhcpServer>(`/api/v1/windows-dhcp/servers/${id}`, p);
  return data;
}

export async function deleteWindowsDhcp(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/windows-dhcp/servers/${id}`);
}

export async function testWindowsDhcp(id: string): Promise<{ host: string; scopes: number }> {
  const { data } = await apiClient.post(`/api/v1/windows-dhcp/servers/${id}/test`);
  return data;
}

export async function syncWindowsDhcp(id: string): Promise<{ task_id: string }> {
  const { data } = await apiClient.post(`/api/v1/windows-dhcp/servers/${id}/sync`);
  return data;
}
