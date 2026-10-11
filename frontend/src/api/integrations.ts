/**
 * Integration endpoints：DNS / LibreNMS / Firewall (OPNsense) / Wazuh / Plugins。
 */
import { apiClient } from "@/api/client";
import type { Paginated } from "@/api/admin";
import type { MissingPage, MissingQuery } from "@/composables/useRemoteMissing";

// 整合同步/測試可能要打外部 API、跑數百筆 ingest，遠遠超過全域 15s 預設。
// 給長時操作 5 分鐘空間。
export const LONG_OP_TIMEOUT_MS = 300_000;

// 是否存在重疊網段（同 IP 可能跨子網路多筆）→ 用來提醒未設 scope 的整合可能標錯筆。
export async function getSubnetOverlapExists(): Promise<boolean> {
  const { data } = await apiClient.get<{ has_overlap: boolean }>("/api/v1/subnets/overlaps/exists");
  return !!data.has_overlap;
}

// ─────────────────── DNS ───────────────────

export interface DNSServer {
  id: string;
  name: string;
  type: string;
  api_url: string | null;
  server_address: string | null;
  extra_config?: string | null;  // JSON：username / verify_tls 等
  enabled: boolean;
  sync_interval_seconds: number;
  scope_subnet_ids?: string[] | null;
  /** 比對群組（互相同步的 DNS 伺服器） */
  compare_group_id?: string | null;
  last_sync_at?: string | null;
  last_error?: string | null;
  created_at: string;
  updated_at: string;
}

export async function listDNSServers(): Promise<{ items: DNSServer[] }> {
  const { data } = await apiClient.get<{ items: DNSServer[] }>("/api/v1/dns/servers");
  return data;
}

export type DNSServerType = "powerdns" | "bind9" | "unbound_opnsense" | "windows_dns" | "univention_ucs" | "technitium";

export interface DNSServerCreate {
  name: string;
  type: DNSServerType;
  api_url?: string | null;
  server_address?: string | null;
  extra_config?: string | null;
  enabled?: boolean;
  sync_interval_seconds?: number;
  scope_subnet_ids?: string[] | null;
  /** 有帶才更新：null＝移出群組 */
  compare_group_id?: string | null;
  api_key?: string | null;
  api_secret?: string | null;
  tsig_key?: string | null;
  password?: string | null;
}

export async function createDNSServer(payload: DNSServerCreate): Promise<DNSServer> {
  const { data } = await apiClient.post<DNSServer>("/api/v1/dns/servers", payload);
  return data;
}

export async function updateDNSServer(id: string, payload: Partial<DNSServerCreate>): Promise<DNSServer> {
  const { data } = await apiClient.patch<DNSServer>(`/api/v1/dns/servers/${id}`, payload);
  return data;
}

