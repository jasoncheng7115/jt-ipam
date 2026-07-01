import { apiClient } from "@/api/client";

// ── by-user 已存 PVE 帳密（沿用 ssh-credentials 金庫，protocol=pve；只回遮罩）──
export interface PveCredential {
  id: string;
  label: string;
  username: string;       // user@realm
  auth_type: "password" | "key";
  protocol: string;
  domain: string | null;
  target_ip_id: string | null;
  has_password: boolean;
  has_private_key: boolean;
  last_used_at: string | null;
  created_at: string;
}
export async function listPveCredentials(targetIpId?: string): Promise<PveCredential[]> {
  const { data } = await apiClient.get<PveCredential[]>("/api/v1/ssh-credentials", {
    params: { protocol: "pve", ...(targetIpId ? { target_ip_id: targetIpId } : {}) },
  });
  return data;
}
export async function createPveCredential(p: {
  label: string; target_ip_id?: string | null; username: string; password: string;
}): Promise<PveCredential> {
  const { data } = await apiClient.post<PveCredential>("/api/v1/ssh-credentials", {
    ...p, auth_type: "password", protocol: "pve",
  });
  return data;
}
export async function deletePveCredential(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/ssh-credentials/${id}`);
}

export interface NovncTicket {
  ticket: string;
  ws_path: string;
  kind: "vm" | "ct";        // vm → noVNC 圖形 / ct → xterm 終端機
  vnc_password: string;     // noVNC 的 RFB 密碼 / xterm term 認證用（PVE vncticket）
  pve_user: string;         // xterm（lxc）term 認證 first message 用
  has_saved_creds: boolean;
  ttl: number;
}

// 換發一次性 ticket：用 PVE 帳密（或金庫憑證）登入 + vncproxy/termproxy。注意帶 /api/v1 首碼。
export async function requestNovncTicket(
  addressId: string,
  body: { username?: string; password?: string; realm?: string; credential_id?: string },
): Promise<NovncTicket> {
  const { data } = await apiClient.post<NovncTicket>(
    `/api/v1/addresses/${addressId}/novnc/ticket`, body,
  );
  return data;
}

export function buildNovncWsUrl(wsPath: string, ticket: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${wsPath}?ticket=${encodeURIComponent(ticket)}`;
}
