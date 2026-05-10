/**
 * Phase 3 features API：custom_fields / scan_agents / notifications /
 * nat / anomaly / scan / migration / import_external / advanced
 * (tenancy/contacts/asn/circuits/wireless) / virt / physical。
 */
import { apiClient } from "@/api/client";
import type { Paginated } from "@/api/admin";

// ─────────────────── Custom Fields ───────────────────

export interface CustomField {
  id: string;
  object_type: "subnet" | "ip" | "device";
  name: string;
  label_zh_tw: string | null;
  label_en_us: string | null;
  field_type: string;
  options: Record<string, unknown> | null;
  validation_regex: string | null;
  required: boolean;
  display_order: number;
  created_at: string;
  updated_at: string;
}

export async function listCustomFields(page = 1): Promise<Paginated<CustomField>> {
  const { data } = await apiClient.get<Paginated<CustomField>>("/api/v1/custom-fields", {
    params: { page, page_size: 200 },
  });
  return data;
}
export async function createCustomField(p: Partial<CustomField>): Promise<CustomField> {
  const { data } = await apiClient.post<CustomField>("/api/v1/custom-fields", p);
  return data;
}
export async function updateCustomField(id: string, p: Partial<CustomField>): Promise<CustomField> {
  const { data } = await apiClient.patch<CustomField>(`/api/v1/custom-fields/${id}`, p);
  return data;
}
export async function deleteCustomField(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/custom-fields/${id}`);
}

// ─────────────────── Scan Agents ───────────────────

export interface ScanAgent {
  id: string;
  name: string;
  description: string | null;
  agent_url: string;
  enabled: boolean;
  last_seen_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export async function listScanAgents(): Promise<Paginated<ScanAgent>> {
  const { data } = await apiClient.get<Paginated<ScanAgent>>("/api/v1/scan-agents", {
    params: { page: 1, page_size: 200 },
  });
  return data;
}
export async function createScanAgent(p: {
  name: string; agent_url: string; api_token: string;
  description?: string; enabled?: boolean;
}): Promise<ScanAgent> {
  const { data } = await apiClient.post<ScanAgent>("/api/v1/scan-agents", p);
  return data;
}
export async function updateScanAgent(id: string, p: Partial<{
  description: string; agent_url: string; enabled: boolean; api_token: string;
}>): Promise<ScanAgent> {
  const { data } = await apiClient.patch<ScanAgent>(`/api/v1/scan-agents/${id}`, p);
  return data;
}
export async function deleteScanAgent(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/scan-agents/${id}`);
}

// ─────────────────── Webhooks ───────────────────

export interface Webhook {
  id: string;
  name: string;
  target_url: string;
  events: string[];
  enabled: boolean;
  failure_count: number;
  last_attempt_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
  headers: Record<string, string> | null;
}
export interface WebhookCreated extends Omit<Webhook, "failure_count" | "last_attempt_at" | "last_success_at" | "last_error" | "headers"> {
  secret: string;
}

export async function listWebhooks(): Promise<Paginated<Webhook>> {
  const { data } = await apiClient.get<Paginated<Webhook>>("/api/v1/webhooks", {
    params: { page: 1, page_size: 100 },
  });
  return data;
}
export async function createWebhook(p: {
  name: string; target_url: string; events?: string[];
  headers?: Record<string, string>;
}): Promise<WebhookCreated> {
  const { data } = await apiClient.post<WebhookCreated>("/api/v1/webhooks", p);
  return data;
}
export async function deleteWebhook(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/webhooks/${id}`);
}

// ─────────────────── NAT ───────────────────

export interface NAT {
  id: string;
  name: string;
  type: string;
  src_ip_id: string | null;
  dst_ip_id: string | null;
  src_port: number | null;
  dst_port: number | null;
  protocol: string;
  device_id: string | null;
  description: string | null;
  created_at: string;
  updated_at: string;
}

export async function listNATs(page = 1): Promise<Paginated<NAT>> {
  const { data } = await apiClient.get<Paginated<NAT>>("/api/v1/nat", {
    params: { page, page_size: 200 },
  });
  return data;
}
export async function createNAT(p: Partial<NAT>): Promise<NAT> {
  const { data } = await apiClient.post<NAT>("/api/v1/nat", p);
  return data;
}
export async function updateNAT(id: string, p: Partial<NAT>): Promise<NAT> {
  const { data } = await apiClient.patch<NAT>(`/api/v1/nat/${id}`, p);
  return data;
}
export async function deleteNAT(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/nat/${id}`);
}

// ─────────────────── Anomaly ───────────────────

export interface AnomalyReport {
  ip_conflicts: any[];
  mac_drifts: any[];
  ghost_ips: any[];
  unauthorized_ips: any[];
}

export async function runAnomalyScan(): Promise<AnomalyReport> {
  const { data } = await apiClient.post<AnomalyReport>("/api/v1/anomaly/scan");
  return data;
}

// ─────────────────── Migration ───────────────────

export interface MappingStat { object_type: string; count: number; }

