import { createRouter, createWebHistory, type RouteRecordRaw } from "vue-router";

const routes: RouteRecordRaw[] = [
  {
    path: "/",
    component: () => import("@/components/layout/MainLayout.vue"),
    children: [
      {
        path: "",
        name: "dashboard",
        component: () => import("@/views/Dashboard.vue"),
      },
      {
        path: "sections",
        name: "sections",
        component: () => import("@/views/Sections.vue"),
      },
      {
        path: "subnets",
        name: "subnets",
        component: () => import("@/views/Subnets.vue"),
      },
      {
        path: "addresses",
        name: "addresses",
        component: () => import("@/views/Addresses.vue"),
      },
    ],
  },
];

export const router = createRouter({
  history: createWebHistory(),
  routes,
});
