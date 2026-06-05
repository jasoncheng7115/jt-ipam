<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NIcon, NButton, NModal, NForm, NFormItem,
  NInput, NInputNumber, NSelect, NTabs, NTabPane, NPopconfirm, NTooltip,
  NSpin, NEmpty, NTable,
  useMessage, type DataTableColumns, type DataTableRowKey,
} from "naive-ui";
import {
  listVLANDomains, listVLANs, createVLANDomain, createVLAN,
  updateVLAN, deleteVLAN, updateVLANDomain, deleteVLANDomain,
  bulkDeleteVLANs, bulkDeleteVLANDomains, getVlanDevices, vlanMembers,
  type VLAN, type VLANDomain, type VLANDevice, type VLANMembers,
} from "@/api/basic";
import {
  VlansIcon, SectionsIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SaveIcon, CancelIcon,
} from "@/icons";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { useCustomers } from "@/composables/useCustomers";
import { listSections } from "@/api/sections";

const { options: customerOptions, labelFor: customerLabelFor, ensureLoaded: ensureCustomers } = useCustomers();
const sectionOptions = ref<{ label: string; value: string }[]>([]);
const sectionMap = ref<Record<string, string>>({});
const customerFilter = ref<string | null>(null);
const sectionFilter = ref<string | null>(null);

const { t } = useI18n();
const msg = useMessage();
const tab = ref<"vlans" | "domains">("vlans");

const domains = ref<VLANDomain[]>([]);
const vlans = ref<VLAN[]>([]);
import { useTableQuickFilter } from "@/composables/useTableQuickFilter";
const { query: vlanFilterQ, filtered: vlansFiltered } = useTableQuickFilter(vlans);
const loading = ref(false);

const showVLAN = ref(false);
const editingVLAN = ref<VLAN | null>(null);
const vlanForm = ref({
  domain_id: "", number: 100, name: "", description: "",
  customer_id: null as string | null, section_id: null as string | null,
});

const showDom = ref(false);
const editingDom = ref<VLANDomain | null>(null);
const domForm = ref({ name: "", description: "" });

// feature C：VLAN 上的裝置清單 modal
const showDevices = ref(false);
const devicesLoading = ref(false);
const deviceList = ref<VLANDevice[]>([]);
const devicesVlanLabel = ref("");
async function openVlanDevices(r: VLAN) {
  devicesVlanLabel.value = `VLAN ${r.number} · ${r.name}`;
  showDevices.value = true;
  devicesLoading.value = true;
  deviceList.value = [];
  try { deviceList.value = await getVlanDevices(r.id); }
  catch { /* silent */ } finally { devicesLoading.value = false; }
}

// 連接埠 / 成員明細（哪些裝置的哪些 port + 子網路）
const showMembers = ref(false);
const membersLoading = ref(false);
const members = ref<VLANMembers | null>(null);
const membersLabel = ref("");
async function openVlanMembers(r: VLAN) {
  membersLabel.value = `VLAN ${r.number} · ${r.name}`;
  showMembers.value = true;
  membersLoading.value = true;
  members.value = null;
  try { members.value = await vlanMembers(r.id); }
  catch { /* silent */ } finally { membersLoading.value = false; }
}
const memberPortCols: DataTableColumns<any> = [
  { title: t("cols.device"), key: "device", ellipsis: { tooltip: true } },
  { title: t("cols.port"), key: "port", width: 160 },
  { title: "MAC", key: "mac", width: 170, render: (r: any) => r.mac ?? "—" },
];

const vlanChecked = ref<DataTableRowKey[]>([]);
const domChecked = ref<DataTableRowKey[]>([]);
const bulkBusy = ref(false);

