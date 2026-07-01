import { apiClient } from "@/api/client";
import type { SshCredential } from "@/api/ssh";

export interface BmcTicket {
  ticket: string;
  ws_path: string;
  ttl: number;
}

// 換發短期一次性 ticket（之後用它開 WebSocket）。注意帶 /api/v1 首碼。
export async function requestBmcTicket(addressId: string): Promise<BmcTicket> {
  const { data } = await apiClient.post<BmcTicket>(
    `/api/v1/addresses/${addressId}/bmc/ticket`, {},
  );
  return data;
}

// 由 ws_path + ticket 組出完整 WebSocket URL（同源，沿用目前頁面協定/host）。
export function buildBmcWsUrl(wsPath: string, ticket: string): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}${wsPath}?ticket=${encodeURIComponent(ticket)}`;
}

// BMC 帳密沿用同一個個人加密金庫（ssh-credentials），以 protocol='bmc' 區分。
export async function listBmcCredentials(targetIpId?: string): Promise<SshCredential[]> {
  const { data } = await apiClient.get<SshCredential[]>("/api/v1/ssh-credentials", {
    params: { protocol: "bmc", ...(targetIpId ? { target_ip_id: targetIpId } : {}) },
  });
  return data;
}
export async function createBmcCredential(p: {
  label: string; username: string; password: string; target_ip_id?: string | null;
}): Promise<SshCredential> {
  const { data } = await apiClient.post<SshCredential>("/api/v1/ssh-credentials", {
    ...p, auth_type: "password", protocol: "bmc",
  });
  return data;
}
