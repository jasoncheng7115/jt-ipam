<script setup lang="ts">
import { computed } from "vue";
import {
  NConfigProvider,
  NMessageProvider,
  NDialogProvider,
  NNotificationProvider,
  NLoadingBarProvider,
  darkTheme,
  zhTW,
  enUS,
  dateZhTW,
  dateEnUS,
} from "naive-ui";
import { storeToRefs } from "pinia";
import { useUiStore } from "@/stores/ui";

const ui = useUiStore();
const { effectiveTheme, locale } = storeToRefs(ui);

const naiveTheme = computed(() => (effectiveTheme.value === "dark" ? darkTheme : null));
const naiveLocale = computed(() => (locale.value === "zh-TW" ? zhTW : enUS));
const naiveDateLocale = computed(() => (locale.value === "zh-TW" ? dateZhTW : dateEnUS));
</script>

<template>
  <n-config-provider :theme="naiveTheme" :locale="naiveLocale" :date-locale="naiveDateLocale">
    <n-loading-bar-provider>
      <n-dialog-provider>
        <n-notification-provider>
          <n-message-provider>
            <router-view />
          </n-message-provider>
        </n-notification-provider>
      </n-dialog-provider>
    </n-loading-bar-provider>
  </n-config-provider>
</template>

<style>
html,
body,
#app {
  height: 100%;
  margin: 0;
  font-family:
    -apple-system, BlinkMacSystemFont, "PingFang TC", "Microsoft JhengHei",
    "Noto Sans TC", "Helvetica Neue", Arial, sans-serif;
}
</style>
