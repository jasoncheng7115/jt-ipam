import { apiClient } from "@/api/client";

// 同步 / 測試會打外部 pfSense API，可能慢，放寬 timeout
const SLOW = { timeout: 60000 };

export interface PfSense {
  id: string;
  name: string;
  api_url: string;
  enabled: boolean;
  verify_tls: boolean;
  has_key: boolean;
  sync_interval_seconds: number;
  sync_dhcp: boolean;
  sync_arp: boolean;
  sync_aliases: boolean;
  sync_rules: boolean;
  expose_dsv: boolean;
  scope_subnet_ids: string[] | null;
  description: string | null;
  alias_count: number;
  rule_count: number;
  last_sync_at: string | null;
  last_error: string | null;
}

export interface PfSenseCreate {
  name: string;
  api_url: string;
  api_key: string;
  verify_tls?: boolean;
  enabled?: boolean;
  sync_interval_seconds?: number;
  sync_dhcp?: boolean;
  sync_arp?: boolean;
  sync_aliases?: boolean;
  sync_rules?: boolean;
  expose_dsv?: boolean;
  scope_subnet_ids?: string[] | null;
  description?: string | null;
}
export type PfSenseUpdate = Partial<PfSenseCreate>;
export interface PfRule {
  tracker: number | null; type: string | null; interface: string;
  protocol: string | null; source: any; destination: any;
  destination_port: string | null; descr: string; disabled: boolean;
}

export async function listPfSense(limit = 50, offset = 0): Promise<{ items: PfSense[]; total: number }> {
  const { data } = await apiClient.get("/api/v1/pfsense", { params: { limit, offset } });
  return data;
}
export async function createPfSense(p: PfSenseCreate): Promise<PfSense> {
  const { data } = await apiClient.post("/api/v1/pfsense", p);
  return data;
}
export async function updatePfSense(id: string, p: PfSenseUpdate): Promise<PfSense> {
  const { data } = await apiClient.patch(`/api/v1/pfsense/${id}`, p);
  return data;
}
export async function deletePfSense(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/pfsense/${id}`);
}
export async function testPfSense(id: string): Promise<{ ok: boolean; version: any }> {
  const { data } = await apiClient.post(`/api/v1/pfsense/${id}/test`, {}, SLOW);
  return data;
}
export async function syncPfSense(id: string): Promise<{ ok: boolean; counts: Record<string, number> }> {
  const { data } = await apiClient.post(`/api/v1/pfsense/${id}/sync`, {}, SLOW);
  return data;
}
export async function getPfSenseRules(id: string): Promise<{ items: PfRule[] }> {
  const { data } = await apiClient.get(`/api/v1/pfsense/${id}/rules`);
  return data;
}
export async function getPfSenseNat(id: string): Promise<{ port_forwards: any[]; outbound: any[] }> {
  const { data } = await apiClient.get(`/api/v1/pfsense/${id}/nat`, { timeout: 60000 });
  return data;
}
export async function listPfSenseAliases(
  id: string,
): Promise<{ items: { name: string; type: string | null; members: string[]; descr: string | null }[] }> {
  const { data } = await apiClient.get(`/api/v1/pfsense/${id}/aliases`);
  return data;
}
