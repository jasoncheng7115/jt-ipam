import { apiClient } from "@/api/client";

export interface IPChangeLog {
  id: string;
  ip_id: string | null;
  subnet_id: string | null;
  ip_text: string;
  event_type: string;
  field: string | null;
  old_value: string | null;
  new_value: string | null;
  source: string;
  actor_user_id: string | null;
  note: string | null;
  created_at: string;
  actor_username: string | null;
}

export interface IPChangePage {
  items: IPChangeLog[];
  total: number;
  page: number;
  page_size: number;
}

export interface IPChangeFilter {
  q?: string;
  ip_id?: string;
  subnet_id?: string;
  event_type?: string;
  source?: string;
  since?: string;
  until?: string;
  page?: number;
  page_size?: number;
}

// 單一 IP 的異動記錄 (詳情頁展開用)；offset 分頁（前端「載入更多」）
export async function getAddressHistory(
  addressId: string,
  limit = 100,
  offset = 0,
): Promise<IPChangeLog[]> {
  const { data } = await apiClient.get<IPChangeLog[]>(
    `/api/v1/addresses/${addressId}/history`,
    { params: { limit, offset } },
  );
  return data;
}

// 全域異動記錄 (搜尋 / 篩選 / 分頁)
export async function listIpChanges(
  filter: IPChangeFilter = {},
): Promise<IPChangePage> {
  const { data } = await apiClient.get<IPChangePage>("/api/v1/ip-changes", {
    params: filter,
  });
  return data;
}

// FDB 推得的 switch port(feature E)
export interface SwitchPortLocation {
  switch: string | null;
  switch_ip: string | null;
  port: string | null;
  vlan: number | null;
  macs_on_port: number;
  last_seen_at: string | null;
}
export interface SwitchPortInfo {
  ip: string;
  mac: string | null;
  locations: SwitchPortLocation[];
  likely_access_port?: SwitchPortLocation | null;
}
export async function getAddressSwitchPort(addressId: string): Promise<SwitchPortInfo> {
  const { data } = await apiClient.get<SwitchPortInfo>(`/api/v1/addresses/${addressId}/switch-port`);
  return data;
}

// 事件類型 / 來源 (與後端 EVENT_TYPES / CHANGE_SOURCES 對齊)
export const IP_CHANGE_EVENT_TYPES = [
  "created", "deleted", "hostname_changed", "mac_changed",
  "state_changed", "online", "offline", "arp_changed", "edited",
] as const;

export const IP_CHANGE_SOURCES = [
  "manual", "scanner", "librenms", "dns", "proxmox", "opnsense", "system",
] as const;
