// 裝置群組（堆疊／HA／叢集，客戶 2026-11-05）。讀：登入即可（只列看得到的成員）；寫：管理員。
import { apiClient } from "@/api/client";

export const GROUP_KINDS = ["stack", "ha_active_passive", "ha_active_active", "cluster"] as const;
export type GroupKind = (typeof GROUP_KINDS)[number];
/** 各類型可選的成員角色，第一個是新加入成員的預設 */
export const ROLES_BY_KIND: Record<GroupKind, string[]> = {
  stack: ["member", "master"],
  ha_active_passive: ["standby", "primary"],
  ha_active_active: ["active"],
  cluster: ["member"],
};

export interface DeviceGroupMember {
  device_id: string; name: string; type: string;
  role: string | null; order: number | null;
  rack_id: string | null; rack_name: string | null; u_position: number | null;
}
export interface DeviceGroup {
  id: string; name: string; kind: GroupKind; description: string | null;
  representative_id: string | null;
  members: DeviceGroupMember[];
  shared_ips: { id: string; ip: string; subnet_id: string }[];
  created_at: string; updated_at: string;
}
export interface DeviceGroupWrite {
  name?: string; kind?: GroupKind; description?: string | null;
  members?: { device_id: string; role: string; order: number | null }[];
  representative_id?: string | null;
  shared_ip_ids?: string[];
}

export async function listDeviceGroups(): Promise<DeviceGroup[]> {
  const { data } = await apiClient.get<{ items: DeviceGroup[] }>("/api/v1/device-groups");
  return data.items;
}
export async function getDeviceGroup(id: string): Promise<DeviceGroup> {
  const { data } = await apiClient.get<DeviceGroup>(`/api/v1/device-groups/${id}`);
  return data;
}
export async function createDeviceGroup(body: DeviceGroupWrite): Promise<DeviceGroup> {
  const { data } = await apiClient.post<DeviceGroup>("/api/v1/device-groups", body);
  return data;
}
export async function updateDeviceGroup(id: string, body: DeviceGroupWrite): Promise<DeviceGroup> {
  const { data } = await apiClient.patch<DeviceGroup>(`/api/v1/device-groups/${id}`, body);
  return data;
}
export async function deleteDeviceGroup(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/device-groups/${id}`);
}
