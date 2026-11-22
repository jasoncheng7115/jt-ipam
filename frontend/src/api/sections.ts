import { apiClient } from "@/api/client";
import type { Paginated, Section } from "@/types";

export async function listSections(page = 1, pageSize = 50): Promise<Paginated<Section>> {
  const { data } = await apiClient.get<Paginated<Section>>("/api/v1/sections", {
    params: { page, page_size: pageSize },
  });
  return data;
}

/** 全部區段（後端每頁上限 500，逐頁讀到 total；上限 5000 跟裝置頁同一個數字）。
 *  列表頁的前端分頁、篩選與詳細頁的上層選單都要資料到齊（只讀第一頁會「看不到也搜不到」）。 */
const MAX_SECTIONS = 5000;
export async function fetchAllSections(): Promise<Section[]> {
  const all: Section[] = [];
  for (let p = 1; ; p += 1) {
    const res = await listSections(p, 500);
    all.push(...res.items);
    if (res.items.length === 0 || all.length >= res.total || all.length >= MAX_SECTIONS) break;
  }
  return all;
}

export async function getSection(id: string): Promise<Section> {
  const { data } = await apiClient.get<Section>(`/api/v1/sections/${id}`);
  return data;
}

export interface BulkDeleteResult {
  deleted: number;
  failed: number;
  errors: { id: string; error: string }[];
}
export async function bulkDeleteSections(ids: string[]): Promise<BulkDeleteResult> {
  const { data } = await apiClient.post<BulkDeleteResult>("/api/v1/sections/bulk-delete", { ids });
  return data;
}

export interface SectionCreate {
  name: string;
  description?: string | null;
  parent_id?: string | null;
  strict_mode?: boolean;
  display_order?: number;
  customer_id?: string | null;
}
export interface SectionUpdate {
  name?: string | null;
  description?: string | null;
  parent_id?: string | null;
  strict_mode?: boolean | null;
  display_order?: number | null;
  customer_id?: string | null;
}

export async function createSection(payload: SectionCreate): Promise<Section> {
  const { data } = await apiClient.post<Section>("/api/v1/sections", payload);
  return data;
}
export async function updateSection(id: string, payload: SectionUpdate): Promise<Section> {
  const { data } = await apiClient.patch<Section>(`/api/v1/sections/${id}`, payload);
  return data;
}
export async function deleteSection(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/sections/${id}`);
}
