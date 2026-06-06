<script setup lang="ts">
/**
 * OS 家族 icon：依後端正規化的 os_family（見 backend/app/core/os_fingerprint.py）
 * 顯示對應的小 SVG，顏色區分。比不到 → unknown 通用 icon。無 emoji。
 */
import { computed } from "vue";

const props = withDefaults(defineProps<{ family?: string | null; size?: number }>(), {
  family: "unknown", size: 16,
});

// 每個家族：顏色 + SVG 內容（24x24 viewBox 的 path / 元素片段）
const MAP: Record<string, { color: string; body: string }> = {
  windows: { color: "#2563eb", body:
    '<path fill="currentColor" d="M3 5.5 11 4.3v7.2H3zM13 4l8-1.2v8.7h-8zM3 12.5h8v7.2L3 18.5zM13 12.5h8v8.7L13 20z"/>' },
  linux: { color: "#f59e0b", body:
    '<path fill="currentColor" d="M12 2c-2.2 0-3.5 1.9-3.5 4.2 0 1.6.4 2.3-.6 3.7C6.5 11.7 5 13.6 5 16c0 .9.4 1.5 1 1.9-.2.6.1 1.2.8 1.5.6 1.3 2.6 2.6 5.2 2.6s4.6-1.3 5.2-2.6c.7-.3 1-.9.8-1.5.6-.4 1-1 1-1.9 0-2.4-1.5-4.3-2.9-6.1-1-1.4-.6-2.1-.6-3.7C16 3.9 14.7 2 12 2zm-1.6 4.1c.4 0 .7.4.7.9s-.3.9-.7.9-.7-.4-.7-.9.3-.9.7-.9zm3.2 0c.4 0 .7.4.7.9s-.3.9-.7.9-.7-.4-.7-.9.3-.9.7-.9zM12 9.4c.9 0 2.1.5 2.1 1 0 .3-1.2 1-2.1 1s-2.1-.7-2.1-1c0-.5 1.2-1 2.1-1z"/>' },
  macos: { color: "#6b7280", body:
    '<path fill="currentColor" d="M16.4 12.6c0-2 1.6-2.9 1.7-3-1-1.4-2.4-1.6-2.9-1.6-1.2-.1-2.4.7-3 .7s-1.6-.7-2.6-.7c-1.3 0-2.6.8-3.3 2-1.4 2.4-.4 6 1 8 .7 1 1.5 2.1 2.5 2 1-.04 1.4-.65 2.6-.65s1.5.65 2.6.63c1.1-.02 1.8-1 2.5-2 .5-.7.7-1.1 1-1.9-2.6-1-2.6-3.8-1.6-3.5zM14.4 6c.5-.7.9-1.6.8-2.5-.8 0-1.7.5-2.3 1.2-.5.6-.9 1.5-.8 2.4.9.07 1.7-.45 2.3-1.1z"/>' },
  bsd: { color: "#dc2626", body:
    '<path fill="currentColor" d="M12 2 4 5v6c0 5 3.4 8.5 8 11 4.6-2.5 8-6 8-11V5l-8-3z"/>' },
  ios: { color: "#374151", body:
    '<path fill="currentColor" d="M14.4 6c.5-.7.9-1.6.8-2.5-.8 0-1.7.5-2.3 1.2-.5.6-.9 1.5-.8 2.4.9.07 1.7-.45 2.3-1.1zM16.4 12.6c0-2 1.6-2.9 1.7-3-1-1.4-2.4-1.6-2.9-1.6-1.2-.1-2.4.7-3 .7s-1.6-.7-2.6-.7c-1.3 0-2.6.8-3.3 2-1.4 2.4-.4 6 1 8 .7 1 1.5 2.1 2.5 2 1-.04 1.4-.65 2.6-.65s1.5.65 2.6.63c1.1-.02 1.8-1 2.5-2 .5-.7.7-1.1 1-1.9-2.6-1-2.6-3.8-1.6-3.5z"/>' },
  android: { color: "#16a34a", body:
    '<path fill="currentColor" d="M7 10h10v7a1 1 0 0 1-1 1h-1v2.2a1 1 0 1 1-2 0V18h-2v2.2a1 1 0 1 1-2 0V18H8a1 1 0 0 1-1-1zM4.5 10a1 1 0 0 1 1 1v4.5a1 1 0 1 1-2 0V11a1 1 0 0 1 1-1zm15 0a1 1 0 0 1 1 1v4.5a1 1 0 1 1-2 0V11a1 1 0 0 1 1-1zM8.3 5.1l-.9-1.4a.4.4 0 0 1 .7-.4l.95 1.5a6 6 0 0 1 3.9 0l.95-1.5a.4.4 0 0 1 .7.4l-.9 1.4A5 5 0 0 1 17 9H7a5 5 0 0 1 1.3-3.9zM9.7 7.2a.7.7 0 1 0 0-1.4.7.7 0 0 0 0 1.4zm4.6 0a.7.7 0 1 0 0-1.4.7.7 0 0 0 0 1.4z"/>' },
  network: { color: "#0891b2", body:
    '<path fill="currentColor" d="M4 14h16a1 1 0 0 1 1 1v3a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-3a1 1 0 0 1 1-1zm2.5 2.2a.8.8 0 1 0 0 1.6.8.8 0 0 0 0-1.6zm3 0a.8.8 0 1 0 0 1.6.8.8 0 0 0 0-1.6zM12 3v6m0 0 2.5-2.5M12 9 9.5 6.5"/><path fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" d="M12 3v6m0 0 2.5-2.5M12 9 9.5 6.5"/>' },
  printer: { color: "#7c3aed", body:
    '<path fill="currentColor" d="M7 3h10v4H7zM5 8h14a2 2 0 0 1 2 2v5a1 1 0 0 1-1 1h-2v3a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1v-3H4a1 1 0 0 1-1-1v-5a2 2 0 0 1 2-2zm3 8h8v4H8zm9-4.6a.9.9 0 1 0 0 1.8.9.9 0 0 0 0-1.8z"/>' },
  storage: { color: "#0d9488", body:
    '<path fill="currentColor" d="M5 4h14a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1zm2 2.4a.9.9 0 1 0 0 1.8.9.9 0 0 0 0-1.8zM5 13h14a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-4a1 1 0 0 1 1-1zm2 2.4a.9.9 0 1 0 0 1.8.9.9 0 0 0 0-1.8z"/>' },
  hypervisor: { color: "#9333ea", body:
    '<path fill="currentColor" d="M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z" opacity=".9"/>' },
  unknown: { color: "#9ca3af", body:
    '<path fill="currentColor" d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm.1 14.8a1.1 1.1 0 1 0 0 2.2 1.1 1.1 0 0 0 0-2.2zM12 6.2c-1.9 0-3.2 1.1-3.5 2.8l1.9.4c.1-.8.6-1.4 1.5-1.4.8 0 1.4.5 1.4 1.2 0 .7-.4 1-1.2 1.6-1 .7-1.4 1.3-1.3 2.5v.3h1.9v-.2c0-.7.3-1 1.1-1.6 1-.7 1.6-1.4 1.6-2.7 0-1.7-1.4-2.9-3.4-2.9z"/>' },
};

const fam = computed(() => (props.family && MAP[props.family] ? props.family : "unknown"));
const entry = computed(() => MAP[fam.value]);
</script>

<template>
  <svg :width="size" :height="size" viewBox="0 0 24 24" :style="{ color: entry.color, verticalAlign: '-2px' }"
       v-html="entry.body" aria-hidden="true" />
</template>
