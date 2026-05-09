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
      { path: "tools", name: "tools", component: () => import("@/views/Tools.vue") },
      { path: "settings", name: "settings", component: () => import("@/views/Settings.vue") },
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
  return true;
});
