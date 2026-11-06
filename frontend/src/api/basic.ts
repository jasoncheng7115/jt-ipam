/**
 * 基本 IPAM 資源 API：VLAN / VRF / Device / Location。
 */
import { apiClient } from "@/api/client";
import type { Paginated } from "@/api/admin";

// VLAN
export interface VLANDomain {
  id: string; name: string; description: string | null;
  created_at: string; updated_at: string;
}
export interface VLAN {
  id: string; domain_id: string; number: number; name: string;
  description: string | null; created_at: string; updated_at: string;
  device_count?: number; port_count?: number; ip_count?: number;
  customer_id: string | null; section_id: string | null;
}
export interface VLANMembers {
  vlan: { id: string; number: number; name: string };
  ports: { device: string; port: string; mac: string | null }[];
  subnets: { id: string; cidr: string; ip_count: number }[];
  devices: { id: string; name: string }[];
}
export async function vlanMembers(id: string): Promise<VLANMembers> {
  const { data } = await apiClient.get<VLANMembers>(`/api/v1/vlans/${id}/members`);
  return data;
}
// feature C：掛在 VLAN 上的 LibreNMS 裝置
export interface VLANDevice {
  librenms_device_id: string;
  hostname: string | null;
  primary_ip: string | null;
  source: string;
}
export async function getVlanDevices(vlanId: string): Promise<VLANDevice[]> {
  const { data } = await apiClient.get<VLANDevice[]>(`/api/v1/vlans/${vlanId}/devices`);
  return data;
}
export async function listVLANDomains(): Promise<Paginated<VLANDomain>> {
  const { data } = await apiClient.get<Paginated<VLANDomain>>(
    "/api/v1/vlan-domains", { params: { page: 1, page_size: 200 } });
  return data;
}
export async function listVLANs(
  domain_id?: string,
  filter?: { customer_id?: string; section_id?: string },
): Promise<Paginated<VLAN>> {
  const { data } = await apiClient.get<Paginated<VLAN>>("/api/v1/vlans", {
    params: {
      ...(domain_id ? { domain_id } : {}),
      ...(filter?.customer_id ? { customer_id: filter.customer_id } : {}),
      ...(filter?.section_id ? { section_id: filter.section_id } : {}),
      page: 1, page_size: 500,
    },
  });
  return data;
}
export async function createVLANDomain(name: string, description?: string): Promise<VLANDomain> {
  const { data } = await apiClient.post<VLANDomain>("/api/v1/vlan-domains", { name, description });
  return data;
}
export async function createVLAN(payload: {
  domain_id: string; number: number; name: string; description?: string;
  customer_id?: string | null; section_id?: string | null;
}): Promise<VLAN> {
  const { data } = await apiClient.post<VLAN>("/api/v1/vlans", payload);
  return data;
}
export async function updateVLAN(
  id: string,
  payload: { name?: string; description?: string; customer_id?: string | null; section_id?: string | null },
): Promise<VLAN> {
  const { data } = await apiClient.patch<VLAN>(`/api/v1/vlans/${id}`, payload);
  return data;
}
export async function deleteVLAN(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/vlans/${id}`);
}
export async function updateVLANDomain(
  id: string, payload: { name?: string; description?: string },
): Promise<VLANDomain> {
  const { data } = await apiClient.patch<VLANDomain>(`/api/v1/vlan-domains/${id}`, payload);
  return data;
}
export async function deleteVLANDomain(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/vlan-domains/${id}`);
}

