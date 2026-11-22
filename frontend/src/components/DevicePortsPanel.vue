<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NSpace, NButton, NIcon, NDataTable, NModal, NForm, NFormItem, NInput, NInputNumber,
  NSelect, NPopconfirm, NTag, NTooltip, NDropdown, NRadioButton, NRadioGroup, useDialog, useMessage, type DataTableColumns,
} from "naive-ui";
import { Physical, type DevicePort, type PortTrace } from "@/api/phase3";
import { useDeviceOptions } from "@/composables/useDeviceOptions";
import PatchPanelGrid from "@/components/PatchPanelGrid.vue";
import { PlusIcon, EditIcon, DeleteIcon, LinkIcon, RefreshIcon, PhysicalIcon, ExportIcon, CancelIcon, SaveIcon } from "@/icons";

import { useExportBusy } from "@/composables/useExportBusy";
import { useTablePagination } from "@/composables/useTablePagination";
import { apiErrMsg } from "@/api/client";
import { renderMacWithVendor } from "@/utils/macVendor";
import { withExportValue } from "@/utils/tableExport";
import { saveBlob } from "@/utils/saveFile";
const props = defineProps<{ deviceId: string; deviceName: string; admin: boolean }>();
const { t } = useI18n();
const msg = useMessage();
const dialog = useDialog();
const pg = useTablePagination();
const errCode = (e: unknown): string | undefined => (e as any)?.response?.data?.detail_code;

const ports = ref<DevicePort[]>([]);
const loading = ref(false);
// 連接埠＝資料/理線/序列；電源不在此（改由電力模組：配電盤→供電迴路→插座→裝置）
const PORT_TYPES = ["network", "bridge", "bond", "vlan", "front", "rear", "console"];
const TYPE_TAG: Record<string, "info" | "success" | "warning" | "error" | "default"> = {
  network: "info", bridge: "warning", bond: "success", vlan: "default",
};
const typeOpts = computed(() => PORT_TYPES.map((v) => ({ label: t("ports.type_" + v), value: v })));

