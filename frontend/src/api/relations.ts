import { apiClient } from "@/api/client";

export interface RelationNode {
  type: "section" | "subnet" | "ip" | "vm" | "vmnode" | "device" | "rack" | "location";
  id: string;
  label: string;
  sub?: string | null;
}

export async function getAddressRelations(id: string): Promise<RelationNode[]> {
  const { data } = await apiClient.get<{ chain: RelationNode[] }>(
    `/api/v1/addresses/${id}/relations`,
  );
  return data.chain;
}

export async function getDeviceRelations(id: string): Promise<RelationNode[]> {
  const { data } = await apiClient.get<{ chain: RelationNode[] }>(
    `/api/v1/devices/${id}/relations`,
  );
  return data.chain;
}
