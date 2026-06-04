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
  rack_face?: "front" | "rear" | null;
  rack_side?: "full" | "left" | "right";
}

export interface RackDiagram {
  rack_id: string;
  name: string;
  u_height: number;
  location_id: string | null;
  numbering?: "top-down" | "bottom-up";
  face?: "front" | "rear";
  devices: RackDeviceSlot[];
  conflicts: Record<string, unknown>[];
}

export async function getRackDiagram(id: string): Promise<RackDiagram> {
  const { data } = await apiClient.get<RackDiagram>(`/api/v1/racks/${id}/diagram`);
  return data;
}

// ─────────────────── 機房平面圖 ───────────────────
export interface Rack {
  id: string;
  name: string;
  u_height: number;
  width_mm: number | null;
  depth_mm: number | null;
  location_id: string | null;
  location_name?: string | null;
  description: string | null;
  seq?: number | null;
  device_count?: number;
  numbering?: "top-down" | "bottom-up";
  face?: "front" | "rear";
  pos_x: number | null;
  pos_y: number | null;
  pos_rot: number;
  pos_w: number | null;
  pos_h: number | null;
}

export async function listRacksByLocation(locationId: string): Promise<Rack[]> {
  const { data } = await apiClient.get<{ items: Rack[] }>("/api/v1/racks", {
    params: { location_id: locationId, page: 1, page_size: 500 },
  });
  return data.items;
}

/** 取機房平面圖底圖（需授權）→ 回 object URL，呼叫端用完要 revokeObjectURL。 */
export async function getFloorplanObjectURL(locationId: string): Promise<string> {
  const { data } = await apiClient.get<Blob>(
    `/api/v1/locations/${locationId}/floorplan`, { responseType: "blob" },
  );
  return URL.createObjectURL(data);
}

export async function uploadFloorplan(locationId: string, file: File): Promise<void> {
  const form = new FormData();
  form.append("file", file);
  await apiClient.post(`/api/v1/locations/${locationId}/floorplan`, form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
}

export async function deleteFloorplan(locationId: string): Promise<void> {
  await apiClient.delete(`/api/v1/locations/${locationId}/floorplan`);
}

export async function setRackPositions(
  locationId: string,
  positions: { id: string; pos_x: number; pos_y: number; pos_rot: number; pos_w?: number | null; pos_h?: number | null }[],
): Promise<{ updated: number }> {
  const { data } = await apiClient.put<{ updated: number }>(
    `/api/v1/locations/${locationId}/rack-positions`, { positions },
  );
  return data;
}