// VRF
export interface VRF {
  id: string; name: string; rd: string | null; description: string | null;
  allow_overlap: boolean; created_at: string; updated_at: string;
}
export async function listVRFs(): Promise<Paginated<VRF>> {
  const { data } = await apiClient.get<Paginated<VRF>>("/api/v1/vrfs", {
    params: { page: 1, page_size: 200 },
  });
  return data;
}
export async function createVRF(payload: {
  name: string; rd?: string; description?: string; allow_overlap?: boolean;
}): Promise<VRF> {
  const { data } = await apiClient.post<VRF>("/api/v1/vrfs", payload);
  return data;
}
export async function updateVRF(
  id: string, payload: { name?: string; rd?: string; description?: string; allow_overlap?: boolean },
): Promise<VRF> {
  const { data } = await apiClient.patch<VRF>(`/api/v1/vrfs/${id}`, payload);
  return data;
}
export async function deleteVRF(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/vrfs/${id}`);
}

// Device
export interface Device {
  /** 虛擬機還是實體機（後端依虛擬機清單推導，不是可編輯欄位） */
  is_virtual?: boolean;
  id: string; name: string; type: string;
  fqdn: string | null;
  ip?: string | null;     // 由清單 endpoint 解析 primary_ip 填入
  ip_address_id?: string | null;   // 有對應的 IPAddress → IP 欄可點進該位址
  ip_match_id?: string | null;   // 有相符但未連結的 IPAddress → 可一鍵關聯
  vendor: string | null; model: string | null; serial: string | null;
  location_id: string | null; rack_id: string | null;
  u_position: number | null; u_size: number | null;
  rack_face?: "front" | "rear" | null;
  rack_side?: "full" | "left" | "right" | null;
  description: string | null;
  customer_id: string | null;
  /** 裝置群組（堆疊／HA／叢集）摘要；不在群組裡是 null */
  group?: DeviceGroupSummary | null;
  created_at: string; updated_at: string;
}
export interface DeviceGroupSummary {
  id: string; name: string; kind: string;
  role: string | null; order: number | null;
  member_count: number;
  /** 這台是不是群組的代表裝置（共用 IP 掛在它上面） */
  representative: boolean;
}
export async function listDevices(
  params?: { page?: number; pageSize?: number; q?: string; subnetId?: string | null },
): Promise<Paginated<Device>> {
  const { data } = await apiClient.get<Paginated<Device>>("/api/v1/devices", {
    params: {
      page: params?.page ?? 1,
      page_size: params?.pageSize ?? 200,
      // 伺服器端搜尋：畫面只載得下一頁，只在已載入的資料上過濾的話，
      // 超過一頁的站台會「列表看不到、用名字也搜不到」（客戶回報過）
      q: params?.q || undefined,
      subnet_id: params?.subnetId || undefined,
    },
  });
  return data;
}

// feature C：裝置的 VLAN 清單 (主要來自 LibreNMS)
export interface DeviceVLAN {
  vlan_id: string;
  number: number;
  name: string;
  source: string;
  last_seen_at: string;
}
/** 依 id 取單一裝置。
 *
 *  名稱顯示**不可以**只靠 `listDevices()` 的那份清單：它預設只拿一頁（200 筆），
 *  剛建立或排序落在後面的裝置就會查不到，畫面只好退回顯示一段 UUID。
 *  實機遇過：依建議建立裝置之後，IP 詳細資料的「裝置」欄顯示 `a392af8f…`。
 */
export async function getDevice(id: string): Promise<Device> {
  const { data } = await apiClient.get<Device>(`/api/v1/devices/${id}`);
  return data;
}

export async function getDeviceVlans(deviceId: string): Promise<DeviceVLAN[]> {
  const { data } = await apiClient.get<DeviceVLAN[]>(`/api/v1/devices/${deviceId}/vlans`);
  return data;
}
// 連結到此裝置的 LibreNMS 資料
export interface DeviceLibreNMS {
  hostname: string | null; sysname: string | null; primary_ip: string | null;
  hardware: string | null; os: string | null; version: string | null;
  serial: string | null; uptime: number | null; status: string | null;
  last_seen_at: string | null; url: string | null;
}
export async function getDeviceLibrenms(deviceId: string): Promise<DeviceLibreNMS | null> {
  const { data } = await apiClient.get<DeviceLibreNMS | null>(`/api/v1/devices/${deviceId}/librenms`);
  return data;
}
export async function createDevice(payload: {
  name: string; type?: string; vendor?: string; model?: string;
  serial?: string; description?: string; fqdn?: string | null;
  location_id?: string | null; rack_id?: string | null;
  u_position?: number | null; u_size?: number | null;
  customer_id?: string | null;
}): Promise<Device> {
  const { data } = await apiClient.post<Device>("/api/v1/devices", payload);
  return data;
}
export async function updateDevice(
  id: string,
  payload: Partial<{ name: string; type: string; vendor: string; model: string;
                     serial: string; description: string; fqdn: string | null;
                     location_id: string | null; rack_id: string | null;
                     u_position: number | null; u_size: number | null;
                     customer_id: string | null }>,
): Promise<Device> {
  const { data } = await apiClient.patch<Device>(`/api/v1/devices/${id}`, payload);
  return data;
}
export async function deleteDevice(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/devices/${id}`);
}
export async function bulkDeleteDevices(ids: string[]): Promise<{ deleted: number; failed: number; errors: any[] }> {
  const { data } = await apiClient.post("/api/v1/devices/bulk-delete", { ids });
  return data;
}