async function refresh() {
  loading.value = true;
  try {
    const list = await Physical.ports(props.deviceId);
    // 自然排序（eth1/0/2 在 eth1/0/10 前面，而非字元排序）
    ports.value = list.sort((a, b) => natCompare(a.name, b.name));
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}
// 把名稱拆成「文字 / 數字」段落逐段比較，數字段以數值大小比
function natCompare(a: string, b: string): number {
  const ax = a.match(/(\d+|\D+)/g) ?? [];
  const bx = b.match(/(\d+|\D+)/g) ?? [];
  const n = Math.max(ax.length, bx.length);
  for (let i = 0; i < n; i++) {
    const as = ax[i], bs = bx[i];
    if (as === undefined) return -1;
    if (bs === undefined) return 1;
    const an = Number(as), bn = Number(bs);
    if (!Number.isNaN(an) && !Number.isNaN(bn)) {
      if (an !== bn) return an - bn;
    } else if (as !== bs) {
      return as < bs ? -1 : 1;
    }
  }
  return 0;
}
const peerName = (id: string | null) => id ? (ports.value.find((p) => p.id === id)?.name ?? "—") : "—";

// ── add / edit port ──
const showEdit = ref(false);
const editId = ref<string | null>(null);
const form = ref<{ name: string; type: string; peer_port_id: string | null; position: number | null; description: string }>(
  { name: "", type: "network", peer_port_id: null, position: null, description: "" });
const peerOpts = computed(() => ports.value
  .filter((p) => p.id !== editId.value)
  .map((p) => ({ label: `${p.name} (${p.type})`, value: p.id })));
function openCreate() {
  editId.value = null;
  form.value = { name: "", type: "network", peer_port_id: null, position: null, description: "" };
  showEdit.value = true;
}
function openEdit(p: DevicePort) {
  editId.value = p.id;
  form.value = { name: p.name, type: p.type, peer_port_id: p.peer_port_id, position: p.position, description: p.description ?? "" };
  showEdit.value = true;
}
// 目標埠已經跟別的埠配對：先問，確定才帶 replace_peer 重配（後端會清掉原本那一對的指標）
function confirmReplacePeer(text: string): Promise<boolean> {
  return new Promise((resolve) => {
    dialog.warning({
      title: t("ports.peer_replace_title"), content: text,
      positiveText: t("ports.peer_replace_ok"), negativeText: t("common.cancel"),
      onPositiveClick: () => resolve(true), onNegativeClick: () => resolve(false),
      onClose: () => resolve(false), onMaskClick: () => resolve(false),
    });
  });
}
async function submit(replacePeer = false) {
  if (!form.value.name.trim()) { msg.error(t("ports.name_required")); return; }
  try {
    const body = { ...form.value, description: form.value.description || null };
    if (editId.value) await Physical.updatePort(editId.value, { ...body, ...(replacePeer ? { replace_peer: true } : {}) });
    else await Physical.createPort({ device_id: props.deviceId, ...body });
    showEdit.value = false; msg.success(t("common.ok")); await refresh();
  } catch (e) {
    if (editId.value && !replacePeer && errCode(e) === "port_peer_taken") {
      if (await confirmReplacePeer(apiErrMsg(e))) await submit(true);
      return;
    }
    msg.error(apiErrMsg(e));
  }
}
async function del(id: string) {
  try { await Physical.deletePort(id); msg.success(t("common.ok")); await refresh(); }
  catch (e) { msg.error(apiErrMsg(e)); }
}

const importing = ref(false);
async function importPorts() {
  importing.value = true;
  try {
    const r = await Physical.importPorts(props.deviceId);
    if (!r.linked_librenms) msg.warning(t("ports.import_no_source"));
    else if (r.imported || r.removed || r.pruned) {
      // 新增、以及 LibreNMS 已不再回報而清掉的（網卡拔掉等）一起講
      const parts = [];
      if (r.imported) parts.push(t("ports.import_done", { n: r.imported }));
      if (r.removed || r.pruned) parts.push(t("ports.import_removed", { n: (r.removed ?? 0) + (r.pruned ?? 0) }));
      msg.success(parts.join("；"));
      await refresh();
    } else msg.info(t("ports.import_none"));
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { importing.value = false; }
}

// ── connect ──
const showConnect = ref(false);
const connectFrom = ref<DevicePort | null>(null);
const targetDeviceId = ref<string | null>(null);
const targetPortId = ref<string | null>(null);
const targetPorts = ref<DevicePort[]>([]);
const cableType = ref<string>("cat6");
const cableColor = ref<string>("");
// 裝置選單走後端搜尋（以前只載前 500 台，後面的選不到）
const devOpts = useDeviceOptions();
const CABLE_TYPES = ["cat6", "cat6a", "fiber-mm", "fiber-sm", "dac", "power"];
const cableTypeOpts = computed(() => CABLE_TYPES.map((v) => ({ label: v, value: v })));
const targetPortOpts = computed(() => [...targetPorts.value]
  .sort((a, b) => natCompare(a.name, b.name))
  .map((p) => ({ label: `${p.name} (${p.type})`, value: p.id })));
function openConnect(p: DevicePort) {
  connectFrom.value = p; targetDeviceId.value = null; targetPortId.value = null;
  targetPorts.value = []; cableType.value = "cat6"; cableColor.value = "";
  showConnect.value = true;
  void devOpts.search();
}
watch(targetDeviceId, async (id) => {
  targetPortId.value = null; targetPorts.value = [];
  if (id) { try { targetPorts.value = await Physical.ports(id); } catch { /* */ } }
});
async function doConnect() {
  if (!connectFrom.value || !targetPortId.value) { msg.error(t("ports.pick_target")); return; }
  try {
    await Physical.connectPorts(connectFrom.value.id, targetPortId.value, { type: cableType.value, color: cableColor.value || undefined });
    showConnect.value = false; msg.success(t("ports.connected")); await refresh();
  } catch (e) { msg.error(apiErrMsg(e)); }
}

// ── trace ──
const showTrace = ref(false);
const trace = ref<PortTrace | null>(null);
const traceTitle = ref("");
async function openTrace(p: DevicePort) {
  traceTitle.value = `${props.deviceName}@${p.name}`;
  showTrace.value = true; trace.value = null;
  try { trace.value = await Physical.tracePort(p.id); }
  catch (e) { msg.error(apiErrMsg(e)); }
}
// 面板「追蹤完整路徑」：前埠、後埠各追一次再接起來（外側 ← 前埠 ↔ 後埠 → 外側），
// 單從一個埠出發只會沿它那條線走到另一端，看不到穿過面板的整條路
async function openPanelTrace(p: DevicePort) {
  const peer = p.peer_port_id ? ports.value.find((x) => x.id === p.peer_port_id) : undefined;
  if (!peer || peer.peer_port_id !== p.id) { await openTrace(p); return; }
  const [front, rear] = p.type === "rear" ? [peer, p] : [p, peer];
  traceTitle.value = `${props.deviceName}@${front.name} ↔ ${rear.name}`;
  showTrace.value = true; trace.value = null;
  try {
    const [f, r] = await Promise.all([Physical.tracePort(front.id), Physical.tracePort(rear.id)]);
    // f.nodes 第一個是前埠：反過來接（外側 … 前埠），中間插面板對應，再接後埠往外
    const fn = f.nodes.slice().reverse();
    const fh = f.hops.slice(0, f.nodes.length - 1).reverse();
    const pass = { cable_id: null, cable_label: null, cable_type: "internal", cable_color: null, internal: true,
                   kind: "panel_passthrough" as const, to: r.nodes[0] };
    trace.value = {
      start: fn[0], nodes: [...fn, ...r.nodes], hops: [...fh, pass, ...r.hops],
      cable_count: (f.cable_count ?? 0) + (r.cable_count ?? 0),
      stop_reason: r.stop_reason !== "end" ? r.stop_reason : f.stop_reason,
      stop_port: r.stop_reason !== "end" ? r.stop_port : f.stop_port, candidates: r.candidates ?? f.candidates,
    };
  } catch (e) { msg.error(apiErrMsg(e)); }
}
const traceConnected = computed(() => (trace.value?.hops.length ?? 0) > 0);
// 跳接面板：前埠 / 後埠的格狀檢視（交接包 physical-v1.2 設計圖 06）
const hasPanelPorts = computed(() => ports.value.some((p) => p.type === "front" || p.type === "rear"));
const view = ref<"list" | "grid">("list");   // 編輯、連線、刪除都在清單裡，預設清單
const traceStop = computed(() => {
  const d = trace.value;
  if (!d?.stop_reason || d.stop_reason === "end") return "";
  const sp = d.stop_port;
  return t(`cable_trace.stop_${d.stop_reason}`, {
    port: sp ? `${sp.device_name ?? ""}@${sp.port_name ?? ""}` : "",
    list: (d.candidates ?? []).map((x) => x || t("physical_check.no_label")).join("、") });
});

function escapeXml(s: string): string {
  return s.replace(/[<>&'"]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", "'": "&apos;", '"': "&quot;" }[c] as string));
}
function traceFilename(): string {
  return `cable-trace-${traceTitle.value.replace(/[^\w.-]+/g, "_")}`;
}
function dl(blob: Blob, name: string) {
  saveBlob(name, blob, blob.type, { source: "cable-trace" });
}
// 共用幾何，SVG / PNG 都用
const TRACE_GEO = { W: 420, boxH: 46, gap: 40, padY: 16 };
function buildTraceSvg(): { svg: string; W: number; H: number } | null {
  if (!trace.value) return null;
  const nodes = trace.value.nodes;
  const hops = trace.value.hops;
  const { W, boxH, gap, padY } = TRACE_GEO;
  const rows = nodes.length;
  const H = padY * 2 + rows * boxH + (rows - 1) * gap;
  let svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" font-family="sans-serif">`;
  svg += `<rect width="${W}" height="${H}" fill="#ffffff"/>`;
  const cx = W / 2;
  for (let i = 0; i < rows; i++) {
    const y = padY + i * (boxH + gap);
    if (i > 0) {
      const hop = hops[i - 1];
      const ly = y - gap; const lh = gap;
      const dash = hop?.internal ? ` stroke-dasharray="5 4"` : "";
      const stroke = hop?.internal ? "#f0a020" : (hop?.cable_color || "#888");
      svg += `<line x1="${cx}" y1="${ly}" x2="${cx}" y2="${ly + lh}" stroke="${stroke}" stroke-width="3"${dash}/>`;
      const lbl = `${hop?.internal ? t("ports.internal_link") : (hop?.cable_type || "cable")}${hop?.cable_label ? " · " + hop.cable_label : ""}`;
      svg += `<text x="${cx + 8}" y="${ly + lh / 2 + 3}" font-size="11" fill="#555">${escapeXml(lbl)}</text>`;
    }
    const n = nodes[i];
    svg += `<rect x="${cx - 170}" y="${y}" width="340" height="${boxH}" rx="8" fill="#eef2ff" stroke="#6366f1"/>`;
    svg += `<text x="${cx}" y="${y + 19}" font-size="13" font-weight="700" text-anchor="middle" fill="#1e293b">${escapeXml(n.device_name || n.object_type || "—")}</text>`;
    svg += `<text x="${cx}" y="${y + 36}" font-size="11" text-anchor="middle" fill="#64748b">${escapeXml(n.port_name || n.object_id?.slice(0, 8) || "")}</text>`;
  }
  svg += `</svg>`;
  return { svg, W, H };
}
function exportTraceSvg() {
  const r = buildTraceSvg();
  if (r) dl(new Blob([r.svg], { type: "image/svg+xml" }), `${traceFilename()}.svg`);
}
function exportTracePng() {
  const r = buildTraceSvg();
  if (!r) return;
  const scale = 2;
  const img = new Image();
  img.onload = () => {
    const canvas = document.createElement("canvas");
    canvas.width = r.W * scale; canvas.height = r.H * scale;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.scale(scale, scale);
    ctx.drawImage(img, 0, 0);
    canvas.toBlob((blob) => { if (blob) dl(blob, `${traceFilename()}.png`); }, "image/png");
  };
  img.src = "data:image/svg+xml;base64," + btoa(unescape(encodeURIComponent(r.svg)));
}
// draw.io：每個節點一個方塊、節點之間用箭頭連起來(內部穿透為虛線)
function exportTraceDrawio() {
  if (!trace.value) return;
  const nodes = trace.value.nodes;
  const hops = trace.value.hops;
  const cells: string[] = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>'];
  nodes.forEach((n, i) => {
    const label = `${n.device_name || n.object_type || "—"}\n${n.port_name || (n.object_id ? n.object_id.slice(0, 8) : "")}`;
    cells.push(`<mxCell id="n${i}" value="${escapeXml(label)}" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#eef2ff;strokeColor=#6366f1;" vertex="1" parent="1"><mxGeometry x="120" y="${40 + i * 110}" width="240" height="56" as="geometry"/></mxCell>`);
    if (i > 0) {
      const hop = hops[i - 1];
      const lbl = hop?.internal ? t("ports.internal_link") : `${hop?.cable_type || "cable"}${hop?.cable_label ? " · " + hop.cable_label : ""}`;
      const style = hop?.internal ? "dashed=1;strokeColor=#f0a020;" : `strokeColor=${hop?.cable_color || "#888888"};`;
      cells.push(`<mxCell id="e${i}" value="${escapeXml(lbl)}" style="endArrow=none;html=1;${style}" edge="1" parent="1" source="n${i - 1}" target="n${i}"><mxGeometry relative="1" as="geometry"/></mxCell>`);
    }
  });
  const xml =
    `<mxfile host="jt-ipam"><diagram name="${escapeXml(traceTitle.value)}">` +
    `<mxGraphModel dx="800" dy="600" grid="1" gridSize="10" guides="1" connect="1" arrows="1" page="1" pageScale="1">` +
    `<root>${cells.join("")}</root></mxGraphModel></diagram></mxfile>`;
  dl(new Blob([xml], { type: "application/xml" }), `${traceFilename()}.drawio`);
}
const traceExportOptions = [
  { label: "SVG", key: "svg" },
  { label: "PNG", key: "png" },
  { label: "draw.io", key: "drawio" },
];
const { exporting: traceExporting, run: runTraceExport } = useExportBusy();
function onTraceExport(key: string) { void runTraceExport(() => doTraceExport(key)); }
function doTraceExport(key: string) {
  if (key === "svg") exportTraceSvg();
  else if (key === "png") exportTracePng();
  else if (key === "drawio") exportTraceDrawio();
}

const cols = computed<DataTableColumns<DevicePort>>(() => [
  { title: t("ports.col_name"), key: "name", minWidth: 120,
    sorter: (a, b) => natCompare(a.name, b.name) },
  { title: t("ports.col_type"), key: "type", width: 90,
    sorter: (a, b) => t("ports.type_" + a.type).localeCompare(t("ports.type_" + b.type)),
    render: (r) => h(NTag, { size: "small", type: TYPE_TAG[r.type] ?? "info", bordered: false }, () => t("ports.type_" + r.type)) },
  { title: t("ports.col_link"), key: "link", minWidth: 150,
    sorter: (a, b) => natCompare(a.link ?? "", b.link ?? ""),
    render: (r) => r.link
      // 純文字 tooltip：避免把綠色 tag 放進深色 tooltip 造成淺色主題配色錯
      ? h(NTooltip, null, {
          trigger: () => h(NTag, { size: "small", type: "success", bordered: false, style: "max-width:100%" },
            () => h("span", { style: "display:inline-block;max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:bottom" }, "→ " + r.link)),
          default: () => "→ " + r.link,
        })
      : "—" },
  // MAC 在上、OUI 廠商在下（與 IP 清單同一個呈現，utils/macVendor）
  withExportValue({ title: t("ports.col_mac"), key: "mac_address", minWidth: 150,
    sorter: (a: DevicePort, b: DevicePort) => natCompare(a.mac_address ?? "", b.mac_address ?? ""),
    render: (r: DevicePort) => renderMacWithVendor(r.mac_address, r.mac_vendor) },
    (r) => [r.mac_address, r.mac_vendor].filter(Boolean).join(" ")),
  { title: t("ports.col_peer"), key: "peer_port_id", width: 100,
    sorter: (a, b) => natCompare(peerName(a.peer_port_id), peerName(b.peer_port_id)),
    render: (r) => peerName(r.peer_port_id) },
  { title: t("common.description"), key: "description", minWidth: 110, ellipsis: { tooltip: true },
    sorter: (a, b) => natCompare(a.description ?? "", b.description ?? ""),
    render: (r) => r.description ?? "—" },
  {
    title: t("common.actions"), key: "actions", width: 150,
    render: (r) => h(NSpace, { size: 2, wrapItem: false }, () => {
      const acts = [
        h(NButton, { size: "tiny", quaternary: true, title: t("ports.trace"), onClick: () => openTrace(r) },
          { icon: () => h(NIcon, null, () => h(LinkIcon)) }),
      ];
      if (props.admin) {
        acts.push(
          h(NButton, { size: "tiny", quaternary: true, type: "primary", title: t("ports.connect"), "data-testid": `port-connect-${r.name}`, onClick: () => openConnect(r) },
            { icon: () => h(NIcon, null, () => h(PhysicalIcon)) }),
          h(NButton, { size: "tiny", quaternary: true, "data-testid": `port-edit-${r.name}`, onClick: () => openEdit(r) }, { icon: () => h(NIcon, null, () => h(EditIcon)) }),
          h(NPopconfirm, { onPositiveClick: () => del(r.id) }, {
            trigger: () => h(NButton, { size: "tiny", quaternary: true, type: "error" }, { icon: () => h(NIcon, null, () => h(DeleteIcon)) }),
            default: () => t("common.confirm_delete"),
          }),
        );
      }
      return acts;
    }),
  },
]);

// 換一台裝置要重抓。機櫃圖點裝置會導到同一條路由 `/devices/:id`，只換參數時 Vue
// 不會重建元件、只是換 prop —— 只寫 onMounted 的話畫面會一直停在第一次點的那台
// （客戶回報 0.5.208）。隔壁的電源埠面板與可用性長條圖本來就有這個 watch。
watch(() => props.deviceId, () => { void refresh(); });
onMounted(() => { void refresh(); });
</script>

<template>
  <n-card :title="() => h('span', { style: 'display:inline-flex;align-items:center;gap:8px' }, [h(NIcon, { size: 18 }, () => h(PhysicalIcon)), t('ports.title')])" size="small">
    <!-- 控制列：自標題列搬到內文最上方 -->
    <n-space align="center" justify="end" style="margin-bottom: 10px">
      <n-button v-if="admin" size="small" type="primary" @click="openCreate">
        <template #icon><n-icon><PlusIcon /></n-icon></template>{{ t("ports.add") }}
      </n-button>
      <n-button v-if="admin" size="small" :loading="importing" @click="importPorts">
        <template #icon><n-icon><LinkIcon /></n-icon></template>{{ t("ports.import") }}
      </n-button>
      <n-button size="small" @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}
      </n-button>
      <!-- 有前埠 / 後埠（跳接面板）才有格狀檢視 -->
      <n-radio-group v-if="hasPanelPorts" v-model:value="view" size="small" data-testid="ports-view">
        <n-radio-button value="list">{{ t("panel_grid.view_list") }}</n-radio-button>
        <n-radio-button value="grid">{{ t("panel_grid.view_grid") }}</n-radio-button>
      </n-radio-group>
    </n-space>
    <PatchPanelGrid v-if="hasPanelPorts && view === 'grid'" :ports="ports" @trace="openPanelTrace" />
    <n-data-table v-else :columns="cols" :data="ports" :loading="loading" size="small" :bordered="false" :pagination="pg">
      <template #empty><div style="text-align:center;opacity:.5;padding:12px">{{ t("ports.empty") }}</div></template>
    </n-data-table>

    <!-- add/edit -->
    <n-modal v-model:show="showEdit" preset="card" style="width:460px" :title="editId ? t('ports.edit') : t('ports.add')">
      <n-form>
        <n-form-item :label="t('ports.col_name')"><n-input v-model:value="form.name" placeholder="eth0 / ge-0/0/1 / Port 1" /></n-form-item>
        <n-form-item :label="t('ports.col_type')"><n-select v-model:value="form.type" :options="typeOpts" /></n-form-item>
        <n-form-item :label="t('ports.peer_hint')">
          <n-select v-model:value="form.peer_port_id" :options="peerOpts" clearable :placeholder="t('ports.peer_none')" data-testid="port-peer" />
        </n-form-item>
        <n-form-item :label="t('ports.position')">
          <n-space vertical :size="2" style="width:100%">
            <n-input-number v-model:value="form.position" :min="0" :max="100000" clearable style="width:160px" data-testid="port-position" />
            <span style="font-size:12px;opacity:.7">{{ t("ports.position_hint") }}</span>
          </n-space>
        </n-form-item>
        <n-form-item :label="t('common.description')"><n-input v-model:value="form.description" type="textarea" :rows="2" /></n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end"><n-button @click="showEdit = false"><template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}</n-button>
          <n-button type="primary" data-testid="port-save" @click="submit()"><template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}</n-button></n-space>
      </template>
    </n-modal>

    <!-- connect -->
    <n-modal v-model:show="showConnect" preset="card" style="width:480px" :title="t('ports.connect_title')">
      <n-form>
        <n-form-item :label="t('ports.from')"><n-input :value="`${deviceName}@${connectFrom?.name ?? ''}`" disabled /></n-form-item>
        <n-form-item :label="t('ports.target_device')">
          <n-select v-model:value="targetDeviceId" :options="devOpts.options.value" filterable remote clearable
                    :loading="devOpts.loading.value" @search="devOpts.onSearch" :placeholder="t('common.select')"
                    data-testid="connect-device" />
        </n-form-item>
        <n-form-item :label="t('ports.target_port')">
          <n-select v-model:value="targetPortId" :options="targetPortOpts" :disabled="!targetDeviceId" :placeholder="t('ports.target_port_ph')" data-testid="connect-port" />
        </n-form-item>
        <n-space :size="10">
          <n-form-item :label="t('ports.cable_type')"><n-select v-model:value="cableType" :options="cableTypeOpts" style="width:150px" /></n-form-item>
          <n-form-item :label="t('ports.cable_color')"><n-input v-model:value="cableColor" placeholder="#888 / blue" style="width:130px" /></n-form-item>
        </n-space>
      </n-form>
      <template #footer>
        <n-space justify="end"><n-button @click="showConnect = false"><template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}</n-button>
          <n-button type="primary" data-testid="connect-do" @click="doConnect"><template #icon><n-icon><LinkIcon /></n-icon></template>{{ t("ports.connect") }}</n-button></n-space>
      </template>
    </n-modal>

    <!-- trace -->
    <n-modal v-model:show="showTrace" preset="card" style="width:520px" :title="t('ports.trace_title', { p: traceTitle })">
      <!-- 控制列：自標題列搬到內文最上方 -->
      <n-space align="center" justify="end" style="margin-bottom: 10px">
        <n-dropdown v-if="traceConnected" trigger="click" :options="traceExportOptions" :disabled="traceExporting" @select="onTraceExport">
          <n-button size="tiny" :loading="traceExporting" :disabled="traceExporting">
            <template #icon><n-icon><ExportIcon /></n-icon></template>
            {{ t("common.download") }}
          </n-button>
        </n-dropdown>
      </n-space>
      <div v-if="!trace" style="text-align:center;padding:20px;opacity:.6">…</div>
      <div v-else-if="!traceConnected && !traceStop" style="text-align:center;padding:20px;opacity:.6">{{ t("ports.not_connected") }}</div>
      <div v-else class="trace-chain">
        <template v-for="(n, i) in trace.nodes" :key="i">
          <div v-if="i > 0" class="trace-cable">
            <span class="trace-line" :class="{ 'trace-line-internal': trace.hops[i-1]?.internal }"
                  :style="{ background: trace.hops[i-1]?.internal ? 'transparent' : (trace.hops[i-1]?.cable_color || '#888') }"></span>
            <span class="trace-cable-lbl">{{ trace.hops[i-1]?.internal ? t('ports.internal_link') : (trace.hops[i-1]?.cable_type || 'cable') }}<template v-if="trace.hops[i-1]?.cable_label"> · {{ trace.hops[i-1]?.cable_label }}</template></span>
          </div>
          <div class="trace-node">
            <div class="trace-dev">{{ n.device_name || n.object_type || "—" }}</div>
            <div class="trace-port">{{ n.port_name || (n.object_id ? n.object_id.slice(0,8) : "") }}</div>
          </div>
        </template>
      </div>
      <!-- 為什麼停在這裡（交接包 physical-v1.2：未配對的面板、同一埠多條線、只有規劃中的線…） -->
      <div v-if="trace && traceStop" class="trace-stop" data-testid="port-trace-stop">{{ traceStop }}</div>
    </n-modal>
  </n-card>
</template>

<style scoped>
.trace-chain { display: flex; flex-direction: column; align-items: center; padding: 6px 0; }
.trace-stop { margin-top: 8px; padding: 8px 12px; border-radius: 6px; background: rgba(240, 160, 32, .14); font-size: 13px; }
.trace-node {
  width: 320px; max-width: 100%; border: 2px solid #6366f1; background: rgba(99,102,241,0.08);
  border-radius: 8px; padding: 8px 12px; text-align: center;
}
.trace-dev { font-weight: 700; }
.trace-port { font-size: 12px; opacity: 0.7; font-family: monospace; }
.trace-cable { display: flex; align-items: center; gap: 8px; height: 40px; }
.trace-line { width: 3px; height: 100%; border-radius: 2px; }
.trace-line-internal { background: transparent !important; border-left: 3px dashed #f0a020; width: 0; }
.trace-cable-lbl { font-size: 12px; opacity: 0.65; }
</style>
