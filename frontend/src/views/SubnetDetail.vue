<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { useI18n } from "vue-i18n";
import {
  NCard,
  NSpace,
  NDescriptions,
  NDescriptionsItem,
  NProgress,
  NSpin,
  NButton,
  NCheckbox,
  NAlert,
  NCode,
  type UploadCustomRequestOptions,
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

const dryRun = ref(true);
const importBusy = ref(false);
const importResult = ref<Record<string, unknown> | null>(null);

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

function exportCsvUrl(): string {
  if (!subnet.value) return "";
  const base = (import.meta.env.VITE_API_BASE_URL || "") as string;
  return `${base}/api/v1/addresses/export.csv?subnet_id=${subnet.value.id}`;
}

async function handleExport() {
  if (!subnet.value) return;
  // 用 axios 帶 token，存成檔案下載
  try {
    const resp = await apiClient.get("/api/v1/addresses/export.csv", {
      params: { subnet_id: subnet.value.id },
      responseType: "blob",
    });
    const blob = new Blob([resp.data], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `addresses-${subnet.value.cidr.replace("/", "_")}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  } catch {
    msg.error(t("errors.network"));
  }
}

async function uploadCsv(opts: UploadCustomRequestOptions) {
  const { file } = opts;
  if (!subnet.value) return;
  if (!file.file) {
    opts.onError();
    return;
  }
  importBusy.value = true;
  importResult.value = null;
  try {
    const form = new FormData();
    form.append("subnet_id", subnet.value.id);
    form.append("file", file.file as Blob, file.name);
    form.append("dry_run", String(dryRun.value));
    const resp = await apiClient.post("/api/v1/addresses/import", form, {
      headers: { "Content-Type": "multipart/form-data" },
    });
    importResult.value = resp.data;
    if (!dryRun.value) {
      msg.success(`Imported: ${resp.data.inserted}, skipped: ${resp.data.skipped}`);
      // 重新載入
      await load(subnet.value.id);
    } else {
      msg.info(`Dry-run: ${resp.data.preview?.length ?? 0} preview rows`);
    }
    opts.onFinish();
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? "Import failed");
    opts.onError();
  } finally {
    importBusy.value = false;
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
        <template #header-extra>
          <n-space>
            <n-button @click="handleExport">Export CSV</n-button>
          </n-space>
        </template>
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
          <div>{{ usage.used }} / {{ usage.total }} used ({{ usage.used_pct }}%)</div>
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

      <n-card v-if="subnet" title="Import addresses (CSV)">
        <n-space vertical :size="12">
          <n-alert type="info" size="small">
            欄位順序不重要；header 必須含
            <code>ip</code>。可選欄位：<code>hostname, mac, state, description, owner, switch_port, note</code>。
            UTF-8（含 BOM）；自動偵測逗號 / 分號 / Tab。
          </n-alert>
          <n-checkbox v-model:checked="dryRun">
            Dry-run（不寫入 DB，只回傳預覽 + 錯誤）
          </n-checkbox>
          <n-upload
            :custom-request="uploadCsv"
            :show-file-list="false"
            accept=".csv,text/csv"
            :disabled="importBusy"
          >
            <n-button :loading="importBusy">選擇 CSV 上傳</n-button>
          </n-upload>
          <n-card v-if="importResult" size="small" title="結果">
            <pre>{{ JSON.stringify(importResult, null, 2) }}</pre>
          </n-card>
        </n-space>
      </n-card>
    </n-space>
  </n-spin>
</template>

<style scoped>
pre {
  font-size: 12px;
  background: rgba(127, 127, 127, 0.08);
  padding: 8px;
  border-radius: 4px;
  overflow-x: auto;
  max-height: 320px;
}
</style>
