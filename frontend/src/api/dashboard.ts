import { apiClient } from "@/api/client";

export interface TopSubnet {
  subnet_id: string;
  cidr: string;
  description: string | null;
  section_id: string;
  customer_id: string | null;
  customer_label: string | null;
  used: number;
  total: number;
  used_pct: number;
}

export interface DashboardOverview {
  sections: number;
  subnets: number;
  addresses: number;
  total_capacity: number;
  used: number;
  used_pct: number;
  status: { online: number; offline: number; unknown: number };
  status_sources: string[];
  top_full_subnets: TopSubnet[];
  pinned_subnets: TopSubnet[];
  section_heat: {
    section_id: string;
    name: string;
    subnet_count: number;
    total_hosts: number;
    used: number;
    used_pct: number;
    avg_subnet_pct: number;
    bands: { full: number; high: number; mid: number; low: number };
  }[];
  audit_24h: number;
  devices: number;
  racks: number;
  locations: number;
  vms: number;
  device_types: { type: string; count: number }[];
  customer_resources: { customer_id: string | null; label: string; subnets: number; ips: number; devices: number }[];
  activity_trend: { day: string; audit: number; ip_changes: number }[];
  rack_usage: { rack_id: string; name: string; used_u: number; total_u: number; pct: number }[];
}

export async function getOverview(): Promise<DashboardOverview> {
  const { data } = await apiClient.get<DashboardOverview>("/api/v1/dashboard/overview");
  return data;
}