export async function deleteDNSServer(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/dns/servers/${id}`);
}

export async function syncDNSServer(id: string): Promise<{ task_id: string }> {
  const { data } = await apiClient.post(`/api/v1/dns/servers/${id}/sync`, null);
  return data;
}

export async function testDNSServer(id: string): Promise<{ ok: boolean; server?: Record<string, unknown> }> {
  const { data } = await apiClient.post(`/api/v1/dns/servers/${id}/test`);
  return data;
}

// ── DNS 比對群組：互相同步的伺服器；同步後比對各台、合併顯示（2026-10-10）──
export type DNSCompareStatus = "ok" | "mismatch" | "pending" | "incomplete" | "single" | "incompatible";

export interface DNSCompareGroupMember {
  id: string; name: string; type: string; enabled: boolean;
  last_sync_at: string | null; last_error: string | null;
}

export interface DNSCompareGroup {
  id: string;
  name: string;
  description: string | null;
  notify_enabled: boolean;
  grace_minutes: number;
  last_checked_at: string | null;
  last_status: DNSCompareStatus | null;
  last_message: string | null;
  alerted_at: string | null;
  created_at: string;
  updated_at: string;
  members: DNSCompareGroupMember[];
  diff_count: number;
  /** 不比對的 zone（主從只複寫部分 zone、或某台另外放自己的 zone） */
  excluded_zones: string[];
  /** 成員拉回來的 zone：給「不比對的 zone」選單用 */
  zones: string[];
}

export interface DNSCompareGroupInput {
  name: string;
  description?: string | null;
  notify_enabled?: boolean;
  grace_minutes?: number;
  server_ids?: string[];
  excluded_zones?: string[];
}

export interface DNSCompareGroupDiff {
  id: string;
  kind: "zone" | "record";
  zone: string;
  name: string;
  type: string;
  value: string;
  present_on: { id: string; name: string }[];
  missing_on: { id: string; name: string }[];
  first_seen_at: string;
  last_seen_at: string;
  confirmed: boolean;
}

export async function listDNSCompareGroups(): Promise<DNSCompareGroup[]> {
  return (await apiClient.get<DNSCompareGroup[]>("/api/v1/dns/compare-groups")).data;
}

export async function createDNSCompareGroup(payload: DNSCompareGroupInput): Promise<DNSCompareGroup> {
  return (await apiClient.post<DNSCompareGroup>("/api/v1/dns/compare-groups", payload)).data;
}

export async function updateDNSCompareGroup(id: string, payload: Partial<DNSCompareGroupInput>): Promise<DNSCompareGroup> {
  return (await apiClient.patch<DNSCompareGroup>(`/api/v1/dns/compare-groups/${id}`, payload)).data;
}

export async function deleteDNSCompareGroup(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/dns/compare-groups/${id}`);
}

export async function checkDNSCompareGroup(id: string): Promise<{ status: DNSCompareStatus; message?: string | null }> {
  return (await apiClient.post(`/api/v1/dns/compare-groups/${id}/check`)).data;
}

export async function listDNSCompareGroupDiffs(id: string): Promise<DNSCompareGroupDiff[]> {
  return (await apiClient.get<DNSCompareGroupDiff[]>(`/api/v1/dns/compare-groups/${id}/diffs`)).data;
}

// ─────────────────── LibreNMS ───────────────────

