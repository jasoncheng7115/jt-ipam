import { apiClient } from "@/api/client";
import type { IPAddress } from "@/types";

// 連線管理頁：列出所有已啟用 SSH 且目前使用者可連線的 IP
export async function listSshTargets(): Promise<IPAddress[]> {
  const { data } = await apiClient.get<IPAddress[]>("/api/v1/addresses/ssh/targets");
  return data;
}

// ── by-user 已存帳密（憑證只回遮罩，永不含明文）──
export interface SshCredential {
  id: string;
  label: string;
  username: string;
  auth_type: "password" | "key";
  target_ip_id: string | null;
  has_password: boolean;
  has_private_key: boolean;
  last_used_at: string | null;
  created_at: string;
}
export async function listSshCredentials(targetIpId?: string): Promise<SshCredential[]> {
  const { data } = await apiClient.get<SshCredential[]>("/api/v1/ssh-credentials", {
    params: targetIpId ? { target_ip_id: targetIpId } : {},
  });
  return data;
}
export async function createSshCredential(p: {
  label: string; username: string; auth_type: "password" | "key";
  target_ip_id?: string | null; password?: string; private_key?: string; passphrase?: string;
}): Promise<SshCredential> {
  const { data } = await apiClient.post<SshCredential>("/api/v1/ssh-credentials", p);
  return data;
}
export async function deleteSshCredential(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/ssh-credentials/${id}`);
}

export interface SshTicket {
  ticket: string;
  ws_path: string;
  host_key_pinned: boolean;
  default_port: number;
  ttl: number;
}

// 換發短期一次性 ticket（之後用它開 WebSocket）。注意帶 /api/v1 首碼。
export async function requestSshTicket(addressId: string): Promise<SshTicket> {
  const { data } = await apiClient.post<SshTicket>(
    `/api/v1/addresses/${addressId}/ssh/ticket`,
  );
  return data;
}

// 由 ws_path + ticket 組出完整 WebSocket URL（同源，沿用目前頁面協定/host）。
export function buildSshWsUrl(wsPath: string, ticket: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${wsPath}?ticket=${encodeURIComponent(ticket)}`;
}
