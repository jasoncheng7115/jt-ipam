<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { useI18n } from "vue-i18n";
import {
  NCard,
  NSpace,
  NDescriptions,
  NDescriptionsItem,
  NTag,
  NProgress,
  NSpin,
  useMessage,
} from "naive-ui";
import { apiClient } from "@/api/client";
import { listAddresses } from "@/api/addresses";
import { getSubnetUsage } from "@/api/subnets";
import SubnetGrid from "@/components/SubnetGrid.vue";
import type { IPAddress, Subnet, SubnetUsage } from "@/types";

const route = useRoute();
const { t } = useI18n();
const msg = useMessage();

const subnet = ref<Subnet | null>(null);
const usage = ref<SubnetUsage | null>(null);
const addresses = ref<IPAddress[]>([]);
const loading = ref(false);

async function load(id: string) {
  loading.value = true;
  try {
    const [s, u, a] = await Promise.all([
      apiClient.get<Subnet>(`/api/v1/subnets/${id}`).then((r) => r.data),
      getSubnetUsage(id),
      listAddresses({ subnetId: id, page: 1, pageSize: 1000 }),
    ]);
    subnet.value = s;
    usage.value = u;
    addresses.value = a.items;
  } catch {
    msg.error(t("errors.network"));
  } finally {
    loading.value = false;
  }
}

watch(
  () => route.params.id,
  (id) => {
    if (typeof id === "string") void load(id);
  },
);

onMounted(() => {
  const id = route.params.id;
  if (typeof id === "string") void load(id);
});
</script>

<template>
  <n-spin :show="loading">
    <n-space vertical :size="16">
      <n-card v-if="subnet" :title="subnet.cidr">
        <n-descriptions bordered :column="2">
          <n-descriptions-item label="CIDR">{{ subnet.cidr }}</n-descriptions-item>
          <n-descriptions-item label="VRF">{{ subnet.vrf_id ?? "—" }}</n-descriptions-item>
          <n-descriptions-item label="VLAN">{{ subnet.vlan_id ?? "—" }}</n-descriptions-item>
          <n-descriptions-item label="Section">{{ subnet.section_id }}</n-descriptions-item>
          <n-descriptions-item label="Description" :span="2">
            {{ subnet.description ?? "—" }}
          </n-descriptions-item>
        </n-descriptions>
      </n-card>

      <n-card v-if="usage" title="Usage">
        <n-space vertical>
          <div>
            {{ usage.used }} / {{ usage.total }} used ({{ usage.used_pct }}%)
          </div>
          <n-progress
            type="line"
            :percentage="usage.used_pct"
            :status="
              usage.used_pct >= 90 ? 'error' : usage.used_pct >= 75 ? 'warning' : 'success'
            "
          />
        </n-space>
      </n-card>

      <n-card v-if="subnet" title="Visualisation">
        <subnet-grid :cidr="subnet.cidr" :addresses="addresses" />
      </n-card>
    </n-space>
  </n-spin>
</template>
