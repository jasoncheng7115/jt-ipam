import type { ServerMessage } from "@/utils/wsError";
import { apiClient } from "@/api/client";
import { LONG_OP_TIMEOUT_MS } from "@/api/integrations";

export interface GraylogDsv {
  enabled: boolean; fmt: string; path: string;
  /** 權杖不再隨設定回傳：按「顯示」才另外取（後端留稽核） */
  token_set: boolean;
  token_expires_at: string | null;
  /** 明文 8088 埠要明確打開才服務 */
  allow_plain_http: boolean;
  /** 只接受這些網段來的查表（空＝不限制） */
  allowed_sources: string[];
}
export async function getGraylogDsv(): Promise<GraylogDsv> {
  const { data } = await apiClient.get<GraylogDsv>("/api/v1/system/graylog-dsv");
  return data;
}
/** 取 DSV 權杖明文（組 Graylog 用的網址）；每次呼叫後端都留稽核。 */
export async function revealGraylogDsvToken(): Promise<string> {
  const { data } = await apiClient.get<{ token: string }>("/api/v1/system/graylog-dsv/token");
  return data.token;
}
export async function putGraylogDsv(p: {
  enabled: boolean; fmt: string; path: string; regenerate_token?: boolean; token_days?: number;
  allow_plain_http?: boolean; allowed_sources?: string[];
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
  /** 驗證伺服器憑證用的企業內部 CA（PEM）；空＝系統信任清單。subjects 是後端解析出來的憑證主旨 */
  ca_cert_pem: string | null;
  ca_cert_subjects?: string[];
  /** 接受 RSA 1024 位元等較弱金鑰的伺服器憑證（舊版 AD）；憑證照樣驗證 */
  allow_weak_tls: boolean;
}
export type LdapPatch = Omit<LdapConfig, "password_set" | "ca_cert_subjects"> & { bind_password?: string | null };

export async function getLdap(): Promise<LdapConfig> {
  const { data } = await apiClient.get<LdapConfig>("/api/v1/system/ldap");
  return data;
}
export async function putLdap(p: LdapPatch): Promise<LdapConfig> {
  const { data } = await apiClient.put<LdapConfig>("/api/v1/system/ldap", p);
  return data;
}
export async function testLdap(): Promise<{ bound: boolean; server: string; port: number; tls: string; who_am_i?: string;
  directory?: "ad" | "ldap"; warnings?: { code: string; params: Record<string, unknown> }[] }> {
  const { data } = await apiClient.post("/api/v1/system/ldap/test", {});
  return data;
}
export interface LdapAuthTestResult {
  ok: boolean; dn: string; username: string; display_name: string | null; email: string | null; is_admin: boolean;
  /** 有沒有設定「管理員群組」；沒有＝管理員身分由使用者頁的開關決定 */
  admin_groups_configured?: boolean;
  /** 比對到的設定值與方式：direct（DN）／name（群組名稱）／nested（經由別的群組） */
  admin_via?: { group: string; via: "direct" | "name" | "nested" } | null;
  /** 目錄回的直接所屬群組 */
  groups?: string[];
}
export async function testLdapAuth(username: string, password: string): Promise<LdapAuthTestResult> {
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
  /** ollama（自架）或 openai（OpenAI 相容端點）。舊設定沒有這個欄位，視為 ollama。 */
  provider?: string;
  url: string;
  /** 金鑰只回「有沒有設」，本身不回傳到瀏覽器。 */
  api_key_set?: boolean;
  embedding_model: string;
  embedding_base_url: string;
  chat_model: string;
  timeout: number;
  num_ctx?: number | null;
  mcp_external_enabled: boolean;
  mcp_api_key_set: boolean;
  /** 對外 MCP 金鑰的到期時間（過期的金鑰會被拒絕） */
  mcp_api_key_expires_at?: string | null;
  /** 目前這把金鑰最後一次被 MCP 用戶端使用（重產後歸零） */
  mcp_api_key_last_used_at?: string | null;
  mcp_api_key_last_used_ip?: string | null;
  ai_audit_enabled: boolean;
  ai_audit_times: string[];
  ai_audit_frequency: string;
  ai_audit_weekdays: number[];
  ai_audit_month_day: number;
  ai_audit_model: string | null;
  ai_audit_num_ctx: number | null;
  // AI 判讀（未授權 IP 判讀／IP 調查／防火牆規則異動解讀）；null＝沿用對話模型
  ai_interpret_model: string | null;
  ai_interpret_num_ctx: number | null;
  /** 判讀／巡檢自己的 LLM Server；位址空字串＝繼承基本設定（對話＋嵌入）那台 */
  ai_interpret_provider: string;
  ai_interpret_url: string;
  ai_interpret_api_key_set: boolean;
  ai_audit_provider: string;
  ai_audit_url: string;
  ai_audit_api_key_set: boolean;
  // AI 對話允許模型先思考（false＝每一輪都送關閉思考的參數）
  chat_thinking: boolean;
  server_timezone: string;
}

export interface LLMConfigPatch {
  enabled?: boolean;
  provider?: string;
  url?: string;
  api_key?: string;
  embedding_model?: string;
  embedding_base_url?: string;
  chat_model?: string;
  timeout?: number;
  num_ctx?: number | null;
  mcp_external_enabled?: boolean;
  ai_audit_enabled?: boolean;
  ai_audit_times?: string[];
  ai_audit_frequency?: string;
  ai_audit_weekdays?: number[];
  ai_audit_month_day?: number;
  ai_audit_model?: string;
  ai_audit_num_ctx?: number;
  ai_interpret_model?: string;
  ai_interpret_num_ctx?: number;
  /** 位址空字串＝回到繼承基本設定（供應商與金鑰一起清掉）；金鑰空字串＝清掉 */
  ai_interpret_provider?: string;
  ai_interpret_url?: string;
  ai_interpret_api_key?: string;
  ai_audit_provider?: string;
  ai_audit_url?: string;
  ai_audit_api_key?: string;
  chat_thinking?: boolean;
}

export async function getLLMConfig(): Promise<LLMConfig> {
  const { data } = await apiClient.get<LLMConfig>("/api/v1/system/llm");
  return data;
}

export async function patchLLMConfig(payload: LLMConfigPatch): Promise<LLMConfig> {
  const { data } = await apiClient.patch<LLMConfig>("/api/v1/system/llm", payload);
  return data;
}

// 對外 MCP 金鑰（唯讀）：檢視目前明文 / 重新產生
export async function revealMcpKey(): Promise<string | null> {
  const { data } = await apiClient.get<{ api_key: string | null }>("/api/v1/system/llm/mcp-key");
  return data.api_key;
}

export async function rotateMcpKey(expiresInDays = 90): Promise<{ api_key: string; expires_at: string | null }> {
  const { data } = await apiClient.post<{ api_key: string; expires_at: string | null }>(
    "/api/v1/system/llm/mcp-key/rotate", { expires_in_days: expiresInDays });
  return data;
}

export interface OllamaModel {
  name: string;
  size: number | null;
  modified_at: string | null;
  family: string | null;
  parameter_size: string | null;
}

/** `error_detail` 是結構化訊息（代碼＋參數），句子由前端組；`error` 是舊欄位的退路。 */
export interface OllamaModelsResult {
  models: OllamaModel[];
  error?: string;
  error_detail?: ServerMessage;
}

/** role：哪一個用途的 LLM Server（判讀／巡檢可以各接一台，沒設就是基本設定那台） */
export async function listOllamaModels(role: "chat" | "interpret" | "audit" | "embedding" = "chat"): Promise<OllamaModelsResult> {
  const { data } = await apiClient.get<OllamaModelsResult>(
    "/api/v1/system/llm/models", { params: role === "chat" ? undefined : { role } },
  );
  return data;
}

export interface VersionInfo {
  current: string;
  /** SPDX 授權識別字（來自後端；與 pyproject / package.json / LICENSE 綁在一起） */
  license?: string;
  python: string;
  packages: Record<string, string | null>;
  frontend?: Record<string, string | null>;
  host?: {
    os: string | null;
    kernel: string | null;
    nginx: string | null;
    node: string | null;
    postgres: string | null;
    /** 選用的作業系統相依：功能存在但主機不一定裝了對應執行檔 */
    optional_tools?: Record<string, {
      present: boolean; package: string; used_by: string; fallback?: boolean; version?: string | null;
      /** 要管理員自己設定才會有（GeoIP 的 MaxMind 帳號）：沒設定不算缺少 */
      opt_in?: boolean;
      /** FreeRDP 引擎沒選用：那組套件本來就不裝，不算缺 */
      engine_off?: boolean;
    }>;
    /** 必要相依（guacd）：沒裝或沒在跑，對應功能就不能正常運作 */
    required_tools?: Record<string, {
      present: boolean; running: boolean; version: string | null; package: string; used_by: string;
      protocols?: Record<string, boolean>; address?: string; error?: string;
    }>;
  };
  /** Recog 指紋資料庫（選用；探測用） */
  recog?: RecogStatus | null;
}

/** Recog 指紋資料庫（rapid7/recog）：安裝／升級時下載，之後每週自動檢查新版 */
export interface RecogStatus {
  installed: boolean;
  release: string | null;
  databases: number;
  fingerprints: number;
  skipped: number;
  updated_at: string | null;
  checked_at: string | null;
  last_ok_at: string | null;
  latest: string | null;
  error: string | null;
  project_url: string;
  license: string;
}
/** Recog 頁：每個指紋檔（例如 http_servers.xml）與它的筆數 */
export interface RecogDbRow { key: string; protocol: string | null; database_type: string | null; fingerprints: number }
export async function getRecogStatus(): Promise<RecogStatus & { database_list: RecogDbRow[] }> {
  const { data } = await apiClient.get("/api/v1/system/recog/status");
  return data;
}
export interface RecogUpdateResult {
  result: { status: "up_to_date" | "updated" | "error"; release?: string | null; previous?: string | null;
            latest?: string; fingerprints?: number; error?: string };
  status: RecogStatus;
}
/** 立即檢查新版（有就下載安裝）；下載＋匯入要十幾秒，用長逾時 */
export async function updateRecog(): Promise<RecogUpdateResult> {
  const { data } = await apiClient.post<RecogUpdateResult>("/api/v1/system/recog/update", null,
    { timeout: LONG_OP_TIMEOUT_MS });
  return data;
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

// 連線管理資安設定（RDP 控制端貼上文字到被控端、RDP 連線引擎）
export type RdpEngine = "aardwolf" | "freerdp" | "guacd";
/** VNC／SSH 主控台的引擎：builtin＝一路以來的實作；guacd＝jt-ipam-guacd 服務 */
export type ConsoleEngine = "builtin" | "guacd";
export interface ConsoleSecurity {
  rdp_clipboard_paste: boolean;
  rdp_engine: RdpEngine;
  // 唯讀：這台機器實際上能不能用 FreeRDP 引擎，缺什麼、怎麼裝（由後端算）
  freerdp_available?: boolean;
  /** aardwolf（預設引擎與 VNC 主控台）有沒有裝起來，以及伺服器的 Python 版本（issue #39） */
  aardwolf_available?: boolean;
  python_version?: string;
  freerdp_missing?: string[];
  freerdp_install_cmd?: string;
  vnc_engine?: ConsoleEngine;
  ssh_engine?: ConsoleEngine;
  /** 唯讀：guacd 服務有沒有在跑、哪些協定的外掛載得到（由後端實際連一次問出來） */
  guacd_available?: boolean;
  guacd_protocols?: Record<string, boolean>;
  guacd_address?: string;
  guacd_error?: string;
  guacd_install_cmd?: string;
  /** SFTP 單檔上下傳上限（MB），預設 100 */
  sftp_max_file_mb?: number;
  /** 允許主控台經由掃描代理中繼（issue #24 階段二，預設關） */
  console_relay?: boolean;
}
/** PUT 只送得改的欄位；可用性是伺服器算出來的事實，送回去會被擋（422）。 */
export type ConsoleSecurityPatch = Pick<ConsoleSecurity, "rdp_clipboard_paste" | "rdp_engine">
  & Partial<Pick<ConsoleSecurity, "vnc_engine" | "ssh_engine" | "sftp_max_file_mb" | "console_relay">>;

/** SFTP 傳輸路徑測試的票證（管理者限定）；ws_path 刻意跟 SFTP 是同一條路徑 */
export interface SftpProbeTicket { ticket: string; ws_path: string; up_bytes: number; down_bytes: number; ttl: number }
export async function requestSftpProbeTicket(): Promise<SftpProbeTicket> {
  const { data } = await apiClient.post<SftpProbeTicket>("/api/v1/system/sftp-probe/ticket");
  return data;
}
export async function getConsoleSecurity(): Promise<ConsoleSecurity> {
  const { data } = await apiClient.get<ConsoleSecurity>("/api/v1/system/console-security");
  return data;
}
export async function setConsoleSecurity(p: ConsoleSecurityPatch): Promise<ConsoleSecurity> {
  const { data } = await apiClient.put<ConsoleSecurity>("/api/v1/system/console-security", p);
  return data;
}

// 介面顯示設定（系統層；目前：異動記錄淡化天數）
export interface DevicePortFilter { filter_pseudo: boolean; ignore_patterns: string[] }
export async function getDevicePortFilter(): Promise<DevicePortFilter> {
  const { data } = await apiClient.get<DevicePortFilter>("/api/v1/system/device-port-filter");
  return data;
}
export async function setDevicePortFilter(p: DevicePortFilter): Promise<DevicePortFilter> {
  const { data } = await apiClient.put<DevicePortFilter>("/api/v1/system/device-port-filter", p);
  return data;
}

export interface UiDisplay { change_log_dim_days: number }
export async function getUiDisplay(): Promise<UiDisplay> {
  const { data } = await apiClient.get<UiDisplay>("/api/v1/system/ui-display");
  return data;
}
export async function setUiDisplay(p: UiDisplay): Promise<UiDisplay> {
  const { data } = await apiClient.put<UiDisplay>("/api/v1/system/ui-display", p);
  return data;
}


// ── AI 巡檢 ──
export interface AIFinding {
  id: string; run_id: string; severity: "low" | "medium" | "high"; category: string;
  title: string; detail: string; recommendation: string | null;
  evidence: Record<string, unknown> | null;
  object_type: string | null; object_id: string | null;
  status: string; created_at: string | null;
  /** 寫出這條結論的模型 —— 換過模型後要分得出哪幾條出自哪一個 */
  model?: string | null;
}
export interface AIAuditSummary {
  ip_count: number;
  counts: { low: number; medium: number; high: number };
  total: number; last_run_at: string | null;
}

export async function getAIAuditSummary(): Promise<AIAuditSummary> {
  const { data } = await apiClient.get("/api/v1/ai-audit/summary");
  return data;
}
export async function listAIFindings(params: { status?: string; severity?: string; category?: string; page?: number; page_size?: number } = {}) {
  const { data } = await apiClient.get("/api/v1/ai-audit/findings", { params });
  return data as { items: AIFinding[]; total: number; page: number; page_size: number };
}
export async function runAIAudit(): Promise<{ task_id: string; status: string }> {
  const { data } = await apiClient.post("/api/v1/ai-audit/run");
  return data;
}

// 巡檢跑十幾分鐘，而且是背景作業 —— 進度存在作業列，不在瀏覽器裡。
// 所以關掉分頁、切走再回來都看得到現況（之前綁在連線上，一離開就整個消失）。
export interface AIAuditProgress {
  stage?: "collecting" | "analyzing" | "saving" | "done";
  current?: number;
  total?: number;
  ips?: number;
  model?: string;
  found?: number;
  batch?: number;
  written?: number;
  phase?: string;
}

export interface AIAuditTask {
  id: string;
  status: "pending" | "running" | "succeeded" | "failed" | "cancelled";
  progress: number;
  summary: { live?: AIAuditProgress; findings?: number; error?: string | null } | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export async function getAIAuditStatus(): Promise<{ task: AIAuditTask | null }> {
  const { data } = await apiClient.get("/api/v1/ai-audit/status");
  return data;
}

export async function dismissAIFindings(ids: string[]): Promise<{ dismissed: number }> {
  const { data } = await apiClient.post("/api/v1/ai-audit/dismiss", { ids });
  return data;
}

// 忽略會影響往後每一次巡檢（同一件事之後都自動忽略）→ 一定要能反悔
/** 清空整份發現清單（含已忽略）。是刪除不是忽略 —— 忽略會讓下次巡檢自動略過同一件事。 */
export async function clearAIFindings(): Promise<{ deleted: number }> {
  const { data } = await apiClient.delete("/api/v1/ai-audit/findings");
  return data;
}

export async function restoreAIFindings(ids: string[]): Promise<{ restored: number }> {
  const { data } = await apiClient.post("/api/v1/ai-audit/restore", { ids });
  return data;
}

export interface EmbeddingCheck {
  ok: boolean;
  dim: number | null;
  expected: number;
  error: string | null;
}

/** 實際取一次向量，確認嵌入模型的維度與資料庫欄位相符。 */
export async function checkEmbedding(): Promise<EmbeddingCheck> {
  const { data } = await apiClient.get<EmbeddingCheck>("/api/v1/ai/embedding-check");
  return data;
}

/** 思考檢查：用 AI 巡檢／判讀同一套「關閉思考」參數問一句極短的話，看模型照不照做 */
export interface ThinkingCheck {
  role: "chat" | "audit" | "interpret";
  model: string;
  ok: boolean;
  thinking: boolean | null;
  reasoning_chars: number;
  think_tag: boolean;
  empty_answer: boolean;
  answer: string;
  seconds: number;
  rejected_params: string[];
  error: string | null;
}

export async function checkThinking(): Promise<ThinkingCheck[]> {
  // 最多三個模型、每個最多等 2 分鐘（後端上限），比全域預設的逾時長得多
  const { data } = await apiClient.get<{ results: ThinkingCheck[] }>("/api/v1/ai/thinking-check",
    { timeout: LONG_OP_TIMEOUT_MS * 2 });
  return data.results;
}

export interface ReindexResult {
  subnets: number;
  ip_addresses: number;
  devices: number;
  failed: number;
  error: string | null;
  /** 第一個失敗的錯誤代碼（errors.<code>）與參數；舊後端沒有 */
  error_code?: string | null;
  error_params?: Record<string, unknown> | null;
}

/** 語意搜尋的索引需不需要重建（不呼叫模型，只看資料庫） */
export interface IndexStatus {
  model: string;
  last: { model: string; dim: number; at: string; indexed: number; failed: number } | null;
  counts: Record<string, { described: number; indexed: number }>;
  described: number;
  missing: number;
  needs_reindex: boolean;
  reason: "never" | "model_changed" | "missing" | null;
}
export async function getIndexStatus(): Promise<IndexStatus> {
  const { data } = await apiClient.get<IndexStatus>("/api/v1/ai/index-status");
  return data;
}

/** 重新計算所有描述的向量。慢（依資料量可能數分鐘），只在換模型或初次設定後需要。 */
export async function reindexEmbeddings(): Promise<ReindexResult> {
  const { data } = await apiClient.post<ReindexResult>("/api/v1/ai/reindex");
  return data;
}

export interface AutolinkConfig {
  enabled: boolean;
  scope_subnet_ids: string[] | null;
}

export interface AutolinkPreview {
  would_link: number;
  samples: { ip: string; hostname: string | null; device: string | null }[];
  skipped: Record<string, number>;
}

export async function getAutolink(): Promise<AutolinkConfig> {
  const { data } = await apiClient.get<AutolinkConfig>("/api/v1/system/ip-device-autolink");
  return data;
}

export async function putAutolink(p: Partial<AutolinkConfig>): Promise<AutolinkConfig> {
  const { data } = await apiClient.put<AutolinkConfig>("/api/v1/system/ip-device-autolink", p);
  return data;
}

/** 只計算不寫入 —— 開啟前先看會動到什麼。 */
export async function previewAutolink(): Promise<AutolinkPreview> {
  const { data } = await apiClient.post<AutolinkPreview>(
    "/api/v1/system/ip-device-autolink/preview");
  return data;
}

// ── 每日備份加密 ──
export interface BackupEncryption { enabled: boolean; set_at: string | null }
export async function getBackupEncryption(): Promise<BackupEncryption> {
  return (await apiClient.get<BackupEncryption>("/api/v1/system/backup-encryption")).data;
}
export async function setBackupEncryption(passphrase: string): Promise<BackupEncryption> {
  return (await apiClient.put<BackupEncryption>("/api/v1/system/backup-encryption", { passphrase })).data;
}
export async function removeBackupEncryption(): Promise<void> {
  await apiClient.delete("/api/v1/system/backup-encryption");
}

// ── 外部 DNS 解析器（系統設定；2026-10-19）──
export interface DnsResolver { name: string; address: string; port: number; enabled: boolean }
export interface DnsResolversConfig {
  resolvers: DnsResolver[];
  public_domains: string[];
  public_ranges: string[];
  query_authoritative: boolean;
  interval_hours: number;
  notify_on_change: boolean;
  state?: { last_run_at?: string; last_summary?: Record<string, unknown> };
}
export async function getDnsResolvers(): Promise<DnsResolversConfig> {
  const { data } = await apiClient.get<DnsResolversConfig>("/api/v1/system/dns-resolvers");
  return data;
}
export async function putDnsResolvers(p: Omit<DnsResolversConfig, "state">): Promise<DnsResolversConfig> {
  const { data } = await apiClient.put<DnsResolversConfig>("/api/v1/system/dns-resolvers", p);
  return data;
}
export interface DnsTestResult {
  name: string; resolver: string; rcode: string; a: string[]; aaaa: string[]; cname: string[];
  ttl: number | null; error: string | null;
}
export async function testDnsResolver(address: string, port: number, name = ""): Promise<DnsTestResult> {
  const { data } = await apiClient.post<DnsTestResult>("/api/v1/system/dns-resolvers/test", { address, port, name });
  return data;
}
