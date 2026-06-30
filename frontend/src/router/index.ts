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
    path: "/",
    component: () => import("@/components/layout/MainLayout.vue"),
    children: [
      { path: "", name: "dashboard", component: () => import("@/views/Dashboard.vue") },
      { path: "sections", name: "sections", component: () => import("@/views/Sections.vue") },
      { path: "sections/:id", name: "section-detail", component: () => import("@/views/SectionDetail.vue") },
      { path: "subnets", name: "subnets", component: () => import("@/views/Subnets.vue") },
      { path: "subnets/:id", name: "subnet-detail", component: () => import("@/views/SubnetDetail.vue") },
      { path: "addresses", name: "addresses", component: () => import("@/views/Addresses.vue") },
      { path: "addresses/:id", name: "address-detail", component: () => import("@/views/IPDetail.vue") },
      { path: "ip-changes", name: "ip_changes", component: () => import("@/views/IPChanges.vue") },
      { path: "hostname-precedence", name: "hostname_precedence", component: () => import("@/views/HostnamePrecedence.vue"), meta: { admin: true } },
      { path: "racks", name: "racks", component: () => import("@/views/Racks.vue") },
      { path: "requests", name: "requests", component: () => import("@/views/IPRequests.vue") },
      { path: "requests/:id", name: "request-detail", component: () => import("@/views/IPRequestDetail.vue") },
      { path: "tools", name: "tools", component: () => import("@/views/Tools.vue") },
      { path: "tasks", name: "tasks", component: () => import("@/views/Tasks.vue") },
      { path: "topology", name: "topology", component: () => import("@/views/Topology.vue") },
      { path: "settings", name: "settings", component: () => import("@/views/Settings.vue") },
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
      { path: "pfsense-fw", name: "pfsense_fw", component: () => import("@/views/PfSenseFirewallView.vue") },
      { path: "wazuh", name: "wazuh", component: () => import("@/views/WazuhAdmin.vue"), meta: { admin: true } },
      { path: "plugins", name: "plugins", component: () => import("@/views/PluginsAdmin.vue"), meta: { admin: true } },
      // Phase 3
      { path: "oui", name: "oui_admin", component: () => import("@/views/OUIAdmin.vue"), meta: { admin: true } },
      { path: "custom-fields", name: "custom_fields", component: () => import("@/views/CustomFields.vue"), meta: { admin: true } },
      { path: "customers", name: "customers", component: () => import("@/views/Customers.vue"), meta: { admin: true } },
      { path: "customers/:id", name: "customer-detail", component: () => import("@/views/CustomerDetail.vue"), meta: { admin: true } },
      { path: "llm", name: "llm_settings", component: () => import("@/views/LLMSettings.vue"), meta: { admin: true } },
      { path: "system-settings", name: "system_settings", component: () => import("@/views/SystemSettings.vue"), meta: { admin: true } },
      { path: "notification-channels", name: "notification_channels", component: () => import("@/views/NotificationChannels.vue"), meta: { admin: true } },
      { path: "ip-request-policy", name: "ip_request_policy", component: () => import("@/views/IPRequestPolicy.vue"), meta: { admin: true } },
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
      { path: "advanced", name: "advanced", component: () => import("@/views/Advanced.vue") },
      { path: "advanced/tenancy", name: "adv-tenancy", component: () => import("@/views/Advanced.vue"), props: { mode: "tenancy" } },
      { path: "advanced/asn", name: "adv-asn", component: () => import("@/views/Advanced.vue"), props: { mode: "asn" } },
      { path: "advanced/circuits", name: "adv-circuits", component: () => import("@/views/Advanced.vue"), props: { mode: "circuits" } },
      { path: "advanced/contacts", name: "adv-contacts", component: () => import("@/views/Advanced.vue"), props: { mode: "contacts" } },
      { path: "advanced/wireless", name: "adv-wireless", component: () => import("@/views/Advanced.vue"), props: { mode: "wireless" } },
      { path: "advanced/connections", name: "adv-connections", component: () => import("@/views/Connections.vue") },
      { path: "advanced/dns-records", name: "adv-dns-records", component: () => import("@/views/DnsRecords.vue") },
      { path: "advanced/cert-status", name: "adv-cert-status", component: () => import("@/views/CertStatus.vue") },
      { path: "virt", name: "virt", component: () => import("@/views/Virtualization.vue") },
      { path: "virt-admin", name: "virt_admin", component: () => import("@/views/Virtualization.vue"), meta: { admin: true } },
      { path: "cabling", name: "cabling", component: () => import("@/views/Physical.vue"), props: { mode: "cabling" } },
      { path: "power", name: "power", component: () => import("@/views/Physical.vue"), props: { mode: "power" } },
      { path: "vpn", name: "vpn-tunnels", component: () => import("@/views/Physical.vue"), props: { mode: "vpn" } },
      { path: "physical", redirect: { name: "cabling" } },
      { path: "migration", name: "migration", component: () => import("@/views/Migration.vue"), meta: { admin: true } },
      { path: "import", name: "import", component: () => import("@/views/ImportExternal.vue"), meta: { admin: true } },
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
function reloadOnce(target?: string): void {
  const KEY = "jt-chunk-reload-at";
  const last = Number(sessionStorage.getItem(KEY) || 0);
  // 30 秒內已自動重載過就不再重載(避免伺服器真的故障時無限刷新)
  if (Date.now() - last < 30000) return;
  sessionStorage.setItem(KEY, String(Date.now()));
  if (target && target !== window.location.pathname) window.location.assign(target);
  else window.location.reload();
}

router.onError((err, to) => {
  if (isChunkLoadError(err)) reloadOnce(to?.fullPath);
});

// Vite 預載動態 chunk 失敗時會丟此事件(比 router.onError 更早攔到)
window.addEventListener("vite:preloadError", ((e: Event) => {
  e.preventDefault();
  reloadOnce();
}) as EventListener);

router.beforeEach(async (to, _from) => {
  const auth = useAuthStore();
  if (to.meta.public) return true;

  if (!auth.isAuthenticated) {
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
