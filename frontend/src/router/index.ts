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
    path: "/",
    component: () => import("@/components/layout/MainLayout.vue"),
    children: [
      { path: "", name: "dashboard", component: () => import("@/views/Dashboard.vue") },
      { path: "sections", name: "sections", component: () => import("@/views/Sections.vue") },
      { path: "subnets", name: "subnets", component: () => import("@/views/Subnets.vue") },
      { path: "subnets/:id", name: "subnet-detail", component: () => import("@/views/SubnetDetail.vue") },
      { path: "addresses", name: "addresses", component: () => import("@/views/Addresses.vue") },
      { path: "racks", name: "racks", component: () => import("@/views/Racks.vue") },
      { path: "requests", name: "requests", component: () => import("@/views/IPRequests.vue") },
      { path: "requests/:id", name: "request-detail", component: () => import("@/views/IPRequestDetail.vue") },
      { path: "tools", name: "tools", component: () => import("@/views/Tools.vue") },
      { path: "topology", name: "topology", component: () => import("@/views/Topology.vue") },
      { path: "settings", name: "settings", component: () => import("@/views/Settings.vue") },
      // Admin
      { path: "audit", name: "audit", component: () => import("@/views/Audit.vue"), meta: { admin: true } },
      { path: "users", name: "users", component: () => import("@/views/Users.vue"), meta: { admin: true } },
      { path: "groups", name: "groups", component: () => import("@/views/Groups.vue"), meta: { admin: true } },
      { path: "vlans", name: "vlans", component: () => import("@/views/VLANs.vue") },
      { path: "vrfs", name: "vrfs", component: () => import("@/views/VRFs.vue") },
      { path: "devices", name: "devices", component: () => import("@/views/Devices.vue") },
      { path: "locations", name: "locations", component: () => import("@/views/Locations.vue") },
      { path: "dns", name: "dns", component: () => import("@/views/DNSAdmin.vue"), meta: { admin: true } },
      { path: "librenms", name: "librenms", component: () => import("@/views/LibreNMSAdmin.vue"), meta: { admin: true } },
      { path: "firewall", name: "firewall", component: () => import("@/views/FirewallAdmin.vue"), meta: { admin: true } },
      { path: "wazuh", name: "wazuh", component: () => import("@/views/WazuhAdmin.vue"), meta: { admin: true } },
      { path: "plugins", name: "plugins", component: () => import("@/views/PluginsAdmin.vue"), meta: { admin: true } },
    ],
  },
];

export const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach(async (to, _from) => {
  const auth = useAuthStore();
  if (to.meta.public) return true;

  if (!auth.isAuthenticated) {
    return {
      name: "login",
      query: { next: to.fullPath },
    };
  }

  // 已認證但尚未拿過 me：嘗試取一次（驗 token 有效）
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

  // admin-only routes — non-admin 退回 dashboard（實際權限由 backend 401/403 把關）
  if (to.meta.admin && !auth.me?.is_admin) {
    return { name: "dashboard" };
  }
  return true;
});
