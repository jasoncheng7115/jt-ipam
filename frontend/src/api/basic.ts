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
}
export async function listVLANDomains(): Promise<Paginated<VLANDomain>> {
  const { data } = await apiClient.get<Paginated<VLANDomain>>(
    "/api/v1/vlan-domains", { params: { page: 1, page_size: 200 } });
  return data;
}
export async function listVLANs(domain_id?: string): Promise<Paginated<VLAN>> {
  const { data } = await apiClient.get<Paginated<VLAN>>("/api/v1/vlans", {
    params: { ...(domain_id ? { domain_id } : {}), page: 1, page_size: 500 },
  });
  return data;
}
export async function createVLANDomain(name: string, description?: string): Promise<VLANDomain> {
  const { data } = await apiClient.post<VLANDomain>("/api/v1/vlan-domains", { name, description });
  return data;
}
export async function createVLAN(payload: {
  domain_id: string; number: number; name: string; description?: string;
}): Promise<VLAN> {
  const { data } = await apiClient.post<VLAN>("/api/v1/vlans", payload);
  return data;
}
export async function updateVLAN(
  id: string, payload: { name?: string; description?: string },
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
  id: string; name: string; type: string;
  vendor: string | null; model: string | null; serial: string | null;
  location_id: string | null; rack_id: string | null;
  u_position: number | null; u_size: number | null;
  description: string | null;
  created_at: string; updated_at: string;
}
export async function listDevices(): Promise<Paginated<Device>> {
  const { data } = await apiClient.get<Paginated<Device>>("/api/v1/devices", {
    params: { page: 1, page_size: 200 },
  });
  return data;
}
export async function createDevice(payload: {
  name: string; type?: string; vendor?: string; model?: string;
  serial?: string; description?: string;
}): Promise<Device> {
  const { data } = await apiClient.post<Device>("/api/v1/devices", payload);
  return data;
}
export async function updateDevice(
  id: string,
  payload: Partial<{ name: string; type: string; vendor: string; model: string;
                     serial: string; description: string }>,
): Promise<Device> {
  const { data } = await apiClient.patch<Device>(`/api/v1/devices/${id}`, payload);
  return data;
}
export async function deleteDevice(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/devices/${id}`);
}

// Location
export interface Location {
  id: string; name: string; site: string | null;
  address: string | null; description: string | null;
  created_at: string; updated_at: string;
}
export async function listLocations(): Promise<Paginated<Location>> {
  const { data } = await apiClient.get<Paginated<Location>>("/api/v1/locations", {
    params: { page: 1, page_size: 200 },
  });
  return data;
}
export async function createLocation(payload: {
  name: string; site?: string; address?: string; description?: string;
}): Promise<Location> {
  const { data } = await apiClient.post<Location>("/api/v1/locations", payload);
  return data;
}
export async function updateLocation(
  id: string,
  payload: Partial<{ name: string; site: string; address: string; description: string }>,
): Promise<Location> {
  const { data } = await apiClient.patch<Location>(`/api/v1/locations/${id}`, payload);
  return data;
}
export async function deleteLocation(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/locations/${id}`);
}
