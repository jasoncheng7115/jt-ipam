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
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard,
  NSpace,
  NCheckbox,
  NSpin,
  NButton,
  NButtonGroup,
  NDropdown,
  NSelect,
  useMessage,
} from "naive-ui";
import { NIcon } from "naive-ui";
import { TopologyIcon, RefreshIcon, FitIcon, ExportIcon } from "@/icons";
import { useRouter } from "vue-router";
import cytoscape from "cytoscape";
import coseBilkent from "cytoscape-cose-bilkent";
import { getTopology, type TopologyData } from "@/api/topology";
import { listSubnets } from "@/api/subnets";
import { usePinnedSubnets } from "@/composables/usePinnedSubnets";

cytoscape.use(coseBilkent as any);

const { t } = useI18n();
const msg = useMessage();
const containerRef = ref<HTMLDivElement | null>(null);
const includeWireless = ref(true);
const includeVpn = ref(true);
const includeL3 = ref(true);
const onlineOnly = ref(false);   // 預設：不管上線與否都畫
const loading = ref(false);
const selected = ref<Record<string, any> | null>(null);

// 友善欄位名稱（中文），對應 cytoscape node/edge data 的 key
const FIELD_LABELS = computed<Record<string, string>>(() => ({
  label: t("cols.name"),
  type: t("topology.field_type"),
  vendor: t("topology.field_vendor"),
  model: t("topology.field_model"),
  kind: t("topology.field_kind"),
  b_endpoint: t("topology.field_b_endpoint"),
  ip: "IP",
  mac: "MAC",
  serial: t("topology.field_serial"),
  rack: t("topology.field_rack"),
  location: t("topology.field_location"),
  os: t("topology.field_os"),
  hardware: t("topology.field_hardware"),
  sw_version: t("topology.field_sw_version"),
  sysname: t("topology.field_sysname"),
  status: t("topology.field_status"),
  description: t("topology.field_description"),
  via: t("topology.field_via"),
}));
const VIA_LABELS = computed<Record<string, string>>(() => ({
  ip: t("topology.via_ip"),
  name: t("topology.via_name"),
  arp: t("topology.via_arp"),
  librenms: t("topology.via_librenms"),
}));
const TYPE_LABELS = computed<Record<string, string>>(() => ({
  router: t("topology.type_router"),
  switch: t("topology.type_switch"),
  firewall: t("topology.type_firewall"),
  ap: t("topology.type_ap"),
  server: t("topology.type_server"),
  storage: t("topology.type_storage"),
  ipmi: "IPMI",
  other: t("topology.type_other"),
  subnet: t("topology.type_subnet"),
  vpn_site: t("topology.type_vpn_site"),
}));
const KIND_LABELS = computed<Record<string, string>>(() => ({
  cable: t("topology.kind_cable"),
  wireless: t("topology.kind_wireless"),
  vpn: t("topology.kind_vpn"),
  l3: t("topology.kind_l3"),
}));
// 內部欄位不顯示給使用者看
const HIDDEN_FIELDS = new Set([
  "id", "source", "target", "a_device_id", "b_device_id",
  "rack_id", "location_id", "subnet_uuid", "label",
]);

function displayValue(key: string, val: any): string {
  if (key === "type") return TYPE_LABELS.value[val] ?? String(val);
  if (key === "kind") return KIND_LABELS.value[val] ?? String(val);
  if (key === "via") {
    return String(val).split(",").map((v) => VIA_LABELS.value[v] ?? v).join("、");
  }
  if (key === "status") {
    if (val === "up") return t("topology.status_up");
    if (val === "down") return t("topology.status_down");
  }
  return String(val);
}

