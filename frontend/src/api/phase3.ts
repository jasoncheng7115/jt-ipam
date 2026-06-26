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

export interface ScanAgentTool {
  name: string;
  installed: boolean;
  version: string | null;
  probes: string[];
  package: string | null;
}
export interface ScanAgent {
  id: string;
  name: string;
  description: string | null;
  agent_url: string | null;
  enabled: boolean;
  has_key: boolean;
  agent_version: string | null;
  server_agent_version: string | null;
  last_source_ip: string | null;
  enabled_probes: string[];
  probe_intervals: Record<string, number> | null;
  available_probes: string[] | null;
  tools: ScanAgentTool[] | null;
  subnet_count: number;
  last_seen_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}
// 建立 / rotate 時多回一次性 enroll_key
export interface ScanAgentCreated extends ScanAgent { enroll_key: string; }

export async function listScanAgents(): Promise<Paginated<ScanAgent>> {
  const { data } = await apiClient.get<Paginated<ScanAgent>>("/api/v1/scan-agents", {
    params: { page: 1, page_size: 200 },
  });
  return data;
}
export async function createScanAgent(p: {
  name: string; description?: string; enabled?: boolean;
  enabled_probes?: string[]; probe_intervals?: Record<string, number>;
}): Promise<ScanAgentCreated> {
  const { data } = await apiClient.post<ScanAgentCreated>("/api/v1/scan-agents", p);
  return data;
}
export async function rotateScanAgentKey(id: string): Promise<ScanAgentCreated> {
  const { data } = await apiClient.post<ScanAgentCreated>(`/api/v1/scan-agents/${id}/rotate-key`);
  return data;
}
export async function scanNowAgent(id: string): Promise<{ queued: boolean; eta_seconds: number }> {
  const { data } = await apiClient.post(`/api/v1/scan-agents/${id}/scan-now`);
  return data;
}
export async function updateScanAgent(id: string, p: Partial<{
  description: string; enabled: boolean;
  enabled_probes: string[]; probe_intervals: Record<string, number>;
}>): Promise<ScanAgent> {
  const { data } = await apiClient.patch<ScanAgent>(`/api/v1/scan-agents/${id}`, p);
  return data;
}
export async function deleteScanAgent(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/scan-agents/${id}`);
}
export async function getAgentSubnets(id: string): Promise<string[]> {
  const { data } = await apiClient.get<{ subnet_ids: string[] }>(`/api/v1/scan-agents/${id}/subnets`);
  return data.subnet_ids;
}
export async function setAgentSubnets(id: string, subnetIds: string[]): Promise<string[]> {
  const { data } = await apiClient.put<{ subnet_ids: string[] }>(
    `/api/v1/scan-agents/${id}/subnets`, { subnet_ids: subnetIds },
  );
  return data.subnet_ids;
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
  src_interface: string | null;
  protocol: string;
  device_id: string | null;
  description: string | null;
  // OPNsense 完整欄位
  disabled: boolean;
  no_rdr: boolean;
  ip_version: string;
  src_not: boolean;
  dst_not: boolean;
  src_port_to: number | null;
  dst_port_to: number | null;
  log: boolean;
  category: string | null;
  nat_reflection: string | null;
  pool_options: string | null;
  filter_rule: string | null;
  src_alias: string | null;
  dst_alias: string | null;
  src_port_alias: string | null;
  dst_port_alias: string | null;
  redirect_alias: string | null;
  created_at: string;
  updated_at: string;
  source_origin: string | null;
  source_kind: string | null;
  source_firewall_id: string | null;
  source_label: string | null;
  external_id: string | null;
}

export async function bulkDeleteNATs(
  ids: string[],
): Promise<{ deleted: number; failed: number; errors: any[] }> {
  const { data } = await apiClient.post("/api/v1/nat/bulk-delete", { ids });
  return data;
}

export async function listNATs(
  params: {
    page?: number;
    deviceId?: string;
    ipId?: string;
    sourceKind?: string | string[];
    sourceFirewallId?: string;
  } = {},
): Promise<Paginated<NAT>> {
  const { data } = await apiClient.get<Paginated<NAT>>("/api/v1/nat", {
    params: {
      page: params.page ?? 1,
      page_size: 200,
      device_id: params.deviceId,
      ip_id: params.ipId,
      source_kind: params.sourceKind,
      source_firewall_id: params.sourceFirewallId,
    },
    // 陣列以重複 key 序列化 (source_kind=a&source_kind=b)，對齊 FastAPI list[str]
    paramsSerializer: { indexes: null },
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
  const { data } = await apiClient.post<AnomalyReport>("/api/v1/anomalies/scan");
  return data;
}

// ─────────────────── Migration ───────────────────

export interface MappingStat { object_type: string; count: number; }

export async function migrationStatus(): Promise<MappingStat[]> {
  const { data } = await apiClient.get<MappingStat[]>("/api/v1/migration/phpipam/status");
  return data;
}
export async function migrationSync(p: {
  mysql_url: string; on_conflict: "skip" | "overwrite"; dry_run: boolean;
}): Promise<unknown> {
  const { data } = await apiClient.post("/api/v1/migration/phpipam/sync", p);
  return data;
}

export interface MigrationConfig {
  mysql_via: "socket" | "tcp"; mysql_socket_path: string;
  host: string; port: number; username: string | null; database: string;
  ssh_host: string | null; ssh_port: number; ssh_username: string | null;
  ssh_known_host: string | null; has_private_key: boolean;
}
export async function getMigrationConfig(): Promise<MigrationConfig> {
  const { data } = await apiClient.get<MigrationConfig>("/api/v1/migration/phpipam/config");
  return data;
}
export async function saveMigrationConfig(p: Record<string, unknown>): Promise<MigrationConfig> {
  const { data } = await apiClient.put<MigrationConfig>("/api/v1/migration/phpipam/config", p);
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
export interface Provider { id: string; name: string; asn: number | null; account_number: string | null; portal_url: string | null; noc_contact: string | null; description: string | null; }
export interface CircuitType { id: string; name: string; description: string | null; created_at: string; updated_at: string; }
export interface Circuit { id: string; cid: string; provider_id: string; type_id: string; status: string; monthly_fee_cents: number | null; commit_rate_kbps: number | null; up_kbps: number | null; down_kbps: number | null; install_date: string | null; contract_end_date: string | null; ip_address: string | null; gateway: string | null; netmask: string | null; dns_servers: string | null; device_id: string | null; description: string | null; created_at: string; updated_at: string; }
export interface ContactGroup { id: string; name: string; description: string | null; created_at: string; updated_at: string; }
export interface ContactRole { id: string; name: string; description: string | null; created_at: string; updated_at: string; }
export interface Contact { id: string; name: string; email: string | null; phone: string | null; group_id: string | null; description: string | null; created_at: string; updated_at: string; }
export interface WirelessSSID { id: string; name: string; description: string | null; created_at: string; updated_at: string; }
export interface WirelessLink { id: string; ssid_id: string; description: string | null; created_at: string; updated_at: string; }

async function getList<T>(url: string): Promise<T[]> {
  // 逐頁抓完整清單（後端 page_size 上限 500）→ 超過 500 筆（如 592 台 VM）也不會被截斷
  const PAGE = 500;
  const out: T[] = [];
  for (let page = 1; page <= 1000; page++) {
    const { data } = await apiClient.get<Paginated<T> | { items: T[]; total?: number }>(url, {
      params: { page, page_size: PAGE },
    });
    const items = ("items" in data && Array.isArray(data.items)) ? data.items : [];
    out.push(...items);
    const total = (data as { total?: number }).total;
    if (items.length < PAGE || (typeof total === "number" && out.length >= total)) break;
  }
  return out;
}

export const Advanced = {
  tenants: () => getList<Tenant>("/api/v1/tenants"),
  tenantGroups: () => getList<TenantGroup>("/api/v1/tenant-groups"),
  asns: () => getList<ASN>("/api/v1/asns"),
  providers: () => getList<Provider>("/api/v1/providers"),
  circuitTypes: () => getList<CircuitType>("/api/v1/circuit-types"),
  createCircuitType: (name: string) =>
    apiClient.post<CircuitType>("/api/v1/circuit-types", { name }).then((r) => r.data),
  circuits: () => getList<Circuit>("/api/v1/circuits"),
  contactGroups: () => getList<ContactGroup>("/api/v1/contact-groups"),
  contactRoles: () => getList<ContactRole>("/api/v1/contact-roles"),
  contacts: () => getList<Contact>("/api/v1/contacts"),
  ssids: () => getList<WirelessSSID>("/api/v1/wireless/ssids"),
  links: () => getList<WirelessLink>("/api/v1/wireless/links"),
};

// ─────────────────── 裝置電源埠（PSU → 插座） ───────────────────
export interface DevicePowerPort {
  id: string;
  device_id: string;
  name: string;
  outlet_id: string | null;
  outlet_label: string | null;
  max_watts: number | null;
  description: string | null;
}
export async function listDevicePowerPorts(deviceId: string): Promise<DevicePowerPort[]> {
  const { data } = await apiClient.get<DevicePowerPort[]>("/api/v1/device-power-ports", {
    params: { device_id: deviceId },
  });
  return data;
}
export async function createDevicePowerPort(p: {
  device_id: string; name: string; outlet_id?: string | null; max_watts?: number | null; description?: string | null;
}): Promise<DevicePowerPort> {
  const { data } = await apiClient.post<DevicePowerPort>("/api/v1/device-power-ports", p);
  return data;
}
export async function updateDevicePowerPort(id: string, p: {
  name?: string; outlet_id?: string | null; max_watts?: number | null; description?: string | null;
}): Promise<DevicePowerPort> {
  const { data } = await apiClient.patch<DevicePowerPort>(`/api/v1/device-power-ports/${id}`, p);
  return data;
}
export async function deleteDevicePowerPort(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/device-power-ports/${id}`);
}

