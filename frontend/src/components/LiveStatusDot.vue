<script setup lang="ts">
/**
 * IP 狀態燈 — 永遠用 last_seen_* 即時算 (不信 backend 的 effective_status 快照)。
 * 閾值跟著 user preference online_grace_minutes 走。
 */
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import type { IPAddress } from "@/types";
import { classifyAddressLiveness, isArpOnlyEvidence, onlineGraceMinutes } from "@/composables/useLivenessSettings";
import { fmtDateTime } from "@/utils/datetime";
import { fwSeenLabel } from "@/utils/investigateSections";

const { t } = useI18n();

const props = withDefaults(defineProps<{
  address: IPAddress;
  /** 燈號直徑（px）；清單用預設 10，IP 詳情標題字比較大用 12 */
  size?: number;
}>(), { size: 10 });

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
  // 依據：各來源最後看到的時間。防火牆逐來源、設備介面位址、「跟著設備」（arp_seen）也要列出來 ——
  // 以前只列三種，燈號是綠的卻看不出是誰說它在線
  const ts = [
    { key: "scanner", at: a.last_seen_scanner },
    { key: "LibreNMS", at: a.last_seen_librenms },
    { key: "Wazuh", at: a.last_seen_wazuh },
    { key: "Zabbix", at: a.last_seen_zabbix },
    { key: "Checkmk", at: a.last_seen_checkmk },
    { key: "ARP", at: a.last_seen_arp },
    ...Object.entries(a.arp_seen || {}).map(([k, at]) => ({
      key: k === "device"
        ? (a.device_evidence_ip ? t("live_dot.via_device", { ip: a.device_evidence_ip }) : t("live_dot.via_device_librenms"))
        : fwSeenLabel(k, t),
      at,
    })),
  ].filter((x) => x.at) as { key: string; at: string }[];
  // 勾了「上線狀態跟著設備」卻沒掛設備：不回應探測不算離線，要講清楚為什麼是灰的
  const silent = !!a.liveness_from_device && !a.device_id;
  // 只有 ARP 撐著 → 綠燈的可信度與實際探測不同，要標出來（ARP 沒有時間概念）
  const arpOnly = isArpOnlyEvidence(a);
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
  if (arpOnly && kind === "online") label = t("live_dot.arp_only_label");
  if (silent && kind === "unknown") label = t("live_dot.silent_label");
  return {
    // 只有 ARP 時用比較淡的綠：仍然算上線，但不要跟「實際探測到」長得一模一樣
    color: arpOnly && kind === "online" ? "#84cc16" : colorMap[kind],
    label, ts, kind, grace: onlineGraceMinutes.value, arpOnly, silent: silent && kind === "unknown",
  };
});
</script>

<template>
  <span
    class="live-dot"
    :style="{ background: meta.color, boxShadow: `0 0 6px ${meta.color}`, width: `${size}px`, height: `${size}px` }"
    @mouseenter="showTip"
    @mousemove="moveTip"
    @mouseleave="hideTip"
  />
  <Teleport to="body">
    <div v-if="tip" class="live-dot-tip" :style="{ left: tip.x + 'px', top: tip.y + 'px' }">
      <div class="tip-row tip-head"><span class="tip-swatch" :style="{ background: meta.color }" />{{ meta.label }}</div>
      <div v-if="meta.ts.length" class="tip-sep" />
      <div v-for="x in meta.ts" :key="x.key" class="tip-row">
        <span class="tip-src">{{ x.key }}</span><span class="tip-ts">{{ fmtDateTime(x.at) }}</span>
      </div>
      <div v-if="!meta.ts.length" class="tip-row tip-empty">{{ t("live_dot.no_records") }}</div>
      <div v-if="meta.arpOnly" class="tip-sep" />
      <div v-if="meta.arpOnly" class="tip-row tip-warn">{{ t("live_dot.arp_only_hint") }}</div>
      <div v-if="meta.silent" class="tip-sep" />
      <div v-if="meta.silent" class="tip-row tip-warn" data-testid="live-dot-silent">{{ t("live_dot.silent_hint") }}</div>
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
  flex: none;
  border-radius: 50%;
  cursor: help;
}
</style>

<style>
.live-dot-tip .tip-warn {
  color: #fbbf24;
  max-width: 260px;
  white-space: normal;
  line-height: 1.5;
}
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
