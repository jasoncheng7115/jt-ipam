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
  hostname?: string | null;
  description?: string | null;
  state?: string | null;
  mac?: string | null;
  owner?: string | null;
  device_id?: string | null;
  switch_port?: string | null;
  exclude_from_ping?: boolean | null;
  excluded_probes?: string[] | null;
  ptr_ignore?: boolean | null;
  note?: string | null;
  customer_id?: string | null;
  hostname_source_pin?: string | null;
  ssh_enabled?: boolean | null;
  rdp_enabled?: boolean | null;
  vnc_enabled?: boolean | null;
  novnc_enabled?: boolean | null;
  bmc_enabled?: boolean | null;
  is_dhcp_server?: boolean | null;
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

export interface NotifyStaleResult { notified_admins: number; ip_count: number; }
export async function notifyStaleAddresses(subnetId: string, ids: string[], days: number): Promise<NotifyStaleResult> {
  const { data } = await apiClient.post<NotifyStaleResult>("/api/v1/addresses/notify-stale",
    { subnet_id: subnetId, ids, days });
  return data;
}
