export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface UserMe {
  id: string;
  username: string;
  email: string;
  display_name: string | null;
  auth_provider: string;
  is_active: boolean;
  is_admin: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface TokenResponse {
  access_token: string | null;
  refresh_token: string | null;
  token_type: string;
  expires_in: number | null;
  mfa_required: boolean;
  mfa_token: string | null;
}

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

export interface Subnet {
  id: string;
  section_id: string;
  master_subnet_id: string | null;
  cidr: string;
  description: string | null;
  vlan_id: string | null;
  vrf_id: string | null;
  is_pool: boolean;
  is_full: boolean;
  scan_enabled: boolean;
  scan_method: string[];
  threshold_pct: number | null;
  auto_dns: boolean;
  custom_fields: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface SubnetUsage {
  subnet_id: string;
  cidr: string;
  total: number;
  used: number;
  free: number;
  used_pct: number;
}

export interface IPAddress {
  id: string;
  subnet_id: string;
  ip: string;
  hostname: string | null;
  description: string | null;
  state: string;
  mac: string | null;
  owner: string | null;
  device_id: string | null;
  switch_port: string | null;
  exclude_from_ping: boolean;
  ptr_ignore: boolean;
  note: string | null;
  custom_fields: Record<string, unknown> | null;
  discovery_source: string;
  last_seen_scanner: string | null;
  last_seen_librenms: string | null;
  last_seen_dns: string | null;
  effective_status: string | null;
  created_at: string;
  updated_at: string;
}
