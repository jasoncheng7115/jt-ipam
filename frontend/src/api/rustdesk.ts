// RustDesk Server（開源版）整合：伺服器設定、專用 RustDesk 代理的金鑰／立即同步／測試、裝置清單與連線稽核。admin only。
import { apiClient } from "@/api/client";
import type { Paginated } from "@/types";

export interface RustDeskDbStatus { path: string | null; ok: boolean; error: string | null; truncated?: boolean }

export interface RustDeskAgentReceiver { listening: boolean; port: number | null; error: string | null }

export interface RustDeskAgentCapabilities { delete: boolean; delete_reason: string | null }

export interface RustDeskServer {
  id: string;
  name: string;
  has_agent_key: boolean;
  agent_version: string | null;
  /** 代理怎麼驗證伺服器（X-Agent-Tls）：pinned / ca / insecure；null＝舊版代理 */
  agent_tls_mode?: string | null;
  agent_latest_version: string | null;
  agent_last_seen_at: string | null;
  agent_source_ip: string | null;
  agent_hostname: string | null;
  agent_status: { data_dir?: string | null; receiver?: RustDeskAgentReceiver | null;
                  /** 代理 1.1.0 起：讀不讀得到 hbbr／hbbs 日誌（讀不到就看不出客戶端的 Key 設錯） */
                  logs?: { dir?: string | null; hbbr?: boolean; hbbs?: boolean; error?: string | null } | null;
                  /** 代理 1.2.0 起：寫不寫得了 hbbs 的資料庫（「刪除舊註冊」要主機端以 --allow-delete 安裝） */
                  capabilities?: RustDeskAgentCapabilities | null } | null;
  /** 目前 Key 設錯的裝置數（中繼拒絕過、之後沒通過） */
  key_problems?: number;
  /** 代理目前算不算上線（後端在回應當下判斷） */
  agent_online?: boolean;
  agent_poll_seconds: number;
  force_report_at: string | null;
  enabled: boolean;
  report_interval_seconds: number;
  client_address: string | null;
  description: string | null;
  public_key: string | null;
  server_version: string | null;
  last_report_at: string | null;
  last_error: string | null;
  file_status: { db?: RustDeskDbStatus } | null;
  last_summary: { peers?: number; online?: number; matched?: number; removed?: number;
                  online_ok?: boolean; truncated?: boolean } | null;
  receive_reports: boolean;
  api_port: number;
  last_events_at: string | null;
  events_dropped: Record<string, number> | null;
  /** 相容 RustDesk 的網頁連線：網頁上的開關（預設關）、後端連 hbbs／hbbr 的位址與傳輸 */
  web_enabled: boolean;
  hbbs_host: string | null;
  relay_host: string | null;
  transport: "tcp" | "ws";
  /** 「刪除舊註冊」：網頁上的開關（預設關）；另外要主機端以 --allow-delete 安裝代理 */
  allow_peer_delete: boolean;
  /** 網頁檔案傳輸（預設關，規格附錄 J.6）與上傳上限（MB：單檔、單次總量） */
  web_file_transfer: boolean;
  web_file_max_file_mb: number;
  web_file_max_total_mb: number;
}

export interface RustDeskServerCreated extends RustDeskServer { agent_key: string }

export interface RustDeskTestCheck { key: string; ok: boolean; detail: string | null }
export interface RustDeskTestState {
  test_id: string | null;
  requested_at: string | null;
  result_at: string | null;
  checks: RustDeskTestCheck[];
  agent_last_seen_at: string | null;
}

export interface RustDeskServerWrite {
  name: string;
  enabled?: boolean;
  report_interval_seconds?: number;
  client_address?: string | null;
  description?: string | null;
  receive_reports?: boolean;
  api_port?: number;
  web_enabled?: boolean;
  hbbs_host?: string | null;
  relay_host?: string | null;
  transport?: "tcp" | "ws";
  allow_peer_delete?: boolean;
  web_file_transfer?: boolean;
  web_file_max_file_mb?: number;
  web_file_max_total_mb?: number;
}

export type RustDeskMatch = "matched" | "conflict" | "hostname_only" | "not_seen_online" | "stale" | "shared"
  | "ambiguous" | "unmanaged" | "no_ip";

export interface RustDeskPeer {
  id: string;
  rustdesk_id: string;
  registered_ip: string | null;
  first_registered_at: string | null;
  online: boolean;
  last_online_at: string | null;
  match_status: RustDeskMatch;
  address_id: string | null;
  address_ip: string | null;
  address_hostname: string | null;
  address_device_kind: string | null;
  address_device_model: string | null;
  subnet_id: string | null;
  match_evidence: ("registered_ip" | "report_ip" | "hostname")[] | null;
  candidate_address_id: string | null;
  candidate_ip: string | null;
  candidate_hostname: string | null;
  hostname: string | null;
  os_name: string | null;
  username: string | null;
  client_version: string | null;
  last_heartbeat_at: string | null;
  active_conns: number | null;
  report_ip: string | null;
  /** Key 設錯：代理在 hbbr／hbbs 日誌看到這台因為 Key 被拒，之後沒有通過中繼 */
  key_problem?: RustDeskKeyProblem | null;
}

