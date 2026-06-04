<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NIcon, NButton, NTag,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { PhysicalIcon, PowerIcon, VpnIcon, RefreshIcon } from "@/icons";
import { Physical } from "@/api/phase3";
import { autoSort } from "@/composables/useTableSort";

// 三個獨立頁面共用此元件，以 mode 決定顯示哪一段（佈線 / 電力 / VPN）
const props = defineProps<{ mode?: "cabling" | "power" | "vpn" }>();
const mode = computed(() => props.mode ?? "cabling");

const { t } = useI18n();
const msg = useMessage();

const cables = ref<any[]>([]);
const panels = ref<any[]>([]);
const feeds = ref<any[]>([]);
const outlets = ref<any[]>([]);
const vpns = ref<any[]>([]);
const loading = ref(false);

async function refresh() {
  loading.value = true;
  try {
    if (mode.value === "cabling") {
      cables.value = await Physical.cables();
    } else if (mode.value === "power") {
      [panels.value, feeds.value, outlets.value] = await Promise.all([
        Physical.panels(), Physical.feeds(), Physical.outlets(),
      ]);
    } else {
      vpns.value = await Physical.vpns();
    }
  } catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}

const pageTitle = computed(() =>
  mode.value === "power" ? t("nav.power")
    : mode.value === "vpn" ? t("nav.vpn_tunnels")
    : t("nav.cabling"));
const PageIcon = computed(() => (mode.value === "power" ? PowerIcon : mode.value === "vpn" ? VpnIcon : PhysicalIcon));

const cableCols = computed<DataTableColumns<any>>(() => autoSort([
  { title: t("cols.type"), key: "type" },
  { title: t("common.status"), key: "status" },
  { title: t("sections.description"), key: "description" },
]));
const panelCols = computed<DataTableColumns<any>>(() => autoSort([
  { title: t("common.name"), key: "name" },
  { title: t("nav.locations"), key: "location_id", render: (r: any) => r.location_id ?? "—" },
]));
const feedCols = computed<DataTableColumns<any>>(() => autoSort([
  { title: t("common.name"), key: "name" },
  { title: t("cols.panel"), key: "panel_id" },
]));
const outletCols = computed<DataTableColumns<any>>(() => autoSort([
  { title: t("common.name"), key: "name" },
  { title: t("cols.feed"), key: "feed_id" },
]));
const vpnCols = computed<DataTableColumns<any>>(() => autoSort([
  { title: t("common.name"), key: "name" },
  { title: t("cols.type"), key: "type" },
  { title: t("common.status"), key: "status" },
  {
    title: t("physical.vpn_peer"), key: "peer", minWidth: 260,
    render: (r) => {
      if (r.peered && r.a_device_name && r.b_device_name) {
        const reliable = r.pairing_method === "wireguard_pubkey";
        const badge = h(NTag, {
          size: "tiny", type: reliable ? "success" : "warning", bordered: false,
          style: "margin-left:8px",
        }, () => reliable ? t("physical.pair_reliable") : t("physical.pair_besteffort"));
        return h("span", { class: "vpn-peered" }, [
          `${r.a_device_name} ⇄ ${r.b_device_name}`, badge,
        ]);
      }
      if (r.b_endpoint) return h("span", { style: "opacity:.7" }, `→ ${r.b_endpoint}`);
      return h("span", { style: "opacity:.4" }, "—");
    },
  },
]));

onMounted(() => { void refresh(); });
watch(mode, () => { void refresh(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><component :is="PageIcon" /></n-icon>
        <span>{{ pageTitle }}</span>
      </n-space>
    </template>
    <n-space style="margin-bottom: 12px">
      <n-button @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
    </n-space>

    <n-data-table v-if="mode === 'cabling'"
      :columns="cableCols" :data="cables" :loading="loading" :bordered="false" />

    <template v-else-if="mode === 'power'">
      <h3>{{ t("physical.panels") }} ({{ panels.length }})</h3>
      <n-data-table :columns="panelCols" :data="panels" :loading="loading" :bordered="false" />
      <h3 style="margin-top: 16px">{{ t("physical.feeds") }} ({{ feeds.length }})</h3>
      <n-data-table :columns="feedCols" :data="feeds" :loading="loading" :bordered="false" />
      <h3 style="margin-top: 16px">{{ t("physical.outlets") }} ({{ outlets.length }})</h3>
      <n-data-table :columns="outletCols" :data="outlets" :loading="loading" :bordered="false" />
    </template>

    <n-data-table v-else
      :columns="vpnCols" :data="vpns" :loading="loading" :bordered="false" />
  </n-card>
</template>

<style scoped>
.vpn-peered {
  color: #18a058;
  font-weight: 600;
}
</style>