export interface LibreNMSInstance {
  id: string;
  name: string;
  api_url: string;
  enabled: boolean;
  verify_tls: boolean;
  sync_devices: boolean;
  sync_arp: boolean;
  sync_fdb: boolean;
  sync_vlans: boolean;
  sync_links: boolean;
  scope_subnet_ids: string[] | null;
  use_for_status: boolean;
  auto_add_devices: boolean;
  auto_create_ips: boolean;
  /** 依 ARP 表自動建立 IP（#48，預設關） */
  auto_create_from_arp?: boolean;
  arp_create_require_fdb?: boolean;
  arp_create_skip_dhcp?: boolean;
  sync_interval_seconds: number;
  last_sync_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export async function listLibreNMS(
  page = 1, page_size = 50,
): Promise<Paginated<LibreNMSInstance>> {
  const { data } = await apiClient.get<Paginated<LibreNMSInstance>>(
    "/api/v1/librenms/instances",
    { params: { page, page_size } },
  );
  return data;
}

export interface LibreNMSInstanceCreate {
  name: string;
  api_url: string;
  api_token: string;
  enabled?: boolean;
  verify_tls?: boolean;
  sync_devices?: boolean;
  sync_arp?: boolean;
  sync_fdb?: boolean;
  sync_vlans?: boolean;
  sync_links?: boolean;
  scope_subnet_ids?: string[] | null;
  use_for_status?: boolean;
  auto_add_devices?: boolean;
  auto_create_ips?: boolean;
  sync_interval_seconds?: number;
}

export async function createLibreNMS(payload: LibreNMSInstanceCreate): Promise<LibreNMSInstance> {
  const { data } = await apiClient.post<LibreNMSInstance>("/api/v1/librenms/instances", payload);
  return data;
}

export async function deleteLibreNMS(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/librenms/instances/${id}`);
}

export async function testLibreNMS(id: string): Promise<unknown> {
  const { data } = await apiClient.post(`/api/v1/librenms/instances/${id}/test`,
    undefined, { timeout: LONG_OP_TIMEOUT_MS });
  return data;
}

export async function syncLibreNMS(id: string): Promise<{ task_id: string }> {
  const { data } = await apiClient.post(`/api/v1/librenms/instances/${id}/sync`,
    undefined, { timeout: LONG_OP_TIMEOUT_MS });
  return data;
}

export interface LinkDevicesResult { candidates: number; linked: number; created: number; }
export async function linkLibreNMSDevices(id: string): Promise<LinkDevicesResult> {
  const { data } = await apiClient.post<LinkDevicesResult>(
    `/api/v1/librenms/instances/${id}/link-devices`,
    undefined, { timeout: LONG_OP_TIMEOUT_MS });
  return data;
}

export interface LibreNMSInstanceUpdate {
  api_url?: string;
  api_token?: string;
  enabled?: boolean;
  sync_devices?: boolean;
  sync_arp?: boolean;
  sync_fdb?: boolean;
  sync_vlans?: boolean;
  sync_links?: boolean;
  scope_subnet_ids?: string[] | null;
  use_for_status?: boolean;
  auto_add_devices?: boolean;
  auto_create_ips?: boolean;
  sync_interval_seconds?: number;
}
export async function updateLibreNMS(
  id: string, payload: LibreNMSInstanceUpdate,
): Promise<LibreNMSInstance> {
  const { data } = await apiClient.patch<LibreNMSInstance>(
    `/api/v1/librenms/instances/${id}`, payload,
  );
  return data;
}

// ─────────────────── OPNsense Firewall ───────────────────

export interface OPNsenseFirewall {
  id: string;
  name: string;
  api_url: string;
  enabled: boolean;
  verify_tls: boolean;
  sync_interval_seconds: number;
  sync_dhcp: boolean;
  sync_arp: boolean;
  sync_openvpn: boolean;
  sync_rules: boolean;
  sync_nat: boolean;
  sync_aliases?: boolean;
  expose_dsv?: boolean;
  description: string | null;
  scope_location_id?: string | null;
  scope_customer_id?: string | null;
  scope_subnet_ids?: string[] | null;
  iface_subnet_map?: Record<string, string> | null;
  last_sync_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface OPNsenseFirewallCreate {
  name: string;
  api_url: string;
  api_key: string;
  api_secret: string;
  enabled?: boolean;
  verify_tls?: boolean;
  sync_dhcp?: boolean;
  sync_arp?: boolean;
  sync_openvpn?: boolean;
  sync_rules?: boolean;
  sync_nat?: boolean;
  sync_aliases?: boolean;
  expose_dsv?: boolean;
  description?: string;
  scope_location_id?: string | null;
  scope_customer_id?: string | null;
  scope_subnet_ids?: string[] | null;
  iface_subnet_map?: Record<string, string> | null;
}

export interface OPNsenseRule {
  id: string;
  firewall_id: string;
  legacy_uuid: string;
  enabled: boolean;
  sequence: number | null;
  action: string | null;
  interface: string | null;
  direction: string | null;
  protocol: string | null;
  source_net: string | null;
  source_port: string | null;
  destination_net: string | null;
  destination_port: string | null;
  description: string | null;
  last_synced_at: string;
}

export async function listFirewallRules(fwId: string, page = 1): Promise<Paginated<OPNsenseRule>> {
  const { data } = await apiClient.get<Paginated<OPNsenseRule>>(
    `/api/v1/firewalls/opnsense/${fwId}/rules`, { params: { page, page_size: 500 } },
  );
  return data;
}

export interface OPNsenseSyncedAlias {
  id: string;
  name: string;
  alias_type: string | null;
  description: string | null;
  enabled: boolean;
  content: string[];
  member_count: number;
  last_synced_at: string | null;
}

export async function listFirewallAliases(fwId: string): Promise<OPNsenseSyncedAlias[]> {
  const { data } = await apiClient.get<OPNsenseSyncedAlias[]>(
    `/api/v1/firewalls/opnsense/${fwId}/aliases`,
  );
  return data;
}

export interface OPNsenseAliasMapping {
  id: string;
  firewall_id: string;
  alias_name: string;
  alias_type: string;
  selector: Record<string, unknown>;
  direction: "push" | "pull" | "both";
  last_alias_uuid: string | null;
  last_synced_count: number | null;
  last_sync_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export async function listFirewalls(
  limit = 50, offset = 0,
): Promise<Paginated<OPNsenseFirewall>> {
  const { data } = await apiClient.get<Paginated<OPNsenseFirewall>>(
    "/api/v1/firewalls/opnsense",
    { params: { limit, offset } },
  );
  return data;
}

export async function createFirewall(payload: OPNsenseFirewallCreate): Promise<OPNsenseFirewall> {
  const { data } = await apiClient.post<OPNsenseFirewall>(
    "/api/v1/firewalls/opnsense", payload,
  );
  return data;
}

export async function updateFirewall(
  id: string,
  payload: Partial<OPNsenseFirewallCreate & { sync_interval_seconds: number }>,
): Promise<OPNsenseFirewall> {
  const { data } = await apiClient.patch<OPNsenseFirewall>(
    `/api/v1/firewalls/opnsense/${id}`, payload,
  );
  return data;
}

export async function deleteFirewall(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/firewalls/opnsense/${id}`);
}

