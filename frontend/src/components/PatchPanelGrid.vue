<script setup lang="ts">
/**
 * 跳接面板的埠位（交接包 physical-v1.2 設計圖 06）：正面一排、背面一排，點一個埠就標出它登錄的背面（或正面）對應，
 * 旁邊顯示兩邊的線標與內部對應有沒有登錄。對應只看登錄的配對，不用排序位置去猜（F1 不一定對 R1）。
 * 正面 / 背面是面板的埠面，跟整台設備裝在機櫃哪一面無關。
 */
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import { NButton, NIcon, NTag } from "naive-ui";
import type { DevicePort } from "@/api/phase3";
import { LinkIcon } from "@/icons";

const props = defineProps<{ ports: DevicePort[] }>();
const emit = defineEmits<{ (e: "trace", port: DevicePort): void }>();
const { t } = useI18n();

const natural = (a: DevicePort, b: DevicePort) =>
  (a.position ?? 1e9) - (b.position ?? 1e9) || a.name.localeCompare(b.name, undefined, { numeric: true });
const front = computed(() => props.ports.filter((p) => p.type === "front").sort(natural));
const rear = computed(() => props.ports.filter((p) => p.type === "rear").sort(natural));
const byId = computed(() => new Map(props.ports.map((p) => [p.id, p])));
const selected = ref<DevicePort | null>(null);
const peer = computed(() => (selected.value?.peer_port_id ? byId.value.get(selected.value.peer_port_id) ?? null : null));
// 配對要兩邊互指才算登錄完整；只有一邊指過去＝不對稱
const pairState = computed(() => {
  if (!selected.value) return null;
  if (!peer.value) return "none";
  return peer.value.peer_port_id === selected.value.id ? "ok" : "asym";
});
const frontPort = computed(() => (selected.value?.type === "front" ? selected.value : peer.value));
const rearPort = computed(() => (selected.value?.type === "rear" ? selected.value : peer.value));

function cls(p: DevicePort) {
  return {
    "pg-port": true,
    "pg-sel": selected.value?.id === p.id,
    "pg-peer": peer.value?.id === p.id,
    "pg-cabled": !!p.cable_label || p.cable_status === "connected",
  };
}
</script>

<template>
  <div class="pg-wrap" data-testid="panel-grid">
    <div class="pg-faces">
      <div class="pg-face">
        <div class="pg-title">{{ t("panel_grid.front") }}</div>
        <div class="pg-row">
          <button v-for="p in front" :key="p.id" :class="cls(p)" :data-testid="`pg-${p.name}`" @click="selected = p">{{ p.name }}</button>
          <span v-if="!front.length" class="hint">—</span>
        </div>
      </div>
      <div class="pg-face">
        <div class="pg-title">{{ t("panel_grid.rear") }}</div>
        <div class="pg-row">
          <button v-for="p in rear" :key="p.id" :class="cls(p)" :data-testid="`pg-${p.name}`" @click="selected = p">{{ p.name }}</button>
          <span v-if="!rear.length" class="hint">—</span>
        </div>
      </div>
      <div class="hint">{{ t("panel_grid.hint") }}</div>
    </div>
    <div class="pg-side" data-testid="panel-grid-side">
      <template v-if="selected">
        <div class="hint">{{ t("panel_grid.selected") }}</div>
        <div class="pg-pair">{{ frontPort?.name ?? "?" }} ↔ {{ rearPort?.name ?? "?" }}</div>
        <div class="pg-line"><span>{{ t("panel_grid.front_cable") }}</span><b>{{ frontPort?.cable_label || (frontPort?.link ? t("physical_check.no_label") : "—") }}</b></div>
        <div class="pg-line"><span>{{ t("panel_grid.rear_cable") }}</span><b>{{ rearPort?.cable_label || (rearPort?.link ? t("physical_check.no_label") : "—") }}</b></div>
        <div class="pg-line"><span>{{ t("panel_grid.mapping") }}</span>
          <n-tag size="small" :bordered="false" :type="pairState === 'ok' ? 'success' : 'warning'" data-testid="pg-pair-state">
            {{ t(`panel_grid.state_${pairState}`) }}
          </n-tag>
        </div>
        <n-button size="small" type="info" style="margin-top: 10px" data-testid="pg-trace" @click="emit('trace', selected)">
          <template #icon><n-icon><LinkIcon /></n-icon></template>{{ t("panel_grid.trace") }}
        </n-button>
      </template>
      <div v-else class="hint">{{ t("panel_grid.pick") }}</div>
    </div>
  </div>
</template>

<style scoped>
.hint { font-size: 12px; opacity: .7; }
.pg-wrap { display: grid; grid-template-columns: minmax(0, 3fr) minmax(220px, 1fr); gap: 14px; }
@media (max-width: 800px) { .pg-wrap { grid-template-columns: 1fr; } }
.pg-face { margin-bottom: 12px; }
.pg-title { font-weight: 600; margin-bottom: 6px; }
.pg-row { display: flex; flex-wrap: wrap; gap: 6px; }
.pg-port { min-width: 52px; padding: 5px 8px; border-radius: 6px; border: 1px solid rgba(127, 127, 127, .35);
           background: rgba(127, 127, 127, .06); color: inherit; cursor: pointer; font: inherit; font-size: 12.5px; }
.pg-cabled { background: rgba(24, 160, 88, .14); }
.pg-peer { border-color: #18a058; }
.pg-sel { border: 2px solid #18a058; font-weight: 700; }
.pg-side { padding: 12px; border-radius: 6px; background: rgba(127, 127, 127, .06); }
.pg-pair { font-size: 22px; font-weight: 700; margin: 4px 0 10px; }
.pg-line { display: flex; justify-content: space-between; gap: 8px; margin: 6px 0; }
</style>
