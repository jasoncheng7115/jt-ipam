<script setup lang="ts">
/**
 * 主控台斷線覆蓋層 —— SSH / RDP / VNC / noVNC / xterm / BMC 共用。
 * 斷線（phase closed/error）時在顯示區中央蓋一層大字 + icon，讓使用者一眼看出斷線；
 * 重連後（show=false）自動淡出移除。放在各主控台顯示容器內（該容器需 position:relative）。
 */
import { NIcon } from "naive-ui";
import { useI18n } from "vue-i18n";
import { DisconnectedIcon } from "@/icons";

defineProps<{ show: boolean; error?: boolean; message?: string }>();
const { t } = useI18n();
</script>

<template>
  <transition name="cdo-fade">
    <div v-if="show" class="cdo" :class="{ 'cdo-error': error }">
      <div class="cdo-box">
        <n-icon :component="DisconnectedIcon" :size="72" class="cdo-icon" />
        <div class="cdo-title">{{ message || (error ? t("common.disconnected_err") : t("common.disconnected")) }}</div>
        <div class="cdo-hint">{{ t("common.disconnected_hint") }}</div>
      </div>
    </div>
  </transition>
</template>

<style scoped>
.cdo {
  position: absolute; inset: 0; z-index: 30;
  display: flex; align-items: center; justify-content: center;
  background: rgba(8, 11, 18, .66); backdrop-filter: blur(2px);
  pointer-events: none;  /* 讓工具列「重新連線」仍可點 */
}
.cdo-box { text-align: center; color: #e9eefb; padding: 20px; }
.cdo-icon { color: #f0a020; filter: drop-shadow(0 3px 10px rgba(0, 0, 0, .5)); }
.cdo-error .cdo-icon { color: #e05561; }
.cdo-title { margin-top: 14px; font-size: 30px; font-weight: 800; letter-spacing: 3px; }
.cdo-hint { margin-top: 8px; font-size: 14px; opacity: .72; }
.cdo-fade-enter-active, .cdo-fade-leave-active { transition: opacity .28s ease; }
.cdo-fade-enter-from, .cdo-fade-leave-to { opacity: 0; }
</style>
