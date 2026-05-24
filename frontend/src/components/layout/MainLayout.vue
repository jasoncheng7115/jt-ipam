<script setup lang="ts">
import { computed, h } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import {
  NLayout,
  NLayoutHeader,
  NLayoutSider,
  NLayoutContent,
  NMenu,
  NSpace,
  NSelect,
  NDropdown,
  NButton,
  NAvatar,
  NIcon,
  type MenuOption,
} from "naive-ui";
import { storeToRefs } from "pinia";
import { useUiStore } from "@/stores/ui";
import { useAuthStore } from "@/stores/auth";
import NotificationBell from "@/components/NotificationBell.vue";
import GlobalSearch from "@/components/GlobalSearch.vue";
import ChatWidget from "@/components/ChatWidget.vue";
import {
  // 主導覽
  DashboardIcon, SectionsIcon, SubnetsIcon, AddressesIcon, VlansIcon, VrfsIcon,
  NatIcon, DevicesIcon, RacksIcon, LocationsIcon, RequestsIcon, TopologyIcon,
  ToolsIcon, SettingsIcon,
  // Phase 3 / Admin
  Phase3Icon, AdvancedIcon, VirtualizationIcon, PhysicalIcon,
  AdminIcon, AuditIcon, UsersIcon, GroupsIcon, CustomFieldsIcon, AnomalyIcon,
  DnsIcon, LibreNMSIcon, FirewallIcon, WazuhIcon, ScanAgentsIcon, WebhooksIcon,
  MigrationIcon, ImportIcon, PluginsIcon,
  // topbar / user menu
  LogoutIcon,
  renderIcon,
} from "@/icons";
import { User as UserOutline } from "@iconoir/vue";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const ui = useUiStore();
const auth = useAuthStore();
const { theme, locale } = storeToRefs(ui);
const { me } = storeToRefs(auth);

const menuOptions = computed<MenuOption[]>(() => {
  const base: MenuOption[] = [
    { label: () => t("nav.dashboard"),   key: "dashboard",  icon: renderIcon(DashboardIcon) },
    { label: () => t("nav.sections"),    key: "sections",   icon: renderIcon(SectionsIcon) },
    { label: () => t("nav.subnets"),     key: "subnets",    icon: renderIcon(SubnetsIcon) },
    { label: () => t("nav.addresses"),   key: "addresses",  icon: renderIcon(AddressesIcon) },
    { label: () => t("nav.vlans"),       key: "vlans",      icon: renderIcon(VlansIcon) },
    { label: () => t("nav.vrfs"),        key: "vrfs",       icon: renderIcon(VrfsIcon) },
    { label: () => t("nav.nat"),         key: "nat",        icon: renderIcon(NatIcon) },
    { label: () => t("nav.devices"),     key: "devices",    icon: renderIcon(DevicesIcon) },
    { label: () => t("nav.racks"),       key: "racks",      icon: renderIcon(RacksIcon) },
    { label: () => t("nav.locations"),   key: "locations",  icon: renderIcon(LocationsIcon) },
    { label: () => t("nav.requests"),    key: "requests",   icon: renderIcon(RequestsIcon) },
    { label: () => t("nav.topology"),    key: "topology",   icon: renderIcon(TopologyIcon) },
    {
      label: () => t("nav.phase3_section"),
      key: "phase3",
      icon: renderIcon(Phase3Icon),
      children: [
        { label: () => t("nav.advanced"),       key: "advanced", icon: renderIcon(AdvancedIcon) },
        { label: () => t("nav.virtualization"), key: "virt",     icon: renderIcon(VirtualizationIcon) },
        { label: () => t("nav.physical"),       key: "physical", icon: renderIcon(PhysicalIcon) },
      ],
    },
    { label: () => t("nav.tools"),       key: "tools",      icon: renderIcon(ToolsIcon) },
  ];
  if (me.value?.is_admin) {
    base.push(
      { type: "divider", key: "d-admin" },
      {
        label: () => t("nav.admin_section"),
        key: "admin",
        icon: renderIcon(AdminIcon),
        children: [
          { label: () => t("nav.audit"),         key: "audit",          icon: renderIcon(AuditIcon) },
          { label: () => t("nav.users"),         key: "users",          icon: renderIcon(UsersIcon) },
          { label: () => t("nav.groups"),        key: "groups",         icon: renderIcon(GroupsIcon) },
          { label: () => t("nav.custom_fields"), key: "custom_fields",  icon: renderIcon(CustomFieldsIcon) },
          { label: () => t("nav.anomaly"),       key: "anomaly",        icon: renderIcon(AnomalyIcon) },
          { label: () => t("nav.dns"),           key: "dns",            icon: renderIcon(DnsIcon) },
          { label: () => t("nav.librenms"),      key: "librenms",       icon: renderIcon(LibreNMSIcon) },
          { label: () => t("nav.firewall"),      key: "firewall",       icon: renderIcon(FirewallIcon) },
          { label: () => t("nav.wazuh"),         key: "wazuh",          icon: renderIcon(WazuhIcon) },
          { label: () => t("nav.scan_agents"),   key: "scan_agents",    icon: renderIcon(ScanAgentsIcon) },
          { label: () => t("nav.webhooks"),      key: "webhooks",       icon: renderIcon(WebhooksIcon) },
          { label: () => t("nav.migration"),     key: "migration",      icon: renderIcon(MigrationIcon) },
          { label: () => t("nav.import"),        key: "import",         icon: renderIcon(ImportIcon) },
          { label: () => t("nav.plugins"),       key: "plugins",        icon: renderIcon(PluginsIcon) },
        ],
      },
    );
  }
  return base;
});

