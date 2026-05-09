import { apiClient } from "@/api/client";
import type { Paginated, Section } from "@/types";

export async function listSections(page = 1, pageSize = 50): Promise<Paginated<Section>> {
  const { data } = await apiClient.get<Paginated<Section>>("/api/v1/sections", {
    params: { page, page_size: pageSize },
  });
  return data;
}

export async function getSection(id: string): Promise<Section> {
  const { data } = await apiClient.get<Section>(`/api/v1/sections/${id}`);
  return data;
}
