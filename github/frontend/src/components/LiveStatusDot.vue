<script setup lang="ts">
/**
 * IP 狀態燈 — 永遠用 last_seen_* 即時算 (不信 backend 的 effective_status 快照)。
 * 閾值跟著 user preference online_grace_minutes 走。
 */
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import type { IPAddress } from "@/types";
import { classifyAddressLiveness, onlineGraceMinutes } from "@/composables/useLivenessSettings";

const { t } = useI18n();

const props = defineProps<{
  address: IPAddress;
}>();

const tip = ref<{ x: number; y: number } | null>(null);

function showTip(ev: MouseEvent) {
  tip.value = { x: ev.clientX + 14, y: ev.clientY + 16 };
}
function moveTip(ev: MouseEvent) {
  if (tip.value) {
    tip.value.x = ev.clientX + 14;
    tip.value.y = ev.clientY + 16;
  }
}
function hideTip() { tip.value = null; }

function fmtAge(ageMin: number): string {
  if (ageMin < 60) return t("live_dot.ago_minutes", { n: Math.round(ageMin) });
  if (ageMin < 1440) return t("live_dot.ago_hours", { n: Math.round(ageMin / 60) });
  return t("live_dot.ago_days", { n: Math.round(ageMin / 1440) });
}

const meta = computed(() => {
  const a = props.address;
  const ts = [
    { key: "scanner", at: a.last_seen_scanner },
    { key: "LibreNMS", at: a.last_seen_librenms },
    { key: "DNS", at: a.last_seen_dns },
  ].filter((x) => x.at) as { key: string; at: string }[];
  const newestMs = ts.length ? Math.max(...ts.map((x) => new Date(x.at).getTime())) : null;
  const kind = classifyAddressLiveness(a);
  const colorMap = {
    online: "#22c55e",
    stale: "#f59e0b",
    offline: "#ef4444",
    unknown: "rgba(127,127,127,0.55)",
  };
  const labelMap = {
    online: t("visualisation.online"),
    stale: t("visualisation.stale"),
    offline: t("visualisation.offline"),
    unknown: t("visualisation.unknown"),
  };
  let label = labelMap[kind];
  if (newestMs) {
    const ageMin = (Date.now() - newestMs) / 60000;
    label = `${labelMap[kind]}(${fmtAge(ageMin)})`;
  }
  return { color: colorMap[kind], label, ts, kind, grace: onlineGraceMinutes.value };
});

function fmt(s: string): string {
  return s.replace("T", " ").split(".")[0];
}
</script>

<template>
  <span
    class="live-dot"
    :style="{ background: meta.color, boxShadow: `0 0 6px ${meta.color}` }"
    @mouseenter="showTip"
    @mousemove="moveTip"
    @mouseleave="hideTip"
  />
  <Teleport to="body">
    <div v-if="tip" class="live-dot-tip" :style="{ left: tip.x + 'px', top: tip.y + 'px' }">
      <div class="tip-row tip-head"><span class="tip-swatch" :style="{ background: meta.color }" />{{ meta.label }}</div>
      <div v-if="meta.ts.length" class="tip-sep" />
      <div v-for="x in meta.ts" :key="x.key" class="tip-row">
        <span class="tip-src">{{ x.key }}</span><span class="tip-ts">{{ fmt(x.at) }}</span>
      </div>
      <div v-if="!meta.ts.length" class="tip-row tip-empty">{{ t("live_dot.no_records") }}</div>
      <div class="tip-sep" />
      <div class="tip-row" style="font-size: 11px; opacity: 0.55;">
        {{ t("live_dot.online_threshold", { n: meta.grace }) }}
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.live-dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  cursor: help;
}
</style>

<style>
.live-dot-tip {
  position: fixed;
  z-index: 9999;
  background: rgba(0, 0, 0, 0.9);
  color: #fff;
  font-size: 12px;
  padding: 6px 10px;
  border-radius: 4px;
  pointer-events: none;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", monospace;
  min-width: 160px;
}
.live-dot-tip .tip-row {
  display: flex;
  align-items: center;
  gap: 8px;
  line-height: 1.5;
}
.live-dot-tip .tip-head {
  font-weight: 600;
}
.live-dot-tip .tip-swatch {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}
.live-dot-tip .tip-sep {
  height: 1px;
  background: rgba(255, 255, 255, 0.18);
  margin: 4px 0;
}
.live-dot-tip .tip-src {
  opacity: 0.75;
  min-width: 70px;
}
.live-dot-tip .tip-ts {
  font-family: monospace;
}
.live-dot-tip .tip-empty {
  opacity: 0.7;
  font-style: italic;
}
</style>