const selectedRows = computed(() => {
  const d = selected.value;
  if (!d) return [];
  return Object.keys(d)
    .filter((k) => !HIDDEN_FIELDS.has(k) && d[k] != null && d[k] !== "")
    .map((k) => ({ key: k, label: FIELD_LABELS.value[k] ?? k, value: displayValue(k, d[k]) }));
});
const selectedTitle = computed(() =>
  selected.value ? (selected.value.label ?? selected.value.id ?? t("topology.element")) : "",
);
const router = useRouter();
// 是 device 節點（非 subnet:/vpnsite: 合成節點）→ 可連到裝置頁
const selectedDeviceId = computed<string | null>(() => {
  const d = selected.value;
  if (!d || typeof d.id !== "string") return null;
  if (d.id.includes(":")) return null;            // subnet:/vpnsite:
  if (d.type === "subnet" || d.type === "vpn_site") return null;
  return d.id;
});
const selectedSubnetId = computed<string | null>(() => {
  const d = selected.value;
  return d && d.type === "subnet" && d.subnet_uuid ? String(d.subnet_uuid) : null;
});
function goDevice() {
  if (selectedDeviceId.value) router.push({ name: "device-detail", params: { id: selectedDeviceId.value } });
}
function goSubnet() {
  if (selectedSubnetId.value) router.push({ name: "subnet-detail", params: { id: selectedSubnetId.value } });
}
const subnetIds = ref<string[]>([]);
const subnetOptions = ref<{ label: string; value: string }[]>([]);

async function loadSubnetOptions() {
  try {
    const r = await listSubnets({ page: 1, pageSize: 500 });
    subnetOptions.value = r.items.map((s) => ({
      label: s.description ? `${s.cidr} — ${s.description}` : s.cidr,
      value: s.id,
    }));
  } catch { /* silent */ }
}

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
  subnet: "#0ea5e9",  // L3 subnet 節點 — 青藍色，跟 device 區分
  vpn_site: "#9333ea",  // site-to-site VPN 遠端站點 — 紫色（跟 vpn 邊一致）
};

const { pinned, ensureLoaded } = usePinnedSubnets();

