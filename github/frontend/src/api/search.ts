import { apiClient } from "@/api/client";

export interface SearchHit {
  type: "section" | "subnet" | "ip_address" | "device" | "vlan";
  id: string;
  label: string;
  sublabel: string | null;
  score: number;
}

export interface SearchResponse {
  detected: string;
  results: SearchHit[];
}

export async function search(q: string, limitPerType = 8): Promise<SearchResponse> {
  const { data } = await apiClient.get<SearchResponse>("/api/v1/search", {
    params: { q, limit_per_type: limitPerType },
  });
  return data;
}
