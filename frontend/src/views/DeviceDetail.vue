<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import {
  NCard, NSpace, NIcon, NButton, NDescriptions, NDescriptionsItem,
  NTag, NDataTable, NSpin, NTooltip, NModal, NSelect,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { ArrowLeft as ArrowLeftIcon } from "@iconoir/vue";
import { DevicesIcon, RefreshIcon, EditIcon, TopologyIcon, AddressesIcon, LibreNMSIcon, WazuhIcon, VirtualizationIcon, SubnetsIcon, LinkIcon } from "@/icons";
import { apiClient } from "@/api/client";
import { listAddresses, updateAddress } from "@/api/addresses";
import { listLocations, listRacks, getDeviceVlans, getDeviceLibrenms, type Device, type Location, type Rack, type DeviceVLAN, type DeviceLibreNMS } from "@/api/basic";
import { getDeviceRelations, type RelationNode } from "@/api/relations";
import RelationChain from "@/components/RelationChain.vue";
import RackDiagram from "@/components/RackDiagram.vue";
import DevicePortsPanel from "@/components/DevicePortsPanel.vue";
import DevicePowerPortsPanel from "@/components/DevicePowerPortsPanel.vue";
import SwitchPortLabel from "@/components/SwitchPortLabel.vue";
import { getRackDiagram } from "@/api/racks";
type RackDiagramData = Awaited<ReturnType<typeof getRackDiagram>>;
import IPAddressEditModal from "@/components/IPAddressEditModal.vue";
import DeviceEditModal from "@/components/DeviceEditModal.vue";
import LiveStatusDot from "@/components/LiveStatusDot.vue";
import type { IPAddress } from "@/types";
import { autoSort } from "@/composables/useTableSort";
import { fmtDateTime } from "@/utils/datetime";
import { useCustomers } from "@/composables/useCustomers";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import ColumnPicker from "@/components/ColumnPicker.vue";
import { useAuthStore } from "@/stores/auth";
import { storeToRefs } from "pinia";
import { useTablePagination } from "@/composables/useTablePagination";
const pg = useTablePagination();
const { t } = useI18n();

const { me } = storeToRefs(useAuthStore());
const isAdmin = computed(() => !!me.value?.is_admin);
// 卡片標題：icon + 文字（NCard title 支援 render function）
function cardHead(icon: any, text: string) {
  return h("span", { style: "display:inline-flex;align-items:center;gap:8px" },
    [h(NIcon, { size: 18 }, () => h(icon)), text]);
}
const { labelFor: customerLabelFor, ensureLoaded: ensureCustomersLoaded } = useCustomers();
const { visibleKeys: ipVisibleKeys, setVisible: setIpVisible, reset: resetIpVisible } = useColumnPrefs(
  "device_detail_ips",
  ["live", "ip", "hostname", "state", "mac", "mac_vendor", "switch_port", "description", "last_seen"],
  ["live", "ip", "hostname", "state", "mac", "mac_vendor", "switch_port", "last_seen"],
);
const ipColumnPickerItems = [
  { key: "live", label: t("cols.live") },
  { key: "ip", label: "IP" },
  { key: "hostname", label: t("cols.hostname") },
  { key: "state", label: t("cols.status") },
  { key: "mac", label: "MAC" },
  { key: "mac_vendor", label: t("cols.vendor") },
  { key: "switch_port", label: t("cols.switch_port") },
  { key: "description", label: t("cols.description") },
  { key: "last_seen", label: t("cols.last_seen") },
];

const route = useRoute();
const router = useRouter();
const msg = useMessage();

const device = ref<Device | null>(null);
const editShow = ref(false);
const relations = ref<RelationNode[]>([]);
const location = ref<Location | null>(null);
const rack = ref<Rack | null>(null);
const rackDiagram = ref<RackDiagramData | null>(null);
const addresses = ref<IPAddress[]>([]);

// 新增 IP 對應：把一個現有 IP 指派給本裝置（一個裝置可多 IP）
const showLinkIp = ref(false);
const linkIpId = ref<string | null>(null);
const linkCandidates = ref<IPAddress[]>([]);
const linking = ref(false);
const linkOptions = computed(() => linkCandidates.value.map((a) => ({
  label: `${a.ip}${a.hostname ? " — " + a.hostname : ""}`, value: a.id,
})));
async function openLinkIp() {
  if (!device.value) return;
  linkIpId.value = null;
  try {
    const r = await listAddresses({ page: 1, pageSize: 2000 });
    linkCandidates.value = r.items.filter((a) => (a as any).device_id !== device.value!.id);
  } catch { msg.error(t("errors.network")); return; }
  showLinkIp.value = true;
}
async function doLinkIp() {
  if (!linkIpId.value || !device.value) return;
  linking.value = true;
  try {
    await updateAddress(linkIpId.value, { device_id: device.value.id });
    msg.success(t("common.ok"));
    showLinkIp.value = false;
    await load(device.value.id);
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.network")); }
  finally { linking.value = false; }
}
const vlans = ref<DeviceVLAN[]>([]);
const lnms = ref<DeviceLibreNMS | null>(null);
const integrations = ref<{ wazuh: any; vm: any } | null>(null);
const loading = ref(false);

const selected = ref<IPAddress | null>(null);
const modalShow = ref(false);

async function load(id: string) {
  loading.value = true;
  try {
    const [dev, addrs] = await Promise.all([
      apiClient.get<Device>(`/api/v1/devices/${id}`).then((r) => r.data),
      listAddresses({ deviceId: id, page: 1, pageSize: 1000 }),
    ]);
    device.value = dev;
    addresses.value = addrs.items;
    getDeviceRelations(id).then((c) => { relations.value = c; }).catch(() => { relations.value = []; });
    getDeviceVlans(id).then((v) => { vlans.value = v; }).catch(() => { vlans.value = []; });
    getDeviceLibrenms(id).then((l) => { lnms.value = l; }).catch(() => { lnms.value = null; });
    apiClient.get(`/api/v1/devices/${id}/integrations`).then((r) => { integrations.value = r.data; }).catch(() => { integrations.value = null; });

    const tasks: Promise<unknown>[] = [];
    if (dev.location_id) {
      tasks.push(
        listLocations().then((res) => {
          location.value = res.items.find((l) => l.id === dev.location_id) ?? null;
        }).catch(() => { location.value = null; }),
      );
    } else { location.value = null; }
    if (dev.rack_id) {
      tasks.push(
        listRacks().then((res) => {
          rack.value = res.items.find((r) => r.id === dev.rack_id) ?? null;
        }).catch(() => { rack.value = null; }),
      );
      tasks.push(
        getRackDiagram(dev.rack_id)
          .then((d) => { rackDiagram.value = d; })
          .catch(() => { rackDiagram.value = null; }),
      );
    } else { rack.value = null; rackDiagram.value = null; }
    await Promise.all(tasks);
  } catch {
    msg.error(t("errors.network"));
  } finally {
    loading.value = false;
  }
}

function typeColor(type: string): "success" | "info" | "warning" | "error" | "default" {
  return ({
    router: "info",
    switch: "success",
    firewall: "error",
    server: "default",
    storage: "warning",
    ap: "info",
    ipmi: "warning",
    other: "default",
  } as Record<string, "success" | "info" | "warning" | "error" | "default">)[type] ?? "default";
}

function stateTag(state: string) {
  const map: Record<string, "success" | "warning" | "error" | "default" | "info"> = {
    active: "success", reserved: "info", offline: "error", dhcp: "warning", used: "default",
  };
  const key = `addresses.state_${state}`;
  const label = t(key) === key ? state : t(key);
  return h(NTag, { type: map[state] ?? "default", size: "small" }, () => label);
}

// LibreNMS device status：1=up / 0=down（原始值對使用者沒意義，翻成上線/離線）
function lnmsStatusLabel(s: unknown): string {
  if (s == null || s === "") return "—";
  const v = String(s);
  if (v === "1" || v.toLowerCase() === "up") return t("topology.status_up");
  if (v === "0" || v.toLowerCase() === "down") return t("topology.status_down");
  return v;
}
function lastSeen(r: IPAddress): string {
  const arr = [r.last_seen_scanner, r.last_seen_librenms, r.last_seen_dns].filter(Boolean) as string[];
  if (!arr.length) return "—";
  return arr.sort().reverse()[0].replace("T", " ").split(".")[0];
}

function liveDot(r: IPAddress) {
  return h(LiveStatusDot, { address: r });
}

const allIpColumns = computed<DataTableColumns<IPAddress>>(() => autoSort([
  { title: "", key: "live", width: 28, render: (r) => liveDot(r) },
  { title: t("addresses.ip"), key: "ip", width: 130 },
  // 固定寬度 + ellipsis：不再吃掉多餘寬度（短主機名稱不留大片空白，長的省略號）
  { title: t("addresses.hostname"), key: "hostname", width: 130,
    ellipsis: { tooltip: true }, render: (r) => r.hostname ?? "" },
  { title: t("common.status"), key: "state", width: 92, render: (r) => stateTag(r.state) },
  { title: t("addresses.mac"), key: "mac", width: 160,
    render: (r) => r.mac ? h("span", { style: "white-space:nowrap" }, r.mac) : "" },
  { title: t("cols.vendor"), key: "mac_vendor", width: 110,
    ellipsis: { tooltip: true }, render: (r) => r.mac_vendor ?? "—" },
  // 全部固定寬度：空欄不再被撐大；表格比卡片窄時，多餘空白留在右側而非塞進某一欄
  { title: t("addresses.switch_port"), key: "switch_port", width: 180,
    ellipsis: { tooltip: false },
    render: (r) => !r.switch_port ? ""
      : h(NTooltip, null, {
          trigger: () => h(SwitchPortLabel, { value: r.switch_port, dim: r.switch_port_confident === false }),
          default: () => r.switch_port_confident === false
            ? t("addresses.switch_port_uncertain") : r.switch_port }) },
  { title: t("common.description"), key: "description", width: 160,
    ellipsis: { tooltip: true }, render: (r) => r.description ?? "" },
  { title: t("addresses.last_seen"), key: "last_seen", width: 172,
    render: (r) => h("span", { style: "white-space:nowrap" }, [lastSeen(r)]) },
]));

const ipColumns = computed<DataTableColumns<IPAddress>>(() =>
  allIpColumns.value.filter((c: any) => ipVisibleKeys.value.includes(c.key)),
);

function openRow(row: IPAddress) {
  void router.push({ name: "address-detail", params: { id: row.id } });
}

function onSaved(updated: IPAddress) {
  selected.value = updated;
  const idx = addresses.value.findIndex((r) => r.id === updated.id);
  if (idx >= 0) addresses.value[idx] = updated;
}

function onDeleted(id: string) {
  addresses.value = addresses.value.filter((r) => r.id !== id);
}

watch(() => route.params.id, (id) => {
  if (typeof id === "string") void load(id);
});

onMounted(() => {
  const id = route.params.id;
  if (typeof id === "string") void load(id);
  void ensureCustomersLoaded();
});
</script>

<template>
  <n-spin :show="loading">
    <n-space vertical :size="16">
      <n-card v-if="device">
        <template #header>
          <n-space align="center" :wrap-item="false">
            <n-icon :size="22"><DevicesIcon /></n-icon>
            <span>{{ device.name }}</span>
            <n-tag :type="typeColor(device.type)" size="small">{{ device.type }}</n-tag>
          </n-space>
        </template>
        <template #header-extra>
          <n-space :size="8">
            <n-button type="primary" size="small" @click="editShow = true">
              <template #icon><n-icon><EditIcon /></n-icon></template>
              {{ t("common.edit") }}
            </n-button>
            <n-button @click="router.push({ name: 'devices' })" size="small">
              <template #icon><n-icon><ArrowLeftIcon /></n-icon></template>
              {{ t("common.back") }}
            </n-button>
          </n-space>
        </template>
        <div class="dev-head-row">
          <div class="dev-head-info">
        <n-descriptions bordered :column="3" size="small" label-placement="left">
          <n-descriptions-item :label="t('common.name')">{{ device.name }}</n-descriptions-item>
          <n-descriptions-item :label="t('common.type')">{{ device.type }}</n-descriptions-item>
          <n-descriptions-item :label="t('devices.vendor')">{{ device.vendor ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('devices.model')">{{ device.model ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('devices.serial')">{{ device.serial ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('nav.locations')">
            <a v-if="location" href="#" class="entity-link"
               @click.prevent="router.push({ name: 'locations' })">{{ location.name }}</a>
            <span v-else>—</span>
          </n-descriptions-item>
          <n-descriptions-item :label="t('nav.racks')">
            <a v-if="rack" href="#" class="entity-link"
               @click.prevent="router.push({ name: 'racks' })">{{ rack.name }} ({{ rack.u_height }}U)</a>
            <span v-else>—</span>
          </n-descriptions-item>
          <n-descriptions-item :label="t('devices.u_position')">{{ device.u_position ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('devices.u_size')">{{ device.u_size ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('devices.rack_face')">
            {{ (device as any).rack_face === "rear" ? t("devices.rack_face_rear")
               : (device as any).rack_face === "front" ? t("devices.rack_face_front") : "—" }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('nav.customers')" :span="3">
            <a v-if="device.customer_id" href="#" class="entity-link"
               @click.prevent="router.push({ name: 'customers' })">
              {{ customerLabelFor(device.customer_id) }}
            </a>
            <span v-else>—</span>
          </n-descriptions-item>
          <n-descriptions-item :label="t('common.description')" :span="3">
            {{ device.description ?? "—" }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('common.created_at')">{{ fmtDateTime(device.created_at) }}</n-descriptions-item>
          <n-descriptions-item :label="t('common.updated_at')" :span="2">{{ fmtDateTime(device.updated_at) }}</n-descriptions-item>
        </n-descriptions>
          </div>
          <div v-if="rackDiagram" class="dev-head-rack">
            <RackDiagram :diagram="rackDiagram" :show-legend="false" :highlight-id="device.id" :compact="true" :bare="true" />
          </div>
        </div>
      </n-card>

      <n-card v-if="device && relations.length > 1" :title="() => cardHead(TopologyIcon, t('relations.title'))" size="small">
        <relation-chain :nodes="relations" :current-id="device.id" />
      </n-card>

      <DevicePortsPanel v-if="device" :device-id="device.id" :device-name="device.name" :admin="isAdmin" />

      <DevicePowerPortsPanel v-if="device" :device-id="device.id" :device-name="device.name" :admin="isAdmin" />

      <n-card v-if="device" :title="() => cardHead(AddressesIcon, `${t('addresses.ip_list_title')} (${addresses.length})`)">
        <template #header-extra>
          <n-space>
            <n-button size="small" type="primary" @click="openLinkIp">
              <template #icon><n-icon><LinkIcon /></n-icon></template>
              {{ t("devices.link_ip") }}
            </n-button>
            <ColumnPicker size="small" :all="ipColumnPickerItems" :visible="ipVisibleKeys"
                          @update:visible="setIpVisible" @reset="resetIpVisible" />
            <n-button size="small" @click="load(device.id)" :loading="loading">
              <template #icon><n-icon><RefreshIcon /></n-icon></template>
              {{ t("common.refresh") }}
            </n-button>
          </n-space>
        </template>
        <n-data-table
          :columns="ipColumns"
          :data="addresses"
          :pagination="pg"
          :bordered="false"
          size="small"
          :scroll-x="1162"
          :row-props="(row: IPAddress) => ({
            style: 'cursor: pointer',
            onClick: () => openRow(row),
          })"
        >
          <template #empty>
            <n-space justify="center">{{ t("common.no_data") }}</n-space>
          </template>
        </n-data-table>
      </n-card>

      <n-modal v-model:show="showLinkIp" preset="card" :title="t('devices.link_ip')" style="width: 480px">
        <n-space vertical :size="12">
          <span style="font-size: 13px; opacity: .75">{{ t("devices.link_ip_hint") }}</span>
          <n-select v-model:value="linkIpId" :options="linkOptions" filterable clearable
                    :placeholder="t('devices.link_ip_ph')" />
          <n-space justify="end">
            <n-button @click="showLinkIp = false">{{ t("common.cancel") }}</n-button>
            <n-button type="success" :loading="linking" :disabled="!linkIpId" @click="doLinkIp">
              {{ t("common.ok") }}
            </n-button>
          </n-space>
        </n-space>
      </n-modal>

      <n-card v-if="device && lnms" :title="() => cardHead(LibreNMSIcon, 'LibreNMS')">
        <n-descriptions bordered :column="2" size="small" label-placement="left"
                        :label-style="{ whiteSpace: 'nowrap' }">
          <n-descriptions-item :label="t('cols.hostname')">{{ lnms.hostname ?? "—" }}</n-descriptions-item>
          <n-descriptions-item label="OS">{{ lnms.os ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('device_detail.hardware')">{{ lnms.hardware ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('cols.version')">{{ lnms.version ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('devices.serial')">{{ lnms.serial ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('common.status')">{{ lnmsStatusLabel(lnms.status) }}</n-descriptions-item>
          <n-descriptions-item :label="t('device_detail.primary_ip')">{{ lnms.primary_ip ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('scanAgentHelp.col_last_seen')">{{ fmtDateTime(lnms.last_seen_at) }}</n-descriptions-item>
        </n-descriptions>
      </n-card>

      <!-- Wazuh agent（依裝置 IP 比對）-->
      <n-card v-if="integrations && integrations.wazuh" :title="() => cardHead(WazuhIcon, 'Wazuh')" style="margin-top: 16px">
        <n-descriptions bordered :column="2" size="small" label-placement="left"
                        :label-style="{ whiteSpace: 'nowrap' }">
          <n-descriptions-item :label="t('device_detail.wz_agent')">{{ integrations.wazuh.name ?? "—" }} ({{ integrations.wazuh.agent_id }})</n-descriptions-item>
          <n-descriptions-item :label="t('common.status')">{{ integrations.wazuh.status ?? "—" }}</n-descriptions-item>
          <n-descriptions-item label="OS">{{ integrations.wazuh.os_platform ?? "—" }} {{ integrations.wazuh.os_version ?? "" }}</n-descriptions-item>
          <n-descriptions-item :label="t('device_detail.wz_agent_version')">{{ integrations.wazuh.agent_version ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('device_detail.wz_group')">{{ integrations.wazuh.group ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('device_detail.wz_cve')">{{ integrations.wazuh.cve_high ?? 0 }} / {{ integrations.wazuh.cve_critical ?? 0 }}</n-descriptions-item>
          <n-descriptions-item :label="t('device_detail.wz_instance')">{{ integrations.wazuh.instance ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('scanAgentHelp.col_last_seen')">{{ fmtDateTime(integrations.wazuh.last_keep_alive) }}</n-descriptions-item>
        </n-descriptions>
      </n-card>

      <!-- Proxmox VM（依裝置 IP 比對）-->
      <n-card v-if="integrations && integrations.vm" :title="() => cardHead(VirtualizationIcon, t('nav.virtualization'))" style="margin-top: 16px">
        <n-descriptions bordered :column="2" size="small" label-placement="left"
                        :label-style="{ whiteSpace: 'nowrap' }">
          <n-descriptions-item label="VM">{{ integrations.vm.name ?? "—" }}</n-descriptions-item>
          <n-descriptions-item label="status">{{ integrations.vm.status ?? "—" }}</n-descriptions-item>
          <n-descriptions-item label="node">{{ integrations.vm.node ?? "—" }}</n-descriptions-item>
          <n-descriptions-item label="cluster">{{ integrations.vm.cluster ?? "—" }}</n-descriptions-item>
          <n-descriptions-item label="vCPU">{{ integrations.vm.vcpus ?? "—" }}</n-descriptions-item>
          <n-descriptions-item label="RAM (MB)">{{ integrations.vm.memory_mb ?? "—" }}</n-descriptions-item>
        </n-descriptions>
      </n-card>

      <n-card v-if="device && vlans.length" :title="() => cardHead(SubnetsIcon, `VLAN (${vlans.length})`)">
        <n-space :size="8" style="flex-wrap: wrap">
          <n-tag v-for="v in vlans" :key="v.vlan_id" type="info" :bordered="false" size="small">
            {{ v.number }} · {{ v.name }}
            <span style="opacity: .55; margin-left: 4px; font-size: 11px">{{ v.source }}</span>
          </n-tag>
        </n-space>
      </n-card>
    </n-space>
  </n-spin>

  <IPAddressEditModal
    v-model:show="modalShow"
    :address="selected"
    @saved="onSaved"
    @deleted="onDeleted"
  />
  <DeviceEditModal v-model:show="editShow" :device="device"
                   @saved="() => load(String(route.params.id))" />
</template>

<style scoped>
.entity-link {
  color: var(--primary-color, #18a058);
  text-decoration: none;
  cursor: pointer;
}
.entity-link:hover { text-decoration: underline; }
/* 第一張卡片：左資訊 + 右側機櫃圖（標示本機位置） */
.dev-head-row { display: flex; gap: 16px; align-items: flex-start; flex-wrap: wrap; }
/* 寬表(IP 清單)用內部水平捲動吸收寬度，卡片不被撐爆溢出 */
:deep(.n-card) { min-width: 0; }
:deep(.n-data-table) { max-width: 100%; }
.dev-head-info { flex: 1 1 420px; min-width: 0; }
.dev-head-rack { flex: 0 0 auto; max-width: 320px; }
@media (max-width: 900px) { .dev-head-rack { max-width: 100%; flex: 1 1 100%; } }
</style>
