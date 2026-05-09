/**
 * Integration endpoints：DNS / LibreNMS / Firewall (OPNsense) / Wazuh / Plugins。
 */
import { apiClient } from "@/api/client";
import type { Paginated } from "@/api/admin";

// ─────────────────── DNS ───────────────────

export interface DNSServer {
  id: string;
  name: string;
  type: string;
  endpoint: string;
  enabled: boolean;
  is_authoritative: boolean;
  managed_zones?: string[];
  last_error?: string | null;
  created_at: string;
  updated_at: string;
}

export async function listDNSServers(): Promise<{ items: DNSServer[] }> {
  const { data } = await apiClient.get<{ items: DNSServer[] }>("/api/v1/dns/servers");
  return data;
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

export async function testLibreNMS(id: string): Promise<unknown> {
  const { data } = await apiClient.post(`/api/v1/librenms/instances/${id}/test`);
  return data;
}

export async function syncLibreNMS(id: string): Promise<unknown> {
  const { data } = await apiClient.post(`/api/v1/librenms/instances/${id}/sync`);
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
  description?: string;
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

export async function deleteFirewall(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/firewalls/opnsense/${id}`);
}

export async function testFirewall(id: string): Promise<unknown> {
  const { data } = await apiClient.post(`/api/v1/firewalls/opnsense/${id}/test`);
  return data;
}

export async function syncFirewall(id: string): Promise<unknown> {
  const { data } = await apiClient.post(`/api/v1/firewalls/opnsense/${id}/sync`);
  return data;
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
  const { data } = await apiClient.post(`/api/v1/firewalls/opnsense/mappings/${id}/sync`);
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

export async function deleteWazuh(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/wazuh/instances/${id}`);
}

export async function testWazuh(id: string): Promise<unknown> {
  const { data } = await apiClient.post(`/api/v1/wazuh/instances/${id}/test`);
  return data;
}

export async function syncWazuh(id: string): Promise<unknown> {
  const { data } = await apiClient.post(`/api/v1/wazuh/instances/${id}/sync`);
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
