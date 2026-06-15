<script setup lang="ts">
/**
 * Customer 詳情頁：metadata + 旗下 sections / subnets / devices / IPs。
 */
import { h, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import {
  NCard, NSpace, NIcon, NButton, NDescriptions, NDescriptionsItem,
  NTag, NDataTable, NSpin, NStatistic,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { ArrowLeft as ArrowLeftIcon } from "@iconoir/vue";
import { CustomersIcon, SectionsIcon, SubnetsIcon, DevicesIcon, AddressesIcon } from "@/icons";
import { apiClient } from "@/api/client";
import { fmtDateTime } from "@/utils/datetime";
import { useEntityLinks } from "@/composables/useEntityLinks";
import { autoSort } from "@/composables/useTableSort";
import { useTablePagination } from "@/composables/useTablePagination";
const pg = useTablePagination();

interface Summary {
  customer: {
    id: string; name: string; title: string | null; description: string | null;
    contact: string | null; email: string | null; phone: string | null; address: string | null;
    created_at: string; updated_at: string;
  };
  counts: { sections: number; subnets: number; devices: number; ip_addresses: number };
  sections: { id: string; name: string; description: string | null }[];
  subnets: { id: string; cidr: string; description: string | null }[];
  devices: { id: string; name: string; type: string }[];
  ip_addresses: { id: string; ip: string; hostname: string | null; subnet_id: string }[];
}

const route = useRoute();
const router = useRouter();
const { t } = useI18n();
const msg = useMessage();
const links = useEntityLinks(router);

const data = ref<Summary | null>(null);
const loading = ref(false);

async function load(id: string) {
  loading.value = true;
  try {
    const res = await apiClient.get<Summary>(`/api/v1/customers/${id}/summary`);
    data.value = res.data;
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.network"));
  } finally {
    loading.value = false;
  }
}

const sectionCols: DataTableColumns<Summary["sections"][number]> = autoSort([
  { title: t("cols.name"), key: "name", render: (r) => links.section(r.id, r.name) },
  { title: t("cols.description"), key: "description", render: (r) => r.description ?? "—" },
]);
const subnetCols: DataTableColumns<Summary["subnets"][number]> = autoSort([
  { title: "CIDR", key: "cidr", render: (r) => links.subnet(r.id, r.cidr) },
  { title: t("cols.description"), key: "description", render: (r) => r.description ?? "—" },
]);
const deviceCols: DataTableColumns<Summary["devices"][number]> = autoSort([
  { title: t("cols.name"), key: "name", render: (r) => links.device(r.id, r.name) },
  { title: t("cols.type"), key: "type", render: (r) => h(NTag, { size: "small", type: "info" }, () => r.type) },
]);
const ipCols: DataTableColumns<Summary["ip_addresses"][number]> = autoSort([
  { title: "IP", key: "ip", render: (r) => links.ipByText(r.ip) },
  { title: t("cols.hostname"), key: "hostname", render: (r) => r.hostname ?? "—" },
  { title: t("cols.subnet"), key: "subnet_id",
    render: (r) => links.subnet(r.subnet_id, r.subnet_id.slice(0, 8) + "…") },
]);

watch(() => route.params.id, (id) => {
  if (typeof id === "string") void load(id);
});

onMounted(() => {
  const id = route.params.id;
  if (typeof id === "string") void load(id);
});
</script>

<template>
  <n-spin :show="loading">
    <n-space vertical :size="16">
      <n-card v-if="data">
        <template #header>
          <n-space align="center" :wrap-item="false">
            <n-icon :size="22"><CustomersIcon /></n-icon>
            <span>{{ data.customer.title || data.customer.name }}</span>
          </n-space>
        </template>
        <template #header-extra>
          <n-button @click="router.push({ name: 'customers' })" size="small">
            <template #icon><n-icon><ArrowLeftIcon /></n-icon></template>
            {{ t("common.back_to_list") }}
          </n-button>
        </template>
        <n-descriptions bordered :column="3" size="small" label-placement="left">
          <n-descriptions-item :label="t('customer_detail.internal_name')">{{ data.customer.name }}</n-descriptions-item>
          <n-descriptions-item :label="t('customer_detail.display_name')">{{ data.customer.title ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('cols.contact')">{{ data.customer.contact ?? "—" }}</n-descriptions-item>
          <n-descriptions-item label="Email">{{ data.customer.email ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('cols.phone')">{{ data.customer.phone ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('locations.address')" :span="2">{{ data.customer.address ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('common.description')" :span="3">{{ data.customer.description ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('common.created_at')">{{ fmtDateTime(data.customer.created_at) }}</n-descriptions-item>
          <n-descriptions-item :label="t('common.updated_at')" :span="2">{{ fmtDateTime(data.customer.updated_at) }}</n-descriptions-item>
        </n-descriptions>
      </n-card>

      <!-- KPI 卡 -->
      <n-card v-if="data" :title="t('customer_detail.resources')">
        <n-space :size="32">
          <n-statistic :label="t('sections.title')" :value="data.counts.sections">
            <template #prefix><n-icon><SectionsIcon /></n-icon></template>
          </n-statistic>
          <n-statistic :label="t('subnets.title')" :value="data.counts.subnets">
            <template #prefix><n-icon><SubnetsIcon /></n-icon></template>
          </n-statistic>
          <n-statistic :label="t('nav.devices')" :value="data.counts.devices">
            <template #prefix><n-icon><DevicesIcon /></n-icon></template>
          </n-statistic>
          <n-statistic label="IP" :value="data.counts.ip_addresses">
            <template #prefix><n-icon><AddressesIcon /></n-icon></template>
          </n-statistic>
        </n-space>
      </n-card>

      <n-card v-if="data?.sections?.length" :title="`${t('sections.title')} (${data.counts.sections})`">
        <n-data-table :columns="sectionCols" :data="data.sections" :bordered="false" size="small" :pagination="pg" />
      </n-card>

      <n-card v-if="data?.subnets?.length" :title="`${t('subnets.title')} (${data.counts.subnets})`">
        <n-data-table :columns="subnetCols" :data="data.subnets" :bordered="false" size="small" :pagination="pg" />
      </n-card>

      <n-card v-if="data?.devices?.length" :title="`${t('nav.devices')} (${data.counts.devices})`">
        <n-data-table :columns="deviceCols" :data="data.devices" :bordered="false" size="small" :pagination="pg" />
      </n-card>

      <n-card v-if="data?.ip_addresses?.length" :title="`IP (${data.counts.ip_addresses})`">
        <n-data-table :columns="ipCols" :data="data.ip_addresses" :bordered="false" size="small"
                      :pagination="pg" />
      </n-card>
    </n-space>
  </n-spin>
</template>