// Location
export interface Location {
  id: string; name: string;
  address: string | null; latitude: number | null; longitude: number | null;
  description: string | null;
  floor_plan_path: string | null;
  rack_count?: number; device_count?: number;
  created_at: string; updated_at: string;
}
export type MapProvider = "builtin" | "osm" | "google";
export async function getMapProvider(): Promise<MapProvider> {
  try {
    const { data } = await apiClient.get<{ provider: string }>("/api/v1/system/map-provider");
    return (["builtin", "osm", "google"].includes(data.provider) ? data.provider : "builtin") as MapProvider;
  } catch { return "builtin"; }
}
export async function setMapProvider(provider: MapProvider): Promise<void> {
  await apiClient.put("/api/v1/system/map-provider", { provider });
}

/** 一個候選的上線判定證據來源。 */
export interface LivenessSource {
  key: string;
  /** 證據會不會過期 —— 不會過期的勾了就等於「看過一次就永遠上線」 */
  aging: boolean;
  /** 這個站台真的有這個整合 */
  configured: boolean;
}
export interface LivenessConfig {
  minutes: number;
  sources: string[];
  /** 後端算好的候選清單（只含這個站台有的整合，加上已勾選的） */
  available: LivenessSource[];
}

export async function getOnlineGrace(): Promise<LivenessConfig> {
  try {
    const { data } = await apiClient.get<LivenessConfig>("/api/v1/system/online-grace");
    return {
      minutes: Number(data.minutes) || 30,
      sources: Array.isArray(data.sources) ? data.sources : ["scanner", "librenms"],
      available: Array.isArray(data.available) ? data.available : [],
    };
  } catch { return { minutes: 30, sources: ["scanner", "librenms"], available: [] }; }
}
export async function setOnlineGrace(minutes: number, sources: string[]): Promise<void> {
  await apiClient.put("/api/v1/system/online-grace", { minutes, sources });
}

export async function getIpCooldown(): Promise<number> {
  try {
    const { data } = await apiClient.get<{ days: number }>("/api/v1/system/ip-cooldown");
    return Number(data.days) || 0;
  } catch { return 30; }
}
export async function setIpCooldown(days: number): Promise<void> {
  await apiClient.put("/api/v1/system/ip-cooldown", { days });
}

/** 憑證到期通知的**全域預設**天數（每張憑證仍可各自覆寫）。 */
export async function getCertExpiryDays(): Promise<number> {
  try {
    const { data } = await apiClient.get<{ days: number }>("/api/v1/system/cert-expiry-alert");
    return Number(data.days) || 21;
  } catch { return 21; }
}
export async function setCertExpiryDays(days: number): Promise<void> {
  await apiClient.put("/api/v1/system/cert-expiry-alert", { days });
}

