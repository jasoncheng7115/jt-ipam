<template>
  <div class="osp">
    <!-- 這個連線的概況：最近同步、最近錯誤、最近一次測試得到的能力 -->
    <div class="osp-top">
      <div class="osp-meta">
        <div class="osp-line">
          <n-tag size="small" :bordered="false" :type="instance.product === 'cubecos' ? 'info' : 'default'">
            {{ instance.product === "cubecos" ? "Bigstack CubeCOS" : "OpenStack" }}
          </n-tag>
          <span class="osp-url">{{ instance.auth_url }}</span>
          <n-tag v-if="/^http:\/\//i.test(instance.auth_url)" size="small" type="warning" :bordered="false"
                 :title="t('openstack.http_warning')">{{ t("openstack.http_tag") }}</n-tag>
        </div>
        <div class="osp-line osp-dim">
          {{ t("cols.last_sync") }}：{{ instance.last_sync_at ? fmtDateTime(instance.last_sync_at) : "—" }}
          <template v-if="instance.last_full_success_at">
            ・{{ t("openstack.last_full_success", { at: fmtDateTime(instance.last_full_success_at) }) }}
          </template>
        </div>
        <div v-if="instance.last_error" class="osp-line osp-err">{{ instance.last_error }}</div>
        <n-space :size="8" style="margin-top: 8px">
          <n-button size="small" :loading="testing" :disabled="testing" data-testid="os-panel-test" @click="emit('test')">
            <template #icon><n-icon><TestIcon /></n-icon></template>{{ t("common.test") }}
          </n-button>
          <n-button size="small" type="primary" :loading="syncing" :disabled="syncing" data-testid="os-panel-sync"
                    @click="emit('sync')">
            <template #icon><n-icon><SyncIcon /></n-icon></template>{{ t("common.pull") }}
          </n-button>
          <n-button size="small" :loading="loading" @click="reload">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}
          </n-button>
        </n-space>
      </div>
      <div class="osp-caps">
        <OpenStackCapabilityList v-if="instance.capabilities" :capabilities="instance.capabilities"
                                 :product="instance.product" />
        <div v-else class="osp-dim">{{ t("openstack.no_caps") }}</div>
      </div>
    </div>

    <n-tabs v-model:value="sub" type="line" size="small" style="margin-top: 12px">
      <n-tab-pane name="subnets">
        <template #tab>
          <span class="tab-l"><n-icon :size="16"><SubnetsIcon /></n-icon>{{ `${t("openstack.tab_subnets")} (${subnets.length})` }}</span>
        </template>
        <n-alert type="info" :bordered="false" :show-icon="false" style="margin-bottom: 8px">
          {{ t("openstack.subnets_hint") }}
        </n-alert>
        <n-space align="center" style="margin-bottom: 8px">
          <n-input v-model:value="q" clearable style="width: 180px" :placeholder="t('common.filter')" />
          <span class="osp-switch">
            <n-switch v-model:value="onlyPending" size="small" data-testid="os-only-pending" />
            {{ t("openstack.only_pending") }}
          </span>
          <n-tag v-if="pendingCount" type="warning" size="small" :bordered="false" data-testid="os-pending-count">
            {{ t("openstack.pending_count", { n: pendingCount }) }}
          </n-tag>
          <ExportButton :columns="subnetCols" :rows="shownSubnets" :filename="`openstack-subnets-${instance.name}`"
                        :title="t('openstack.tab_subnets')" />
        </n-space>
        <n-data-table :columns="subnetCols" :data="shownSubnets" :loading="loading" size="small" :bordered="false"
                      :scroll-x="1116" :pagination="pg" :row-key="(r: OpenStackSubnet) => r.id"
                      data-testid="os-subnets-table">
          <template #empty><span class="osp-dim">{{ t("openstack.subnets_empty") }}</span></template>
        </n-data-table>
      </n-tab-pane>
      <n-tab-pane v-if="instance.product === 'cubecos'" name="nodes">
        <template #tab>
          <span class="tab-l"><n-icon :size="16"><DevicesIcon /></n-icon>{{ `${t("openstack.tab_nodes")} (${nodes.length})` }}</span>
        </template>
        <n-data-table :columns="nodeCols" :data="nodes" :loading="loading" size="small" :bordered="false"
                      :scroll-x="960" :row-key="(r: CubeCOSNode) => r.hostname" data-testid="os-nodes-table">
          <template #empty><span class="osp-dim">{{ t("openstack.nodes_empty") }}</span></template>
        </n-data-table>
      </n-tab-pane>
    </n-tabs>
  </div>
