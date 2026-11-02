import { apiClient } from "@/api/client";

export interface RelationNode {
  type: "section" | "subnet" | "ip" | "vm" | "vmnode" | "device" | "rack" | "location";
  id: string;
  label: string;
  sub?: string | null;
  /** proxmox / vmware —— 決定節點標籤與要連到哪一個虛擬化頁面 */
  platform?: string | null;
  /** 這個裝置節點其實是一台虛擬機（由後端推導） */
  is_vm?: boolean;
  /** IP 節點：這台裝置另外還有幾個 IP */
  more?: number;
  /** IP 節點（裝置頁）：這台裝置所有看得到的 IP，第一個是卡片上那個；可能跨好幾個網段（作業系統＋iDRAC 管理網段） */
  ips?: { id: string; ip: string; hostname?: string | null; subnet?: string | null; subnet_id?: string | null }[];
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