export async function testFirewall(id: string): Promise<unknown> {
  const { data } = await apiClient.post(`/api/v1/firewalls/opnsense/${id}/test`,
    undefined, { timeout: LONG_OP_TIMEOUT_MS });
  return data;
}

export async function syncFirewall(id: string): Promise<{ task_id: string }> {
  const { data } = await apiClient.post(`/api/v1/firewalls/opnsense/${id}/sync`,
    undefined, { timeout: LONG_OP_TIMEOUT_MS });
  return data;
}

export interface DhcpPoolRange {
  id: string;
  source_type: string;            // opnsense / pfsense / windows_dhcp
  source_id: string;
  source_name?: string | null;    // 顯示用（哪一台防火牆／DHCP 伺服器）
  subnet_cidr: string | null;
  start_ip: string; end_ip: string; family: number;
  source: string;                 // DHCP 引擎：kea / isc / pfsense / windows
}
// 所有來源的 DHCP 發放範圍（IP 清單／詳細資料用來標示 DHCP 動態區）。
// 需具全域讀取權；取不到時回空（只是少顯示標籤，不影響其他功能）。
export async function listDhcpRanges(): Promise<DhcpPoolRange[]> {
  try {
    const { data } = await apiClient.get<DhcpPoolRange[]>("/api/v1/dhcp-ranges");
    return data;
  } catch {
    return [];
  }
}

export async function listAliasMappings(
  firewall_id?: string, limit = 100, offset = 0,
): Promise<Paginated<OPNsenseAliasMapping>> {
  const { data } = await apiClient.get<Paginated<OPNsenseAliasMapping>>(
    "/api/v1/firewalls/opnsense/mappings",
    { params: { ...(firewall_id ? { firewall_id } : {}), limit, offset } },
  );
  return data;
}

export async function createAliasMapping(
  payload: Pick<OPNsenseAliasMapping, "firewall_id" | "alias_name" | "alias_type" | "selector" | "direction">,
): Promise<OPNsenseAliasMapping> {
  const { data } = await apiClient.post<OPNsenseAliasMapping>(
    "/api/v1/firewalls/opnsense/mappings", payload,
  );
  return data;
}

