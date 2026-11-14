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
  totp_enabled?: boolean;
  /** 管理員的 MFA 政策要求這個帳號一定要開 */
  mfa_required_by_policy?: boolean;
  /** 還沒用過的復原碼組數 */
  recovery_codes_remaining?: number;
  has_visibility?: boolean;
  has_global_read?: boolean;
  can_edit?: boolean;
  /** 可以新增的物件類型（照角色授權，例如「系統管理員」角色＝全部、「網路操作員」＝子網路／IP／裝置） */
  create_types?: string[];
  ai_enabled?: boolean;
  /** 資料庫結構落後於程式（只給 admin）—— 落後時畫面會到處 500 */
  schema_behind?: boolean;
  can_ssh?: boolean;
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
  /** 管理員要求 MFA、這個帳號還沒設定：用 mfa_token 走設定流程 */
  mfa_setup_required?: boolean;
  /** 剛設定好 TOTP 時的 10 組復原碼（只出現這一次） */
  recovery_codes?: string[] | null;
}

export interface Section {
  id: string;
  name: string;
  description: string | null;
  parent_id: string | null;
  strict_mode: boolean;
  display_order: number;
  subnet_count: number;
  /** 數字的相容 ID：phpIPAM 相容 API、Proxmox VE SDN 的「區段」欄位填這個 */
  compat_id?: number | null;
  customer_id: string | null;
  /** 主控台的連線出口（issue #24）：空＝繼承上層或直連 */
  jump_host_id?: string | null;
  console_agent_id?: string | null;
  created_at: string;
  updated_at: string;
}

export type ScanScope = "auto" | "all" | "known" | "ranges";

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
  ai_audit_enabled: boolean;
  anomaly_enabled: boolean;
  scan_enabled: boolean;
  scan_method: string[];
  scan_agent_id: string | null;
  /** 掃描範圍（0205）：auto／all／known／ranges，規則在 backend services/scan_plan.py */
  scan_scope?: ScanScope;
  threshold_pct: number | null;
  auto_dns: boolean;
  customer_id: string | null;
  /** 主控台的連線出口（issue #24）：空＝繼承上層或直連 */
  jump_host_id?: string | null;
  console_agent_id?: string | null;
  customer_name: string | null;
  gateway: string | null;
  dns_servers: string | null;
  location_id: string | null;
  archived_at: string | null;
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
  /** 共用給裝置群組（堆疊管理 IP、HA 虛擬 IP）：device_id 跟著群組的代表裝置，只能在群組設定改 */
  device_group_id?: string | null;
  device_group_name?: string | null;
  switch_port: string | null;
  exclude_from_ping: boolean;
  /** 上線狀態跟著所屬設備（多 LAN 路由器的閘道不回 ping 但在用） */
  liveness_from_device?: boolean;
  /** 「跟著設備」這一輪的依據是同設備的哪個位址 */
  device_evidence_ip?: string | null;
  excluded_probes: string[];
  os_guess: string | null;
  /** 掃描代理定期偵測判讀出的設備類型（camera／printer…）與廠牌型號 */
  device_kind?: string | null;
  device_model?: string | null;
  device_identified_at?: string | null;
  os_family: string | null;
  os_source: string | null;
  probe_last_run: Record<string, string> | null;
  effective_probes: string[] | null;
  ptr_ignore: boolean;
  note: string | null;
  customer_id: string | null;
  /** 主控台的連線出口（issue #24）：空＝繼承上層或直連 */
  jump_host_id?: string | null;
  console_agent_id?: string | null;
  custom_fields: Record<string, unknown> | null;
  hostname_source_pin: string | null;
  switch_port_confident: boolean | null;
  discovery_source: string;
  in_dhcp_lease?: boolean;
  /** DHCP 上把這個位址綁給某張網卡（固定分配），不會被回收給別台 */
  dhcp_reserved?: boolean;
  dhcp_reservation?: {
    mac?: string | null; hostname?: string | null; description?: string | null;
    source_name?: string | null; source_type?: string | null; engine?: string | null;
  } | null;
  is_dhcp_server?: boolean;     // 手動標記為 DHCP 伺服器
  dhcp_server_auto?: boolean;   // 自動：整合中的 DHCP 來源指向的主機（後端 services/dhcp_known）
  dhcp_server_integration?: string | null;   // 哪一種整合（產品名稱）
  is_gateway?: boolean;         // 所屬子網路閘道
  in_dhcp_range?: boolean;      // 落在 DHCP pool 範圍內
  // DHCP 伺服器把這個位址排除在發放範圍外（Windows Exclusion、FortiGate exclude-range…）
  dhcp_exclusion?: {
    source_type?: string | null; source_name?: string | null; engine?: string | null;
    start: string; end: string;
  } | null;
  last_seen_scanner: string | null;
  last_seen_librenms: string | null;
  last_seen_arp: string | null;
  /** Wazuh 代理的 keep-alive（manager 端維護，會過期 → 算得上上線證據） */
  last_seen_wazuh?: string | null;
  last_seen_ocs?: string | null;
  /** RustDesk Server（開源版）：對應到這個 IP 的 ID。connect_uri 只給有遠端主控台權限的人；
   *  web_available＝可以在網頁裡直接連（相容 RustDesk 的網頁連線：伺服器開放、而且有權限） */
  rustdesk?: { id: string; online: boolean; last_online_at: string | null; server_id: string;
               server_name: string; connect_uri: string | null; web_available?: boolean;
               /** 網頁檔案傳輸可用（伺服器開了「允許網頁檔案傳輸」、而且有權限；附錄 J.6） */
               file_available?: boolean;
               hostname?: string | null; os?: string | null;
               username?: string | null; version?: string | null; evidence?: string[];
               /** Key 設錯：中繼拒絕過這台、之後沒通過（網頁連線與外網連線都會失敗） */
               key_problem?: { at: string; scope: "relay" | "hbbs" | null; count: number } | null } | null;
  /** 這個 IP 沒有 RustDesk 裝置、同一台裝置的其他 IP 有（一台電腦多張網卡時 RustDesk 只對應得到連出去的那個） */
  rustdesk_elsewhere?: { address_id: string; ip: string; rustdesk_id: string; enabled: boolean }[];
  /** Zabbix 最後一次回報這台主機可用 */
  last_seen_zabbix?: string | null;
  last_seen_checkmk?: string | null;
  /** 防火牆給的逐來源觀測時間：`{"arp:opnsense": "…", "lease:pfsense": "…"}` */
  arp_seen?: Record<string, string> | null;
  last_seen_dns: string | null;
  /** 「各來源最後出現」的整合有沒有設定（只在單筆讀取帶） */
  seen_integrations?: Record<string, boolean> | null;
  effective_status: string | null;
  subnet_scan_enabled: boolean | null;
  ssh_enabled?: boolean;
  ssh_available?: boolean;
  sftp_enabled?: boolean;
  sftp_available?: boolean;
  rdp_enabled?: boolean;
  rdp_available?: boolean;
  vnc_enabled?: boolean;
  vnc_available?: boolean;
  novnc_enabled?: boolean;
  novnc_available?: boolean;
  bmc_enabled?: boolean;
  rustdesk_enabled?: boolean;
  /** 連線管理頁：相容 RustDesk 的網頁連線可用 */
  rustdesk_web_available?: boolean;
  bmc_available?: boolean;
  pve?: { kind: "vm" | "ct"; node: string; vmid: number; cluster: string | null } | null;
  mac_vendor: string | null;
  device_name: string | null;
  created_at: string;
  updated_at: string;
}
