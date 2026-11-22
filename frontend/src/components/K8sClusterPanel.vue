<template>
  <div>
    <n-space align="center" style="margin-bottom:10px" :wrap-item="false">
      <span class="meta">{{ cluster.api_url }}</span>
      <n-tag v-if="cluster.server_version" size="small" :bordered="false">{{ cluster.server_version }}</n-tag>
      <span class="meta">{{ t("cols.last_sync") }}：{{ cluster.last_sync_at ? fmtDateTime(cluster.last_sync_at) : "—" }}</span>
      <n-button size="small" :loading="testing" :disabled="testing" data-testid="k8s-panel-test" @click="emit('test')">
        <template #icon><n-icon><TestIcon /></n-icon></template>{{ t("common.test") }}
      </n-button>
      <n-button size="small" type="primary" :loading="syncing" :disabled="syncing" data-testid="k8s-panel-sync"
                @click="emit('sync')">
        <template #icon><n-icon><SyncIcon /></n-icon></template>{{ t("common.pull") }}
      </n-button>
    </n-space>
    <n-alert v-if="cluster.last_error" type="error" :bordered="false" style="margin-bottom:10px">
      {{ cluster.last_error }}
    </n-alert>
    <n-alert v-if="cluster.missing_permissions?.length" type="warning" :bordered="false" style="margin-bottom:10px"
             data-testid="k8s-missing-perms">
      {{ t("k8s.missing_perms", { list: cluster.missing_permissions.join(", ") }) }}
    </n-alert>

    <n-tabs v-model:value="sub" type="segment" size="small" animated>
      <n-tab-pane name="nodes" :tab="`${t('k8s.tab_nodes')} (${nodes.length})`">
        <n-data-table :columns="nodeCols" :data="nodes" :loading="loading" size="small" :bordered="false"
                      :scroll-x="1300" :row-key="(r: K8sNode) => r.id" :pagination="pg" data-testid="k8s-nodes" />
      </n-tab-pane>
      <n-tab-pane name="services" :tab="`${t('k8s.tab_services')} (${svcs.length})`">
        <div class="hint" style="margin-bottom:6px">{{ t("k8s.services_hint") }}</div>
        <n-data-table :columns="svcCols" :data="svcs" :loading="loading" size="small" :bordered="false"
                      :scroll-x="1050" :row-key="(r: K8sServiceIP) => r.id" :pagination="pg" data-testid="k8s-services" />
      </n-tab-pane>
      <n-tab-pane name="hostnames" :tab="`${t('k8s.tab_hostnames')} (${hostnames.length})`">
        <div class="hint" style="margin-bottom:6px">{{ t("k8s.hostnames_hint") }}</div>
        <n-data-table :columns="hostCols" :data="hostnames" :loading="loading" size="small" :bordered="false"
                      :scroll-x="1100" :row-key="(r: K8sHostname) => r.id" :pagination="pg" data-testid="k8s-hostnames" />
      </n-tab-pane>
      <n-tab-pane name="pools" :tab="`${t('k8s.tab_pools')} (${pools.length})`">
        <div class="hint" style="margin-bottom:6px">
          {{ cluster.create_ip_ranges ? t("k8s.pools_hint_ranges") : t("k8s.pools_hint_no_ranges") }}
        </div>
        <n-data-table :columns="poolCols" :data="pools" :loading="loading" size="small" :bordered="false"
                      :row-key="(r: K8sPool) => r.id" data-testid="k8s-pools" />
      </n-tab-pane>
      <n-tab-pane name="cidrs" :tab="`${t('k8s.tab_cidrs')} (${cidrs.length})`">
        <div class="hint" style="margin-bottom:6px">{{ t("k8s.cidrs_hint") }}</div>
        <n-data-table :columns="cidrCols" :data="cidrs" :loading="loading" size="small" :bordered="false"
                      :scroll-x="900" :row-key="(r: K8sCidr) => r.id" data-testid="k8s-cidrs" />
      </n-tab-pane>
      <n-tab-pane name="conflicts">
        <template #tab>
          <span :class="{ warn: conflicts.length > 0 }">{{ `${t("k8s.tab_conflicts")} (${conflicts.length})` }}</span>
        </template>
        <n-alert :type="conflicts.length ? 'warning' : 'success'" :bordered="false" style="margin-bottom:8px">
          {{ conflicts.length ? t("k8s.conflicts_found", { n: conflicts.length }) : t("k8s.conflicts_none") }}
        </n-alert>
        <n-data-table v-if="conflicts.length" :columns="conflictCols" :data="conflicts" size="small" :bordered="false"
                      :scroll-x="900" :row-key="(r: K8sConflict) => `${r.kind}:${r.ip}:${r.what}`"
                      data-testid="k8s-conflicts" />
      </n-tab-pane>
    </n-tabs>
  </div>
