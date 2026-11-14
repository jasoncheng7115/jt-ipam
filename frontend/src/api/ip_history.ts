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
  section_id?: string;
  customer_id?: string;
  event_type?: string;
  source?: string;
  since?: string;
  until?: string;
  page?: number;
  page_size?: number;
  sort?: string;                 // created_at（預設）/ ip / event_type / field / source / actor
  order?: "asc" | "desc";
}

export interface HistoryFacet { value: string; count: number }
export interface AddressHistoryPage {
  items: IPChangeLog[];
  total: number;              // 目前篩選條件下的總數（非本頁筆數）
  returned: number;
  event_types: HistoryFacet[];
  sources: HistoryFacet[];
}

// 單一 IP 的異動記錄（詳細資料頁展開用）。回傳帶總數與篩選選項 ——
// 實機單一 IP 可達 1,800+ 筆，只回一頁陣列會讓人以為那就是全部。
export async function getAddressHistory(
  addressId: string,
  opts: { limit?: number; offset?: number; event_type?: string; source?: string } = {},
): Promise<AddressHistoryPage> {
  const { data } = await apiClient.get<AddressHistoryPage>(
    `/api/v1/addresses/${addressId}/history`,
    { params: { limit: opts.limit ?? 100, offset: opts.offset ?? 0,
                event_type: opts.event_type || undefined,
                source: opts.source || undefined } },
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
  "state_changed", "online", "offline", "arp_changed", "os_changed", "kind_changed", "edited",
] as const;

// 篩選選項：各整合寫入時用的來源名稱（以前只列 7 個，mikrotik、fortigate 等選不到）。
// 「上線／失聯」翻轉的來源是讓它上線的那個整合；失聯是 system（系統判定證據過期，#49）
export const IP_CHANGE_SOURCES = [
  "manual", "user", "system", "scanner", "librenms", "dns",
  "proxmox", "esxi", "hyperv", "openstack", "opnsense", "pfsense", "fortigate", "paloalto", "checkpoint", "mikrotik",
  "wazuh", "zabbix", "checkmk", "ocs", "adguard", "kea_dhcp", "isc_dhcp", "isoinsight", "technitium", "windows_dhcp", "rustdesk",
  // SNMP 收集（0217）：ARP 補 MAC 時寫的異動記錄
  "snmp",
] as const;