</template>

<script setup lang="ts">
/**
 * 一個 OpenStack 連線的頁籤內容：概況與能力、子網路對應、CubeCOS 節點。
 *
 * 子網路對應決定 VM 的位址能不能連到 IP 記錄（只有對到 jt-ipam 子網路的 Neutron 子網路才會連），
 * 所以「待對應」要一眼看得到、在這裡就能指定。
 */
import { computed, h, onMounted, ref, watch } from "vue";
import {
  NAlert, NButton, NDataTable, NIcon, NInput, NSelect, NSpace, NSwitch, NTabPane, NTabs, NTag, NTooltip,
  useMessage, type DataTableColumns, type SelectOption,
} from "naive-ui";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import OpenStackCapabilityList from "@/components/OpenStackCapabilityList.vue";
import ExportButton from "@/components/ExportButton.vue";
import {
  OpenStack, mappingState, type CubeCOSNode, type OpenStackInstance, type OpenStackSubnet,
} from "@/api/openstack";
import { apiErrMsg } from "@/api/client";
import { useTablePagination } from "@/composables/useTablePagination";
import { DevicesIcon, RefreshIcon, SubnetsIcon, SyncIcon, TestIcon } from "@/icons";
import { fmtDateTime } from "@/utils/datetime";
import { withExportValue } from "@/utils/tableExport";
import { autoSort } from "@/composables/useTableSort";
import { cmpNatural } from "@/utils/sort";

export interface SubnetChoice { label: string; value: string; cidr: string }

const props = defineProps<{
  instance: OpenStackInstance;
  /** 子網路下拉：管理員頁面，列出的都是可以修改的子網路 */
  subnetOptions: SubnetChoice[];
  testing?: boolean;
  syncing?: boolean;
}>();
const emit = defineEmits<{ (e: "test"): void; (e: "sync"): void }>();

const { t } = useI18n();
const msg = useMessage();
const router = useRouter();
const pg = useTablePagination();

const sub = ref<"subnets" | "nodes">("subnets");
const subnets = ref<OpenStackSubnet[]>([]);
const nodes = ref<CubeCOSNode[]>([]);
const loading = ref(false);
const savingRow = ref<string | null>(null);
const q = ref("");
const onlyPending = ref(false);

