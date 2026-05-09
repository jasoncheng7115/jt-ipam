import { apiClient } from "@/api/client";

export interface RackDeviceSlot {
  device_id: string;
  name: string;
  type: string;
  vendor: string | null;
  model: string | null;
  u_position: number;
  u_size: number;
  primary_ip: string | null;
}

export interface RackDiagram {
  rack_id: string;
  name: string;
  u_height: number;
  location_id: string | null;
  devices: RackDeviceSlot[];
  conflicts: Record<string, unknown>[];
}

export async function getRackDiagram(id: string): Promise<RackDiagram> {
  const { data } = await apiClient.get<RackDiagram>(`/api/v1/racks/${id}/diagram`);
  return data;
}
