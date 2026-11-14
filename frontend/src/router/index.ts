import { createRouter, createWebHistory, type RouteRecordRaw } from "vue-router";
import { useAuthStore } from "@/stores/auth";

const routes: RouteRecordRaw[] = [
  {
    path: "/login",
    name: "login",
    component: () => import("@/views/Login.vue"),
    meta: { public: true },
  },
  {
    // 另開視窗的全頁 SSH 終端機（不套 MainLayout 側欄；仍需登入）
    path: "/ssh/:id",
    name: "ssh-console",
    component: () => import("@/views/SshConsole.vue"),
  },
  {
    // 另開視窗的全頁 SFTP 檔案瀏覽器（與 SSH 同一道權限閘門）
    path: "/sftp/:id",
    name: "sftp-console",
    component: () => import("@/views/SftpConsole.vue"),
  },
  {
    // 另開視窗的全頁 RDP 畫面（不套 MainLayout 側欄；仍需登入）
    path: "/rdp/:id",
    name: "rdp-console",
    component: () => import("@/views/RdpConsole.vue"),
  },
  {
    // 另開視窗的全頁 VNC 畫面（不套 MainLayout 側欄；仍需登入）
    path: "/vnc/:id",
    name: "vnc-console",
    component: () => import("@/views/VncConsole.vue"),
  },
  {
    // 另開視窗的全頁 PVE 主控台（noVNC / xterm）
    path: "/novnc/:id",
    name: "novnc-console",
    component: () => import("@/views/NoVncConsole.vue"),
  },
  {
    // 另開視窗的全頁 BMC OOB主控台（IPMI SOL）
    path: "/bmc/:id",
    name: "bmc-console",
    component: () => import("@/views/BmcConsole.vue"),
  },
  {
    // 另開分頁的全頁「相容 RustDesk 的網頁連線」（透過 RustDesk 伺服器連線；不套 MainLayout 側欄；仍需登入）
    path: "/rustdesk/:id",
    name: "rustdesk-console",
    component: () => import("@/views/RustDeskConsole.vue"),
  },
  {
    // 另開分頁的全頁「相容 RustDesk 的網頁連線」檔案傳輸（另一條連線，附錄 J；同一道權限＋伺服器的檔案傳輸開關）
    path: "/rustdesk/:id/files",
    name: "rustdesk-files",
    component: () => import("@/views/RustDeskFilesConsole.vue"),
  },
  {
    // 戰情室（大螢幕看板，2026-10-21）：不套 MainLayout、不走登入守衛 —— 電視可以只帶顯示權杖，
    // 頁面自己判斷（有權杖用權杖；沒有就照一般登入流程）
    path: "/warroom",
    name: "warroom",
    meta: { public: true },
    component: () => import("@/views/warroom/WarRoom.vue"),
  },
  {
    path: "/",
    component: () => import("@/components/layout/MainLayout.vue"),
    children: [
      { path: "", name: "dashboard", component: () => import("@/views/Dashboard.vue") },
      { path: "sections", name: "sections", component: () => import("@/views/Sections.vue") },
      { path: "sections/:id", name: "section-detail", component: () => import("@/views/SectionDetail.vue") },
      { path: "subnets", name: "subnets", component: () => import("@/views/Subnets.vue") },
      { path: "subnets/:id", name: "subnet-detail", component: () => import("@/views/SubnetDetail.vue") },
      { path: "subnets/:id/unmanaged/:ip", name: "address-unmanaged", component: () => import("@/views/UnmanagedAddress.vue") },
      { path: "addresses", name: "addresses", component: () => import("@/views/Addresses.vue") },
      { path: "addresses/:id", name: "address-detail", component: () => import("@/views/IPDetail.vue") },
      // IP 的「探測」（只有管理員）：獨立頁面，歷次結果都留著、可以點回來看
      { path: "addresses/:id/identify", name: "address-identify", component: () => import("@/views/IpIdentify.vue") },
      // IPAM 還沒有記錄的位址（異常偵測的「未授權 IP」）：以位址探測，同一個畫面
      { path: "identify/ip/:ip", name: "ip-identify", component: () => import("@/views/IpIdentify.vue") },
      { path: "ai-audit", name: "ai_audit", component: () => import("@/views/AIAudit.vue"), meta: { admin: true } },
      { path: "ip-changes", name: "ip_changes", component: () => import("@/views/IPChanges.vue") },
      { path: "change-impact", name: "change_impact", component: () => import("@/views/ChangeImpactList.vue") },
      // 要排在 change-impact/:id 前面，否則 services 會被當成計畫 id
      { path: "change-impact/services", name: "change_impact_services", component: () => import("@/views/ChangeImpactServices.vue") },
      { path: "change-impact/:id", name: "change_impact_plan", component: () => import("@/views/ChangeImpactPlan.vue") },
      { path: "hostname-precedence", name: "hostname_precedence", component: () => import("@/views/HostnamePrecedence.vue"), meta: { admin: true } },
      { path: "racks", name: "racks", component: () => import("@/views/Racks.vue") },
      { path: "requests", name: "requests", component: () => import("@/views/IPRequests.vue") },
      { path: "requests/:id", name: "request-detail", component: () => import("@/views/IPRequestDetail.vue") },
      { path: "tools", name: "tools", component: () => import("@/views/Tools.vue") },
      // MAC 歷程：以一個 MAC 為中心（用過的 IP、交換器埠、DHCP、裝置、時間軸）
      { path: "mac/:mac?", name: "mac-history", component: () => import("@/views/MacHistory.vue") },
      { path: "tasks", name: "tasks", component: () => import("@/views/Tasks.vue") },
      { path: "topology", name: "topology", component: () => import("@/views/Topology.vue") },
      { path: "settings", name: "settings", component: () => import("@/views/Settings.vue") },
      // API 權杖是自助功能（每個帳號管自己的），不掛 admin。
      // 路徑刻意放在 /account/ 底下：nginx 是 `location /api/`（帶斜線）所以 /api-tokens
      // 其實不會被反代吃掉，但只差一個字元，換成 /account/ 就不必依賴那個細節。
      { path: "account/api-tokens", name: "api_tokens", component: () => import("@/views/ApiTokens.vue") },
      { path: "notifications", name: "notifications", component: () => import("@/views/Notifications.vue") },
      // Admin
      { path: "audit", name: "audit", component: () => import("@/views/Audit.vue"), meta: { admin: true } },
      { path: "users", name: "users", component: () => import("@/views/Users.vue"), meta: { admin: true } },
      { path: "groups", name: "groups", component: () => import("@/views/Groups.vue"), meta: { admin: true } },
      { path: "vlans", name: "vlans", component: () => import("@/views/VLANs.vue") },
      { path: "vrfs", name: "vrfs", component: () => import("@/views/VRFs.vue") },
      { path: "devices", name: "devices", component: () => import("@/views/Devices.vue") },
      { path: "devices/:id", name: "device-detail", component: () => import("@/views/DeviceDetail.vue") },
      { path: "locations", name: "locations", component: () => import("@/views/Locations.vue") },
      { path: "dns", name: "dns", component: () => import("@/views/DNSAdmin.vue"), meta: { admin: true } },
      { path: "adguard", name: "adguard", component: () => import("@/views/AdGuardAdmin.vue"), meta: { admin: true } },
      { path: "librenms", name: "librenms", component: () => import("@/views/LibreNMSAdmin.vue"), meta: { admin: true } },
      { path: "firewall", name: "firewall", component: () => import("@/views/FirewallAdmin.vue"), meta: { admin: true } },
      { path: "firewall-admin", name: "firewall_admin", component: () => import("@/views/FirewallAdmin.vue"), meta: { admin: true } },
      { path: "pfsense", name: "pfsense", component: () => import("@/views/PfSenseAdmin.vue"), meta: { admin: true } },
      { path: "windows-dhcp", name: "windows_dhcp", component: () => import("@/views/WindowsDhcpAdmin.vue"), meta: { admin: true } },
      { path: "kea-dhcp", name: "kea_dhcp", component: () => import("@/views/KeaDhcpAdmin.vue"), meta: { admin: true } },
      { path: "technitium-dhcp", name: "technitium_dhcp", component: () => import("@/views/TechnitiumDhcpAdmin.vue"), meta: { admin: true } },
      { path: "isc-dhcp", name: "isc_dhcp", component: () => import("@/views/IscDhcpAdmin.vue"), meta: { admin: true } },
      { path: "isoinsight", name: "isoinsight", component: () => import("@/views/IsoInsightAdmin.vue"), meta: { admin: true } },
      { path: "rustdesk", name: "rustdesk", component: () => import("@/views/RustDeskAdmin.vue"), meta: { admin: true } },
      { path: "fortigate", name: "fortigate", component: () => import("@/views/FortiGateAdmin.vue"), meta: { admin: true } },
      { path: "paloalto", name: "paloalto", component: () => import("@/views/PaloAltoAdmin.vue"), meta: { admin: true } },
      { path: "checkpoint", name: "checkpoint", component: () => import("@/views/CheckPointAdmin.vue"), meta: { admin: true } },
      { path: "doctor", name: "doctor", component: () => import("@/views/SystemDoctor.vue"), meta: { admin: true } },
      // 跳板移到掃描代理頁的頁籤（2026-10-04）；舊網址與書籤照樣到得了
      { path: "jump-hosts", name: "jump_hosts", redirect: { name: "scan_agents", query: { tab: "jump" } } },
      { path: "mikrotik", name: "mikrotik", component: () => import("@/views/MikroTikAdmin.vue"), meta: { admin: true } },
      // SNMP 收集（0217）：掃描代理以唯讀 SNMP 讀網路設備的 ARP／MAC 表／鄰居／介面
      { path: "snmp", name: "snmp", component: () => import("@/views/SnmpAdmin.vue"), meta: { admin: true } },
      { path: "ocs", name: "ocs", component: () => import("@/views/OcsAdmin.vue"), meta: { admin: true } },
      { path: "pfsense-fw", name: "pfsense_fw", component: () => import("@/views/PfSenseFirewallView.vue") },
      { path: "fortigate-fw", name: "fortigate_fw", component: () => import("@/views/FortiGateFirewallView.vue") },
      { path: "paloalto-fw", name: "paloalto_fw", component: () => import("@/views/PaloAltoFirewallView.vue") },
      { path: "mikrotik-fw", name: "mikrotik_fw", component: () => import("@/views/MikroTikRouterView.vue") },
      { path: "wazuh", name: "wazuh", component: () => import("@/views/WazuhAdmin.vue"), meta: { admin: true } },
      { path: "event-rules", name: "event_rules", component: () => import("@/views/EventRules.vue"), meta: { admin: true } },
      { path: "zabbix", name: "zabbix", component: () => import("@/views/ZabbixAdmin.vue"), meta: { admin: true } },
      { path: "checkmk", name: "checkmk", component: () => import("@/views/CheckmkAdmin.vue"), meta: { admin: true } },
      { path: "plugins", name: "plugins", component: () => import("@/views/PluginsAdmin.vue"), meta: { admin: true } },
      // Phase 3
      { path: "oui", name: "oui_admin", component: () => import("@/views/OUIAdmin.vue"), meta: { admin: true } },
      { path: "recog", name: "recog_admin", component: () => import("@/views/RecogAdmin.vue"), meta: { admin: true } },
      { path: "custom-fields", name: "custom_fields", component: () => import("@/views/CustomFields.vue"), meta: { admin: true } },
      { path: "customers", name: "customers", component: () => import("@/views/Customers.vue"), meta: { admin: true } },
      { path: "customers/:id", name: "customer-detail", component: () => import("@/views/CustomerDetail.vue"), meta: { admin: true } },
      { path: "llm", name: "llm_settings", component: () => import("@/views/LLMSettings.vue"), meta: { admin: true } },
      { path: "system-settings", name: "system_settings", component: () => import("@/views/SystemSettings.vue"), meta: { admin: true } },
      { path: "notification-channels", name: "notification_channels", component: () => import("@/views/NotificationChannels.vue"), meta: { admin: true } },
      // 申請審核設定（原「IP 申請審核設定」，2026-10-08 改名並分成 IP 申請審核／IP 變更評估審核兩個頁籤）；舊網址轉過來
      { path: "approval-settings", name: "approval_settings", component: () => import("@/views/ApprovalSettings.vue"), meta: { admin: true } },
      { path: "ip-request-policy", name: "ip_request_policy", redirect: { name: "approval_settings" } },
      { path: "version", name: "version", component: () => import("@/views/VersionInfo.vue"), meta: { admin: true } },
      { path: "system-logs", name: "system_logs", component: () => import("@/views/SystemLogs.vue"), meta: { admin: true } },
      { path: "graylog-dsv", name: "graylog_dsv", component: () => import("@/views/GraylogDsvSettings.vue"), meta: { admin: true } },
      { path: "chat-history", name: "chat_history", component: () => import("@/views/ChatHistoryAdmin.vue"), meta: { admin: true } },
      { path: "my-chat-history", name: "my_chat_history", component: () => import("@/views/MyChatHistory.vue") },
      { path: "permissions", name: "permissions", component: () => import("@/views/Permissions.vue"), meta: { admin: true } },
      { path: "scan-agents", name: "scan_agents", component: () => import("@/views/ScanAgents.vue"), meta: { admin: true } },
      { path: "certificates", name: "certificates", component: () => import("@/views/Certificates.vue"), meta: { admin: true } },
      { path: "webhooks", name: "webhooks", component: () => import("@/views/Webhooks.vue"), meta: { admin: true } },
      { path: "nat", name: "nat", component: () => import("@/views/NAT.vue") },
      { path: "anomaly", name: "anomaly", component: () => import("@/views/Anomaly.vue"), meta: { admin: true } },
      { path: "fw-rule-changes", name: "fw_rule_changes", component: () => import("@/views/FwRuleChanges.vue"), meta: { admin: true } },
      { path: "attack-surface", name: "attack_surface", component: () => import("@/views/AttackSurface.vue") },
      { path: "advanced", name: "advanced", component: () => import("@/views/Advanced.vue") },
      { path: "advanced/tenancy", name: "adv-tenancy", component: () => import("@/views/Advanced.vue"), props: { mode: "tenancy" } },
      { path: "advanced/asn", name: "adv-asn", component: () => import("@/views/Advanced.vue"), props: { mode: "asn" } },
      { path: "advanced/circuits", name: "adv-circuits", component: () => import("@/views/Advanced.vue"), props: { mode: "circuits" } },
      { path: "advanced/circuits/:id", name: "adv-circuit-detail", component: () => import("@/views/CircuitDetail.vue") },
      { path: "maintenance", name: "maintenance", component: () => import("@/views/Maintenance.vue") },
      { path: "maintenance/:id", name: "maintenance-detail", component: () => import("@/views/MaintenanceDetail.vue") },
      { path: "advanced/domains", name: "adv-domains", component: () => import("@/views/Domains.vue") },
      { path: "advanced/domains/:id", name: "adv-domain-detail", component: () => import("@/views/DomainDetail.vue") },
      { path: "advanced/contacts", name: "adv-contacts", component: () => import("@/views/Advanced.vue"), props: { mode: "contacts" } },
      { path: "advanced/wireless", name: "adv-wireless", component: () => import("@/views/Advanced.vue"), props: { mode: "wireless" } },
      { path: "advanced/connections", name: "adv-connections", component: () => import("@/views/Connections.vue") },
      { path: "advanced/dns-records", name: "adv-dns-records", component: () => import("@/views/DnsRecords.vue") },
      { path: "advanced/cert-status", name: "adv-cert-status", component: () => import("@/views/CertStatus.vue") },
      // 虛擬化拆成兩頁：同一個元件，靠 platform 決定顯示哪個平台的叢集與 VM
      { path: "virt", name: "virt", component: () => import("@/views/Virtualization.vue"),
        props: { platform: "proxmox" } },
      { path: "virt-vmware", name: "virt_vmware",
        component: () => import("@/views/Virtualization.vue"), props: { platform: "vmware" } },
      { path: "virt-hyperv", name: "virt_hyperv",
        component: () => import("@/views/Virtualization.vue"), props: { platform: "hyperv" } },
      { path: "virt-openstack", name: "virt_openstack",
        component: () => import("@/views/Virtualization.vue"), props: { platform: "openstack" } },
      { path: "virt-admin", name: "virt_admin", component: () => import("@/views/Virtualization.vue"), meta: { admin: true } },
      { path: "esxi", name: "esxi_admin", component: () => import("@/views/ESXiAdmin.vue"), meta: { admin: true } },
      { path: "hyperv", name: "hyperv_admin", component: () => import("@/views/HyperVAdmin.vue"), meta: { admin: true } },
      { path: "openstack", name: "openstack_admin", component: () => import("@/views/OpenStackAdmin.vue"), meta: { admin: true } },
      { path: "warroom-settings", name: "warroom-settings", component: () => import("@/views/WarroomSettings.vue"), meta: { admin: true } },
      { path: "cabling", name: "cabling", component: () => import("@/views/Physical.vue"), props: { mode: "cabling" } },
      { path: "power", name: "power", component: () => import("@/views/Physical.vue"), props: { mode: "power" } },
      { path: "vpn", name: "vpn-tunnels", component: () => import("@/views/Physical.vue"), props: { mode: "vpn" } },
      { path: "vpn/:id", name: "vpn-detail", component: () => import("@/views/VpnDetail.vue") },
      { path: "physical", redirect: { name: "cabling" } },
      { path: "migration", name: "migration", component: () => import("@/views/Migration.vue"), meta: { admin: true } },
      { path: "system-transfer", name: "system_transfer", component: () => import("@/views/SystemTransfer.vue"), meta: { admin: true } },
      { path: "import", name: "import", component: () => import("@/views/ImportExternal.vue"), meta: { admin: true } },
      // 不存在的網址：顯示「找不到頁面」，不要整頁空白（要放最後，前面的路由先比對）
      { path: ":pathMatch(.*)*", name: "not_found", component: () => import("@/views/NotFound.vue") },
    ],
  },
];

