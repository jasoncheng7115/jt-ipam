<template>
  <!-- 沒有資料就整張不出現（大部分位址都不在 Kubernetes 裡） -->
  <n-card v-if="data && (data.nodes.length || data.services.length || data.hostnames.length || data.pod_networks.length)"
          size="small" :bordered="true"
          data-testid="k8s-ip-card">
    <div class="k8c-title"><n-icon :size="16"><VirtualizationIcon /></n-icon>Kubernetes</div>
    <div class="k8c-list">
      <div v-for="n in data.nodes" :key="`n:${n.cluster_id}:${n.name}`" class="k8c-item" data-testid="k8s-ip-node">
        <n-tag size="small" type="info" :bordered="false">{{ t("k8s.ip_node") }}</n-tag>
        <span>{{ n.cluster }} / <b>{{ n.name }}</b></span>
        <span v-if="n.roles?.length" class="hint">{{ n.roles.join(", ") }}</span>
        <n-tag size="tiny" :bordered="false" :type="n.ready ? 'success' : 'error'">{{ n.ready ? "Ready" : "NotReady" }}</n-tag>
        <span v-if="n.kubelet_version" class="hint">kubelet {{ n.kubelet_version }}</span>
      </div>
      <div v-for="s in data.services" :key="`s:${s.cluster_id}:${s.namespace}:${s.name}:${s.kind}`" class="k8c-item"
           data-testid="k8s-ip-service">
        <n-tag size="small" :bordered="false">{{ t(`k8s.svc_kind_${s.kind}`) }}</n-tag>
        <span>{{ s.cluster }} / {{ s.namespace }} / <b>{{ s.name }}</b></span>
        <span v-if="s.node" class="hint">{{ t("k8s.egress_node") }}：{{ s.node }}</span>
        <span v-if="s.ports?.length" class="hint">{{ s.ports.map((p) => `${p.port}/${p.protocol ?? "TCP"}`).join(", ") }}</span>
      </div>
      <div v-if="data.hostnames.length" class="k8c-item" data-testid="k8s-ip-hostnames">
        <n-tag size="small" :bordered="false" type="success">{{ t("k8s.ip_hostnames") }}</n-tag>
        <span v-for="hn in data.hostnames" :key="`h:${hn.cluster_id}:${hn.kind}:${hn.namespace}:${hn.name}:${hn.host}`"
              class="k8c-host" :title="`${hn.cluster} / ${t(`k8s.hn_kind_${hn.kind}`)} ${hn.namespace}/${hn.name}`">
          {{ hn.host }}<span v-if="hn.tls" class="hint"> (TLS)</span>
        </span>
      </div>
      <div v-for="pn in data.pod_networks" :key="`p:${pn.cluster_id}:${pn.cidr}`" class="k8c-item hint"
           data-testid="k8s-ip-pod-network">
        {{ t("k8s.ip_pod_network", { cluster: pn.cluster ?? "", cidr: pn.cidr }) }}
      </div>
    </div>
  </n-card>
</template>

<script setup lang="ts">
/** IP 詳細資料頁：這個位址在 Kubernetes 裡是哪個叢集的節點、哪個 Service／Pod 在用、哪些網域名稱指向它，
 *  或落在可直接路由的 Pod 網段裡（對這筆 IP 有讀取權就看得到）。 */
import { onMounted, ref, watch } from "vue";
import { NCard, NIcon, NTag } from "naive-ui";
import { useI18n } from "vue-i18n";
import { K8s, type K8sByIp } from "@/api/k8s";
import { VirtualizationIcon } from "@/icons";

const props = defineProps<{ addressId: string }>();
const { t } = useI18n();
const data = ref<K8sByIp | null>(null);

async function load() {
  data.value = null;
  if (!props.addressId) return;
  try { data.value = await K8s.byIp(props.addressId); }
  catch { data.value = null; /* 讀不到就不顯示，不打擾 */ }
}
onMounted(load);
watch(() => props.addressId, () => { void load(); });
</script>

<style scoped>
.k8c-title { display: flex; align-items: center; gap: 6px; font-weight: 600; margin-bottom: 6px; }
.k8c-list { display: flex; flex-direction: column; gap: 6px; }
.k8c-item { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 13px; }
.hint { font-size: 12px; opacity: .65; }
.k8c-host { font-family: var(--mono-font, monospace); font-size: 12px; }
</style>
