import { apiClient } from "@/api/client";
import type { Paginated, Subnet, SubnetUsage } from "@/types";

export async function listSubnets(
  params: { sectionId?: string; page?: number; pageSize?: number } = {},
): Promise<Paginated<Subnet>> {
  const { data } = await apiClient.get<Paginated<Subnet>>("/api/v1/subnets", {
    params: {
      section_id: params.sectionId,
      page: params.page ?? 1,
      page_size: params.pageSize ?? 50,
    },
  });
  return data;
}

export async function getSubnetUsage(id: string): Promise<SubnetUsage> {
  const { data } = await apiClient.get<SubnetUsage>(`/api/v1/subnets/${id}/usage`);
  return data;
}

export async function getFirstFreeAddress(
  id: string,
): Promise<{ subnet_id: string; cidr: string; ip: string | null }> {
  const { data } = await apiClient.get(`/api/v1/subnets/${id}/first_free_address`);
  return data;
}
