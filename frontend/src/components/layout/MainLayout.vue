<script setup lang="ts">
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import {
  NLayout,
  NLayoutHeader,
  NLayoutSider,
  NLayoutContent,
  NMenu,
  NIcon,
  NSpace,
  NSelect,
  NButton,
  NTooltip,
  type MenuOption,
} from "naive-ui";
import { storeToRefs } from "pinia";
import { useUiStore } from "@/stores/ui";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const ui = useUiStore();
const { theme, locale, effectiveTheme } = storeToRefs(ui);

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
  { label: () => t("nav.tools"), key: "tools" },
  { label: () => t("nav.audit"), key: "audit" },
  { label: () => t("nav.settings"), key: "settings" },
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

function handleMenu(key: string) {
  router.push({ name: key }).catch(() => {
    // 未實作的頁面忽略
  });
}
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
        :value="route.name as string"
        @update:value="handleMenu"
      />
    </n-layout-sider>
    <n-layout>
      <n-layout-header bordered class="topbar">
        <n-space align="center" justify="space-between" style="width: 100%">
          <span class="title">{{ t("app.title") }}</span>
          <n-space>
            <n-select
              :value="locale"
              :options="localeOptions"
              size="small"
              style="width: 120px"
              @update:value="ui.setLocale"
            />
            <n-tooltip>
              <template #trigger>
                <n-select
                  :value="theme"
                  :options="themeOptions"
                  size="small"
                  style="width: 100px"
                  @update:value="ui.setTheme"
                />
              </template>
              {{ effectiveTheme }}
            </n-tooltip>
          </n-space>
        </n-space>
      </n-layout-header>
      <n-layout-content content-style="padding: 16px;">
        <router-view />
      </n-layout-content>
    </n-layout>
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
