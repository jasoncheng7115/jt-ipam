/**
 * 憑證集中保管 + 派送 API。
 */
import { apiClient } from "@/api/client";
import type { Paginated } from "@/api/admin";

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
  current_is_self_signed: boolean;
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
export async function deleteCertificate(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/certificates/${id}`);
}
export async function listVersions(id: string): Promise<CertVersion[]> {
  const { data } = await apiClient.get(`/api/v1/certificates/${id}/versions`);
  return data;
}
export async function downloadVersionFile(certId: string, versionId: string, fmt: string, password = ""): Promise<void> {
  const res = await apiClient.get(`/api/v1/certificates/${certId}/versions/${versionId}/file`, {
    params: { fmt, ...(password ? { password } : {}) },
    responseType: "blob",
  });
  const cd = String(res.headers["content-disposition"] ?? "");
  const m = cd.match(/filename="?([^"]+)"?/);
  const filename = m ? m[1] : `cert.${fmt}`;
  const url = URL.createObjectURL(res.data as Blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename; document.body.appendChild(a); a.click();
  a.remove(); URL.revokeObjectURL(url);
}
export async function uploadVersion(
  id: string, files: { cert: File; key: File; chain?: File | null }, allowExpired = false,
): Promise<CertVersion> {
  const fd = new FormData();
  fd.append("cert_file", files.cert);
  fd.append("key_file", files.key);
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
export async function fetchCertNow(id: string): Promise<{ status: string; error?: string; fingerprint?: string; not_after?: string }> {
  const { data } = await apiClient.post(`/api/v1/certificates/${id}/fetch-now`);
  return data;
}
export async function testCertSource(id: string, payload: CertSourcePayload): Promise<{ ok: boolean; message: string }> {
  const { data } = await apiClient.post(`/api/v1/certificates/${id}/source/test`, payload);
  return data;
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
export async function getServerAgentVersion(): Promise<{ version: string | null }> {
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
  server_agent_version: string | null;
  deployments: CertStatusDeployment[];
}
export async function getCertAgentStatus(): Promise<{ agents: CertAgentStatus[] }> {
  const { data } = await apiClient.get("/api/v1/cert-agents/status");
  return data;
}