const localeOptions = [
  { label: "繁體中文", value: "zh-TW" },
  { label: "English",  value: "en-US" },
];

const themeOptions = computed(() => [
  { label: t("topbar.theme.light"), value: "light" },
  { label: t("topbar.theme.dark"),  value: "dark" },
  { label: t("topbar.theme.auto"),  value: "auto" },
]);

const userMenuOptions = computed(() => [
  { label: t("topbar.user_menu.profile"),     key: "profile",     icon: renderIcon(UserOutline, 16) },
  { label: t("topbar.user_menu.preferences"), key: "preferences", icon: renderIcon(SettingsIcon, 16) },
  { type: "divider" as const, key: "d" },
  { label: t("topbar.user_menu.logout"),      key: "logout",      icon: renderIcon(LogoutIcon, 16) },
]);

function handleMenu(key: string) {
  router.push({ name: key }).catch(() => {});
}

async function handleUserMenu(key: string) {
  if (key === "logout") {
    await auth.logout();
    router.push({ name: "login" });
  } else if (key === "preferences" || key === "profile") {
    router.push({ name: "settings" });
  }
}

const userInitial = computed(() => (me.value?.username || "?").slice(0, 2).toUpperCase());
</script>

<template>
  <n-layout has-sider style="height: 100vh">
    <n-layout-sider
      bordered
      collapse-mode="width"
      :collapsed-width="64"
      :width="240"
      show-trigger
    >
      <div class="brand">
        <strong>jt-ipam</strong>
      </div>
      <n-menu
        :options="menuOptions"
        :value="(route.name as string)"
        @update:value="handleMenu"
      />
    </n-layout-sider>
    <n-layout>
      <n-layout-header bordered class="topbar">
        <n-space align="center" justify="space-between" style="width: 100%">
          <span class="title">{{ t("app.title") }}</span>
          <n-space align="center">
            <global-search v-if="me" />
            <n-select
              :value="locale"
              :options="localeOptions"
              size="small"
              style="width: 120px"
              @update:value="ui.setLocale"
            />
            <n-select
              :value="theme"
              :options="themeOptions"
              size="small"
              style="width: 100px"
              @update:value="ui.setTheme"
            />
            <notification-bell v-if="me" />
            <n-dropdown
              v-if="me"
              :options="userMenuOptions"
              trigger="click"
              @select="handleUserMenu"
            >
              <n-button text style="display: flex; gap: 6px; align-items: center">
                <n-avatar size="small" round>{{ userInitial }}</n-avatar>
                <span>{{ me.username }}{{ me.is_admin ? " · admin" : "" }}</span>
              </n-button>
            </n-dropdown>
          </n-space>
        </n-space>
      </n-layout-header>
      <n-layout-content content-style="padding: 16px;">
        <router-view />
      </n-layout-content>
    </n-layout>
    <chat-widget v-if="me" />
  </n-layout>
</template>

<style scoped>
.brand {
  padding: 16px;
  font-size: 18px;
  letter-spacing: 0.5px;
}
.topbar {
  padding: 8px 16px;
}
.title {
  font-size: 14px;
  opacity: 0.8;
}
</style>