// 圖例可點：點暗某類別 → 圖上隱藏該類節點（及其連線）
// 預設先把「伺服器 / 其他」這類點暗——它們數量最多、最會把網路骨幹（防火牆/路由器/
// 交換器/AP/子網路/VPN）洗掉。使用者點圖例即可重新顯示。
const hiddenTypes = ref<Set<string>>(new Set(["server", "storage", "ipmi", "other"]));
const LEGEND_GROUPS: Record<string, string[]> = {
  firewall: ["firewall"], router: ["router"], switch: ["switch"], ap: ["ap"],
  server: ["server", "storage", "ipmi", "other"], vpn_site: ["vpn_site"], subnet: ["subnet"],
};
function isGroupOff(group: string): boolean {
  return (LEGEND_GROUPS[group] || [group]).every((ty) => hiddenTypes.value.has(ty));
}
function toggleGroup(group: string) {
  const types = LEGEND_GROUPS[group] || [group];
  const off = isGroupOff(group);
  const next = new Set(hiddenTypes.value);
  for (const ty of types) { if (off) next.delete(ty); else next.add(ty); }
  hiddenTypes.value = next;
  applyVisibility();
}
function applyVisibility() {
  if (!cy) return;
  cy.batch(() => {
    cy!.nodes().forEach((n) => {
      n.style("display", hiddenTypes.value.has(n.data("type") as string) ? "none" : "element");
    });
    cy!.edges().forEach((e) => {
      const hide = e.source().style("display") === "none" || e.target().style("display") === "none";
      e.style("display", hide ? "none" : "element");
    });
  });
}
function zoomBy(f: number) {
  if (!cy) return;
  cy.zoom({ level: cy.zoom() * f, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
}
function fitView() { if (cy) cy.fit(undefined, 30); }

// ── 匯出：PNG(cytoscape 內建) / SVG / draw.io(依節點座標重建) ──
const EDGE_STYLE: Record<string, { color: string; width: number; dash: string }> = {
  cable: { color: "#475569", width: 2, dash: "" },
  wireless: { color: "#3b82f6", width: 2, dash: "6,4" },
  vpn: { color: "#9333ea", width: 4, dash: "10,5" },
  l3: { color: "#0ea5e9", width: 1.5, dash: "5,3" },
};
function dlBlob(blob: Blob, name: string) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = name; a.click();
  URL.revokeObjectURL(a.href);
}
function escXml(s: string): string {
  return String(s ?? "").replace(/[<>&'"]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", "'": "&apos;", '"': "&quot;" }[c] as string));
}
function visibleEls(): { nodes: any[]; edges: any[] } {
  const nodes = cy!.nodes().filter((n) => n.style("display") !== "none").toArray() as any[];
  const edges = cy!.edges().filter((e) => e.style("display") !== "none").toArray() as any[];
  return { nodes, edges };
}
function exportPng() {
  if (!cy) return;
  const blob = cy.png({ full: true, scale: 2, bg: "#ffffff", output: "blob" }) as Blob;
  dlBlob(blob, "ip-topology.png");
}
function exportSvg() {
  if (!cy) return;
  const { nodes, edges } = visibleEls();
  if (!nodes.length) return;
  const R = 19, pad = 50;
  const pos = nodes.map((n) => n.position());
  const minX = Math.min(...pos.map((p) => p.x)), maxX = Math.max(...pos.map((p) => p.x));
  const minY = Math.min(...pos.map((p) => p.y)), maxY = Math.max(...pos.map((p) => p.y));
  const W = (maxX - minX) + pad * 2, H = (maxY - minY) + pad * 2;
  const X = (x: number) => x - minX + pad, Y = (y: number) => y - minY + pad;
  let s = `<svg xmlns="http://www.w3.org/2000/svg" width="${Math.round(W)}" height="${Math.round(H)}" viewBox="0 0 ${Math.round(W)} ${Math.round(H)}" font-family="sans-serif"><rect width="100%" height="100%" fill="#ffffff"/>`;
  edges.forEach((e) => {
    const sp = e.source().position(), tp = e.target().position();
    const st = EDGE_STYLE[e.data("kind") as string] || { color: "#94a3b8", width: 2, dash: "" };
    s += `<line x1="${X(sp.x)}" y1="${Y(sp.y)}" x2="${X(tp.x)}" y2="${Y(tp.y)}" stroke="${st.color}" stroke-width="${st.width}"${st.dash ? ` stroke-dasharray="${st.dash}"` : ""}/>`;
  });
  nodes.forEach((n) => {
    const p = n.position(); const fill = NODE_COLOURS[n.data("type") as string] || NODE_COLOURS.other;
    s += `<circle cx="${X(p.x)}" cy="${Y(p.y)}" r="${R}" fill="${fill}"/>`;
    s += `<text x="${X(p.x)}" y="${Y(p.y) + 3}" font-size="10" font-weight="600" text-anchor="middle" fill="#fff" paint-order="stroke" stroke="#0f172a" stroke-width="0.6">${escXml(n.data("label"))}</text>`;
  });
  s += `</svg>`;
  dlBlob(new Blob([s], { type: "image/svg+xml" }), "ip-topology.svg");
}
function exportDrawio() {
  if (!cy) return;
  const { nodes, edges } = visibleEls();
  if (!nodes.length) return;
  const pos = nodes.map((n) => n.position());
  const minX = Math.min(...pos.map((p) => p.x)), minY = Math.min(...pos.map((p) => p.y));
  const cells: string[] = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>'];
  const id: Record<string, string> = {};
  nodes.forEach((n, i) => {
    id[n.id()] = `n${i}`; const p = n.position();
    const fill = NODE_COLOURS[n.data("type") as string] || NODE_COLOURS.other;
    cells.push(`<mxCell id="n${i}" value="${escXml(n.data("label"))}" style="ellipse;whiteSpace=wrap;html=1;fillColor=${fill};strokeColor=#0f172a;fontColor=#ffffff;" vertex="1" parent="1"><mxGeometry x="${Math.round(p.x - minX + 40)}" y="${Math.round(p.y - minY + 40)}" width="56" height="56" as="geometry"/></mxCell>`);
  });
  edges.forEach((e, i) => {
    const st = EDGE_STYLE[e.data("kind") as string] || { color: "#94a3b8", width: 2, dash: "" };
    if (!id[e.source().id()] || !id[e.target().id()]) return;
    const style = `endArrow=none;html=1;strokeColor=${st.color};strokeWidth=${st.width};${st.dash ? "dashed=1;" : ""}`;
    cells.push(`<mxCell id="e${i}" style="${style}" edge="1" parent="1" source="${id[e.source().id()]}" target="${id[e.target().id()]}"><mxGeometry relative="1" as="geometry"/></mxCell>`);
  });
  const xml = `<mxfile host="jt-ipam"><diagram name="IP topology"><mxGraphModel dx="800" dy="600" grid="1" gridSize="10" guides="1" connect="1" arrows="1" page="1" pageScale="1"><root>${cells.join("")}</root></mxGraphModel></diagram></mxfile>`;
  dlBlob(new Blob([xml], { type: "application/xml" }), "ip-topology.drawio");
}
const exportOptions = [
  { label: "PNG", key: "png" },
  { label: "SVG", key: "svg" },
  { label: "draw.io", key: "drawio" },
];
function onExport(key: string) {
  if (!cy) { msg.warning(t("errors.network")); return; }
  if (key === "png") exportPng();
  else if (key === "svg") exportSvg();
  else if (key === "drawio") exportDrawio();
}

async function refresh() {
  loading.value = true;
  try {
    const data = await getTopology({
      includeWireless: includeWireless.value,
      includeVpn: includeVpn.value,
      includeL3: includeL3.value,
      onlineOnly: onlineOnly.value,
      subnetIds: subnetIds.value,
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
          "line-color": "#9333ea",
          "line-style": "dashed",
          "line-dash-pattern": [10, 5],
          width: 4,
          // 中間標 tunnel 類型；兩端各自標「該端的對外位址」，貼在各自節點旁，不互蓋
          label: "data(type)",
          "source-label": "data(a_endpoint)",
          "target-label": "data(b_endpoint)",
          "source-text-offset": 46 as any,
          "target-text-offset": 46 as any,
          "font-size": 10,
          "font-weight": "bold",
          color: "#6b21a8",
          "text-rotation": 0 as any,
          "text-background-color": "#ffffff",
          "text-background-opacity": 1,
          "text-background-padding": "3px",
          "text-background-shape": "roundrectangle",
          "text-border-color": "#9333ea",
          "text-border-width": 1,
          "text-border-opacity": 1,
          "z-index": 9999,
        },
      },
      {
        selector: 'edge[kind = "l3"]',
        style: {
          "line-color": "#0ea5e9",
          "line-style": "dashed",
          width: 1.5,
          opacity: 0.7,
        },
      },
      {
        selector: 'node[type = "subnet"]',
        style: {
          shape: "round-rectangle",
          width: 70,
          height: 28,
          "font-size": 10,
          "font-weight": "bold",
        },
      },
      {
        selector: 'node[type = "vpn_site"]',
        style: {
          shape: "diamond",
          width: 44,
          height: 44,
          "font-size": 10,
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
      // 拉長邊、加大斥力，並把「標籤尺寸」算進節點碰撞 → 節點與邊上的文字
      // （VPN 端點 / tunnel 名 / IP）才不會疊在一起。
      idealEdgeLength: 150,
      nodeRepulsion: 9000,
      edgeElasticity: 0.4,
      nodeDimensionsIncludeLabels: true,
      gravity: 0.25,
      animate: false,
    } as any,
  });

  cy.on("tap", "node", (evt) => {
    selected.value = { ...evt.target.data() };
  });
  cy.on("tap", "edge", (evt) => {
    selected.value = { ...evt.target.data() };
  });
  cy.on("tap", (evt) => {
    if (evt.target === cy) {
      selected.value = null;
    }
  });
  applyVisibility();   // 套用圖例的點暗（隱藏類別）狀態
}

watch(subnetIds, () => { void refresh(); });
watch([includeWireless, includeVpn, includeL3, onlineOnly], () => {
  void refresh();
});

onMounted(async () => {
  await loadSubnetOptions();
  // 有設常用子網路 → 進來預設只畫這些；否則畫全部
  let usedPinned = false;
  try {
    await ensureLoaded();
    if (pinned.value.length) { subnetIds.value = [...pinned.value]; usedPinned = true; }
  } catch { /* ignore */ }
  // 設了 subnetIds 會觸發 watch → refresh；沒設才在這裡主動畫一次
  if (!usedPinned) void refresh();
});
onUnmounted(() => {
  if (cy) cy.destroy();
});
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><TopologyIcon /></n-icon>
        <span>{{ t("nav.topology") }}</span>
      </n-space>
    </template>
    <template #header-extra>
      <n-space :size="8" :wrap-item="false">
        <n-dropdown trigger="click" :options="exportOptions" @select="onExport">
          <n-button size="small">
            <template #icon><n-icon><ExportIcon /></n-icon></template>
            {{ t("common.export") }}
          </n-button>
        </n-dropdown>
        <n-button @click="refresh" size="small">
          <template #icon><n-icon><RefreshIcon /></n-icon></template>
          {{ t("common.refresh") }}
        </n-button>
      </n-space>
    </template>
    <n-space class="topo-toolbar" align="center" :wrap="true" style="margin-bottom: 12px; row-gap: 8px">
      <n-select
        v-model:value="subnetIds"
        :options="subnetOptions"
        multiple filterable clearable
        :placeholder="t('topology.filter_subnets')"
        style="min-width: 240px; max-width: 460px"
        :consistent-menu-width="false"
        :max-tag-count="2"
      />
        <n-checkbox v-model:checked="includeWireless">{{ t("topology.wireless") }}</n-checkbox>
        <n-checkbox v-model:checked="includeVpn">{{ t("topology.vpn") }}</n-checkbox>
        <n-checkbox v-model:checked="includeL3">{{ t("topology.l3") }}</n-checkbox>
        <n-checkbox v-model:checked="onlineOnly">{{ t("topology.online_only") }}</n-checkbox>
        <n-button-group>
          <n-button @click="zoomBy(1.2)" :title="t('topology.zoom_in')">＋</n-button>
          <n-button @click="zoomBy(0.83)" :title="t('topology.zoom_out')">－</n-button>
          <n-button @click="fitView">
            <template #icon><n-icon><FitIcon /></n-icon></template>
            {{ t("topology.fit") }}
          </n-button>
        </n-button-group>
      </n-space>
    <n-spin :show="loading">
      <div class="topology-shell">
        <div ref="containerRef" class="cy"></div>
        <n-card v-if="selected" size="small" class="info-pane" :title="selectedTitle" closable @close="selected = null">
          <table class="info-table">
            <tbody>
              <tr v-for="row in selectedRows" :key="row.key">
                <th>{{ row.label }}</th>
                <td>{{ row.value }}</td>
              </tr>
            </tbody>
          </table>
          <template v-if="selectedDeviceId || selectedSubnetId" #action>
            <n-button v-if="selectedDeviceId" size="small" type="primary" ghost block @click="goDevice">
              {{ t("topology.open_device") }}
            </n-button>
            <n-button v-else size="small" type="primary" ghost block @click="goSubnet">
              {{ t("topology.open_subnet") }}
            </n-button>
          </template>
        </n-card>
      </div>
    </n-spin>
    <div class="topo-legend">
      <span class="lg lg-head">{{ t("topology.legend_links") }}</span>
      <span class="lg"><svg width="26" height="10"><line x1="0" y1="5" x2="26" y2="5" stroke="#475569" stroke-width="2"/></svg>{{ t("topology.kind_cable") }}</span>
      <span class="lg"><svg width="26" height="10"><line x1="0" y1="5" x2="26" y2="5" stroke="#3b82f6" stroke-width="2" stroke-dasharray="5,3"/></svg>{{ t("topology.kind_wireless") }}</span>
      <span class="lg"><svg width="26" height="10"><line x1="0" y1="5" x2="26" y2="5" stroke="#9333ea" stroke-width="4" stroke-dasharray="8,4"/></svg>{{ t("topology.kind_vpn") }}</span>
      <span class="lg"><svg width="26" height="10"><line x1="0" y1="5" x2="26" y2="5" stroke="#0ea5e9" stroke-width="1.5" stroke-dasharray="5,3"/></svg>{{ t("topology.kind_l3") }}</span>
      <span class="lg lg-sep"></span>
      <span class="lg lg-head">{{ t("topology.legend_nodes") }}</span>
      <span class="lg clickable" :class="{ off: isGroupOff('firewall') }" @click="toggleGroup('firewall')"><i class="dot" style="background:#ef4444"></i>{{ t("topology.type_firewall") }}</span>
      <span class="lg clickable" :class="{ off: isGroupOff('router') }" @click="toggleGroup('router')"><i class="dot" style="background:#6366f1"></i>{{ t("topology.type_router") }}</span>
      <span class="lg clickable" :class="{ off: isGroupOff('switch') }" @click="toggleGroup('switch')"><i class="dot" style="background:#22c55e"></i>{{ t("topology.type_switch") }}</span>
      <span class="lg clickable" :class="{ off: isGroupOff('ap') }" @click="toggleGroup('ap')"><i class="dot" style="background:#3b82f6"></i>AP</span>
      <span class="lg clickable" :class="{ off: isGroupOff('server') }" @click="toggleGroup('server')"><i class="dot" style="background:#9ca3af"></i>{{ t("topology.server_other") }}</span>
      <span class="lg clickable" :class="{ off: isGroupOff('vpn_site') }" @click="toggleGroup('vpn_site')"><svg width="14" height="14"><rect x="2" y="2" width="9" height="9" transform="rotate(45 7 7)" fill="#9333ea"/></svg>{{ t("topology.type_vpn_site") }}</span>
      <span class="lg clickable" :class="{ off: isGroupOff('subnet') }" @click="toggleGroup('subnet')"><i class="dot dot-rect" style="background:#0ea5e9"></i>{{ t("topology.type_subnet") }}</span>
      <span class="lg muted">{{ t("topology.toggle_hint") }}</span>
    </div>
  </n-card>
</template>

<style scoped>
.topo-legend {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 16px;
  margin-top: 8px;
  font-size: 12px;
  opacity: 0.85;
}
.topo-legend .lg { display: inline-flex; align-items: center; gap: 6px; }
.topo-legend .lg.clickable { cursor: pointer; user-select: none; padding: 1px 4px; border-radius: 4px; }
.topo-legend .lg.clickable:hover { background: rgba(127,127,127,0.12); }
.topo-legend .lg.off { opacity: 0.32; text-decoration: line-through; }
.topo-legend .lg.muted { opacity: 0.6; margin-left: auto; }
.topo-legend .lg-head { font-weight: 600; opacity: 0.55; }
.topo-legend .lg-sep { width: 1px; height: 14px; background: rgba(127,127,127,0.3); }
.topo-legend .dot { width: 11px; height: 11px; border-radius: 50%; display: inline-block; }
.topo-legend .dot-rect { width: 16px; height: 9px; border-radius: 3px; }
.info-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.info-table th {
  text-align: left;
  white-space: nowrap;
  padding: 3px 10px 3px 0;
  color: var(--n-text-color-3, #888);
  font-weight: 500;
  vertical-align: top;
}
.info-table td { padding: 3px 0; word-break: break-all; }
.topology-shell {
  position: relative;
  width: 100%;
  /* 自動延展到接近視窗底部（扣掉頂列／卡片頭／工具列／圖例的概略高度） */
  height: calc(100vh - 270px);
  min-height: 420px;
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
