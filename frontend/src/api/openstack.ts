import { apiClient } from "./client";

/**
 * Bigstack CubeCOS／OpenStack 整合（唯讀）。
 *
 * Keystone v3 取權杖，之後只對 Nova／Neutron／CubeCOS 發 GET；不會寫入平台。
 * 路徑要自帶 /api/v1 首碼（client 的 baseURL 是 "/"）。
 */
export type OpenStackProduct = "openstack" | "cubecos";
/** password＝帳號密碼；app_credential＝Keystone 應用程式憑證（建議：可限縮角色、可隨時撤銷） */
export type OpenStackAuthMethod = "password" | "app_credential";
export type OpenStackInterface = "public" | "internal" | "admin";

export interface OpenStackServiceState {
  ok: boolean;
  url?: string | null;
  /** 失敗原因（後端的錯誤代碼與參數，跟 ui_detail 同一套） */
  code?: string | null;
  params?: Record<string, unknown> | null;
  message?: string | null;
}

/** 最近一次測試／同步得到的能力（capabilities JSONB） */
export interface OpenStackCapabilities {
  services?: Partial<Record<"identity" | "compute" | "network" | "cubecos", OpenStackServiceState>>;
  all_projects?: boolean | null;
  host_visible?: boolean | null;
  port_binding_visible?: boolean | null;
  nova_microversion?: string | null;
  neutron_extensions?: string[] | null;
  projects_visible?: number | null;
  checked_at?: string | null;
}

export interface OpenStackInstance {
  id: string;
  name: string;
  product: OpenStackProduct;
  auth_url: string;
  auth_method: OpenStackAuthMethod;
  username?: string | null;
  user_domain: string;
  project_name?: string | null;
  project_domain: string;
  app_credential_id?: string | null;
  has_secret: boolean;
  region?: string | null;
  interface: OpenStackInterface;
  verify_tls: boolean;
  has_ca_cert: boolean;
  all_projects: boolean;
  cubecos_url?: string | null;
  enabled: boolean;
  cluster_id?: string | null;
  sync_interval_seconds: number;
  last_sync_at?: string | null;
  last_error?: string | null;
  last_full_success_at?: string | null;
  scope_subnet_ids?: string[] | null;
  auto_create_ips: boolean;
  description?: string | null;
  capabilities?: OpenStackCapabilities | null;
  vm_count?: number;
  port_count?: number;
  node_count?: number;
}

/** 新增／編輯送出的欄位。secret、ca_cert 是只寫欄位：省略＝不變更；ca_cert 送 "" ＝清除 */
export interface OpenStackPayload {
  name: string;
  product: OpenStackProduct;
  auth_url: string;
  auth_method: OpenStackAuthMethod;
  username: string | null;
  user_domain: string;
  project_name: string | null;
  project_domain: string;
  app_credential_id: string | null;
  secret?: string;
  region: string | null;
  interface: OpenStackInterface;
  verify_tls: boolean;
  ca_cert?: string;
  all_projects: boolean;
  cubecos_url: string | null;
  enabled: boolean;
  sync_interval_seconds: number;
  scope_subnet_ids: string[] | null;
  auto_create_ips: boolean;
  description: string;
}

export interface OpenStackTestResult {
  ok: boolean;
  capabilities: OpenStackCapabilities | null;
  error: { code: string; params?: Record<string, unknown> | null; message?: string | null } | null;
}

/** Neutron 子網路與它對到的 jt-ipam 子網路 */
export interface OpenStackSubnet {
  id: string;
  instance_id?: string;
  subnet_id: string;
  network_id?: string | null;
  network_name?: string | null;
  name?: string | null;
  project_id?: string | null;
  project_name?: string | null;
  cidr: string;
  ip_version?: number | null;
  gateway_ip?: string | null;
  allocation_pools?: { start: string; end: string }[] | null;
  enable_dhcp?: boolean | null;
  shared?: boolean | null;
  mapped_subnet_id?: string | null;
  mapped_manually?: boolean;
  mapped_subnet_cidr?: string | null;
  mapped_subnet_name?: string | null;
  last_seen_at?: string | null;
}

export interface CubeCOSNode {
  hostname: string;
  role?: string | null;
  status?: string | null;
  management_ip?: string | null;
  provider_ip?: string | null;
  storage_ip?: string | null;
  serial?: string | null;
}

/** IP 詳細資料頁的「OpenStack 網卡」：這個位址出現在哪些 Neutron port 的 fixed IP 上 */
export interface OpenStackPortByIp {
  id: string;
  instance_id: string;
  instance_name?: string | null;
  port_id: string;
  mac?: string | null;
  status?: string | null;
  network_id?: string | null;
  network_name?: string | null;
  project_id?: string | null;
  project_name?: string | null;
  vm_id?: string | null;
  vm_name?: string | null;
  device_owner?: string | null;
  binding_host?: string | null;
  fixed_ips?: { subnet_id: string; ip_address: string }[] | null;
  floating_ips?: { floating_ip_address: string; floating_ip_id?: string | null; router_id?: string | null }[] | null;
  last_seen_at?: string | null;
}