async function reload() {
  loading.value = true;
  try {
    const [s, n] = await Promise.all([
      OpenStack.subnets(props.instance.id),
      props.instance.product === "cubecos" ? OpenStack.nodes(props.instance.id) : Promise.resolve([]),
    ]);
    // 待對應的排前面：要處理的事放在最上面
    subnets.value = [...s].sort((a, b) => Number(mappingState(b) === "pending") - Number(mappingState(a) === "pending"));
    nodes.value = n;
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}
defineExpose({ reload });

const pendingCount = computed(() => subnets.value.filter((r) => mappingState(r) === "pending").length);
const shownSubnets = computed(() => {
  const k = q.value.trim().toLowerCase();
  return subnets.value.filter((r) => (!onlyPending.value || mappingState(r) === "pending")
    && (!k || [r.name, r.subnet_id, r.network_name, r.project_name, r.cidr, r.gateway_ip, r.mapped_subnet_cidr]
      .some((v) => String(v ?? "").toLowerCase().includes(k))));
});

async function setMapping(r: OpenStackSubnet, value: string | null) {
  savingRow.value = r.id;
  try {
    const out = await OpenStack.mapSubnet(props.instance.id, r.id, value);
    const chosen = props.subnetOptions.find((o) => o.value === value);
    // 後端回傳整列就用回傳的；沒帶的欄位用選到的選項補（畫面立刻反映）
    const merged: OpenStackSubnet = {
      ...r, mapped_subnet_id: value, mapped_manually: true,
      mapped_subnet_cidr: chosen?.cidr ?? null, ...(out && typeof out === "object" ? out : {}),
    };
    subnets.value = subnets.value.map((x) => (x.id === r.id ? merged : x));
    msg.success(t("openstack.map_saved"));
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { savingRow.value = null; }
}

const STATE_TAG: Record<string, "warning" | "success" | "info" | "default"> = {
  pending: "warning", auto: "success", manual: "info", none: "default",
};

const dash = (v: unknown) => (v == null || v === "" ? "—" : String(v));

// 排序比的是畫面上看到的值；空值排最後
const by = <T,>(f: (r: T) => unknown) => (a: T, b: T) => {
  const x = f(a), y = f(b), ex = x == null || x === "", ey = y == null || y === "";
  return ex || ey ? Number(ex) - Number(ey) : cmpNatural(x, y);
};
const STATE_ORDER = ["pending", "manual", "auto", "none"];
const subnetCols = computed<DataTableColumns<OpenStackSubnet>>(() => autoSort<OpenStackSubnet>([
  withExportValue({
    title: t("openstack.col_neutron_subnet"), key: "name", minWidth: 150, ellipsis: { tooltip: true },
    sorter: by((r: OpenStackSubnet) => r.name || r.subnet_id),
    render: (r: OpenStackSubnet) => h(NTooltip, null, {
      trigger: () => h("span", null, r.name || r.subnet_id),
      default: () => r.subnet_id,
    }),
  }, (r) => r.name || r.subnet_id),
  // 對應狀態放前面：待對應一眼就看得到，不必捲到右邊
  withExportValue({
    title: t("openstack.col_mapping_state"), key: "state", width: 100,
    sorter: (a: OpenStackSubnet, b: OpenStackSubnet) => STATE_ORDER.indexOf(mappingState(a)) - STATE_ORDER.indexOf(mappingState(b)),
    render: (r: OpenStackSubnet) => {
      const s = mappingState(r);
      return h(NTag, { size: "small", bordered: false, type: STATE_TAG[s], "data-testid": `os-map-state-${s}` },
        { default: () => t(`openstack.map_${s}`) });
    },
  }, (r) => t(`openstack.map_${mappingState(r)}`)),
  {
    title: t("openstack.col_network"), key: "network_name", minWidth: 140,
    render: (r: OpenStackSubnet) => h("span", { class: "osp-nowrap" }, [
      dash(r.network_name),
      r.shared ? h(NTag, { size: "tiny", bordered: false, style: "margin-left:6px" },
                   { default: () => t("openstack.shared") }) : null,
    ]),
  },
  { title: t("openstack.project"), key: "project_name", minWidth: 110, ellipsis: { tooltip: true },
    render: (r: OpenStackSubnet) => dash(r.project_name) },
  { title: "CIDR", key: "cidr", width: 150 },
  { title: t("openstack.col_gateway"), key: "gateway_ip", width: 130, render: (r: OpenStackSubnet) => dash(r.gateway_ip) },
  withExportValue({
    title: "DHCP", key: "enable_dhcp", width: 76,
    sorter: by((r: OpenStackSubnet) => (r.enable_dhcp == null ? null : r.enable_dhcp ? 0 : 1)),
    render: (r: OpenStackSubnet) => (r.enable_dhcp == null ? "—"
      : h(NTag, { size: "small", bordered: false, type: r.enable_dhcp ? "success" : "default" },
          { default: () => (r.enable_dhcp ? t("common.yes") : t("common.no")) })),
  }, (r) => (r.enable_dhcp == null ? "" : r.enable_dhcp ? t("common.yes") : t("common.no"))),
  withExportValue({
    // 實際上的「操作」：選了就存（PATCH），之後同步不會覆寫
    title: t("openstack.col_mapping"), key: "mapping", width: 260, fixed: "right", className: "col-actions",
    render: (r: OpenStackSubnet) => h(NSelect, {
      value: r.mapped_subnet_id ?? null,
      options: props.subnetOptions as unknown as SelectOption[],
      size: "small", filterable: true, clearable: true, placeholder: t("openstack.map_ph"),
      loading: savingRow.value === r.id, disabled: savingRow.value === r.id,
      consistentMenuWidth: false, status: mappingState(r) === "pending" ? "warning" : undefined,
      "data-testid": "os-map-select",
      // 已對到、但不在下拉清單裡（例如已歸檔）：仍顯示 CIDR，不顯示一串 UUID
      fallbackOption: (v: string | number) => ({ label: r.mapped_subnet_cidr ?? String(v), value: v }),
      // 相同 CIDR 的選項標出來：大多數時候正確答案就是它
      renderLabel: (o: SelectOption) => ((o as unknown as SubnetChoice).cidr === r.cidr
        ? h("span", null, [String(o.label), h(NTag, { size: "tiny", type: "success", bordered: false,
            style: "margin-left:6px" }, { default: () => t("openstack.map_same_cidr") })])
        : String(o.label)),
      "onUpdate:value": (v: string | null) => { if ((v ?? null) !== (r.mapped_subnet_id ?? null)) void setMapping(r, v ?? null); },
    }),
  }, (r) => r.mapped_subnet_cidr ?? ""),
]));

function ipLink(ip?: string | null) {
  if (!ip) return "—";
  return h(NButton, { text: true, type: "primary", onClick: () => router.push({ name: "addresses", query: { q: ip } }) },
    { default: () => ip });
}
const nodeCols = computed<DataTableColumns<CubeCOSNode>>(() => autoSort<CubeCOSNode>([
  { title: t("cols.hostname"), key: "hostname", minWidth: 150, ellipsis: { tooltip: true } },
  { title: t("openstack.col_role"), key: "role", width: 140, render: (r: CubeCOSNode) => dash(r.role) },
  {
    title: t("common.status"), key: "status", width: 110,
    render: (r: CubeCOSNode) => (r.status
      ? h(NTag, { size: "small", bordered: false,
                  type: /^(ok|up|online|ready|active|running|healthy)$/i.test(r.status) ? "success" : "warning" },
          { default: () => r.status })
      : "—"),
  },
  { title: t("openstack.col_mgmt_ip"), key: "management_ip", width: 150, render: (r: CubeCOSNode) => ipLink(r.management_ip) },
  // CubeCOS 的 provider IP 不可靠（上線節點的 ip 就是管理 IP），後端多半不給：有任何一台有值才顯示這一欄
  ...(nodes.value.some((n) => n.provider_ip)
    ? [{ title: t("openstack.col_provider_ip"), key: "provider_ip", width: 150,
         render: (r: CubeCOSNode) => ipLink(r.provider_ip) }]
    : []),
  { title: t("openstack.col_storage_ip"), key: "storage_ip", width: 150, render: (r: CubeCOSNode) => ipLink(r.storage_ip) },
  { title: t("openstack.col_serial"), key: "serial", minWidth: 130, ellipsis: { tooltip: true }, render: (r: CubeCOSNode) => dash(r.serial) },
]));

onMounted(reload);
// 同步跑完、或換了一個連線：重新讀
watch(() => [props.instance.id, props.instance.last_sync_at, props.instance.product], () => { void reload(); });
</script>

<style scoped>
.osp-top { display: flex; gap: 24px; flex-wrap: wrap; }
.osp-meta { flex: 1 1 320px; min-width: 0; }
.osp-caps { flex: 1 1 380px; min-width: 0; }
.osp-line { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 4px; font-size: 13px; }
.osp-url { font-family: var(--font-mono, ui-monospace, monospace); font-size: 12px; overflow-wrap: anywhere; }
.osp-dim { opacity: .65; font-size: 13px; }
.osp-err { color: #d03050; overflow-wrap: anywhere; }
.osp-switch { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; white-space: nowrap; }
.tab-l { display: inline-flex; align-items: center; gap: 6px; }
:deep(.osp-nowrap) { white-space: nowrap; }
</style>
