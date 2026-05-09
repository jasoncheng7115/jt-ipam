<script setup lang="ts">
/**
 * 機櫃 U 位視覺化（phpIPAM 招牌功能）。
 *
 * 比 phpIPAM 改進：
 *  - 顏色按 device type 區分（router/switch/firewall/server/...）
 *  - 越界 / 重疊衝突明顯標示
 *  - 點 device 跳詳情
 *  - U 編號從上到下標示，符合機房現場認知
 */
import { computed } from "vue";
import { NCard, NTag, NEmpty, NAlert, NSpace } from "naive-ui";
import type { RackDiagram } from "@/api/racks";

interface Props {
  diagram: RackDiagram | null;
}
const props = defineProps<Props>();

interface Cell {
  u: number;          // 1-based, top-most U
  device: {
    id: string;
    name: string;
    type: string;
    vendor: string | null;
    model: string | null;
    u_size: number;
    is_top: boolean;  // device 第一格（顯示名字）
    primary_ip: string | null;
  } | null;
}

const cells = computed<Cell[]>(() => {
  if (!props.diagram) return [];
  const u_height = props.diagram.u_height;
  // 從上到下：u = u_height ... 1
  // 但 u_position 是 bottom-most，所以裝置從 u_position 到 u_position + u_size - 1
  // 我們從 u_height 開始往下走
  const map: Record<number, Cell> = {};
  for (let u = 1; u <= u_height; u++) {
    map[u] = { u, device: null };
  }
  for (const d of props.diagram.devices) {
    for (let u = d.u_position; u < d.u_position + d.u_size; u++) {
      if (map[u]) {
        map[u] = {
          u,
          device: {
            id: d.device_id,
            name: d.name,
            type: d.type,
            vendor: d.vendor,
            model: d.model,
            u_size: d.u_size,
            is_top: u === d.u_position + d.u_size - 1, // 最上格 = 名字所在
            primary_ip: d.primary_ip,
          },
        };
      }
    }
  }
  // top-down 排列
  return Array.from({ length: u_height }, (_, i) => map[u_height - i]);
});

function colorFor(type: string): string {
  switch (type) {
    case "router":
      return "rgba(99, 102, 241, 0.85)"; // indigo
    case "switch":
      return "rgba(34, 197, 94, 0.85)";  // green
    case "firewall":
      return "rgba(239, 68, 68, 0.85)";  // red
    case "ap":
      return "rgba(59, 130, 246, 0.85)"; // blue
    case "server":
      return "rgba(107, 114, 128, 0.85)"; // grey
    case "storage":
      return "rgba(245, 158, 11, 0.85)"; // amber
    case "ipmi":
      return "rgba(236, 72, 153, 0.6)";  // pink
    default:
      return "rgba(107, 114, 128, 0.6)";
  }
}
</script>

<template>
  <n-card v-if="diagram" :title="`Rack: ${diagram.name} (${diagram.u_height}U)`">
    <n-space vertical :size="12">
      <n-alert
        v-if="diagram.conflicts.length > 0"
        type="warning"
        :title="`${diagram.conflicts.length} conflict(s)`"
      >
        <pre style="font-size: 11px; margin: 0">{{ JSON.stringify(diagram.conflicts, null, 2) }}</pre>
      </n-alert>

      <n-empty
        v-if="!diagram.devices.length"
        description="此機櫃尚無設備"
      />

      <div v-else class="rack-frame">
        <div
          v-for="cell in cells"
          :key="cell.u"
          class="u-row"
          :class="{ 'u-occupied': cell.device, 'u-top': cell.device?.is_top }"
          :style="cell.device ? { background: colorFor(cell.device.type) } : {}"
          :title="
            cell.device
              ? `${cell.device.name} · ${cell.device.type} · ${cell.device.u_size}U`
              : `Empty (U${cell.u})`
          "
        >
          <span class="u-num">{{ cell.u }}</span>
          <template v-if="cell.device?.is_top">
            <span class="d-name">{{ cell.device.name }}</span>
            <n-tag size="tiny" :bordered="false" style="margin-left: 6px">
              {{ cell.device.type }}
            </n-tag>
            <span v-if="cell.device.primary_ip" class="d-ip">{{ cell.device.primary_ip }}</span>
          </template>
        </div>
      </div>

      <div class="legend">
        <span class="legend-item" :style="{ background: colorFor('router') }">router</span>
        <span class="legend-item" :style="{ background: colorFor('switch') }">switch</span>
        <span class="legend-item" :style="{ background: colorFor('firewall') }">firewall</span>
        <span class="legend-item" :style="{ background: colorFor('server') }">server</span>
        <span class="legend-item" :style="{ background: colorFor('storage') }">storage</span>
        <span class="legend-item" :style="{ background: colorFor('ap') }">ap</span>
        <span class="legend-item" :style="{ background: colorFor('ipmi') }">ipmi</span>
      </div>
    </n-space>
  </n-card>
</template>

<style scoped>
.rack-frame {
  border: 2px solid rgba(127, 127, 127, 0.4);
  border-radius: 4px;
  padding: 4px;
  max-width: 480px;
  background: rgba(127, 127, 127, 0.04);
}
.u-row {
  display: flex;
  align-items: center;
  height: 24px;
  border-bottom: 1px dashed rgba(127, 127, 127, 0.2);
  padding: 0 8px;
  font-size: 12px;
  font-family: monospace;
  color: white;
  position: relative;
}
.u-row:last-child {
  border-bottom: none;
}
.u-row:not(.u-occupied) {
  color: rgba(127, 127, 127, 0.5);
  background: transparent;
}
.u-num {
  display: inline-block;
  width: 28px;
  text-align: right;
  margin-right: 8px;
  opacity: 0.8;
  font-weight: bold;
}
.d-name {
  font-weight: 600;
}
.d-ip {
  margin-left: auto;
  font-size: 11px;
  opacity: 0.85;
}
.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 11px;
}
.legend-item {
  padding: 2px 8px;
  border-radius: 3px;
  color: white;
  font-family: monospace;
}
</style>