const BASE = "/api/v1/openstack-instances";

export const OpenStack = {
  async list(): Promise<OpenStackInstance[]> {
    const { data } = await apiClient.get(BASE);
    return data;
  },
  async create(payload: OpenStackPayload): Promise<OpenStackInstance> {
    const { data } = await apiClient.post(BASE, payload);
    return data;
  },
  async update(id: string, payload: Partial<OpenStackPayload>): Promise<OpenStackInstance> {
    const { data } = await apiClient.patch(`${BASE}/${id}`, payload);
    return data;
  },
  async remove(id: string): Promise<void> {
    await apiClient.delete(`${BASE}/${id}`);
  },
  /** 同步執行、逾時很短：回報每個服務連不連得到、看得到哪些欄位 */
  async test(id: string): Promise<OpenStackTestResult> {
    const { data } = await apiClient.post(`${BASE}/${id}/test`, undefined, { timeout: 60_000 });
    return data;
  },
  /** 背景作業：回 task_id，用 trackTask 顯示進度 */
  async sync(id: string): Promise<{ task_id: string }> {
    const { data } = await apiClient.post(`${BASE}/${id}/sync`);
    return data;
  },
  async subnets(id: string): Promise<OpenStackSubnet[]> {
    const { data } = await apiClient.get(`${BASE}/${id}/subnets`);
    return data;
  },
  /** 手動指定對應（null＝取消對應）；之後同步不會再覆寫這一列 */
  async mapSubnet(id: string, rowId: string, mappedSubnetId: string | null): Promise<OpenStackSubnet> {
    const { data } = await apiClient.patch(`${BASE}/${id}/subnets/${rowId}`, { mapped_subnet_id: mappedSubnetId });
    return data;
  },
  async nodes(id: string): Promise<CubeCOSNode[]> {
    const { data } = await apiClient.get(`${BASE}/${id}/nodes`);
    return data ?? [];
  },
  async portsByIp(ipId: string): Promise<OpenStackPortByIp[]> {
    const { data } = await apiClient.get(`${BASE}/ports/by-ip/${ipId}`);
    return data ?? [];
  },
};

// ───────────────────── 表單與顯示用的純函式（有單元測試） ─────────────────────

export interface OpenStackForm {
  name: string;
  product: OpenStackProduct;
  auth_url: string;
  auth_method: OpenStackAuthMethod;
  username: string;
  user_domain: string;
  project_name: string;
  project_domain: string;
  app_credential_id: string;
  secret: string;
  region: string;
  interface: OpenStackInterface;
  verify_tls: boolean;
  /** 新貼上的 CA 憑證（空＝不變更，除非 ca_cert_clear） */
  ca_cert: string;
  /** 使用者按了「清除」既有的 CA 憑證 */
  ca_cert_clear: boolean;
  all_projects: boolean;
  cubecos_url: string;
  enabled: boolean;
  sync_interval_seconds: number;
  scope_subnet_ids: string[];
  auto_create_ips: boolean;
  description: string;
}

export function blankOpenStackForm(): OpenStackForm {
  return {
    name: "", product: "cubecos", auth_url: "", auth_method: "password",
    username: "", user_domain: "Default", project_name: "", project_domain: "Default",
    app_credential_id: "", secret: "", region: "", interface: "public", verify_tls: true,
    ca_cert: "", ca_cert_clear: false, all_projects: true, cubecos_url: "", enabled: true,
    sync_interval_seconds: 300, scope_subnet_ids: [], auto_create_ips: false, description: "",
  };
}

export function formFromInstance(r: OpenStackInstance): OpenStackForm {
  return {
    name: r.name, product: r.product, auth_url: r.auth_url, auth_method: r.auth_method,
    username: r.username ?? "", user_domain: r.user_domain || "Default",
    project_name: r.project_name ?? "", project_domain: r.project_domain || "Default",
    app_credential_id: r.app_credential_id ?? "", secret: "", region: r.region ?? "",
    interface: r.interface, verify_tls: r.verify_tls, ca_cert: "", ca_cert_clear: false,
    all_projects: r.all_projects, cubecos_url: r.cubecos_url ?? "", enabled: r.enabled,
    sync_interval_seconds: r.sync_interval_seconds, scope_subnet_ids: r.scope_subnet_ids ?? [],
    auto_create_ips: !!r.auto_create_ips, description: r.description ?? "",
  };
}

const orNull = (s: string): string | null => (s.trim() ? s.trim() : null);

