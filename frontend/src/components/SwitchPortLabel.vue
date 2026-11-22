<script setup lang="ts">
/**
 * 交換器位置顯示：把「switch-003 / eth1/0/24」呈現為「switch-003@eth1/0/24」，
 * 其中 @ 用品牌色標示。只切第一個 " / "（port 內含的斜線如 eth1/0/24 不動）。
 */
import { computed } from "vue";
import type { RouteLocationRaw } from "vue-router";

/** to：裝置名稱做成連結（追線、機房佈線這類列出兩端的地方） */
const props = defineProps<{ value?: string | null; dim?: boolean; to?: RouteLocationRaw | null }>();

const parts = computed<{ sw: string; port: string | null }>(() => {
  const v = (props.value ?? "").trim();
  const idx = v.indexOf(" / ");
  if (idx < 0) return { sw: v, port: null };
  return { sw: v.slice(0, idx), port: v.slice(idx + 3) };
});
</script>

<template>
  <span v-if="parts.sw" class="swp" :class="{ dim }">
    <router-link v-if="to" :to="to" class="swp-dev">{{ parts.sw }}</router-link><template v-else>{{ parts.sw }}</template><template v-if="parts.port"><span class="swp-at">@</span>{{ parts.port }}</template>
  </span>
</template>

<style scoped>
/* 低信心（LibreNMS 未標記為高信心）：橘色 + 斜體 + 虛線底線，與高信心明顯區隔 */
.swp.dim {
  color: #d97706;
  font-style: italic;
  text-decoration: underline dotted;
  text-underline-offset: 2px;
}
.swp.dim .swp-at { color: #d97706; }
.swp-at { color: #18a058; font-weight: 700; margin: 0 1px; }
.swp-dev { color: var(--primary-color, #18a058); text-decoration: none; }
</style>
