/**
 * 憑證集中保管 + 派送 API。
 */
import { apiClient } from "@/api/client";
import type { Paginated } from "@/api/admin";
import { saveBlob } from "@/utils/saveFile";

export interface CertVersion {
  id: string;
  fingerprint_sha256: string;
  serial: string | null;
  subject: string | null;
  issuer: string | null;
  not_before: string | null;
  not_after: string;
  domains: string[] | null;
  is_current: boolean;
  uploaded_by: string | null;
  created_at: string;
  chain_has_root: boolean;
  chain_complete: boolean;
  chain_can_rebuild: boolean;
}

export async function rebuildChain(certId: string, versionId: string): Promise<CertVersion> {
  const { data } = await apiClient.post(`/api/v1/certificates/${certId}/versions/${versionId}/rebuild-chain`);
  return data;
}

export interface Certificate {
  id: string;
  name: string;
  description: string | null;
  domains: string[] | null;
  created_at: string;
  updated_at: string;
  current_fingerprint: string | null;
  current_not_after: string | null;
  current_days_remaining: number | null;
  version_count: number;
  /** 到期前幾天通知；null＝沿用全域預設 */
  expiry_warn_days: number | null;
  current_is_self_signed: boolean;
  /** 目前版本的簽發者：完整名稱、CN（沒有 CN 時用 O）、O */
  current_issuer?: string | null;
  current_issuer_name?: string | null;
  current_issuer_org?: string | null;
  current_common_name: string | null;
  current_sans: string[] | null;
  source_type: string;
  source_config: Record<string, unknown> | null;
  fetch_interval_seconds: number;
  last_fetch_at: string | null;
  last_fetch_error: string | null;
}

export interface CertSourcePayload {
  source_type: "none" | "url" | "sftp";
  source_config: Record<string, unknown>;
  fetch_interval_seconds: number;
  source_password?: string | null;
  source_private_key?: string | null;
}

export interface CertAgent {
  id: string;
  name: string;
  description: string | null;
  enabled: boolean;
  scope_cert_ids: string[] | null;
  device_id: string | null;
  device_name: string | null;
  source_ip_id: string | null;
  last_seen_at: string | null;
  last_source_ip: string | null;
  recent_source_ips: string[];
  multi_source_recent: boolean;
  agent_version: string | null;
  /** 代理怎麼確認伺服器：pinned / ca / insecure；null＝舊版代理沒報 */
  tls_mode?: string | null;
  server_agent_version: string | null;
  reported: Array<Record<string, unknown>> | null;
  has_key: boolean;
  created_at: string;
  updated_at: string;
}
export interface CertAgentCreated extends CertAgent { enroll_key: string; }

// ── 憑證 ──
export async function listCertificates(): Promise<Paginated<Certificate>> {
  const { data } = await apiClient.get("/api/v1/certificates");
  return data;
}
export async function createCertificate(payload: { name: string; description?: string | null }): Promise<Certificate> {
  const { data } = await apiClient.post("/api/v1/certificates", payload);
  return data;
}
/** 更新憑證設定（目前用於「到期前幾天通知」）。
 *
 *  `clear_expiry_warn_days` 是必要的：PATCH 裡的 `null` 意思是「這個欄位不修改」，
 *  沒有它就沒辦法把一張憑證改回「沿用全域預設」。 */
export async function updateCertificate(
  id: string,
  payload: { name?: string; description?: string | null;
             expiry_warn_days?: number | null; clear_expiry_warn_days?: boolean },
): Promise<Certificate> {
  const { data } = await apiClient.patch(`/api/v1/certificates/${id}`, payload);
  return data;
}

