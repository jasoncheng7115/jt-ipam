import { apiClient } from "@/api/client";

export interface DashboardOverview {
  sections: number;
  subnets: number;
  addresses: number;
  total_capacity: number;
  used: number;
  used_pct: number;
  status: { online: number; offline: number; unknown: number };
  top_full_subnets: {
    subnet_id: string;
    cidr: string;
    description: string | null;
    section_id: string;
    used: number;
    total: number;
    used_pct: number;
  }[];
  section_heat: {
    section_id: string;
    name: string;
    subnet_count: number;
    total_hosts: number;
    used: number;
    used_pct: number;
  }[];
  audit_24h: number;
}

export async function getOverview(): Promise<DashboardOverview> {
  const { data } = await apiClient.get<DashboardOverview>("/api/v1/dashboard/overview");
  return data;
}