</template>

<script setup lang="ts">
/** 一個 Kubernetes 叢集的內容：節點、Service 佔用的位址、LB 位址集區、叢集網段、跟 IPAM 打架的地方。 */
import { computed, h, onMounted, ref, watch } from "vue";
import { NAlert, NButton, NDataTable, NIcon, NSpace, NTabPane, NTabs, NTag, type DataTableColumns } from "naive-ui";
import { autoSort } from "@/composables/useTableSort";
import { cmpNatural } from "@/utils/sort";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import {
  K8s, type K8sCidr, type K8sCluster, type K8sConflict, type K8sHostname, type K8sNode, type K8sPool, type K8sServiceIP,
} from "@/api/k8s";
import { apiErrMsg } from "@/api/client";
import { useMessage } from "naive-ui";
import { useTablePagination } from "@/composables/useTablePagination";
import { SyncIcon, TestIcon } from "@/icons";
import { fmtDateTime } from "@/utils/datetime";

const props = defineProps<{ cluster: K8sCluster; testing: boolean; syncing: boolean }>();
const emit = defineEmits<{ (e: "test"): void; (e: "sync"): void }>();
const { t } = useI18n();
const router = useRouter();
const msg = useMessage();
const pg = useTablePagination();

const sub = ref("nodes");
const loading = ref(false);
const nodes = ref<K8sNode[]>([]);
const svcs = ref<K8sServiceIP[]>([]);
const pools = ref<K8sPool[]>([]);
const cidrs = ref<K8sCidr[]>([]);
const hostnames = ref<K8sHostname[]>([]);
const conflicts = ref<K8sConflict[]>([]);