export async function deleteCertificate(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/certificates/${id}`);
}
export async function listVersions(id: string): Promise<CertVersion[]> {
  const { data } = await apiClient.get(`/api/v1/certificates/${id}/versions`);
  return data;
}
export async function downloadVersionFile(certId: string, versionId: string, fmt: string, password = ""): Promise<void> {
  // POST、密碼放 body：放在網址參數的話，PFX 密碼會原封不動寫進 nginx 存取日誌與瀏覽器歷史
  // （0.6.43 ZAP 登入後掃描抓到；後端現在直接拒絕網址裡帶 password）
  const res = await apiClient.post(`/api/v1/certificates/${certId}/versions/${versionId}/file`,
    { fmt, password }, { responseType: "blob" });
  const cd = String(res.headers["content-disposition"] ?? "");
  const m = cd.match(/filename="?([^"]+)"?/);
  const filename = m ? m[1] : `cert.${fmt}`;
  // 伺服器端已記稽核（cert_export）
  saveBlob(filename, res.data as Blob, "application/octet-stream", { source: "certificate", audited: true });
}
/** 上傳新版：crt [+ key] [+ chain]，或一個 PFX。key 留空＝沿用目前版本的私鑰（已有版本時）。 */
export async function uploadVersion(
  id: string,
  files: { cert?: File | null; key?: File | null; chain?: File | null; pfx?: File | null; pfxPassword?: string },
  allowExpired = false,
): Promise<CertVersion> {
  const fd = new FormData();
  if (files.pfx) {
    fd.append("pfx_file", files.pfx);
    if (files.pfxPassword) fd.append("pfx_password", files.pfxPassword);
  } else {
    if (files.cert) fd.append("cert_file", files.cert);
    if (files.key) fd.append("key_file", files.key);
  }
  if (files.chain) fd.append("chain_file", files.chain);
  fd.append("allow_expired", String(allowExpired));
  const { data } = await apiClient.post(`/api/v1/certificates/${id}/versions`, fd);
  return data;
}
export async function generateSelfSigned(
  id: string, payload: { common_name: string; sans: string[]; days: number },
): Promise<CertVersion> {
  const { data } = await apiClient.post(`/api/v1/certificates/${id}/self-signed`, payload);
  return data;
}

export async function setCertSource(id: string, payload: CertSourcePayload): Promise<Certificate> {
  const { data } = await apiClient.put(`/api/v1/certificates/${id}/source`, payload);
  return data;
}
export async function fetchCertNow(id: string): Promise<{
  status: string; error?: string; fingerprint?: string; not_after?: string;
  code?: string | null; params?: Record<string, unknown>;
}> {
  const { data } = await apiClient.post(`/api/v1/certificates/${id}/fetch-now`);
  return data;
}
export async function testCertSource(id: string, payload: CertSourcePayload): Promise<{
  ok: boolean; message: string; code?: string | null; params?: Record<string, unknown>;
  /** SFTP 主機金鑰的指紋（第一次連上時記住，之後每次都要相同） */
  host_key_fingerprint?: string | null;
}> {
  const { data } = await apiClient.post(`/api/v1/certificates/${id}/source/test`, payload);
  return data;
}
/** 「重新信任主機金鑰」：SFTP 主機重灌或換了金鑰時，確認新指紋後清掉記住的，下一次連線重新記住 */
export async function forgetCertSourceHostKey(id: string): Promise<void> {
  await apiClient.post(`/api/v1/certificates/${id}/source/forget-host-key`);
}
export async function genCertSourceSshKey(id: string, payload: CertSourcePayload): Promise<{ public_key: string; installed: boolean; message: string }> {
  const { data } = await apiClient.post(`/api/v1/certificates/${id}/source/ssh-keypair`, payload);
  return data;
}

// ── 派送代理 ──
export async function listCertAgents(): Promise<Paginated<CertAgent>> {
  const { data } = await apiClient.get("/api/v1/cert-agents");
  return data;
}
export async function createCertAgent(payload: {
  name: string; description?: string | null; enabled?: boolean; scope_cert_ids: string[];
  device_id?: string | null;
}): Promise<CertAgentCreated> {
  const { data } = await apiClient.post("/api/v1/cert-agents", payload);
  return data;
}
export async function updateCertAgent(id: string, payload: Partial<{
  name: string; description: string | null; enabled: boolean; scope_cert_ids: string[];
  device_id: string | null;
}>): Promise<CertAgent> {
  const { data } = await apiClient.patch(`/api/v1/cert-agents/${id}`, payload);
  return data;
}
export async function rotateCertAgentKey(id: string): Promise<CertAgentCreated> {
  const { data } = await apiClient.post(`/api/v1/cert-agents/${id}/rotate-key`);
  return data;
}
export async function getCertAgentKey(id: string): Promise<{ enroll_key: string }> {
  const { data } = await apiClient.get(`/api/v1/cert-agents/${id}/key`);
  return data;
}
/** jt-ipam 自己出示的 HTTPS 憑證（只連本機），給安裝指令釘選用 */
export interface ServerIdentity {
  available: boolean;
  reason?: string;
  ca_trusted?: boolean;
  /** 憑證鏈是公開 CA 簽的（不管主機名稱）：會定期續約，釘選的指紋續約後就對不上 */
  chain_valid?: boolean;
  spki_pin?: string;
  cert_sha256?: string;
  subject?: string;
  not_after?: string;
}
export async function getServerIdentity(origin: string): Promise<ServerIdentity> {
  const { data } = await apiClient.get(`/api/v1/cert-agents/server-identity`, { params: { origin } });
  return data;
}
export async function getServerAgentVersion(): Promise<{ version: string | null; windows_version?: string | null }> {
  const { data } = await apiClient.get(`/api/v1/cert-agents/server-version`);
  return data;
}
export async function deleteCertAgent(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/cert-agents/${id}`);
}

