<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
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
import { RequestsIcon, OkIcon, CancelIcon, SendIcon } from "@/icons";
import {
  listRequests,
  createRequest,
  approveRequest,
  rejectRequest,
  type IPRequest,
} from "@/api/ip_requests";
import { listSubnets } from "@/api/subnets";
import { autoSort } from "@/composables/useTableSort";
import ColumnPicker from "@/components/ColumnPicker.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { useEntityLinks } from "@/composables/useEntityLinks";
import { useTablePagination } from "@/composables/useTablePagination";
const pg = useTablePagination();
const { t } = useI18n();

const { visibleKeys: rqVis, setVisible: rqSet, reset: rqReset } = useColumnPrefs(
  "ip_requests",
  ["status", "subnet_id", "hostname", "purpose", "created_at"],
  ["status", "subnet_id", "hostname", "purpose", "created_at"],
);
const rqPicker = computed(() => [
  { key: "status", label: t("cols.status") },
  { key: "subnet_id", label: t("cols.subnet") },
  { key: "hostname", label: t("cols.hostname") },
  { key: "purpose", label: t("cols.purpose") },
  { key: "created_at", label: t("cols.created_at") },
]);

const router = useRouter();
const links = useEntityLinks(router);
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

const statusOptions = computed(() => [
  { label: t("requests.status_all"),       value: "" },
  { label: t("requests.status_pending"),   value: "pending" },
  { label: t("requests.status_fulfilled"), value: "fulfilled" },
  { label: t("requests.status_rejected"),  value: "rejected" },
  { label: t("requests.status_cancelled"), value: "cancelled" },
]);
const statusLabel = (s: string): string =>
  t(`requests.status_${s}`, s) as string;

const tagType = (s: string): "success" | "warning" | "error" | "default" | "info" => {
  if (s === "fulfilled") return "success";
  if (s === "pending") return "info";
  if (s === "rejected") return "error";
  if (s === "cancelled") return "default";
  return "default";
};

const allColumns = computed<DataTableColumns<IPRequest>>(() => autoSort([
  {
    title: t("requests.col_status"),
    key: "status", width: 120,
    render: (r) =>
      h(NTag, { size: "small", type: tagType(r.status) }, () => statusLabel(r.status)),
  },
  { title: t("requests.col_subnet"),   key: "subnet_id", width: 160,
    render: (r) => links.subnet(r.subnet_id, r.subnet_cidr ?? (r.subnet_id.slice(0, 8) + "…")) },
  { title: t("requests.col_hostname"), key: "hostname", minWidth: 180, ellipsis: { tooltip: true }, render: (r) => r.hostname ?? "—" },
  { title: t("requests.col_purpose"),  key: "purpose", minWidth: 200, ellipsis: { tooltip: true } },
  { title: t("requests.col_created"),  key: "created_at", width: 180 },
  {
    title: t("common.actions"), key: "__act", width: 150,
    render: (r) => r.can_approve
      ? h(NSpace, { size: 6, wrapItem: false }, () => [
          h(NButton, { size: "tiny", type: "primary", onClick: (e: Event) => { e.stopPropagation(); void approve(r); } },
            { icon: () => h(NIcon, null, () => h(OkIcon)), default: () => t("request_detail.approve_btn") }),
          h(NButton, { size: "tiny", type: "error", ghost: true, onClick: (e: Event) => { e.stopPropagation(); openReject(r); } },
            { icon: () => h(NIcon, null, () => h(CancelIcon)), default: () => t("request_detail.reject_btn") }),
        ])
      : "—",
  },
]));

const columns = computed<DataTableColumns<IPRequest>>(() =>
  allColumns.value.filter((c: any) => c.key === "__act" || rqVis.value.includes(c.key)),
);

