import { apiClient } from "@/api/client";

export interface GraylogDsv { enabled: boolean; fmt: string; path: string; token: string; }
export async function getGraylogDsv(): Promise<GraylogDsv> {
  const { data } = await apiClient.get<GraylogDsv>("/api/v1/system/graylog-dsv");
  return data;
}
export async function putGraylogDsv(p: {
  enabled: boolean; fmt: string; path: string; regenerate_token?: boolean;
}): Promise<GraylogDsv> {
  const { data } = await apiClient.put<GraylogDsv>("/api/v1/system/graylog-dsv", p);
  return data;
}

// ── 外部認證 / LDAP（AD） ──
export interface LdapConfig {
  enabled: boolean; server: string | null; port: number;
  use_ssl: boolean; use_starttls: boolean;
  bind_dn: string | null; password_set: boolean;
  search_base: string | null; user_filter: string;
  attr_email: string; attr_display_name: string; attr_member_of: string;
  admin_groups: string[];
  default_group_id: string | null;
}
export type LdapPatch = Omit<LdapConfig, "password_set"> & { bind_password?: string | null };

export async function getLdap(): Promise<LdapConfig> {
  const { data } = await apiClient.get<LdapConfig>("/api/v1/system/ldap");
  return data;
}
export async function putLdap(p: LdapPatch): Promise<LdapConfig> {
  const { data } = await apiClient.put<LdapConfig>("/api/v1/system/ldap", p);
  return data;
}
export async function testLdap(): Promise<{ bound: boolean; server: string; port: number; tls: string; who_am_i?: string }> {
  const { data } = await apiClient.post("/api/v1/system/ldap/test", {});
  return data;
}
export async function testLdapAuth(username: string, password: string): Promise<{ ok: boolean; dn: string; username: string; display_name: string | null; email: string | null; is_admin: boolean }> {
  const { data } = await apiClient.post("/api/v1/system/ldap/test-auth", { username, password });
  return data;
}

// ── 稽核轉送到 Graylog ──
export interface AuditForward { enabled: boolean; host: string | null; port: number; protocol: "tcp" | "udp"; fmt: "gelf" | "syslog" | "cef"; }
export async function getAuditForward(): Promise<AuditForward> {
  const { data } = await apiClient.get<AuditForward>("/api/v1/system/audit-forward");
  return data;
}
export async function putAuditForward(p: AuditForward): Promise<AuditForward> {
  const { data } = await apiClient.put<AuditForward>("/api/v1/system/audit-forward", p);
  return data;
}
export async function testAuditForward(p: AuditForward): Promise<{ ok: boolean; sent_to: string; fmt: string }> {
  const { data } = await apiClient.post("/api/v1/system/audit-forward/test", p);
  return data;
}

// ── OIDC SSO 設定（webui 管理）──
export interface OidcConfig {
  enabled: boolean;
  issuer: string | null;
  client_id: string | null;
  client_secret_set: boolean;
  redirect_uri: string | null;
  scope: string;
  groups_claim: string;
  username_claim: string;
  admin_groups: string[];
  default_group_id: string | null;
}
export interface OidcConfigPatch {
  enabled: boolean;
  issuer: string | null;
  client_id: string | null;
  client_secret?: string | null;  // 留空(undefined)=不變更；空字串=清除
  redirect_uri: string | null;
  scope: string;
  groups_claim: string;
  username_claim: string;
  admin_groups: string[];
  default_group_id: string | null;
}
export async function getOidcConfig(): Promise<OidcConfig> {
  const { data } = await apiClient.get<OidcConfig>("/api/v1/auth/oidc/config");
  return data;
}
export async function putOidcConfig(p: OidcConfigPatch): Promise<OidcConfig> {
  const { data } = await apiClient.put<OidcConfig>("/api/v1/auth/oidc/config", p);
  return data;
}
export async function testOidc(): Promise<{ ok: boolean; issuer?: string; authorization_endpoint?: string; error?: string }> {
  const { data } = await apiClient.get("/api/v1/auth/oidc/test");
  return data;
}

// ── SAML SSO 設定（webui 管理）──
export interface SamlConfig {
  enabled: boolean;
  idp_metadata_url: string | null;
  idp_metadata_xml: string | null;
  sp_entity_id: string | null;
  sp_acs_url: string | null;
  sp_sls_url: string | null;
  sp_x509_cert: string | null;
  sp_private_key_set: boolean;
  want_assertions_signed: boolean;
  want_assertions_encrypted: boolean;
  want_name_id_encrypted: boolean;
  authn_requests_signed: boolean;
  attr_username: string;
  attr_email: string;
  attr_displayname: string;
  attr_groups: string;
  admin_groups: string[];
  default_group_id: string | null;
}
export type SamlConfigPatch = Omit<SamlConfig, "sp_private_key_set"> & { sp_private_key?: string | null };
export async function getSamlConfig(): Promise<SamlConfig> {
  const { data } = await apiClient.get<SamlConfig>("/api/v1/auth/saml/config");
  return data;
}
export async function putSamlConfig(p: SamlConfigPatch): Promise<SamlConfig> {
  const { data } = await apiClient.put<SamlConfig>("/api/v1/auth/saml/config", p);
  return data;
}
export async function testSaml(): Promise<{ entity_id?: string; sso_url?: string; error?: string }> {
  const { data } = await apiClient.get("/api/v1/auth/saml/test");
  return data;
}

export interface LLMConfig {
  enabled: boolean;
  url: string;
  embedding_model: string;
  chat_model: string;
  timeout: number;
}

export interface LLMConfigPatch {
  enabled?: boolean;
  url?: string;
  embedding_model?: string;
  chat_model?: string;
  timeout?: number;
}

export async function getLLMConfig(): Promise<LLMConfig> {
  const { data } = await apiClient.get<LLMConfig>("/api/v1/system/llm");
  return data;
}

export async function patchLLMConfig(payload: LLMConfigPatch): Promise<LLMConfig> {
  const { data } = await apiClient.patch<LLMConfig>("/api/v1/system/llm", payload);
  return data;
}

export interface OllamaModel {
  name: string;
  size: number | null;
  modified_at: string | null;
  family: string | null;
  parameter_size: string | null;
}

export async function listOllamaModels(): Promise<{ models: OllamaModel[]; error?: string }> {
  const { data } = await apiClient.get<{ models: OllamaModel[]; error?: string }>(
    "/api/v1/system/llm/models",
  );
  return data;
}

export interface VersionInfo {
  current: string;
  python: string;
  packages: Record<string, string | null>;
}

export interface LatestVersion {
  current: string;
  latest: string | null;
  update_available: boolean;
  release_url: string;
  error: string | null;
}

export async function getVersionInfo(): Promise<VersionInfo> {
  const { data } = await apiClient.get<VersionInfo>("/api/v1/system/version");
  return data;
}

export async function checkLatestVersion(): Promise<LatestVersion> {
  const { data } = await apiClient.get<LatestVersion>("/api/v1/system/version/check-latest");
  return data;
}
