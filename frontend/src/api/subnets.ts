import { apiClient } from "@/api/client";
import type { Paginated, ScanScope, ScanIntensity, Subnet, SubnetUsage } from "@/types";

export async function listSubnets(
  params: { sectionId?: string; archived?: boolean; page?: number; pageSize?: number; q?: string } = {},
): Promise<Paginated<Subnet>> {
  const { data } = await apiClient.get<Paginated<Subnet>>("/api/v1/subnets", {
    params: {
      section_id: params.sectionId,
      archived: params.archived ?? false,
      // 伺服器端搜尋：CIDR／描述的子字串，或包含某個位址的子網路
      q: params.q || undefined,
      page: params.page ?? 1,
      page_size: params.pageSize ?? 50,
    },
  });
  return data;
}

export async function archiveSubnet(id: string): Promise<Subnet> {
  const { data } = await apiClient.post<Subnet>(`/api/v1/subnets/${id}/archive`);
  return data;
}

export async function unarchiveSubnet(id: string): Promise<Subnet> {
  const { data } = await apiClient.post<Subnet>(`/api/v1/subnets/${id}/unarchive`);
  return data;
}

export async function getSubnetUsage(id: string): Promise<SubnetUsage> {
  const { data } = await apiClient.get<SubnetUsage>(`/api/v1/subnets/${id}/usage`);
  return data;
}

export async function getFirstFreeAddress(
  id: string,
): Promise<{ subnet_id: string; cidr: string; ip: string | null }> {
  const { data } = await apiClient.get(`/api/v1/subnets/${id}/first_free_address`);
  return data;
}

export interface BulkDeleteResult {
  deleted: number;
  failed: number;
  errors: { id: string; error: string }[];
}

export async function bulkDeleteSubnets(ids: string[]): Promise<BulkDeleteResult> {
  const { data } = await apiClient.post<BulkDeleteResult>("/api/v1/subnets/bulk-delete", { ids });
  return data;
}

export async function getSubnet(id: string): Promise<Subnet> {
  const { data } = await apiClient.get<Subnet>(`/api/v1/subnets/${id}`);
  return data;
}

export interface SubnetCreate {
  section_id: string;
  cidr: string;
  description?: string | null;
  vlan_id?: string | null;
  vrf_id?: string | null;
  is_pool?: boolean;
  is_full?: boolean;
  ai_audit_enabled?: boolean;
  anomaly_enabled?: boolean;
  scan_enabled?: boolean;
  scan_method?: string[];
  threshold_pct?: number | null;
  customer_id?: string | null;
  /** 主控台的連線出口（issue #24）：空＝繼承上層或直連 */
  jump_host_id?: string | null;
  console_agent_id?: string | null;
  scan_agent_id?: string | null;
  scan_scope?: ScanScope;
  scan_intensity?: ScanIntensity;
  gateway?: string | null;
  dns_servers?: string | null;
  location_id?: string | null;
  allow_overlap?: boolean;
  custom_fields?: Record<string, unknown>;
}

export interface SubnetUpdate {
  section_id?: string | null;
  description?: string | null;
  vlan_id?: string | null;
  vrf_id?: string | null;
  master_subnet_id?: string | null;
  is_pool?: boolean | null;
  is_full?: boolean | null;
  ai_audit_enabled?: boolean | null;
  anomaly_enabled?: boolean | null;
  scan_enabled?: boolean | null;
  scan_method?: string[] | null;
  threshold_pct?: number | null;
  customer_id?: string | null;
  /** 主控台的連線出口（issue #24）：空＝繼承上層或直連 */
  jump_host_id?: string | null;
  console_agent_id?: string | null;
  scan_agent_id?: string | null;
  scan_scope?: ScanScope;
  scan_intensity?: ScanIntensity;
  gateway?: string | null;
  dns_servers?: string | null;
  location_id?: string | null;
  custom_fields?: Record<string, unknown>;
}

export async function createSubnet(payload: SubnetCreate): Promise<Subnet> {
  const { data } = await apiClient.post<Subnet>("/api/v1/subnets", payload);
  return data;
}

export async function updateSubnet(id: string, payload: SubnetUpdate): Promise<Subnet> {
  const { data } = await apiClient.patch<Subnet>(`/api/v1/subnets/${id}`, payload);
  return data;
}

export async function deleteSubnet(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/subnets/${id}`);
}

// AI 巡檢範圍：寫的是 subnets.ai_audit_enabled —— 跟子網路編輯頁那個勾選框同一個欄位
export async function setAIAuditScope(subnetIds: string[]): Promise<{ included: number; changed: number }> {
  const { data } = await apiClient.put("/api/v1/subnets/ai-audit-scope", { subnet_ids: subnetIds });
  return data;
}

// 異常偵測範圍：同樣寫 subnets.anomaly_enabled（跟子網路編輯頁同一個欄位）
export async function setAnomalyScope(subnetIds: string[]): Promise<{ included: number; changed: number }> {
  const { data } = await apiClient.put("/api/v1/subnets/anomaly-scope", { subnet_ids: subnetIds });
  return data;
}
