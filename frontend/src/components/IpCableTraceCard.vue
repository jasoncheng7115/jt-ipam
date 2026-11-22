<script setup lang="ts">
/**
 * IP 詳情的「實體線路」（交接包 physical-v1.2 CABLING.md 畫面第 5 點）：沿 IP 既有的關聯找可以追的連接埠。
 * - 裝置上 MAC 相同的網卡、FDB 學到的交換器埠：直接列出來，按「追線」
 * - 只有裝置關聯：從那台裝置的埠挑一個
 * - 都沒有：說「尚未對應裝置或連接埠」
 * 不從 IP 在不在線推定實體線路。
 */
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { NButton, NCard, NIcon, NSelect, NSpace, NSpin, NTag } from "naive-ui";
import { apiClient, apiErrMsg } from "@/api/client";
import { Physical, type PortTrace } from "@/api/phase3";
import SwitchPortLabel from "@/components/SwitchPortLabel.vue";
import TracePath from "@/components/TracePath.vue";
import { LinkIcon, PhysicalIcon } from "@/icons";

const props = defineProps<{ addressId: string }>();
const { t } = useI18n();
interface Cand { port_id: string; device_id: string; device: string; port: string; reason: "mac_match" | "switch_port" }
const data = ref<{ device: { id: string; name: string } | null; candidates: Cand[];
                   device_ports: { port_id: string; port: string; type: string }[] } | null>(null);
const loading = ref(false);
const picked = ref<string | null>(null);
const trace = ref<PortTrace | null>(null);
const tracing = ref<string | null>(null);
const err = ref("");

async function load() {
  loading.value = true; trace.value = null; err.value = "";
  try { data.value = (await apiClient.get(`/api/v1/addresses/${props.addressId}/trace-candidates`)).data; }
  catch { data.value = null; }
  finally { loading.value = false; }
}
async function run(portId: string) {
  tracing.value = portId; err.value = "";
  try { trace.value = await Physical.tracePort(portId); }
  catch (e) { trace.value = null; err.value = apiErrMsg(e); }
  finally { tracing.value = null; }
}
const portOpts = computed(() => (data.value?.device_ports ?? []).map((p) => ({ label: p.port, value: p.port_id })));
onMounted(load);
watch(() => props.addressId, () => void load());
</script>

<template>
  <n-card size="small" data-testid="ip-cable-trace">
    <template #header>
      <span style="display:inline-flex;align-items:center;gap:8px"><n-icon :size="18"><PhysicalIcon /></n-icon>{{ t("ip_trace.title") }}</span>
    </template>
    <n-spin :show="loading">
      <template v-if="data">
        <div v-if="data.candidates.length" class="it-list">
          <div v-for="c in data.candidates" :key="c.port_id" class="it-row">
            <SwitchPortLabel :value="`${c.device} / ${c.port}`" :to="{ name: 'device-detail', params: { id: c.device_id } }" />
            <n-tag size="tiny" :bordered="false" :type="c.reason === 'mac_match' ? 'success' : 'info'">{{ t(`ip_trace.reason_${c.reason}`) }}</n-tag>
            <n-button size="tiny" type="info" :loading="tracing === c.port_id" :data-testid="`ip-trace-${c.port}`" @click="run(c.port_id)">
              <template #icon><n-icon><LinkIcon /></n-icon></template>{{ t("cable_trace.title") }}
            </n-button>
          </div>
        </div>
        <n-space v-else-if="data.device_ports.length" align="center">
          <span class="hint">{{ t("ip_trace.pick_port", { device: data.device?.name ?? "" }) }}</span>
          <n-select v-model:value="picked" :options="portOpts" filterable size="small" style="width: 200px" data-testid="ip-trace-port" />
          <n-button size="small" type="info" :disabled="!picked" :loading="!!tracing" @click="picked && run(picked)">
            <template #icon><n-icon><LinkIcon /></n-icon></template>{{ t("cable_trace.title") }}
          </n-button>
        </n-space>
        <div v-else class="hint" data-testid="ip-trace-none">{{ t("ip_trace.none") }}</div>
        <div v-if="err" class="hint" style="margin-top: 8px">{{ err }}</div>
        <div v-if="trace" style="margin-top: 10px"><TracePath :trace="trace" /></div>
      </template>
    </n-spin>
  </n-card>
</template>

<style scoped>
.hint { font-size: 12.5px; opacity: .75; }
.it-list { display: flex; flex-direction: column; gap: 6px; }
.it-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
</style>