export const router = createRouter({
  history: createWebHistory(),
  routes,
});

// 部署新版後會產生新的 chunk 檔名(hash)，舊分頁裡記的舊 chunk 已不存在 → 點功能時
// 動態 import 會 404 失敗、vue-router 靜默中斷導覽，症狀就是「點很多次都沒反應、
// 要等很久 / 硬重整才好」。偵測到這類「載入失敗」就自動重載一次到目標頁(防無限迴圈)。
function isChunkLoadError(e: unknown): boolean {
  const m = (e as Error)?.message || String(e ?? "");
  return /dynamically imported module|Failed to fetch dynamically|Importing a module script failed|Loading chunk|error loading dynamically imported|'?text\/html'? is not a valid JavaScript MIME type/i.test(m);
}
function reloadOnce(target?: string, reason = ""): void {
  const KEY = "jt-chunk-reload-at";
  const last = Number(sessionStorage.getItem(KEY) || 0);
  // 30 秒內已自動重載過就不再重載(避免伺服器真的故障時無限刷新)
  if (Date.now() - last < 30000) return;
  sessionStorage.setItem(KEY, String(Date.now()));
  // 留下原因給下一頁回報（utils/pageDiag）：分辨「我們自己重載」與「瀏覽器重載」
  try { sessionStorage.setItem("jt-diag-reload-reason", `${new Date().toISOString().slice(11, 19)} ${reason}`.slice(0, 300)); } catch { /* noop */ }
  if (target && target !== window.location.pathname) window.location.assign(target);
  else window.location.reload();
}