export async function deleteAliasMapping(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/firewalls/opnsense/mappings/${id}`);
}

export async function syncOneMapping(id: string): Promise<unknown> {
  const { data } = await apiClient.post(`/api/v1/firewalls/opnsense/mappings/${id}/sync`,
    undefined, { timeout: LONG_OP_TIMEOUT_MS });
  return data;
}

// ─────────────────── Wazuh ───────────────────

export interface WazuhInstance {
  id: string;
  name: string;
  api_url: string;
  api_user: string;
  enabled: boolean;
  verify_tls: boolean;
  sync_interval_seconds: number;
  scope_subnet_ids?: string[] | null;
  last_sync_at: string | null;
  last_error: string | null;
  description: string | null;
  created_at: string;
  updated_at: string;
}

export interface WazuhInstanceCreate {
  name: string;
  api_url: string;
  api_user: string;
  api_password: string;
  enabled?: boolean;
  verify_tls?: boolean;
  description?: string;
  scope_subnet_ids?: string[] | null;
}

export interface WazuhAgent {
  id: string;
  instance_id: string;
  agent_id: string;
  name: string | null;
  ip: string | null;
  status: string | null;
  os_platform: string | null;
  /** 顯示用：產品名稱（os.name）優先，沒有時退回平台＋版本 */
  os?: string | null;
  os_name?: string | null;
  agent_version: string | null;
  last_keep_alive: string | null;
  jt_ipam_address_id: string | null;
}

export interface MissingAgent {
  ip_address_id: string;
  ip: string | null;
  hostname: string | null;
  // 所屬範圍（依區段／子網路／單位篩選用）
  subnet_id?: string | null;
  subnet_cidr?: string | null;
  section_id?: string | null;
  section_name?: string | null;
  customer_id?: string | null;
  customer_name?: string | null;
  // 設備類型欄（掃描代理判讀出的類型與型號）
  device_kind?: string | null;
  device_model?: string | null;
}

export async function listWazuh(
  limit = 50, offset = 0,
): Promise<Paginated<WazuhInstance>> {
  const { data } = await apiClient.get<Paginated<WazuhInstance>>(
    "/api/v1/wazuh/instances", { params: { limit, offset } },
  );
  return data;
}

export async function createWazuh(payload: WazuhInstanceCreate): Promise<WazuhInstance> {
  const { data } = await apiClient.post<WazuhInstance>("/api/v1/wazuh/instances", payload);
  return data;
}

export async function updateWazuh(
  id: string, payload: Partial<WazuhInstanceCreate & { sync_interval_seconds: number }>,
): Promise<WazuhInstance> {
  const { data } = await apiClient.patch<WazuhInstance>(
    `/api/v1/wazuh/instances/${id}`, payload,
  );
  return data;
}

export async function deleteWazuh(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/wazuh/instances/${id}`);
}

export async function testWazuh(id: string): Promise<unknown> {
  const { data } = await apiClient.post(`/api/v1/wazuh/instances/${id}/test`,
    undefined, { timeout: LONG_OP_TIMEOUT_MS });
  return data;
}

export async function syncWazuh(id: string): Promise<unknown> {
  const { data } = await apiClient.post(`/api/v1/wazuh/instances/${id}/sync`,
    undefined, { timeout: LONG_OP_TIMEOUT_MS });
  return data;
}

export async function listWazuhAgents(
  instance_id?: string, status?: string, limit = 100, offset = 0,
): Promise<Paginated<WazuhAgent>> {
  const { data } = await apiClient.get<Paginated<WazuhAgent>>("/api/v1/wazuh/agents", {
    params: {
      ...(instance_id ? { instance_id } : {}),
      ...(status ? { status } : {}),
      limit, offset,
    },
  });
  return data;
}

/** 帶 page ＝ 伺服器端分頁：篩選、排序、篩選選項都由後端算（見 useRemoteMissing）。
 *  API 不帶 page 仍回整份清單（相容舊的呼叫端），畫面已不再使用。 */
export async function listMissingAgentsPage(params: MissingQuery): Promise<MissingPage<MissingAgent>> {
  const { data } = await apiClient.get<MissingPage<MissingAgent>>("/api/v1/wazuh/missing-agents", { params });
  return data;
}

// ─────────────────── Plugins ───────────────────

export interface PluginInfo {
  name: string;
  version: string | null;
  description: string | null;
  error: string | null;
}

export async function listPlugins(): Promise<{ count: number; plugins: PluginInfo[] }> {
  const { data } = await apiClient.get<{ count: number; plugins: PluginInfo[] }>("/api/v1/plugins");
  return data;
}

// ─────────────────── AdGuard Home ───────────────────

