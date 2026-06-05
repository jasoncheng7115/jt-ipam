/**
 * Integration endpoints：DNS / LibreNMS / Firewall (OPNsense) / Wazuh / Plugins。
 */
import { apiClient } from "@/api/client";
import type { Paginated } from "@/api/admin";

// 整合同步/測試可能要打外部 API、跑數百筆 ingest，遠遠超過全域 15s 預設。
// 給長時操作 5 分鐘空間。
const LONG_OP_TIMEOUT_MS = 300_000;

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
  last_sync_at?: string | null;
  last_error?: string | null;
  created_at: string;
  updated_at: string;
}

export async function listDNSServers(): Promise<{ items: DNSServer[] }> {
  const { data } = await apiClient.get<{ items: DNSServer[] }>("/api/v1/dns/servers");
  return data;
}

export type DNSServerType = "powerdns" | "bind9" | "unbound_opnsense" | "windows_dns" | "univention_ucs";

export interface DNSServerCreate {
  name: string;
  type: DNSServerType;
  api_url?: string | null;
  server_address?: string | null;
  extra_config?: string | null;
  enabled?: boolean;
  sync_interval_seconds?: number;
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

export async function testDNSServer(id: string): Promise<unknown> {
  const { data } = await apiClient.post(`/api/v1/dns/servers/${id}/test`);
  return data;
}

// ─────────────────── LibreNMS ───────────────────

export interface LibreNMSInstance {
  id: string;
  name: string;
  api_url: string;
  enabled: boolean;
  sync_devices: boolean;
  sync_arp: boolean;
  sync_fdb: boolean;
  sync_vlans: boolean;
  scope_subnet_ids: string[] | null;
  use_for_status: boolean;
  auto_add_devices: boolean;
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
  sync_devices?: boolean;
  sync_arp?: boolean;
  sync_fdb?: boolean;
  sync_vlans?: boolean;
  scope_subnet_ids?: string[] | null;
  use_for_status?: boolean;
  auto_add_devices?: boolean;
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

export async function syncLibreNMS(id: string): Promise<unknown> {
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
  scope_subnet_ids?: string[] | null;
  use_for_status?: boolean;
  auto_add_devices?: boolean;
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
  description: string | null;
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
  description?: string;
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

export async function syncFirewall(id: string): Promise<unknown> {
  const { data } = await apiClient.post(`/api/v1/firewalls/opnsense/${id}/sync`,
    undefined, { timeout: LONG_OP_TIMEOUT_MS });
  return data;
}

export interface DhcpPoolRange {
  id: string; firewall_id: string; subnet_cidr: string;
  start_ip: string; end_ip: string; family: number; source: string;
  firewall_name?: string | null;
}
// 所有 DHCP 發放範圍（IP 清單用來標示 DHCP 動態區）。需 admin；非 admin 取不到時回空。
export async function listDhcpRanges(): Promise<DhcpPoolRange[]> {
  try {
    const { data } = await apiClient.get<DhcpPoolRange[]>("/api/v1/firewalls/opnsense/dhcp-ranges");
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
}

export interface WazuhAgent {
  id: string;
  instance_id: string;
  agent_id: string;
  name: string | null;
  ip: string | null;
  status: string | null;
  os_platform: string | null;
  agent_version: string | null;
  last_keep_alive: string | null;
  jt_ipam_address_id: string | null;
}

export interface MissingAgent {
  ip_address_id: string;
  ip: string | null;
  hostname: string | null;
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

export async function listMissingAgents(): Promise<MissingAgent[]> {
  const { data } = await apiClient.get<MissingAgent[]>("/api/v1/wazuh/missing-agents");
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
export async function syncAdGuard(id: string): Promise<unknown> {
  const { data } = await apiClient.post(`/api/v1/adguard/instances/${id}/sync`, null,
    { timeout: LONG_OP_TIMEOUT_MS });
  return data;
}
