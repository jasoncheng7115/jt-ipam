import { apiClient } from "@/api/client";
import type { IPAddress, Paginated } from "@/types";

export async function listAddresses(
  params: {
    subnetId?: string; sectionId?: string; customerId?: string;
    deviceId?: string; q?: string; exact?: boolean; page?: number; pageSize?: number;
    sort?: string; order?: "asc" | "desc";
  } = {},
): Promise<Paginated<IPAddress>> {
  const { data } = await apiClient.get<Paginated<IPAddress>>("/api/v1/addresses", {
    params: {
      subnet_id: params.subnetId,
      section_id: params.sectionId,
      customer_id: params.customerId,
      device_id: params.deviceId,
      q: params.q || undefined,
      exact: params.exact || undefined,
      sort: params.sort || undefined,
      order: params.order || undefined,
      page: params.page ?? 1,
      page_size: params.pageSize ?? 100,
    },
  });
  return data;
}

export async function getAddress(id: string): Promise<IPAddress> {
  const { data } = await apiClient.get<IPAddress>(`/api/v1/addresses/${id}`);
  return data;
}

export interface IPAddressUpdate {
  /** 主控台的連線出口（issue #24）：空＝沿用所屬子網路；跳板與掃描代理擇一 */
  jump_host_id?: string | null;
  console_agent_id?: string | null;
  hostname?: string | null;
  description?: string | null;
  state?: string | null;
  mac?: string | null;
  owner?: string | null;
  device_id?: string | null;
  /** 共用給裝置群組（堆疊管理 IP、HA 虛擬 IP）；管理員限定。空字串／null＝取消共用 */
  device_group_id?: string | null;
  switch_port?: string | null;
  exclude_from_ping?: boolean | null;
  excluded_probes?: string[] | null;
  ptr_ignore?: boolean | null;
  note?: string | null;
  customer_id?: string | null;
  hostname_source_pin?: string | null;
  ssh_enabled?: boolean | null;
  sftp_enabled?: boolean | null;
  rdp_enabled?: boolean | null;
  vnc_enabled?: boolean | null;
  novnc_enabled?: boolean | null;
  bmc_enabled?: boolean | null;
  rustdesk_enabled?: boolean | null;
  is_dhcp_server?: boolean | null;
  liveness_from_device?: boolean | null;
}

export async function updateAddress(id: string, payload: IPAddressUpdate): Promise<IPAddress> {
  const { data } = await apiClient.patch<IPAddress>(`/api/v1/addresses/${id}`, payload);
  return data;
}

export async function deleteAddress(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/addresses/${id}`);
}

export interface IPAddressCreate {
  subnet_id: string;
  ip: string;
  hostname?: string | null;
  description?: string | null;
  state?: string;
  mac?: string | null;
  owner?: string | null;
  switch_port?: string | null;
  note?: string | null;
  customer_id?: string | null;
  device_id?: string | null;
  /** 主控台的連線出口（issue #24）：空＝沿用所屬子網路 */
  jump_host_id?: string | null;
  console_agent_id?: string | null;
}

export async function createAddress(payload: IPAddressCreate): Promise<IPAddress> {
  const { data } = await apiClient.post<IPAddress>("/api/v1/addresses", payload);
  return data;
}

export interface BulkDeleteResult {
  deleted: number;
  failed: number;
  errors: { id: string; error: string }[];
}

export async function bulkDeleteAddresses(ids: string[]): Promise<BulkDeleteResult> {
  const { data } = await apiClient.post<BulkDeleteResult>("/api/v1/addresses/bulk-delete", { ids });
  return data;
}

export interface BulkStateResult { updated: number; failed: number; errors: { id: string; error: string }[]; }
export async function bulkSetAddressState(ids: string[], state: string): Promise<BulkStateResult> {
  const { data } = await apiClient.post<BulkStateResult>("/api/v1/addresses/bulk-state", { ids, state });
  return data;
}

export interface BulkCreateDevicesResult {
  created: number;
  linked_existing: number;
  skipped: number;
  failed: number;
  errors: { id: string; error: string }[];
  devices: { id: string; name: string; how: "created" | "existing"; ips: string[] }[];
}
/** 勾選的 IP 中還沒歸屬裝置的：比對得到既有裝置就連過去，否則以主機名稱建一台新裝置（僅管理員）。 */
export async function bulkCreateDevices(ids: string[]): Promise<BulkCreateDevicesResult> {
  const { data } = await apiClient.post<BulkCreateDevicesResult>("/api/v1/addresses/bulk-create-devices", { ids });
  return data;
}

export interface NotifyStaleResult { notified_admins: number; ip_count: number; }
export async function notifyStaleAddresses(subnetId: string, ids: string[], days: number): Promise<NotifyStaleResult> {
  const { data } = await apiClient.post<NotifyStaleResult>("/api/v1/addresses/notify-stale",
    { subnet_id: subnetId, ids, days });
  return data;
}

export interface SiblingIP {
  id: string;
  ip: string;
  mac: string | null;
  mac_vendor: string | null;
  /** MAC 與本 IP 相同 —— 幾乎可以確定是同一張網卡 */
  same_mac: boolean;
}

export interface DeviceSuggestion {
  suggested_name: string | null;
  existing_device_id: string | null;
  existing_device_name: string | null;
  match_reason: string | null;
  /** 同主機名稱、尚未關聯的其他 IP。**候選，不是結論** —— 見元件裡的說明 */
  siblings: SiblingIP[];
  can_create: boolean;
}

/** 這個 IP 看起來屬於哪一台裝置 —— 或該建立哪一台。**只查詢，不會動到任何資料。** */
/** 同一個 MAC 的其他位址（看得到的才列）：「上線狀態跟著設備」的提示用 */
export type SameMacAddress = IPAddress & {
  /** 這個 MAC 最早什麼時候出現在那個位址（ARP 記錄）—— 分辨「搬走了」與「同一台的另一個介面」 */
  mac_since?: string | null;
};
export async function getSameMacAddresses(id: string): Promise<SameMacAddress[]> {
  const { data } = await apiClient.get(`/api/v1/addresses/${id}/same-mac`);
  return data;
}

export async function getDeviceSuggestion(id: string): Promise<DeviceSuggestion> {
  const { data } = await apiClient.get(`/api/v1/addresses/${id}/device-suggestion`);
  return data;
}

/** 套用建議：關聯到既有裝置，或建立一台再關聯（可一併接上同名的其他 IP）。 */
export async function applyDeviceSuggestion(
  id: string,
  body: { device_id?: string; create_name?: string; link_ip_ids?: string[] },
): Promise<IPAddress> {
  const { data } = await apiClient.post(
    `/api/v1/addresses/${id}/device-suggestion/apply`, body);
  return data;
}


/** 沒有 IP 記錄、但最近看得到在用的位址（掃描代理目擊＋LibreNMS ARP）：指示計的「未納管」格子 */
export interface UnmanagedAddress {
  ip: string;
  last_seen_at: string | null;
  /** scanner／arp:librenms … */
  sources: string[];
  mac: string | null;
  vendor: string | null;
  hostname: string | null;
}

export async function listUnmanaged(subnetId: string): Promise<UnmanagedAddress[]> {
  const { data } = await apiClient.get<UnmanagedAddress[]>("/api/v1/addresses/unmanaged",
                                                           { params: { subnet_id: subnetId } });
  return data;
}