export interface AdGuardInstance {
  id: string;
  name: string;
  api_url: string;
  api_user: string;
  enabled: boolean;
  verify_tls: boolean;
  sync_clients: boolean;
  sync_rewrites: boolean;
  sync_interval_seconds: number;
  scope_subnet_ids?: string[] | null;
  description: string | null;
  last_sync_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface AdGuardCreate {
  name: string;
  api_url: string;
  api_user: string;
  api_password: string;
  enabled?: boolean;
  verify_tls?: boolean;
  sync_clients?: boolean;
  sync_rewrites?: boolean;
  sync_interval_seconds?: number;
  description?: string;
  scope_subnet_ids?: string[] | null;
}

export async function listAdGuard(): Promise<Paginated<AdGuardInstance>> {
  const { data } = await apiClient.get<Paginated<AdGuardInstance>>("/api/v1/adguard/instances", {
    params: { page: 1, page_size: 200 },
  });
  return data;
}
export async function createAdGuard(payload: AdGuardCreate): Promise<AdGuardInstance> {
  const { data } = await apiClient.post<AdGuardInstance>("/api/v1/adguard/instances", payload);
  return data;
}
export async function updateAdGuard(
  id: string, payload: Partial<AdGuardCreate>,
): Promise<AdGuardInstance> {
  const { data } = await apiClient.patch<AdGuardInstance>(`/api/v1/adguard/instances/${id}`, payload);
  return data;
}
export async function deleteAdGuard(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/adguard/instances/${id}`);
}
export async function testAdGuard(id: string): Promise<unknown> {
  const { data } = await apiClient.post(`/api/v1/adguard/instances/${id}/test`, null,
    { timeout: LONG_OP_TIMEOUT_MS });
  return data;
}
export async function syncAdGuard(id: string): Promise<{ task_id: string }> {
  const { data } = await apiClient.post(`/api/v1/adguard/instances/${id}/sync`, null,
    { timeout: LONG_OP_TIMEOUT_MS });
  return data;
}

// ── DNS 記錄（從整合 DNS server 取回）──
export interface DnsRecord {
  id: string;
  zone_id: string;
  name: string;
  type: string;
  value: string;
  ttl: number;
  source: string;
  consistency_state: string;   // consistent / dns_only / ipam_only / mismatch
  ipam_address_id: string | null;
  matched_ip_id: string | null;   // 依 IP 值實查 ip_addresses 的對應結果（A/AAAA）
  server_id: string | null;       // 來源整合 DNS 伺服器
  server_name: string | null;
  last_seen_at: string | null;
  /** merge=true：同一個比對群組裡相同的紀錄合成這一筆，哪幾台都有 */
  servers?: string[] | null;
  compare_group_name?: string | null;
}

export async function listDnsRecords(params: {
  q?: string; ip?: string; missing_ip?: boolean; consistency?: string;
  server_id?: string; rtype?: string; page?: number; page_size?: number; merge?: boolean;
} = {}): Promise<{ items: DnsRecord[]; total: number; page: number; page_size: number }> {
  const { data } = await apiClient.get("/api/v1/dns/records", {
    params: {
      merge: params.merge || undefined,
      q: params.q || undefined,
      ip: params.ip || undefined,
      missing_ip: params.missing_ip || undefined,
      consistency: params.consistency || undefined,
      server_id: params.server_id || undefined,
      rtype: params.rtype || undefined,
      page: params.page ?? 1,
      page_size: params.page_size ?? 300,
    },
  });
  return data;
}

export async function listDnsRecordTypeCounts(params: {
  q?: string; ip?: string; missing_ip?: boolean; server_id?: string; merge?: boolean;
} = {}): Promise<{ type: string; count: number }[]> {
  const { data } = await apiClient.get("/api/v1/dns/records/type-counts", {
    params: {
      merge: params.merge || undefined,
      q: params.q || undefined,
      ip: params.ip || undefined,
      missing_ip: params.missing_ip || undefined,
      server_id: params.server_id || undefined,
    },
  });
  return data;
}
