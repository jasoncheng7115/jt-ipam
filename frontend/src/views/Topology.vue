<script setup lang="ts">
/**
 * 網路拓樸圖 — Cytoscape.js + cose-bilkent layout。
 *
 * Phase 3 MVP：
 *  - 節點 = device，依 type 顏色編碼
 *  - 邊 = cable / wireless / vpn，三種樣式可區分
 *  - 點節點顯示資訊
 *  - 切換 wireless / vpn 顯示
 */
import { onMounted, onUnmounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard,
  NSpace,
  NCheckbox,
  NSpin,
  NButton,
  NText,
  useMessage,
} from "naive-ui";
import { NIcon } from "naive-ui";
import { TopologyIcon } from "@/icons";
import cytoscape from "cytoscape";
import coseBilkent from "cytoscape-cose-bilkent";
import { getTopology, type TopologyData } from "@/api/topology";

cytoscape.use(coseBilkent as any);

const { t } = useI18n();
const msg = useMessage();
const containerRef = ref<HTMLDivElement | null>(null);
const includeWireless = ref(true);
const includeVpn = ref(true);
const loading = ref(false);
const selectedInfo = ref<string>("");

let cy: cytoscape.Core | null = null;

const NODE_COLOURS: Record<string, string> = {
  router: "#6366f1",
  switch: "#22c55e",
  firewall: "#ef4444",
  ap: "#3b82f6",
  server: "#6b7280",
  storage: "#f59e0b",
  ipmi: "#ec4899",
  other: "#9ca3af",
};

async function refresh() {
  loading.value = true;
  try {
    const data = await getTopology({
      includeWireless: includeWireless.value,
      includeVpn: includeVpn.value,
    });
    render(data);
  } catch {
    msg.error(t("errors.network"));
  } finally {
    loading.value = false;
  }
}

function render(data: TopologyData) {
  if (!containerRef.value) return;
  if (cy) {
    cy.destroy();
    cy = null;
  }
  cy = cytoscape({
    container: containerRef.value,
    elements: [...data.nodes, ...data.edges],
    style: [
      {
        selector: "node",
        style: {
          "background-color": ((node: any) =>
            NODE_COLOURS[node.data("type") as string] || NODE_COLOURS.other) as any,
          label: "data(label)",
          color: "#fff",
          "text-outline-color": "#0f172a",
          "text-outline-width": 1,
          "font-size": 11,
          "text-valign": "center",
          "text-halign": "center",
          width: 38,
          height: 38,
        },
      },
      {
        selector: "edge",
        style: {
          width: 2,
          "curve-style": "bezier",
          "line-color": "#94a3b8",
          "target-arrow-shape": "none",
        },
      },
      {
        selector: 'edge[kind = "cable"]',
        style: {
          "line-color": "#475569",
          width: 2,
        },
      },
      {
        selector: 'edge[kind = "wireless"]',
        style: {
          "line-color": "#3b82f6",
          "line-style": "dashed",
        },
      },
      {
        selector: 'edge[kind = "vpn"]',
        style: {
          "line-color": "#a855f7",
          "line-style": "dotted",
          width: 3,
        },
      },
      {
        selector: ":selected",
        style: {
          "border-width": 3,
          "border-color": "#fbbf24",
        },
      },
    ],
    layout: {
      name: "cose-bilkent",
      idealEdgeLength: 90,
      nodeRepulsion: 4500,
      edgeElasticity: 0.45,
      animate: false,
    } as any,
  });

  cy.on("tap", "node", (evt) => {
    const d = evt.target.data();
    selectedInfo.value = JSON.stringify(d, null, 2);
  });
  cy.on("tap", "edge", (evt) => {
    const d = evt.target.data();
    selectedInfo.value = JSON.stringify(d, null, 2);
  });
  cy.on("tap", (evt) => {
    if (evt.target === cy) {
      selectedInfo.value = "";
    }
  });
}

watch([includeWireless, includeVpn], () => {
  void refresh();
});

onMounted(refresh);
onUnmounted(() => {
  if (cy) cy.destroy();
});
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><TopologyIcon /></n-icon>
        <span>Topology</span>
      </n-space>
    </template>
    <template #header-extra>
      <n-space>
        <n-checkbox v-model:checked="includeWireless">無線連線</n-checkbox>
        <n-checkbox v-model:checked="includeVpn">VPN tunnels</n-checkbox>
        <n-button size="small" @click="refresh">重新整理</n-button>
      </n-space>
    </template>
    <n-spin :show="loading">
      <div class="topology-shell">
        <div ref="containerRef" class="cy"></div>
        <n-card v-if="selectedInfo" size="small" class="info-pane" title="Element">
          <pre>{{ selectedInfo }}</pre>
        </n-card>
      </div>
    </n-spin>
    <n-text depth="3" style="font-size: 11px">
      cable=灰實線 / wireless=藍虛線 / vpn=紫點線；按節點看 detail
    </n-text>
  </n-card>
</template>

<style scoped>
.topology-shell {
  position: relative;
  width: 100%;
  height: 70vh;
  background: rgba(127, 127, 127, 0.04);
  border-radius: 6px;
}
.cy {
  width: 100%;
  height: 100%;
}
.info-pane {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 320px;
  max-height: 60vh;
  overflow: auto;
  z-index: 10;
}
.info-pane pre {
  font-size: 11px;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