export type RackNameAlign = "left" | "center" | "right";
export async function getRackNameAlign(): Promise<RackNameAlign> {
  try {
    const { data } = await apiClient.get<{ align: string }>("/api/v1/system/rack-name-align");
    return (["left", "center", "right"].includes(data.align) ? data.align : "left") as RackNameAlign;
  } catch { return "left"; }
}
export async function setRackNameAlign(align: RackNameAlign): Promise<void> {
  await apiClient.put("/api/v1/system/rack-name-align", { align });
}

export interface GeoIPDb { edition: string; present: boolean; size: number | null; built_at: string | null; }
export interface GeoIPConfig {
  account_id: string | null; has_key: boolean;
  editions: string[]; auto_update: boolean; frequency: string;
  last_update_at: string | null; last_error: string | null;
  dbs: GeoIPDb[]; all_editions: string[]; frequencies: string[];
}
export interface GeoIPConfigPatch {
  account_id?: string | null; license_key?: string | null;
  editions?: string[]; auto_update?: boolean; frequency?: string;
}
export async function getGeoipConfig(): Promise<GeoIPConfig> {
  const { data } = await apiClient.get<GeoIPConfig>("/api/v1/system/geoip");
  return data;
}
export async function setGeoipConfig(patch: GeoIPConfigPatch): Promise<GeoIPConfig> {
  const { data } = await apiClient.put<GeoIPConfig>("/api/v1/system/geoip", patch);
  return data;
}
export async function updateGeoipDbNow(): Promise<{ result: any; config: GeoIPConfig }> {
  const { data } = await apiClient.post("/api/v1/system/geoip/update");
  return data;
}

export async function listLocations(): Promise<Paginated<Location>> {
  const { data } = await apiClient.get<Paginated<Location>>("/api/v1/locations", {
    params: { page: 1, page_size: 200 },
  });
  return data;
}
export async function createLocation(payload: {
  name: string; address?: string; description?: string;
  latitude?: number | null; longitude?: number | null;
}): Promise<Location> {
  const { data } = await apiClient.post<Location>("/api/v1/locations", payload);
  return data;
}
export async function updateLocation(
  id: string,
  payload: Partial<{ name: string; address: string; description: string;
    latitude: number | null; longitude: number | null }>,
): Promise<Location> {
  const { data } = await apiClient.patch<Location>(`/api/v1/locations/${id}`, payload);
  return data;
}
export async function deleteLocation(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/locations/${id}`);
}

// Rack
export interface Rack {
  id: string; name: string; location_id: string | null;
  u_height: number; description: string | null;
  created_at: string; updated_at: string;
}
export async function listRacks(): Promise<Paginated<Rack>> {
  const { data } = await apiClient.get<Paginated<Rack>>("/api/v1/racks", {
    params: { page: 1, page_size: 500 },
  });
  return data;
}

// ─────────────────── bulk-delete helpers ───────────────────
export interface BulkDeleteResult {
  deleted: number;
  failed: number;
  errors: { id: string; error: string }[];
}
async function _bulk(url: string, ids: string[]): Promise<BulkDeleteResult> {
  const { data } = await apiClient.post<BulkDeleteResult>(url, { ids });
  return data;
}
export const bulkDeleteVLANs = (ids: string[]) => _bulk("/api/v1/vlans/bulk-delete", ids);
export const bulkDeleteVLANDomains = (ids: string[]) => _bulk("/api/v1/vlan-domains/bulk-delete", ids);
export const bulkDeleteVRFs = (ids: string[]) => _bulk("/api/v1/vrfs/bulk-delete", ids);
export const bulkDeleteLocations = (ids: string[]) => _bulk("/api/v1/locations/bulk-delete", ids);
export const bulkDeleteRacks = (ids: string[]) => _bulk("/api/v1/racks/bulk-delete", ids);