async function load() {
  loading.value = true;
  try {
    const id = props.cluster.id;
    [nodes.value, svcs.value, pools.value, cidrs.value, hostnames.value, conflicts.value] = await Promise.all([
      K8s.nodes(id), K8s.serviceIps(id), K8s.pools(id), K8s.cidrs(id), K8s.hostnames(id), K8s.conflicts(id)]);
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}
onMounted(load);
// 同步跑完、清單重新讀過時（last_sync_at 變了）一起更新
watch(() => [props.cluster.last_sync_at, props.cluster.pod_network_routable], () => void load());

function ipLink(ip: string | null, id: string | null) {
  if (!ip) return "—";
  return id ? h(NButton, { text: true, type: "primary", onClick: () => router.push(`/addresses/${id}`) },
    { default: () => ip }) : ip;
}
const yes = (v: boolean | null) => (v == null ? "—" : v ? t("common.yes") : t("common.no"));
const PROVIDER: Record<K8sPool["provider"], string> = {
  metallb: "MetalLB", cilium: "Cilium", calico: "Calico", whereabouts: "Whereabouts", antrea: "Antrea", multus: "Multus",
};
const SVC_TAG: Record<K8sServiceIP["kind"], "info" | "success" | "warning" | "error" | "default"> = {
  loadbalancer: "info", external: "default", pod: "success", egress: "warning", apiserver: "error",
};
const DNS_TAG: Record<K8sHostname["dns_state"], "success" | "error" | "default" | "info"> = {
  match: "success", mismatch: "error", none: "default", wildcard: "info", no_ip: "default",
};

// 排序比的是畫面上看到的值；空值排最後
const by = <T,>(f: (r: T) => unknown) => (a: T, b: T) => {
  const x = f(a), y = f(b), ex = x == null || x === "", ey = y == null || y === "";
  return ex || ey ? Number(ex) - Number(ey) : cmpNatural(x, y);
};
const nodeCols = computed<DataTableColumns<K8sNode>>(() => autoSort<K8sNode>([
  { title: t("common.name"), key: "name", minWidth: 160, ellipsis: { tooltip: true } },
  { title: "IP", key: "ip", width: 150, render: (r: K8sNode) => ipLink(r.ip, r.ip_address_id) },
  { title: t("k8s.ready"), key: "ready", width: 90, sorter: by((r: K8sNode) => (r.ready == null ? null : r.ready ? 0 : 1)),
    render: (r: K8sNode) => h(NTag, { size: "small", bordered: false, type: r.ready ? "success" : r.ready === false ? "error" : "default" },
      { default: () => (r.ready ? "Ready" : r.ready === false ? "NotReady" : "—") }) },
  { title: t("k8s.roles"), key: "roles", width: 140, render: (r: K8sNode) => (r.roles ?? []).join(", ") || "—",
    sorter: by((r: K8sNode) => (r.roles ?? []).join(", ")) },
  { title: t("k8s.vm"), key: "vm_name", width: 160, ellipsis: { tooltip: true }, render: (r: K8sNode) => r.vm_name ?? "—" },
  { title: "kubelet", key: "kubelet_version", width: 120, render: (r: K8sNode) => r.kubelet_version ?? "—" },
  { title: t("k8s.os"), key: "os_image", minWidth: 180, ellipsis: { tooltip: true }, render: (r: K8sNode) => r.os_image ?? "—" },
  { title: t("k8s.pod_cidrs"), key: "pod_cidrs", width: 150, render: (r: K8sNode) => (r.pod_cidrs ?? []).join(", ") || "—",
    sorter: by((r: K8sNode) => (r.pod_cidrs ?? []).join(", ")) },
  { title: "providerID", key: "provider_id", minWidth: 200, ellipsis: { tooltip: true }, render: (r: K8sNode) => r.provider_id ?? "—" },
  { title: t("k8s.unschedulable"), key: "unschedulable", width: 100, render: (r: K8sNode) => yes(r.unschedulable),
    sorter: by((r: K8sNode) => (r.unschedulable == null ? null : yes(r.unschedulable))) },
]));
const svcCols = computed<DataTableColumns<K8sServiceIP>>(() => autoSort<K8sServiceIP>([
  { title: "IP", key: "ip", width: 150, render: (r: K8sServiceIP) => ipLink(r.ip, r.ip_address_id) },
  { title: t("k8s.svc_kind"), key: "kind", width: 140, sorter: by((r: K8sServiceIP) => t(`k8s.svc_kind_${r.kind}`)),
    render: (r: K8sServiceIP) => h(NTag, { size: "small", bordered: false, type: SVC_TAG[r.kind] ?? "default" },
      { default: () => t(`k8s.svc_kind_${r.kind}`) }) },
  { title: "Namespace", key: "namespace", width: 160, ellipsis: { tooltip: true } },
  { title: t("common.name"), key: "name", minWidth: 160, ellipsis: { tooltip: true } },
  { title: t("k8s.ports"), key: "ports", minWidth: 160,
    render: (r: K8sServiceIP) => (r.ports ?? []).map((p) => `${p.port}/${p.protocol ?? "TCP"}`).join(", ") || "—",
    sorter: by((r: K8sServiceIP) => (r.ports ?? []).map((p) => `${p.port}/${p.protocol ?? "TCP"}`).join(", ")) },
  { title: t("k8s.egress_node"), key: "node", width: 140, ellipsis: { tooltip: true }, render: (r: K8sServiceIP) => r.node ?? "—" },
  { title: t("k8s.in_ipam"), key: "ip_address_id", width: 110, render: (r: K8sServiceIP) => yes(!!r.ip_address_id),
    sorter: (a: K8sServiceIP, b: K8sServiceIP) => Number(!a.ip_address_id) - Number(!b.ip_address_id) },
]));
const poolCols = computed<DataTableColumns<K8sPool>>(() => autoSort<K8sPool>([
  { title: t("common.name"), key: "name", minWidth: 160 },
  { title: t("k8s.provider"), key: "provider", width: 120, render: (r: K8sPool) => PROVIDER[r.provider] ?? r.provider,
    sorter: by((r: K8sPool) => PROVIDER[r.provider] ?? r.provider) },
  { title: t("k8s.pool_use"), key: "use", width: 150, render: (r: K8sPool) => t(`k8s.pool_use_${r.use}`),
    sorter: by((r: K8sPool) => t(`k8s.pool_use_${r.use}`)) },
  { title: t("k8s.blocks"), key: "blocks", minWidth: 260,
    render: (r: K8sPool) => r.blocks.map((b) => (b.start === b.end ? b.start : `${b.start} - ${b.end}`)).join("、"),
    sorter: by((r: K8sPool) => r.blocks[0]?.start) },
  { title: t("k8s.auto_assign"), key: "auto_assign", width: 110, render: (r: K8sPool) => yes(r.auto_assign),
    sorter: by((r: K8sPool) => yes(r.auto_assign)) },
  { title: t("k8s.disabled"), key: "disabled", width: 90, render: (r: K8sPool) => yes(r.disabled), sorter: by((r: K8sPool) => yes(r.disabled)) },
]));
const cidrCols = computed<DataTableColumns<K8sCidr>>(() => autoSort<K8sCidr>([
  { title: "CIDR", key: "cidr", width: 180 },
  { title: t("k8s.cidr_kind"), key: "kind", width: 140, render: (r: K8sCidr) => t(`k8s.cidr_kind_${r.kind}`),
    sorter: by((r: K8sCidr) => t(`k8s.cidr_kind_${r.kind}`)) },
  { title: t("k8s.cidr_source"), key: "source", minWidth: 180, render: (r: K8sCidr) => r.source ?? "—" },
  { title: t("k8s.routable"), key: "effective_routable", width: 150,
    render: (r: K8sCidr) => (r.routable == null && r.effective_routable ? t("k8s.routable_by_cluster") : yes(r.effective_routable)),
    sorter: by((r: K8sCidr) => (r.effective_routable == null ? null : (r.routable == null && r.effective_routable ? t("k8s.routable_by_cluster") : yes(r.effective_routable)))) },
  { title: t("k8s.encapsulation"), key: "encapsulation", width: 100, render: (r: K8sCidr) => r.encapsulation ?? "—" },
  { title: t("k8s.overlap_subnets"), key: "subnets", minWidth: 180,
    sorter: by((r: K8sCidr) => r.subnets.map((x) => x.cidr).join("、")),
    render: (r: K8sCidr) => (r.subnets.length
      ? h("span", {}, r.subnets.flatMap((s, i) => [i ? "、" : "", h(NButton, { text: true, type: "primary",
        onClick: () => router.push(`/subnets/${s.id}`) }, { default: () => s.cidr })]))
      : "—") },
]));
const hostCols = computed<DataTableColumns<K8sHostname>>(() => autoSort<K8sHostname>([
  { title: t("k8s.host"), key: "host", minWidth: 220, ellipsis: { tooltip: true },
    render: (r: K8sHostname) => h("span", {}, [r.host, r.tls ? h(NTag, { size: "tiny", bordered: false, style: "margin-left:6px" },
      { default: () => t("k8s.tls") }) : null]) },
  { title: "IP", key: "ip", width: 150, render: (r: K8sHostname) => ipLink(r.ip, r.ip_address_id) },
  { title: t("k8s.dns_col"), key: "dns_state", width: 200, sorter: by((r: K8sHostname) => t(`k8s.dns_state_${r.dns_state}`)),
    render: (r: K8sHostname) => h("span", {}, [h(NTag, { size: "small", bordered: false, type: DNS_TAG[r.dns_state] },
      { default: () => t(`k8s.dns_state_${r.dns_state}`) }),
    r.dns_state === "mismatch" ? h("span", { style: "margin-left:6px;font-size:12px;opacity:.7" }, r.dns_ips.join(", ")) : null]) },
  { title: t("k8s.hn_kind"), key: "kind", width: 140, render: (r: K8sHostname) => t(`k8s.hn_kind_${r.kind}`),
    sorter: by((r: K8sHostname) => t(`k8s.hn_kind_${r.kind}`)) },
  { title: "Namespace", key: "namespace", width: 160, ellipsis: { tooltip: true } },
  { title: t("common.name"), key: "name", minWidth: 160, ellipsis: { tooltip: true } },
]));
const conflictCols = computed<DataTableColumns<K8sConflict>>(() => autoSort<K8sConflict>([
  { title: t("anomaly.col.kind"), key: "kind", minWidth: 220, render: (r: K8sConflict) => t(`anomaly.k8sk_${r.kind}`),
    sorter: by((r: K8sConflict) => t(`anomaly.k8sk_${r.kind}`)) },
  { title: "IP", key: "ip", width: 220, render: (r: K8sConflict) => ipLink(r.ip, r.ip_address_id ?? null) },
  { title: t("anomaly.col.what"), key: "what", minWidth: 180, ellipsis: { tooltip: true } },
  { title: t("anomaly.col.detail"), key: "detail", minWidth: 200, ellipsis: { tooltip: true } },
]));
</script>

<style scoped>
.meta { font-size: 12px; opacity: .7; }
.hint { font-size: 12px; opacity: .7; }
.warn { color: #f0a020; }
</style>
