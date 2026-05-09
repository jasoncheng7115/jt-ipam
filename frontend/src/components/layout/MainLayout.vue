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
  type MenuOption,
} from "naive-ui";
import { storeToRefs } from "pinia";
import { useUiStore } from "@/stores/ui";
import { useAuthStore } from "@/stores/auth";
import NotificationBell from "@/components/NotificationBell.vue";
import GlobalSearch from "@/components/GlobalSearch.vue";
import ChatWidget from "@/components/ChatWidget.vue";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const ui = useUiStore();
const auth = useAuthStore();
const { theme, locale } = storeToRefs(ui);
const { me } = storeToRefs(auth);

const menuOptions = computed<MenuOption[]>(() => [
  { label: () => t("nav.dashboard"), key: "dashboard" },
  { label: () => t("nav.sections"), key: "sections" },
  { label: () => t("nav.subnets"), key: "subnets" },
  { label: () => t("nav.addresses"), key: "addresses" },
  { label: () => t("nav.vlans"), key: "vlans" },
  { label: () => t("nav.vrfs"), key: "vrfs" },
  { label: () => t("nav.devices"), key: "devices" },
  { label: () => t("nav.racks"), key: "racks" },
  { label: () => t("nav.locations"), key: "locations" },
  { label: () => t("nav.requests"), key: "requests" },
  { label: () => t("nav.topology"), key: "topology" },
  { label: () => t("nav.tools"), key: "tools" },
]);

const localeOptions = [
  { label: "繁體中文", value: "zh-TW" },
  { label: "English", value: "en-US" },
];

const themeOptions = computed(() => [
  { label: t("topbar.theme.light"), value: "light" },
  { label: t("topbar.theme.dark"), value: "dark" },
  { label: t("topbar.theme.auto"), value: "auto" },
]);

const userMenuOptions = computed(() => [
  { label: t("topbar.user_menu.profile"), key: "profile" },
  { label: t("topbar.user_menu.preferences"), key: "preferences" },
  { type: "divider" as const, key: "d" },
  { label: t("topbar.user_menu.logout"), key: "logout" },
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