// ─────────────────── Virt ───────────────────

export interface VirtCluster { id: string; name: string; type: string | null; is_standalone: boolean; description: string | null; }
export interface VirtualMachine { id: string; name: string; cluster_id: string | null; node: string | null; kind: string | null; status: string | null; ips: string[]; macs: string[]; bridges: string[]; }
export interface ProxmoxInstance { id: string; name: string; api_url: string; extra_api_urls: string[]; node: string | null; auth_username: string; auth_token_id: string; verify_tls: boolean; sync_interval_seconds: number; enabled: boolean; scope_subnet_ids?: string[] | null; last_sync_at: string | null; last_error: string | null; }

export interface ProxmoxWrite {
  cluster_id?: string; api_url: string; extra_api_urls?: string[];
  auth_username: string; auth_token_id: string; token_secret?: string;
  verify_tls?: boolean; enabled?: boolean; sync_interval_seconds?: number;
  scope_subnet_ids?: string[] | null;
}

export const Virt = {
  clusters: () => getList<VirtCluster>("/api/v1/virt/clusters"),
  vms: () => getList<VirtualMachine>("/api/v1/virt/vms"),
  proxmox: () => getList<ProxmoxInstance>("/api/v1/virt/proxmox"),
  syncProxmox: async (id: string) => {
    const { data } = await apiClient.post(`/api/v1/virt/proxmox/${id}/sync`,
      undefined, { timeout: 300_000 });
    return data;
  },
  testProxmox: async (id: string) => {
    const { data } = await apiClient.post(`/api/v1/virt/proxmox/${id}/test`,
      undefined, { timeout: 60_000 });
    return data;
  },
  createCluster: async (p: { name: string; type?: string; description?: string; customer_id?: string | null }) => {
    const { data } = await apiClient.post<VirtCluster>("/api/v1/virt/clusters", p);
    return data;
  },
  updateCluster: async (id: string, p: { name?: string; description?: string; customer_id?: string | null }) => {
    const { data } = await apiClient.patch<VirtCluster>(`/api/v1/virt/clusters/${id}`, p);
    return data;
  },
  createProxmox: async (p: ProxmoxWrite) => {
    const { data } = await apiClient.post<ProxmoxInstance>("/api/v1/virt/proxmox", p);
    return data;
  },
  updateProxmox: async (id: string, p: Partial<ProxmoxWrite>) => {
    const { data } = await apiClient.patch<ProxmoxInstance>(`/api/v1/virt/proxmox/${id}`, p);
    return data;
  },
  deleteProxmox: async (id: string) => {
    await apiClient.delete(`/api/v1/virt/proxmox/${id}`);
  },
};