// ── 唯讀現況（進階，global-read 可看）──
export interface CertStatusDeployment {
  cert: string | null;
  profile: string | null;
  status: string | null;
  applied_at: string | null;
  dry_run: boolean | null;
  reported_fingerprint: string | null;
  current_fingerprint: string | null;
  up_to_date: boolean;
  not_before: string | null;
  not_after: string | null;
  days_remaining: number | null;
}
export interface CertAgentStatus {
  agent: string;
  enabled: boolean;
  device_id: string | null;
  device_name: string | null;
  source_ip_id: string | null;
  last_seen_at: string | null;
  last_source_ip: string | null;
  recent_source_ips: string[];
  multi_source_recent: boolean;
  agent_version: string | null;
  /** 代理怎麼確認伺服器：pinned / ca / insecure；null＝舊版代理沒報 */
  tls_mode?: string | null;
  server_agent_version: string | null;
  deployments: CertStatusDeployment[];
}
export async function getCertAgentStatus(): Promise<{ agents: CertAgentStatus[] }> {
  const { data } = await apiClient.get("/api/v1/cert-agents/status");
  return data;
}

// ── 憑證推送目標（0227）：伺服器主動把憑證推到防火牆 ──
export type CertPushKind = "fortigate" | "opnsense" | "paloalto" | "pfsense";
export interface CertPushStep { step: string; ok: boolean; warn?: boolean; code?: string; detail?: string }
export interface CertPushTarget {
  id: string; name: string; enabled: boolean; kind: CertPushKind;
  certificate_id: string; certificate_name: string | null;
  api_url: string; verify_tls: boolean; options: Record<string, any>;
  verify_endpoint: string | null; auto_push: boolean; has_secret: boolean;
  last_push_at: string | null; last_status: "ok" | "unverified" | "error" | "rolled_back" | null;
  last_error: string | null; last_fingerprint: string | null; last_steps: CertPushStep[];
  current_fingerprint: string | null; up_to_date: boolean;
}
export interface CertPushTargetIn {
  name?: string; certificate_id?: string; kind?: CertPushKind; api_url?: string; verify_tls?: boolean;
  options?: Record<string, any>; verify_endpoint?: string | null; auto_push?: boolean; enabled?: boolean;
  secret?: { api_key: string; api_secret?: string } | null;
}
export async function listCertPushTargets(): Promise<CertPushTarget[]> {
  const { data } = await apiClient.get<{ items: CertPushTarget[] }>("/api/v1/cert-push-targets");
  return data.items;
}
export async function createCertPushTarget(body: CertPushTargetIn): Promise<CertPushTarget> {
  const { data } = await apiClient.post<CertPushTarget>("/api/v1/cert-push-targets", body);
  return data;
}
export async function updateCertPushTarget(id: string, body: CertPushTargetIn): Promise<CertPushTarget> {
  const { data } = await apiClient.patch<CertPushTarget>(`/api/v1/cert-push-targets/${id}`, body);
  return data;
}
export async function deleteCertPushTarget(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/cert-push-targets/${id}`);
}
export async function pushCertNow(id: string): Promise<{ task_id: string }> {
  const { data } = await apiClient.post<{ task_id: string }>(`/api/v1/cert-push-targets/${id}/push`);
  return data;
}
/** 設備上的一張憑證（OPNsense 選要替換哪一張；pfSense 唯讀看哪張在用） */
export interface DeviceCert {
  id: string; name: string; subject: string; in_use?: boolean; is_user?: boolean;
  issuer?: string | null; valid_from?: string | null; valid_to?: string | null;
}

export async function listPushDeviceCerts(id: string): Promise<DeviceCert[]> {
  const { data } = await apiClient.get<{ items: DeviceCert[] }>(
    `/api/v1/cert-push-targets/${id}/device-certs`);
  return data.items;
}

/** 還沒存檔就讀設備上的憑證：用表單上的帳密；編輯時帳密留空就帶 target_id 用已存的（10-22） */
export async function previewPushDeviceCerts(body: {
  kind: CertPushKind; api_url: string; verify_tls: boolean;
  secret?: { api_key: string; api_secret: string } | null; target_id?: string | null;
}): Promise<DeviceCert[]> {
  const { data } = await apiClient.post<{ items: DeviceCert[] }>(
    "/api/v1/cert-push-targets/device-certs/preview", body);
  return data.items;
}
