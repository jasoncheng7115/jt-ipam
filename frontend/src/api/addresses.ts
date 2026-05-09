import { apiClient } from "@/api/client";
import type { IPAddress, Paginated } from "@/types";

export async function listAddresses(
  params: { subnetId?: string; page?: number; pageSize?: number } = {},
): Promise<Paginated<IPAddress>> {
  const { data } = await apiClient.get<Paginated<IPAddress>>("/api/v1/addresses", {
    params: {
      subnet_id: params.subnetId,
      page: params.page ?? 1,
      page_size: params.pageSize ?? 100,
    },
  });
  return data;
}
