import { apiClient } from "@/api/client";

// ── by-user 已存 VNC 密碼（沿用 ssh-credentials 金庫，protocol=vnc；只回遮罩）──
export interface VncCredential {
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
export async function listVncCredentials(targetIpId?: string): Promise<VncCredential[]> {
  const { data } = await apiClient.get<VncCredential[]>("/api/v1/ssh-credentials", {
    params: { protocol: "vnc", ...(targetIpId ? { target_ip_id: targetIpId } : {}) },
  });
  return data;
}
export async function createVncCredential(p: {
  label: string; target_ip_id?: string | null; password: string;
}): Promise<VncCredential> {
  // 傳統 VNC 無帳號；金庫 username 欄必填 → 帶固定值 "vnc"（不用於認證）
  const { data } = await apiClient.post<VncCredential>("/api/v1/ssh-credentials", {
    ...p, username: "vnc", auth_type: "password", protocol: "vnc",
  });
  return data;
}
export async function deleteVncCredential(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/ssh-credentials/${id}`);
}

export interface VncTicket {
  ticket: string;
  ws_path: string;
  default_port: number;
  has_saved_creds: boolean;
  ttl: number;
}

// 換發短期一次性 ticket（之後用它開 WebSocket）。注意帶 /api/v1 首碼。
export async function requestVncTicket(addressId: string): Promise<VncTicket> {
  const { data } = await apiClient.post<VncTicket>(
    `/api/v1/addresses/${addressId}/vnc/ticket`,
  );
  return data;
}

export function buildVncWsUrl(wsPath: string, ticket: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${wsPath}?ticket=${encodeURIComponent(ticket)}`;
}