// ── 核准 / 駁回（審核人專用，可逐列操作）──
const rejectTarget = ref<IPRequest | null>(null);
const rejectReason = ref("");
const showReject = ref(false);
async function approve(r: IPRequest) {
  try {
    await approveRequest(r.id);
    msg.success(t("request_detail.approved_ok"));
    await refresh();
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.network"));
  }
}
function openReject(r: IPRequest) {
  rejectTarget.value = r;
  rejectReason.value = "";
  showReject.value = true;
}
async function doReject() {
  if (!rejectTarget.value || !rejectReason.value.trim()) {
    msg.warning(t("request_detail.reject_reason_required"));
    return;
  }
  try {
    await rejectRequest(rejectTarget.value.id, rejectReason.value.trim());
    msg.success(t("request_detail.rejected_ok"));
    showReject.value = false;
    await refresh();
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.network"));
  }
}

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
    msg.error(t("errors.network"));
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
    msg.warning(t("requests.error_required"));
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
    msg.success(t("requests.submitted"));
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
    msg.error(e?.response?.data?.detail ?? t("errors.server"));
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
        <span>{{ t("requests.title") }}</span>
      </n-space>
    </template>
    <template #header-extra>
      <n-space align="center">
        <n-checkbox v-model:checked="showMine" style="align-self: center" @update:checked="refresh">
          {{ t("requests.only_mine") }}
        </n-checkbox>
        <n-select
          :value="filterStatus ?? ''"
          :options="statusOptions"
          :placeholder="t('requests.col_status')"
          style="width: 140px"
          @update:value="(v: string) => { filterStatus = v || null; refresh(); }"
        />
        <n-button type="primary" @click="showCreate = true">
          <template #icon><n-icon><RequestsIcon /></n-icon></template>
          {{ t("requests.create") }}
        </n-button>
        <ColumnPicker :all="rqPicker" :visible="rqVis"
                      @update:visible="rqSet" @reset="rqReset" />
      </n-space>
    </template>

    <n-data-table
      :columns="columns"
      :data="rows"
      :loading="loading"
      :pagination="pg"
      :bordered="false"
      :scroll-x="840"
      :row-props="(row: IPRequest) => ({
        style: 'cursor: pointer',
        onClick: () => router.push({ name: 'request-detail', params: { id: row.id } }),
      })"
    />
  </n-card>

  <n-modal
    v-model:show="showCreate"
    preset="dialog"
    :title="t('requests.create_title')"
    style="width: 520px"
  >
    <template #icon><n-icon><RequestsIcon /></n-icon></template>
    <n-form>
      <n-form-item :label="t('nav.subnets')" required>
        <n-select
          v-model:value="form.subnet_id"
          :options="subnetOptions"
          :placeholder="t('requests.pick_subnet')"
          filterable
        />
      </n-form-item>
      <n-form-item :label="t('requests.purpose_label')" required>
        <n-input v-model:value="form.purpose" type="textarea" :rows="2" />
      </n-form-item>
      <n-form-item :label="t('requests.hostname_optional')">
        <n-input v-model:value="form.hostname" placeholder="host01.example.com" />
      </n-form-item>
      <n-form-item :label="t('requests.requested_ip_optional')">
        <n-input v-model:value="form.requested_ip" placeholder="10.0.0.42" />
      </n-form-item>
      <n-form-item :label="t('requests.description_optional')">
        <n-input v-model:value="form.description" type="textarea" :rows="2" />
      </n-form-item>
    </n-form>
    <template #action>
      <n-space>
        <n-button @click="showCreate = false">
          <template #icon><n-icon><CancelIcon /></n-icon></template>
          {{ t("common.cancel") }}
        </n-button>
        <n-button type="primary" :loading="submitting" @click="submitCreate">
          <template #icon><n-icon><SendIcon /></n-icon></template>
          {{ t("common.submit") }}
        </n-button>
      </n-space>
    </template>
  </n-modal>

  <n-modal v-model:show="showReject" preset="dialog"
           :title="t('request_detail.reject_title')" :show-icon="false" style="width: 480px">
    <n-form>
      <n-form-item :label="t('request_detail.reject_reason_label')" required>
        <n-input v-model:value="rejectReason" type="textarea" :rows="3"
                 :placeholder="t('request_detail.reject_reason_ph')" />
      </n-form-item>
    </n-form>
    <template #action>
      <n-space>
        <n-button @click="showReject = false">{{ t("common.cancel") }}</n-button>
        <n-button type="error" @click="doReject">{{ t("request_detail.reject_btn") }}</n-button>
      </n-space>
    </template>
  </n-modal>
</template>
