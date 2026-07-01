import { apiClient } from "@/api/client";
import type { IPAddress } from "@/types";

// 進階→連線管理頁：列出所有已啟用 SSH 或 RDP、且目前使用者可連線的 IP（含兩旗標）
export async function listConnectionTargets(): Promise<IPAddress[]> {
  const { data } = await apiClient.get<IPAddress[]>("/api/v1/addresses/connections/targets");
  return data;
}

// ── by-user 已存 RDP 帳密（沿用 ssh-credentials 金庫，protocol=rdp；只回遮罩）──
export interface RdpCredential {
  id: string;
  label: string;
  username: string;
  auth_type: "password" | "key";
  protocol: string;
  domain: string | null;
  target_ip_id: string | null;
  has_password: boolean;
  has_private_key: boolean;
  last_used_at: string | null;
  created_at: string;
}
export async function listRdpCredentials(targetIpId?: string): Promise<RdpCredential[]> {
  const { data } = await apiClient.get<RdpCredential[]>("/api/v1/ssh-credentials", {
    params: { protocol: "rdp", ...(targetIpId ? { target_ip_id: targetIpId } : {}) },
  });
  return data;
}
export async function createRdpCredential(p: {
  label: string; username: string; domain?: string | null;
  target_ip_id?: string | null; password: string;
}): Promise<RdpCredential> {
  const { data } = await apiClient.post<RdpCredential>("/api/v1/ssh-credentials", {
    ...p, auth_type: "password", protocol: "rdp",
  });
  return data;
}
export async function deleteRdpCredential(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/ssh-credentials/${id}`);
}

export interface RdpTicket {
  ticket: string;
  ws_path: string;
  default_size: { width: number; height: number };
  has_saved_creds: boolean;
  clipboard_paste?: boolean;   // 管理者是否允許「控制端貼上文字到被控端」
  ttl: number;
}

// 換發短期一次性 ticket（之後用它開 WebSocket）。注意帶 /api/v1 首碼。
export async function requestRdpTicket(addressId: string): Promise<RdpTicket> {
  const { data } = await apiClient.post<RdpTicket>(
    `/api/v1/addresses/${addressId}/rdp/ticket`,
  );
  return data;
}

// 由 ws_path + ticket 組出完整 WebSocket URL（同源，沿用目前頁面協定/host）。
export function buildRdpWsUrl(wsPath: string, ticket: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${wsPath}?ticket=${encodeURIComponent(ticket)}`;
}