/**
 * 表單 → 送出的欄位。
 * - 驗證方式沒用到的欄位送 null（切換驗證方式後，舊的帳號／憑證 ID 不會殘留在資料庫）
 * - secret：編輯時留空＝不變更（不送），新增時一定送
 * - ca_cert：貼了新的就送；按了清除送 ""；都沒有就不送（保留既有）
 * - cubecos_url 只有 product=cubecos 才有意義，其他一律 null
 * - 說明送字串（"" ＝清空），不送 undefined —— 見 clearableDescription.test.ts
 */
export function buildOpenStackPayload(f: OpenStackForm, editing: boolean): OpenStackPayload {
  const pw = f.auth_method === "password";
  const out: OpenStackPayload = {
    name: f.name.trim(),
    product: f.product,
    auth_url: f.auth_url.trim(),
    auth_method: f.auth_method,
    username: pw ? orNull(f.username) : null,
    user_domain: f.user_domain.trim() || "Default",
    project_name: pw ? orNull(f.project_name) : null,
    project_domain: f.project_domain.trim() || "Default",
    app_credential_id: pw ? null : orNull(f.app_credential_id),
    region: orNull(f.region),
    interface: f.interface,
    verify_tls: f.verify_tls,
    all_projects: f.all_projects,
    cubecos_url: f.product === "cubecos" ? orNull(f.cubecos_url) : null,
    enabled: f.enabled,
    sync_interval_seconds: f.sync_interval_seconds,
    scope_subnet_ids: f.scope_subnet_ids.length ? [...f.scope_subnet_ids] : null,
    auto_create_ips: f.auto_create_ips,
    description: f.description.trim(),
  };
  if (f.secret || !editing) out.secret = f.secret;
  if (f.ca_cert.trim()) out.ca_cert = f.ca_cert.trim();
  else if (f.ca_cert_clear) out.ca_cert = "";
  return out;
}

/** CubeCOS 網址留空時後端用 https://<Keystone 主機>；表單拿來當 placeholder */
export function defaultCubecosUrl(authUrl: string): string {
  const s = authUrl.trim();
  if (!s) return "https://cube.example";
  try {
    const u = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(s) ? s : `https://${s}`);
    return `https://${u.hostname.includes(":") ? `[${u.hostname.replace(/^\[|\]$/g, "")}]` : u.hostname}`;
  } catch {
    return "https://cube.example";
  }
}

/** 能力清單的一列：ok=true 綠勾、false 黃色警告、null 沒有資料 */
export interface CapabilityItem {
  key: "all_projects" | "host_visible" | "port_binding_visible" | "nova_microversion" | "projects_visible";
  ok: boolean | null;
  value?: string;
}

const boolOrNull = (v: unknown): boolean | null => (typeof v === "boolean" ? v : null);

export function capabilityItems(c: OpenStackCapabilities | null | undefined): CapabilityItem[] {
  const caps = c ?? {};
  const pv = typeof caps.projects_visible === "number" ? caps.projects_visible : null;
  return [
    { key: "all_projects", ok: boolOrNull(caps.all_projects) },
    { key: "host_visible", ok: boolOrNull(caps.host_visible) },
    { key: "port_binding_visible", ok: boolOrNull(caps.port_binding_visible) },
    { key: "nova_microversion", ok: caps.nova_microversion ? true : null, value: caps.nova_microversion ?? undefined },
    { key: "projects_visible", ok: pv == null ? null : pv > 0, value: pv == null ? undefined : String(pv) },
  ];
}

export const SERVICE_KEYS = ["identity", "compute", "network", "cubecos"] as const;
export type ServiceKey = (typeof SERVICE_KEYS)[number];

/** 要顯示的服務：前三個一定列；CubeCOS 只有產品是 CubeCOS（或測試結果裡有它）才列 */
export function serviceRows(c: OpenStackCapabilities | null | undefined, product: OpenStackProduct):
  { key: ServiceKey; state: OpenStackServiceState | null }[] {
  const s = c?.services ?? {};
  return SERVICE_KEYS
    .filter((k) => k !== "cubecos" || product === "cubecos" || !!s.cubecos)
    .map((k) => ({ key: k, state: s[k] ?? null }));
}

/**
 * 子網路對應的狀態：
 * pending＝還沒對應（同步時找不到唯一相同 CIDR 的子網路）；none＝有人手動設成不對應；manual／auto
 */
export type MappingState = "pending" | "none" | "manual" | "auto";
export function mappingState(r: Pick<OpenStackSubnet, "mapped_subnet_id" | "mapped_manually">): MappingState {
  if (!r.mapped_subnet_id) return r.mapped_manually ? "none" : "pending";
  return r.mapped_manually ? "manual" : "auto";
}