router.onError((err, to) => {
  if (isChunkLoadError(err)) reloadOnce(to?.fullPath, `router: ${(err as Error)?.message || err}`);
});

// Vite 預載動態 chunk 失敗時會丟此事件(比 router.onError 更早攔到)
window.addEventListener("vite:preloadError", ((e: Event) => {
  e.preventDefault();
  reloadOnce(undefined, `preload: ${String((e as any).payload?.message || (e as any).payload || "")}`);
}) as EventListener);

router.beforeEach(async (to, _from) => {
  const auth = useAuthStore();
  if (to.meta.public) return true;

  // 這個分頁還沒有存取權杖（新分頁、重新整理）：先用 HttpOnly Cookie 換一把，換不到才去登入頁
  if (!auth.isAuthenticated && !(await auth.ensureSession())) {
    return {
      name: "login",
      query: { next: to.fullPath },
    };
  }

  // 已認證但尚未拿過 me：嘗試取一次 (驗 token 有效)
  if (auth.me === null) {
    try {
      await auth.fetchMe();
    } catch {
      auth.clearTokens();
      return {
        name: "login",
        query: { next: to.fullPath },
      };
    }
  }

  // admin-only routes — non-admin 退回 dashboard(實際權限由 backend 401/403 把關)
  if (to.meta.admin && !auth.me?.is_admin) {
    return { name: "dashboard" };
  }
  return true;
});