async function bulkDelVlans() {
  if (!vlanChecked.value.length) return;
  bulkBusy.value = true;
  try {
    const res = await bulkDeleteVLANs(vlanChecked.value.map(String));
    if (res.failed) msg.warning(t("common.deleted_failed", { deleted: res.deleted, failed: res.failed }));
    else msg.success(t("common.deleted_n", { n: res.deleted }));
    vlanChecked.value = [];
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.network")); }
  finally { bulkBusy.value = false; }
}
async function bulkDelDoms() {
  if (!domChecked.value.length) return;
  bulkBusy.value = true;
  try {
    const res = await bulkDeleteVLANDomains(domChecked.value.map(String));
    if (res.failed) msg.warning(t("common.deleted_failed", { deleted: res.deleted, failed: res.failed }));
    else msg.success(t("common.deleted_n", { n: res.deleted }));
    domChecked.value = [];
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.network")); }
  finally { bulkBusy.value = false; }
}

const domainOptions = computed(() =>
  domains.value.map((d) => ({ label: d.name, value: d.id })));

async function refresh() {
  loading.value = true;
  try {
    const [d, v] = await Promise.all([
      listVLANDomains(),
      listVLANs(undefined, {
        customer_id: customerFilter.value || undefined,
        section_id: sectionFilter.value || undefined,
      }),
    ]);
    domains.value = d.items;
    vlans.value = v.items;
  } catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}

async function loadSectionOptions() {
  try {
    const r = await listSections(1, 500);
    sectionOptions.value = r.items.map((s) => ({ label: s.name, value: s.id }));
    sectionMap.value = Object.fromEntries(r.items.map((s) => [s.id, s.name]));
  } catch { /* silent */ }
}

function openVlanCreate() {
  editingVLAN.value = null;
  vlanForm.value = {
    domain_id: domains.value[0]?.id ?? "", number: 100, name: "", description: "",
    customer_id: null, section_id: null,
  };
  showVLAN.value = true;
}
function openVlanEdit(r: VLAN) {
  editingVLAN.value = r;
  vlanForm.value = {
    domain_id: r.domain_id, number: r.number, name: r.name, description: r.description ?? "",
    customer_id: r.customer_id ?? null, section_id: r.section_id ?? null,
  };
  showVLAN.value = true;
}
async function submitVlan() {
  try {
    if (editingVLAN.value) {
      await updateVLAN(editingVLAN.value.id, {
        name: vlanForm.value.name,
        description: vlanForm.value.description || undefined,
        customer_id: vlanForm.value.customer_id ?? null,
        section_id: vlanForm.value.section_id ?? null,
      });
    } else {
      await createVLAN({
        domain_id: vlanForm.value.domain_id,
        number: vlanForm.value.number,
        name: vlanForm.value.name,
        description: vlanForm.value.description || undefined,
        customer_id: vlanForm.value.customer_id ?? null,
        section_id: vlanForm.value.section_id ?? null,
      });
    }
    showVLAN.value = false;
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function delVlan(r: VLAN) {
  try { await deleteVLAN(r.id); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

function openDomCreate() {
  editingDom.value = null;
  domForm.value = { name: "", description: "" };
  showDom.value = true;
}
function openDomEdit(r: VLANDomain) {
  editingDom.value = r;
  domForm.value = { name: r.name, description: r.description ?? "" };
  showDom.value = true;
}
async function submitDom() {
  try {
    if (editingDom.value) {
      await updateVLANDomain(editingDom.value.id, {
        name: domForm.value.name,
        description: domForm.value.description || undefined,
      });
    } else {
      await createVLANDomain(domForm.value.name, domForm.value.description || undefined);
    }
    showDom.value = false;
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function delDom(r: VLANDomain) {
  try { await deleteVLANDomain(r.id); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

const { visibleKeys: vlanVisible, setVisible: setVlanVisible, reset: resetVlan } = useColumnPrefs(
  "vlans",
  ["number", "name", "domain_id", "devices", "ports", "ips", "customer", "description", "actions"],
  ["number", "name", "domain_id", "devices", "ports", "ips", "customer", "section", "description", "actions"],
);
const vlanPickerItems = [
  { key: "number", label: "VID" },
  { key: "name", label: t("cols.name") },
  { key: "domain_id", label: "Domain" },
  { key: "devices", label: t("cols.device_count") },
  { key: "ports", label: t("vlans.port_count") },
  { key: "ips", label: t("vlans.ip_count") },
  { key: "customer", label: t("cols.unit") },
  { key: "section", label: t("cols.section") },
  { key: "description", label: t("cols.description") },
  { key: "actions", label: t("cols.actions") },
];
const { visibleKeys: domVisible, setVisible: setDomVisible, reset: resetDom } = useColumnPrefs(
  "vlan_domains",
  ["name", "description", "actions"],
  ["name", "description", "actions"],
);
const domPickerItems = [
  { key: "name", label: t("cols.name") },
  { key: "description", label: t("cols.description") },
  { key: "actions", label: t("cols.actions") },
];
function iconAction(icon: any, label: string, onClick: () => void, type?: any) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}
const allVlanCols = computed<DataTableColumns<VLAN>>(() => [
  { type: "selection" },
  { title: t("cols.vid"), key: "number", width: 70, sorter: (a, b) => a.number - b.number },
  { title: t("common.name"), key: "name", minWidth: 160, ellipsis: { tooltip: true }, sorter: (a, b) => a.name.localeCompare(b.name) },
  {
    title: t("cols.domain"), key: "domain_id", width: 120, ellipsis: { tooltip: true },
    render: (r) => domains.value.find((d) => d.id === r.domain_id)?.name ?? "—",
    sorter: (a, b) => {
      const an = domains.value.find((d) => d.id === a.domain_id)?.name ?? "";
      const bn = domains.value.find((d) => d.id === b.domain_id)?.name ?? "";
      return an.localeCompare(bn);
    },
  },
  {
    title: t("vlans.device_count"), key: "devices", width: 72,
    sorter: (a, b) => (a.device_count ?? 0) - (b.device_count ?? 0),
    render: (r) => (r.device_count ?? 0) > 0
      ? h(NButton, { size: "tiny", text: true, type: "primary", onClick: () => openVlanDevices(r) },
          { default: () => String(r.device_count) })
      : "—",
  },
  {
    title: t("vlans.port_count"), key: "ports", width: 76,
    sorter: (a, b) => (a.port_count ?? 0) - (b.port_count ?? 0),
    render: (r) => (r.port_count ?? 0) > 0
      ? h(NButton, { size: "tiny", text: true, type: "primary", onClick: () => openVlanMembers(r) },
          { default: () => String(r.port_count) })
      : "—",
  },
  {
    title: t("vlans.ip_count"), key: "ips", width: 68,
    sorter: (a, b) => (a.ip_count ?? 0) - (b.ip_count ?? 0),
    render: (r) => String(r.ip_count ?? 0),
  },
  {
    title: t("nav.customers"), key: "customer", width: 130, ellipsis: { tooltip: true },
    render: (r) => r.customer_id ? customerLabelFor(r.customer_id) : "—",
  },
  {
    title: t("nav.sections"), key: "section", width: 130, ellipsis: { tooltip: true },
    render: (r) => r.section_id ? (sectionMap.value[r.section_id] ?? "—") : "—",
  },
  { title: t("common.description"), key: "description", minWidth: 200, ellipsis: { tooltip: true },
    render: (r) => r.description ?? "—",
    sorter: (a, b) => (a.description ?? "").localeCompare(b.description ?? "") },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 96,
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openVlanEdit(r)),
      h(NPopconfirm, { onPositiveClick: () => delVlan(r) }, {
        trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]);
const vlanCols = computed<DataTableColumns<VLAN>>(() =>
  allVlanCols.value.filter((c: any) => c.type === "selection" || vlanVisible.value.includes(c.key)),
);
const allDomCols = computed<DataTableColumns<VLANDomain>>(() => [
  { type: "selection" },
  { title: t("common.name"), key: "name", minWidth: 180, ellipsis: { tooltip: true }, sorter: (a, b) => a.name.localeCompare(b.name) },
  { title: t("common.description"), key: "description", minWidth: 240, ellipsis: { tooltip: true },
    render: (r) => r.description ?? "—",
    sorter: (a, b) => (a.description ?? "").localeCompare(b.description ?? "") },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 96,
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openDomEdit(r)),
      h(NPopconfirm, { onPositiveClick: () => delDom(r) }, {
        trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]);
const domCols = computed<DataTableColumns<VLANDomain>>(() =>
  allDomCols.value.filter((c: any) => c.type === "selection" || domVisible.value.includes(c.key)),
);

watch([customerFilter, sectionFilter], () => { void refresh(); });
onMounted(() => {
  void refresh();
  void loadSectionOptions();
  void ensureCustomers();
});
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><VlansIcon /></n-icon>
        <span>{{ t("nav.vlans") }}</span>
      </n-space>
    </template>
    <n-tabs v-model:value="tab" type="line">
      <n-tab-pane name="vlans">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><VlansIcon /></n-icon>{{ t('nav.vlans') }}</span>
        </template>
        <n-space style="margin-bottom: 12px" align="center">
          <n-input v-model:value="vlanFilterQ" :placeholder="t('common.filter')" clearable style="width: 160px" />
          <n-button @click="refresh" :loading="loading">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>
            {{ t("common.refresh") }}
          </n-button>
          <n-button type="primary" @click="openVlanCreate">
            <template #icon><n-icon><PlusIcon /></n-icon></template>
            {{ t("common.create") }}
          </n-button>
          <n-select v-model:value="sectionFilter" :options="sectionOptions"
                    clearable filterable :placeholder="t('vlans.filter_section')" style="width: 180px" />
          <n-select v-model:value="customerFilter" :options="customerOptions"
                    clearable filterable :placeholder="t('vlans.filter_unit')" style="width: 180px" />
          <ColumnPicker :all="vlanPickerItems" :visible="vlanVisible"
                        @update:visible="setVlanVisible" @reset="resetVlan" />
          <ExportButton :columns="vlanCols" :rows="vlans" filename="vlans" :title="t('nav.vlans')" />
        </n-space>
        <n-space v-if="vlanChecked.length" align="center" style="margin-bottom: 8px; padding: 8px 12px; background: rgba(127,127,127,0.08); border-radius: 6px;">
          <span>{{ t("common.selected_n", { n: vlanChecked.length }) }}</span>
          <n-popconfirm @positive-click="bulkDelVlans">
            <template #trigger>
              <n-button type="error" size="small" :loading="bulkBusy">
                <template #icon><n-icon><DeleteIcon /></n-icon></template>
                {{ t("common.bulk_delete") }}
              </n-button>
            </template>
            {{ t("common.confirm_delete_n", { n: vlanChecked.length }) }}
          </n-popconfirm>
          <n-button size="small" @click="vlanChecked = []">{{ t("common.clear_selection") }}</n-button>
        </n-space>
        <n-data-table
          :columns="vlanCols" :data="vlansFiltered" :loading="loading" :bordered="false"
          :scroll-x="992"
          :row-key="(row: VLAN) => row.id"
          :checked-row-keys="vlanChecked"
          @update:checked-row-keys="(keys: DataTableRowKey[]) => vlanChecked = keys"
        />
      </n-tab-pane>
      <n-tab-pane name="domains">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><SectionsIcon /></n-icon>VLAN Domain</span>
        </template>
        <n-space style="margin-bottom: 12px">
          <n-button type="primary" @click="openDomCreate">
            <template #icon><n-icon><PlusIcon /></n-icon></template>
            {{ t("common.create") }}
          </n-button>
          <ColumnPicker :all="domPickerItems" :visible="domVisible"
                        @update:visible="setDomVisible" @reset="resetDom" />
        </n-space>
        <n-space v-if="domChecked.length" align="center" style="margin-bottom: 8px; padding: 8px 12px; background: rgba(127,127,127,0.08); border-radius: 6px;">
          <span>{{ t("common.selected_n", { n: domChecked.length }) }}</span>
          <n-popconfirm @positive-click="bulkDelDoms">
            <template #trigger>
              <n-button type="error" size="small" :loading="bulkBusy">
                <template #icon><n-icon><DeleteIcon /></n-icon></template>
                {{ t("common.bulk_delete") }}
              </n-button>
            </template>
            {{ t("common.confirm_delete_n", { n: domChecked.length }) }}
          </n-popconfirm>
          <n-button size="small" @click="domChecked = []">{{ t("common.clear_selection") }}</n-button>
        </n-space>
        <n-data-table
          :columns="domCols" :data="domains" :loading="loading" :bordered="false"
          :scroll-x="556"
          :row-key="(row: VLANDomain) => row.id"
          :checked-row-keys="domChecked"
          @update:checked-row-keys="(keys: DataTableRowKey[]) => domChecked = keys"
        />
      </n-tab-pane>
    </n-tabs>

    <n-modal v-model:show="showVLAN" preset="card" style="width: 460px">
      <template #header>
        <n-space align="center">
          <n-icon :size="20"><component :is="editingVLAN ? EditIcon : PlusIcon" /></n-icon>
          <span>{{ editingVLAN ? t("common.edit") : t("common.create") }}</span>
        </n-space>
      </template>
      <n-form>
        <n-form-item label="Domain">
          <n-select v-model:value="vlanForm.domain_id" :options="domainOptions"
                    :disabled="!!editingVLAN" />
        </n-form-item>
        <n-form-item label="VID">
          <n-input-number v-model:value="vlanForm.number" :min="1" :max="4094"
                          :disabled="!!editingVLAN" />
        </n-form-item>
        <n-form-item :label="t('common.name')">
          <n-input v-model:value="vlanForm.name" />
        </n-form-item>
        <n-form-item :label="t('nav.sections')">
          <n-select v-model:value="vlanForm.section_id" :options="sectionOptions"
                    clearable filterable placeholder="—" />
        </n-form-item>
        <n-form-item :label="t('nav.customers')">
          <n-select v-model:value="vlanForm.customer_id" :options="customerOptions"
                    clearable filterable placeholder="—" />
        </n-form-item>
        <n-form-item :label="t('sections.description')">
          <n-input v-model:value="vlanForm.description" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="showVLAN = false">
          <template #icon><n-icon><CancelIcon /></n-icon></template>
          {{ t("common.cancel") }}
        </n-button>
        <n-button type="primary" @click="submitVlan">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </n-space>
    </n-modal>

    <n-modal v-model:show="showDom" preset="card" style="width: 460px">
      <template #header>
        <n-space align="center">
          <n-icon :size="20"><component :is="editingDom ? EditIcon : PlusIcon" /></n-icon>
          <span>{{ editingDom ? t("common.edit") : t("common.create") }}</span>
        </n-space>
      </template>
      <n-form>
        <n-form-item :label="t('common.name')">
          <n-input v-model:value="domForm.name" />
        </n-form-item>
        <n-form-item :label="t('sections.description')">
          <n-input v-model:value="domForm.description" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="showDom = false">
          <template #icon><n-icon><CancelIcon /></n-icon></template>
          {{ t("common.cancel") }}
        </n-button>
        <n-button type="primary" @click="submitDom">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </n-space>
    </n-modal>

    <n-modal v-model:show="showDevices" preset="card"
             :title="`${devicesVlanLabel} — ${t('vlans.devices_on_vlan')}`"
             style="width: 520px">
      <n-spin :show="devicesLoading">
        <n-empty v-if="!devicesLoading && !deviceList.length" :description="t('common.no_data')" />
        <n-table v-else :bordered="false" size="small">
          <thead>
            <tr><th>{{ t("addresses.hostname") }}</th><th>IP</th><th>{{ t("addresses.source") }}</th></tr>
          </thead>
          <tbody>
            <tr v-for="d in deviceList" :key="d.librenms_device_id">
              <td>{{ d.hostname ?? "—" }}</td>
              <td>{{ d.primary_ip ?? "—" }}</td>
              <td>{{ d.source }}</td>
            </tr>
          </tbody>
        </n-table>
      </n-spin>
    </n-modal>

    <!-- VLAN 成員：連接埠 + 子網路 -->
    <n-modal v-model:show="showMembers" preset="card"
             :title="`${membersLabel} — ${t('vlans.members')}`" style="width: 640px">
      <n-spin :show="membersLoading">
        <template v-if="members">
          <div style="font-weight:600;margin-bottom:6px">{{ t('vlans.ports_on_vlan') }} ({{ members.ports.length }})</div>
          <n-empty v-if="!members.ports.length" :description="t('common.no_data')" size="small" />
          <n-data-table v-else :columns="memberPortCols" :data="members.ports"
                        :bordered="false" size="small" :max-height="320" :scroll-x="500" />
          <div v-if="members.subnets.length" style="font-weight:600;margin:14px 0 6px">{{ t('nav.subnets') }}</div>
          <n-space v-if="members.subnets.length">
            <n-tag v-for="s in members.subnets" :key="s.id" size="small">
              {{ s.cidr }} · {{ s.ip_count }} IP
            </n-tag>
          </n-space>
        </template>
      </n-spin>
    </n-modal>
  </n-card>
</template>
