import { apiClient } from "@/api/client";

export interface Section {
  id: string;
  name: string;
  description: string | null;
  parent_id: string | null;
  strict_mode: boolean;
  display_order: number;
  created_at: string;
  updated_at: string;
}

export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export async function listSections(page = 1, pageSize = 50): Promise<Paginated<Section>> {
  const { data } = await apiClient.get<Paginated<Section>>("/api/v1/sections", {
    params: { page, page_size: pageSize },
  });
  return data;
}