// ─────────────────── Physical ───────────────────

export interface Cable { id: string; type: string; status: string; description: string | null; }
export interface PowerPanel { id: string; name: string; location_id: string | null; }
export interface PowerFeed { id: string; name: string; panel_id: string; }
export interface PowerOutlet { id: string; name: string; feed_id: string; }
export interface VPNTunnel { id: string; name: string; type: string; status: string; }

export interface DevicePort {
  id: string; device_id: string; name: string; type: string;
  peer_port_id: string | null; position: number | null; description: string | null;
  link?: string | null; mac_address?: string | null;
}
export interface TraceNode {
  port_id?: string; port_name?: string; port_type?: string;
  device_id?: string; device_name?: string;
  object_type?: string; object_id?: string;
}
export interface TraceHop {
  cable_id: string | null; cable_label: string | null; cable_type: string | null;
  cable_color: string | null; to: TraceNode | null; internal?: boolean;
}
export interface PortTrace { start: TraceNode; nodes: TraceNode[]; hops: TraceHop[]; }

export const Physical = {
  cables: () => getList<Cable>("/api/v1/cables"),
  panels: () => getList<PowerPanel>("/api/v1/power-panels"),
  feeds: () => getList<PowerFeed>("/api/v1/power-feeds"),
  outlets: () => getList<PowerOutlet>("/api/v1/power-outlets"),
  vpns: () => getList<VPNTunnel>("/api/v1/vpn-tunnels"),
  async ports(deviceId: string): Promise<DevicePort[]> {
    const { data } = await apiClient.get<DevicePort[]>("/api/v1/device-ports", { params: { device_id: deviceId } });
    return data;
  },
  async createPort(p: Partial<DevicePort> & { device_id: string; name: string }): Promise<DevicePort> {
    const { data } = await apiClient.post<DevicePort>("/api/v1/device-ports", p);
    return data;
  },
  async updatePort(id: string, p: Partial<DevicePort>): Promise<DevicePort> {
    const { data } = await apiClient.patch<DevicePort>(`/api/v1/device-ports/${id}`, p);
    return data;
  },
  async deletePort(id: string): Promise<void> { await apiClient.delete(`/api/v1/device-ports/${id}`); },
  async importPorts(deviceId: string): Promise<{ imported: number; found: number; linked_librenms: number; source: string }> {
    const { data } = await apiClient.post("/api/v1/device-ports/import", null, { params: { device_id: deviceId } });
    return data;
  },
  async tracePort(id: string): Promise<PortTrace> {
    const { data } = await apiClient.get<PortTrace>(`/api/v1/ports/${id}/trace`);
    return data;
  },
  async createPanel(p: { name: string; location_id?: string | null; description?: string | null }): Promise<PowerPanel> {
    const { data } = await apiClient.post<PowerPanel>("/api/v1/power-panels", p);
    return data;
  },
  async createFeed(p: { panel_id: string; name: string; description?: string | null }): Promise<PowerFeed> {
    const { data } = await apiClient.post<PowerFeed>("/api/v1/power-feeds", p);
    return data;
  },
  async createOutlet(p: { feed_id?: string | null; label: string; rack_id?: string | null; device_id?: string | null; description?: string | null }): Promise<PowerOutlet> {
    const { data } = await apiClient.post<PowerOutlet>("/api/v1/power-outlets", p);
    return data;
  },
  async updatePanel(id: string, p: Record<string, any>): Promise<void> { await apiClient.patch(`/api/v1/power-panels/${id}`, p); },
  async deletePanel(id: string): Promise<void> { await apiClient.delete(`/api/v1/power-panels/${id}`); },
  async updateFeed(id: string, p: Record<string, any>): Promise<void> { await apiClient.patch(`/api/v1/power-feeds/${id}`, p); },
  async deleteFeed(id: string): Promise<void> { await apiClient.delete(`/api/v1/power-feeds/${id}`); },
  async updateOutlet(id: string, p: Record<string, any>): Promise<void> { await apiClient.patch(`/api/v1/power-outlets/${id}`, p); },
  async deleteOutlet(id: string): Promise<void> { await apiClient.delete(`/api/v1/power-outlets/${id}`); },
  // 連線：建一條 cable + 兩端 termination（都接到 device_port）
  async connectPorts(aPortId: string, bPortId: string, opts: { type?: string; color?: string; label?: string; length_m?: number } = {}): Promise<void> {
    const { data: cable } = await apiClient.post<Cable>("/api/v1/cables", {
      type: opts.type ?? null, color: opts.color ?? null, label: opts.label ?? null,
      length_m: opts.length_m ?? null, status: "connected",
    });
    await apiClient.post("/api/v1/cable-terminations", { cable_id: cable.id, side: "A", object_type: "device_port", object_id: aPortId });
    await apiClient.post("/api/v1/cable-terminations", { cable_id: cable.id, side: "B", object_type: "device_port", object_id: bPortId });
  },
  async updateCable(id: string, patch: { type?: string | null; label?: string | null; color?: string | null; length_m?: number | null; description?: string | null; status?: string }): Promise<void> {
    await apiClient.patch(`/api/v1/cables/${id}`, patch);
  },
  async deleteCable(id: string): Promise<void> {
    await apiClient.delete(`/api/v1/cables/${id}`);
  },
};