export async function migrationStatus(): Promise<MappingStat[]> {
  const { data } = await apiClient.get<MappingStat[]>("/api/v1/migration/status");
  return data;
}
export async function migrationSync(p: {
  mysql_url: string; on_conflict: "skip" | "overwrite"; dry_run: boolean;
}): Promise<unknown> {
  const { data } = await apiClient.post("/api/v1/migration/sync", p);
  return data;
}

// ─────────────────── RIPE / TWNIC import ───────────────────

export async function ripePreview(payload: { handle?: string; cidr?: string }): Promise<unknown> {
  const { data } = await apiClient.post("/api/v1/import/ripe/preview", payload);
  return data;
}
export async function ripeCommit(payload: { handle?: string; cidr?: string; section_id: string }): Promise<unknown> {
  const { data } = await apiClient.post("/api/v1/import/ripe/commit", payload);
  return data;
}

// ─────────────────── Advanced (Phase 3): Tenancy / Contacts / ASN / Circuits / Wireless ───────────────────

export interface Tenant { id: string; name: string; tenant_group_id: string | null; description: string | null; created_at: string; updated_at: string; }
export interface TenantGroup { id: string; name: string; description: string | null; created_at: string; updated_at: string; }
export interface ASN { id: string; number: number; rir: string | null; description: string | null; tenant_id: string | null; created_at: string; updated_at: string; }
export interface Provider { id: string; name: string; account: string | null; description: string | null; created_at: string; updated_at: string; }
export interface CircuitType { id: string; name: string; description: string | null; created_at: string; updated_at: string; }
export interface Circuit { id: string; cid: string; provider_id: string; type_id: string; status: string; description: string | null; created_at: string; updated_at: string; }
export interface ContactGroup { id: string; name: string; description: string | null; created_at: string; updated_at: string; }
export interface ContactRole { id: string; name: string; description: string | null; created_at: string; updated_at: string; }
export interface Contact { id: string; name: string; email: string | null; phone: string | null; group_id: string | null; description: string | null; created_at: string; updated_at: string; }
export interface WirelessSSID { id: string; name: string; description: string | null; created_at: string; updated_at: string; }
export interface WirelessLink { id: string; ssid_id: string; description: string | null; created_at: string; updated_at: string; }

async function getList<T>(url: string): Promise<T[]> {
  const { data } = await apiClient.get<Paginated<T> | { items: T[] }>(url, {
    params: { page: 1, page_size: 500 },
  });
  return ("items" in data && Array.isArray(data.items)) ? data.items : [];
}

export const Advanced = {
  tenants: () => getList<Tenant>("/api/v1/tenants"),
  tenantGroups: () => getList<TenantGroup>("/api/v1/tenant-groups"),
  asns: () => getList<ASN>("/api/v1/asns"),
  providers: () => getList<Provider>("/api/v1/providers"),
  circuitTypes: () => getList<CircuitType>("/api/v1/circuit-types"),
  circuits: () => getList<Circuit>("/api/v1/circuits"),
  contactGroups: () => getList<ContactGroup>("/api/v1/contact-groups"),
  contactRoles: () => getList<ContactRole>("/api/v1/contact-roles"),
  contacts: () => getList<Contact>("/api/v1/contacts"),
  ssids: () => getList<WirelessSSID>("/api/v1/wireless/ssids"),
  links: () => getList<WirelessLink>("/api/v1/wireless/links"),
};

// ─────────────────── Virt ───────────────────

export interface VirtCluster { id: string; name: string; type: string | null; description: string | null; }
export interface VirtualMachine { id: string; name: string; cluster_id: string | null; status: string | null; }
export interface ProxmoxInstance { id: string; name: string; api_url: string; node: string | null; enabled: boolean; last_sync_at: string | null; last_error: string | null; }

export const Virt = {
  clusters: () => getList<VirtCluster>("/api/v1/virt/clusters"),
  vms: () => getList<VirtualMachine>("/api/v1/virt/vms"),
  proxmox: () => getList<ProxmoxInstance>("/api/v1/virt/proxmox"),
  syncProxmox: async (id: string) => {
    const { data } = await apiClient.post(`/api/v1/virt/proxmox/${id}/sync`);
    return data;
  },
};

// ─────────────────── Physical ───────────────────

export interface Cable { id: string; type: string; status: string; description: string | null; }
export interface PowerPanel { id: string; name: string; location_id: string | null; }
export interface PowerFeed { id: string; name: string; panel_id: string; }
export interface PowerOutlet { id: string; name: string; feed_id: string; }
export interface VPNTunnel { id: string; name: string; type: string; status: string; }

export const Physical = {
  cables: () => getList<Cable>("/api/v1/cables"),
  panels: () => getList<PowerPanel>("/api/v1/power-panels"),
  feeds: () => getList<PowerFeed>("/api/v1/power-feeds"),
  outlets: () => getList<PowerOutlet>("/api/v1/power-outlets"),
  vpns: () => getList<VPNTunnel>("/api/v1/vpn-tunnels"),
};
