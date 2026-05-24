<script setup lang="ts">
import { h, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import {
  NCard,
  NDataTable,
  NSpace,
  NTag,
  NButton,
  NModal,
  NForm,
  NFormItem,
  NInput,
  NSelect,
  NCheckbox,
  useMessage,
  type DataTableColumns,
} from "naive-ui";
import { NIcon } from "naive-ui";
import { RequestsIcon } from "@/icons";
import { storeToRefs } from "pinia";
import { useAuthStore } from "@/stores/auth";
import {
  listRequests,
  createRequest,
  type IPRequest,
} from "@/api/ip_requests";
import { listSubnets } from "@/api/subnets";

const router = useRouter();
const auth = useAuthStore();
const { me } = storeToRefs(auth);
const msg = useMessage();

const rows = ref<IPRequest[]>([]);
const loading = ref(false);
const showMine = ref(false);
const filterStatus = ref<string | null>(null);

// Create modal
const showCreate = ref(false);
const subnetOptions = ref<{ label: string; value: string }[]>([]);
const form = ref({
  subnet_id: "",
  hostname: "",
  description: "",
  purpose: "",
  requested_ip: "",
});
const submitting = ref(false);

const statusOptions = [
  { label: "All", value: "" },
  { label: "Pending", value: "pending" },
  { label: "Fulfilled", value: "fulfilled" },
  { label: "Rejected", value: "rejected" },
  { label: "Cancelled", value: "cancelled" },
];

const tagType = (s: string): "success" | "warning" | "error" | "default" | "info" => {
  if (s === "fulfilled") return "success";
  if (s === "pending") return "info";
  if (s === "rejected") return "error";
  if (s === "cancelled") return "default";
  return "default";
};

const columns: DataTableColumns<IPRequest> = [
  {
    title: "Status",
    key: "status",
    render: (r) =>
      h(NTag, { size: "small", type: tagType(r.status) }, () => r.status),
  },
  { title: "Subnet", key: "subnet_id", render: (r) => r.subnet_id.slice(0, 8) },
  { title: "Hostname", key: "hostname", render: (r) => r.hostname ?? "" },
  { title: "Purpose", key: "purpose" },
  { title: "Created", key: "created_at" },
];

async function refresh() {
  loading.value = true;
  try {
    const res = await listRequests({
      mine: showMine.value,
      status: filterStatus.value || undefined,
      page: 1,
      pageSize: 100,
    });
    rows.value = res.items;
  } catch {
    msg.error("Failed to load");
  } finally {
    loading.value = false;
  }
}

async function loadSubnets() {
  try {
    const res = await listSubnets({ page: 1, pageSize: 200 });
    subnetOptions.value = res.items.map((s) => ({
      label: `${s.cidr}${s.description ? " — " + s.description : ""}`,
      value: s.id,
    }));
  } catch {
    /* ignore */
  }
}

async function submitCreate() {
  if (!form.value.subnet_id || !form.value.purpose) {
    msg.warning("Subnet 與 Purpose 為必填");
    return;
  }
  submitting.value = true;
  try {
    const r = await createRequest({
      subnet_id: form.value.subnet_id,
      purpose: form.value.purpose,
      hostname: form.value.hostname || undefined,
      description: form.value.description || undefined,
      requested_ip: form.value.requested_ip || undefined,
    });
    msg.success("已送出申請");
    showCreate.value = false;
    form.value = {
      subnet_id: "",
      hostname: "",
      description: "",
      purpose: "",
      requested_ip: "",
    };
    await refresh();
    router.push({ name: "request-detail", params: { id: r.id } });
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? "Submit failed");
  } finally {
    submitting.value = false;
  }
}

onMounted(() => {
  void refresh();
  void loadSubnets();
});
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><RequestsIcon /></n-icon>
        <span>IP Requests</span>
      </n-space>
    </template>
    <template #header-extra>
      <n-space>
        <n-checkbox v-model:checked="showMine" @update:checked="refresh">
          只看我提出的
        </n-checkbox>
        <n-select
          :value="filterStatus ?? ''"
          :options="statusOptions"
          placeholder="Status"
          size="small"
          style="width: 140px"
          @update:value="(v: string) => { filterStatus = v || null; refresh(); }"
        />
        <n-button type="primary" @click="showCreate = true">
          <template #icon><n-icon><RequestsIcon /></n-icon></template>
          新增申請
        </n-button>
      </n-space>
    </template>

    <n-data-table
      :columns="columns"
      :data="rows"
      :loading="loading"
      :pagination="{ pageSize: 50 }"
      :bordered="false"
      :row-props="(row: IPRequest) => ({
        style: 'cursor: pointer',
        onClick: () => router.push({ name: 'request-detail', params: { id: row.id } }),
      })"
    />
  </n-card>

  <n-modal
    v-model:show="showCreate"
    preset="dialog"
    title="新增 IP 申請"
    :show-icon="false"
    style="width: 520px"
  >
    <n-form>
      <n-form-item label="Subnet" required>
        <n-select
          v-model:value="form.subnet_id"
          :options="subnetOptions"
          placeholder="選擇要申請的子網"
          filterable
        />
      </n-form-item>
      <n-form-item label="Purpose（用途說明）" required>
        <n-input v-model:value="form.purpose" type="textarea" :rows="2" />
      </n-form-item>
      <n-form-item label="Hostname (選填)">
        <n-input v-model:value="form.hostname" placeholder="host01.example.com" />
      </n-form-item>
      <n-form-item label="指定 IP (選填，留空則由系統配發第一個空閒)">
        <n-input v-model:value="form.requested_ip" placeholder="10.0.0.42" />
      </n-form-item>
      <n-form-item label="描述 (選填)">
        <n-input v-model:value="form.description" type="textarea" :rows="2" />
      </n-form-item>
    </n-form>
    <template #action>
      <n-space>
        <n-button @click="showCreate = false">取消</n-button>
        <n-button type="primary" :loading="submitting" @click="submitCreate">送出</n-button>
      </n-space>
    </template>
  </n-modal>
</template>
