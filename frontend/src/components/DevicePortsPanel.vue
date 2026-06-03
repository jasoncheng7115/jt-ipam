<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NSpace, NButton, NIcon, NDataTable, NModal, NForm, NFormItem, NInput,
  NSelect, NPopconfirm, NTag, useMessage, type DataTableColumns,
} from "naive-ui";
import { Physical, type DevicePort, type PortTrace } from "@/api/phase3";
import { listDevices } from "@/api/basic";
import { PlusIcon, EditIcon, DeleteIcon, LinkIcon, RefreshIcon, PhysicalIcon } from "@/icons";

const props = defineProps<{ deviceId: string; deviceName: string; admin: boolean }>();
const { t } = useI18n();
const msg = useMessage();

const ports = ref<DevicePort[]>([]);
const loading = ref(false);
const PORT_TYPES = ["network", "front", "rear", "console", "power"];
const typeOpts = computed(() => PORT_TYPES.map((v) => ({ label: t("ports.type_" + v), value: v })));

async function refresh() {
  loading.value = true;
  try { ports.value = await Physical.ports(props.deviceId); }
  catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
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
async function submit() {
  if (!form.value.name.trim()) { msg.error(t("ports.name_required")); return; }
  try {
    if (editId.value) await Physical.updatePort(editId.value, { ...form.value, description: form.value.description || null });
    else await Physical.createPort({ device_id: props.deviceId, ...form.value, description: form.value.description || null });
    showEdit.value = false; msg.success(t("common.ok")); await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function del(id: string) {
  try { await Physical.deletePort(id); msg.success(t("common.ok")); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

const importing = ref(false);
async function importPorts() {
  importing.value = true;
  try {
    const r = await Physical.importPorts(props.deviceId);
    if (!r.linked_librenms) msg.warning(t("ports.import_no_source"));
    else if (r.imported) { msg.success(t("ports.import_done", { n: r.imported })); await refresh(); }
    else msg.info(t("ports.import_none"));
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
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
const deviceOpts = ref<{ label: string; value: string }[]>([]);
const CABLE_TYPES = ["cat6", "cat6a", "fiber-mm", "fiber-sm", "dac", "power"];
const cableTypeOpts = computed(() => CABLE_TYPES.map((v) => ({ label: v, value: v })));
const targetPortOpts = computed(() => targetPorts.value.map((p) => ({ label: `${p.name} (${p.type})`, value: p.id })));
function openConnect(p: DevicePort) {
  connectFrom.value = p; targetDeviceId.value = null; targetPortId.value = null;
  targetPorts.value = []; cableType.value = "cat6"; cableColor.value = "";
  showConnect.value = true;
  if (!deviceOpts.value.length) {
    void listDevices({ pageSize: 500 }).then((r) => {
      deviceOpts.value = r.items.map((d) => ({ label: d.name, value: d.id }));
    });
  }
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
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

// ── trace ──
const showTrace = ref(false);
const trace = ref<PortTrace | null>(null);
const traceTitle = ref("");
async function openTrace(p: DevicePort) {
  traceTitle.value = `${props.deviceName} · ${p.name}`;
  showTrace.value = true; trace.value = null;
  try { trace.value = await Physical.tracePort(p.id); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
const traceConnected = computed(() => (trace.value?.hops.length ?? 0) > 0);

function downloadSvg() {
  if (!trace.value) return;
  const nodes = trace.value.nodes;
  const hops = trace.value.hops;
  const W = 420, boxH = 46, gap = 40, padY = 16;
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
      svg += `<line x1="${cx}" y1="${ly}" x2="${cx}" y2="${ly + lh}" stroke="${hop?.cable_color || "#888"}" stroke-width="3"/>`;
      const lbl = `${hop?.cable_type || "cable"}${hop?.cable_label ? " · " + hop.cable_label : ""}`;
      svg += `<text x="${cx + 8}" y="${ly + lh / 2 + 3}" font-size="11" fill="#555">${escapeXml(lbl)}</text>`;
    }
    const n = nodes[i];
    svg += `<rect x="${cx - 170}" y="${y}" width="340" height="${boxH}" rx="8" fill="#eef2ff" stroke="#6366f1"/>`;
    svg += `<text x="${cx}" y="${y + 19}" font-size="13" font-weight="700" text-anchor="middle" fill="#1e293b">${escapeXml(n.device_name || n.object_type || "—")}</text>`;
    svg += `<text x="${cx}" y="${y + 36}" font-size="11" text-anchor="middle" fill="#64748b">${escapeXml(n.port_name || n.object_id?.slice(0, 8) || "")}</text>`;
  }
  svg += `</svg>`;
  const blob = new Blob([svg], { type: "image/svg+xml" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `cable-trace-${traceTitle.value.replace(/[^\w.-]+/g, "_")}.svg`;
  a.click();
  URL.revokeObjectURL(a.href);
}
function escapeXml(s: string): string {
  return s.replace(/[<>&'"]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", "'": "&apos;", '"': "&quot;" }[c] as string));
}

const cols = computed<DataTableColumns<DevicePort>>(() => [
  { title: t("ports.col_name"), key: "name", minWidth: 120 },
  { title: t("ports.col_type"), key: "type", width: 100, render: (r) => h(NTag, { size: "small", type: "info", bordered: false }, () => t("ports.type_" + r.type)) },
  { title: t("ports.col_peer"), key: "peer_port_id", width: 110, render: (r) => peerName(r.peer_port_id) },
  { title: t("common.description"), key: "description", minWidth: 120, render: (r) => r.description ?? "—" },
  {
    title: t("common.actions"), key: "actions", width: 150,
    render: (r) => h(NSpace, { size: 2, wrapItem: false }, () => {
      const acts = [
        h(NButton, { size: "tiny", quaternary: true, title: t("ports.trace"), onClick: () => openTrace(r) },
          { icon: () => h(NIcon, null, () => h(LinkIcon)) }),
      ];
      if (props.admin) {
        acts.push(
          h(NButton, { size: "tiny", quaternary: true, type: "primary", title: t("ports.connect"), onClick: () => openConnect(r) },
            { icon: () => h(NIcon, null, () => h(PhysicalIcon)) }),
          h(NButton, { size: "tiny", quaternary: true, onClick: () => openEdit(r) }, { icon: () => h(NIcon, null, () => h(EditIcon)) }),
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

onMounted(() => { void refresh(); });
</script>

<template>
  <n-card :title="() => h('span', { style: 'display:inline-flex;align-items:center;gap:8px' }, [h(NIcon, { size: 18 }, () => h(PhysicalIcon)), t('ports.title')])" size="small">
    <template #header-extra>
      <n-space :size="8">
        <n-button v-if="admin" size="small" type="primary" @click="openCreate">
          <template #icon><n-icon><PlusIcon /></n-icon></template>{{ t("ports.add") }}
        </n-button>
        <n-button v-if="admin" size="small" :loading="importing" @click="importPorts">
          <template #icon><n-icon><LinkIcon /></n-icon></template>{{ t("ports.import") }}
        </n-button>
        <n-button size="small" @click="refresh" :loading="loading">
          <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}
        </n-button>
      </n-space>
    </template>
    <n-data-table :columns="cols" :data="ports" :loading="loading" size="small" :bordered="false">
      <template #empty><div style="text-align:center;opacity:.5;padding:12px">{{ t("ports.empty") }}</div></template>
    </n-data-table>

    <!-- add/edit -->
    <n-modal v-model:show="showEdit" preset="card" style="width:460px" :title="editId ? t('ports.edit') : t('ports.add')">
      <n-form>
        <n-form-item :label="t('ports.col_name')"><n-input v-model:value="form.name" placeholder="eth0 / ge-0/0/1 / Port 1" /></n-form-item>
        <n-form-item :label="t('ports.col_type')"><n-select v-model:value="form.type" :options="typeOpts" /></n-form-item>
        <n-form-item :label="t('ports.peer_hint')">
          <n-select v-model:value="form.peer_port_id" :options="peerOpts" clearable :placeholder="t('ports.peer_none')" />
        </n-form-item>
        <n-form-item :label="t('common.description')"><n-input v-model:value="form.description" type="textarea" :rows="2" /></n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end"><n-button @click="showEdit = false">{{ t("common.cancel") }}</n-button>
          <n-button type="primary" @click="submit">{{ t("common.save") }}</n-button></n-space>
      </template>
    </n-modal>

    <!-- connect -->
    <n-modal v-model:show="showConnect" preset="card" style="width:480px" :title="t('ports.connect_title')">
      <n-form>
        <n-form-item :label="t('ports.from')"><n-input :value="`${deviceName} · ${connectFrom?.name ?? ''}`" disabled /></n-form-item>
        <n-form-item :label="t('ports.target_device')">
          <n-select v-model:value="targetDeviceId" :options="deviceOpts" filterable :placeholder="t('common.select')" />
        </n-form-item>
        <n-form-item :label="t('ports.target_port')">
          <n-select v-model:value="targetPortId" :options="targetPortOpts" :disabled="!targetDeviceId" :placeholder="t('ports.target_port_ph')" />
        </n-form-item>
        <n-space :size="10">
          <n-form-item :label="t('ports.cable_type')"><n-select v-model:value="cableType" :options="cableTypeOpts" style="width:150px" /></n-form-item>
          <n-form-item :label="t('ports.cable_color')"><n-input v-model:value="cableColor" placeholder="#888 / blue" style="width:130px" /></n-form-item>
        </n-space>
      </n-form>
      <template #footer>
        <n-space justify="end"><n-button @click="showConnect = false">{{ t("common.cancel") }}</n-button>
          <n-button type="primary" @click="doConnect">{{ t("ports.connect") }}</n-button></n-space>
      </template>
    </n-modal>

    <!-- trace -->
    <n-modal v-model:show="showTrace" preset="card" style="width:520px" :title="t('ports.trace_title', { p: traceTitle })">
      <template #header-extra>
        <n-button v-if="traceConnected" size="tiny" @click="downloadSvg">{{ t("ports.download_svg") }}</n-button>
      </template>
      <div v-if="!trace" style="text-align:center;padding:20px;opacity:.6">…</div>
      <div v-else-if="!traceConnected" style="text-align:center;padding:20px;opacity:.6">{{ t("ports.not_connected") }}</div>
      <div v-else class="trace-chain">
        <template v-for="(n, i) in trace.nodes" :key="i">
          <div v-if="i > 0" class="trace-cable">
            <span class="trace-line" :style="{ background: trace.hops[i-1]?.cable_color || '#888' }"></span>
            <span class="trace-cable-lbl">{{ trace.hops[i-1]?.cable_type || 'cable' }}<template v-if="trace.hops[i-1]?.cable_label"> · {{ trace.hops[i-1]?.cable_label }}</template></span>
          </div>
          <div class="trace-node">
            <div class="trace-dev">{{ n.device_name || n.object_type || "—" }}</div>
            <div class="trace-port">{{ n.port_name || (n.object_id ? n.object_id.slice(0,8) : "") }}</div>
          </div>
        </template>
      </div>
    </n-modal>
  </n-card>
</template>

<style scoped>
.trace-chain { display: flex; flex-direction: column; align-items: center; padding: 6px 0; }
.trace-node {
  width: 320px; max-width: 100%; border: 2px solid #6366f1; background: rgba(99,102,241,0.08);
  border-radius: 8px; padding: 8px 12px; text-align: center;
}
.trace-dev { font-weight: 700; }
.trace-port { font-size: 12px; opacity: 0.7; font-family: monospace; }
.trace-cable { display: flex; align-items: center; gap: 8px; height: 40px; }
.trace-line { width: 3px; height: 100%; border-radius: 2px; }
.trace-cable-lbl { font-size: 12px; opacity: 0.65; }
</style>
