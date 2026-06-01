<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import {
  NCard, NSpace, NIcon, NButton, NDescriptions, NDescriptionsItem,
  NTag, NDataTable, NSpin,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { ArrowLeft as ArrowLeftIcon } from "@iconoir/vue";
import { DevicesIcon, RefreshIcon, EditIcon } from "@/icons";
import { apiClient } from "@/api/client";
import { listAddresses } from "@/api/addresses";
import { listLocations, listRacks, getDeviceVlans, getDeviceLibrenms, type Device, type Location, type Rack, type DeviceVLAN, type DeviceLibreNMS } from "@/api/basic";
import IPAddressEditModal from "@/components/IPAddressEditModal.vue";
import LiveStatusDot from "@/components/LiveStatusDot.vue";
import type { IPAddress } from "@/types";
import { autoSort } from "@/composables/useTableSort";
import { fmtDateTime } from "@/utils/datetime";
import { useCustomers } from "@/composables/useCustomers";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import ColumnPicker from "@/components/ColumnPicker.vue";
const { t } = useI18n();

const { labelFor: customerLabelFor, ensureLoaded: ensureCustomersLoaded } = useCustomers();
const { visibleKeys: ipVisibleKeys, setVisible: setIpVisible, reset: resetIpVisible } = useColumnPrefs(
  "device_detail_ips",
  ["live", "ip", "hostname", "state", "mac", "mac_vendor", "switch_port", "description", "last_seen"],
  ["live", "ip", "hostname", "state", "mac", "mac_vendor", "switch_port", "description", "last_seen"],
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
const location = ref<Location | null>(null);
const rack = ref<Rack | null>(null);
const addresses = ref<IPAddress[]>([]);
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
    } else { rack.value = null; }
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
  { title: t("addresses.ip"), key: "ip", width: 140 },
  { title: t("addresses.hostname"), key: "hostname", minWidth: 140,
    ellipsis: { tooltip: true }, render: (r) => r.hostname ?? "" },
  { title: t("common.status"), key: "state", width: 100, render: (r) => stateTag(r.state) },
  { title: t("addresses.mac"), key: "mac", width: 150, render: (r) => r.mac ?? "" },
  { title: t("cols.vendor"), key: "mac_vendor", width: 140,
    ellipsis: { tooltip: true }, render: (r) => r.mac_vendor ?? "—" },
  { title: t("addresses.switch_port"), key: "switch_port", width: 140,
    ellipsis: { tooltip: true }, render: (r) => r.switch_port ?? "" },
  { title: t("common.description"), key: "description", width: 220,
    ellipsis: { tooltip: true }, render: (r) => r.description ?? "" },
  { title: t("addresses.last_seen"), key: "last_seen", width: 170, render: (r) => lastSeen(r) },
]));

const ipColumns = computed<DataTableColumns<IPAddress>>(() =>
  allIpColumns.value.filter((c: any) => ipVisibleKeys.value.includes(c.key)),
);

function openRow(row: IPAddress) {
  selected.value = row;
  modalShow.value = true;
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
            <n-button type="primary" size="small"
                      @click="router.push({ name: 'devices', query: { edit: device.id } })">
              <template #icon><n-icon><EditIcon /></n-icon></template>
              {{ t("common.edit") }}
            </n-button>
            <n-button @click="router.push({ name: 'devices' })" size="small">
              <template #icon><n-icon><ArrowLeftIcon /></n-icon></template>
              {{ t("common.back") }}
            </n-button>
          </n-space>
        </template>
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
      </n-card>

      <n-card v-if="device" :title="`${t('addresses.ip_list_title')}(${addresses.length})`">
        <template #header-extra>
          <n-space>
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
          :pagination="{ pageSize: 50, showSizePicker: true, pageSizes: [25, 50, 100] }"
          :bordered="false"
          size="small"
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

      <n-card v-if="device && lnms" title="LibreNMS">
        <n-descriptions bordered :column="2" size="small" label-placement="left"
                        :label-style="{ whiteSpace: 'nowrap' }">
          <n-descriptions-item label="hostname">{{ lnms.hostname ?? "—" }}</n-descriptions-item>
          <n-descriptions-item label="OS">{{ lnms.os ?? "—" }}</n-descriptions-item>
          <n-descriptions-item label="hardware">{{ lnms.hardware ?? "—" }}</n-descriptions-item>
          <n-descriptions-item label="version">{{ lnms.version ?? "—" }}</n-descriptions-item>
          <n-descriptions-item label="serial">{{ lnms.serial ?? "—" }}</n-descriptions-item>
          <n-descriptions-item label="status">{{ lnms.status ?? "—" }}</n-descriptions-item>
          <n-descriptions-item label="primary IP">{{ lnms.primary_ip ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('scanAgentHelp.col_last_seen')">{{ fmtDateTime(lnms.last_seen_at) }}</n-descriptions-item>
        </n-descriptions>
      </n-card>

      <!-- Wazuh agent（依裝置 IP 比對）-->
      <n-card v-if="integrations && integrations.wazuh" title="Wazuh" style="margin-top: 16px">
        <n-descriptions bordered :column="2" size="small" label-placement="left"
                        :label-style="{ whiteSpace: 'nowrap' }">
          <n-descriptions-item label="agent">{{ integrations.wazuh.name ?? "—" }} ({{ integrations.wazuh.agent_id }})</n-descriptions-item>
          <n-descriptions-item label="status">{{ integrations.wazuh.status ?? "—" }}</n-descriptions-item>
          <n-descriptions-item label="OS">{{ integrations.wazuh.os_platform ?? "—" }} {{ integrations.wazuh.os_version ?? "" }}</n-descriptions-item>
          <n-descriptions-item label="agent version">{{ integrations.wazuh.agent_version ?? "—" }}</n-descriptions-item>
          <n-descriptions-item label="group">{{ integrations.wazuh.group ?? "—" }}</n-descriptions-item>
          <n-descriptions-item label="CVE (high/crit)">{{ integrations.wazuh.cve_high ?? 0 }} / {{ integrations.wazuh.cve_critical ?? 0 }}</n-descriptions-item>
          <n-descriptions-item label="instance">{{ integrations.wazuh.instance ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('scanAgentHelp.col_last_seen')">{{ fmtDateTime(integrations.wazuh.last_keep_alive) }}</n-descriptions-item>
        </n-descriptions>
      </n-card>

      <!-- Proxmox VM（依裝置 IP 比對）-->
      <n-card v-if="integrations && integrations.vm" :title="t('nav.virtualization')" style="margin-top: 16px">
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

      <n-card v-if="device && vlans.length" :title="`VLAN(${vlans.length})`">
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
</template>

<style scoped>
.entity-link {
  color: var(--primary-color, #18a058);
  text-decoration: none;
  cursor: pointer;
}
.entity-link:hover { text-decoration: underline; }
</style>