export interface RustDeskKeyProblem { at: string; scope: "relay" | "hbbs" | null; count: number }

export interface RustDeskAudit {
  id: string;
  kind: "conn" | "file" | "alarm" | "note";
  action: "new" | "auth" | "close" | null;
  rustdesk_id: string;
  peer_id: string | null;
  peer_name: string | null;
  ip: string | null;
  conn_type: number | null;
  conn_id: number | null;
  session_id: string | null;
  alarm_type: number | null;
  nonce: string | null;
  verified: boolean;
  detail: Record<string, any> | null;
  src_ip: string | null;
  occurred_at: string;
  device_address_id: string | null;
  device_ip: string | null;
  device_hostname: string | null;
  device_reported_hostname: string | null;
}

const BASE = "/api/v1/rustdesk/servers";

export async function listRustDesk(): Promise<Paginated<RustDeskServer>> {
  return (await apiClient.get<Paginated<RustDeskServer>>(BASE, { params: { page: 1, page_size: 200 } })).data;
}
export async function createRustDesk(p: RustDeskServerWrite): Promise<RustDeskServerCreated> {
  return (await apiClient.post<RustDeskServerCreated>(BASE, p)).data;
}
export async function updateRustDesk(id: string, p: Partial<RustDeskServerWrite>): Promise<RustDeskServer> {
  return (await apiClient.patch<RustDeskServer>(`${BASE}/${id}`, p)).data;
}
export async function deleteRustDesk(id: string): Promise<void> {
  await apiClient.delete(`${BASE}/${id}`);
}
export async function getRustDeskAgentKey(id: string): Promise<string> {
  return (await apiClient.get<{ agent_key: string }>(`${BASE}/${id}/agent-key`)).data.agent_key;
}
export async function rotateRustDeskAgentKey(id: string): Promise<RustDeskServerCreated> {
  return (await apiClient.post<RustDeskServerCreated>(`${BASE}/${id}/rotate-agent-key`)).data;
}
export async function syncRustDeskNow(id: string): Promise<{ queued: boolean; eta_seconds: number; agent_online: boolean }> {
  return (await apiClient.post(`${BASE}/${id}/sync-now`)).data;
}
export async function startRustDeskTest(id: string): Promise<RustDeskTestState> {
  return (await apiClient.post<RustDeskTestState>(`${BASE}/${id}/test`)).data;
}
export async function getRustDeskTest(id: string): Promise<RustDeskTestState> {
  return (await apiClient.get<RustDeskTestState>(`${BASE}/${id}/test`)).data;
}
export type SortOrder = "asc" | "desc";

export async function listRustDeskPeers(id: string, params: {
  q?: string; online?: boolean; never_online?: boolean; match_status?: string; key_problem?: boolean; page?: number;
  page_size?: number; sort?: string; order?: SortOrder;
}): Promise<Paginated<RustDeskPeer>> {
  return (await apiClient.get<Paginated<RustDeskPeer>>(`${BASE}/${id}/peers`, { params })).data;
}
export async function listRustDeskAudit(id: string, params: {
  q?: string; kind?: string; rustdesk_id?: string; since?: string; page?: number; page_size?: number;
  sort?: string; order?: SortOrder;
}): Promise<Paginated<RustDeskAudit>> {
  return (await apiClient.get<Paginated<RustDeskAudit>>(`${BASE}/${id}/audit`, { params })).data;
}

// ── 刪除舊註冊：排入請求，代理下次輪詢取走、在 RustDesk 主機上刪（上線中的略過）──
export type RustDeskPeerDeleteStatus = "pending" | "deleted" | "skipped_online" | "not_found" | "failed" | "cancelled";
export interface RustDeskPeerDelete {
  id: string;
  rustdesk_id: string;
  status: RustDeskPeerDeleteStatus;
  detail: string | null;
  requested_by: string | null;
  requested_by_name: string | null;
  requested_at: string;
  finished_at: string | null;
}
export interface RustDeskPeerDeleteQueued {
  queued: number;
  already_pending: number;
  requested_at: string;
  eta_seconds: number;
  agent_online: boolean;
}
export async function requestRustDeskPeerDelete(id: string, rustdeskIds: string[]): Promise<RustDeskPeerDeleteQueued> {
  return (await apiClient.post<RustDeskPeerDeleteQueued>(`${BASE}/${id}/peers/delete`, { rustdesk_ids: rustdeskIds })).data;
}
export async function listRustDeskPeerDeletes(id: string, params: {
  status?: RustDeskPeerDeleteStatus; since?: string; page?: number; page_size?: number;
}): Promise<Paginated<RustDeskPeerDelete>> {
  return (await apiClient.get<Paginated<RustDeskPeerDelete>>(`${BASE}/${id}/peer-deletes`, { params })).data;
}
