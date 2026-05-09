<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import {
  NCard,
  NSpace,
  NTag,
  NTimeline,
  NTimelineItem,
  NDescriptions,
  NDescriptionsItem,
  NButton,
  NPopconfirm,
  NInput,
  NModal,
  NForm,
  NFormItem,
  NSpin,
  useMessage,
} from "naive-ui";
import { storeToRefs } from "pinia";
import { useAuthStore } from "@/stores/auth";
import {
  approveRequest,
  cancelRequest,
  getRequest,
  rejectRequest,
  type IPRequestDetail,
} from "@/api/ip_requests";

const route = useRoute();
const auth = useAuthStore();
const { me } = storeToRefs(auth);
const msg = useMessage();

const detail = ref<IPRequestDetail | null>(null);
const loading = ref(false);
const showReject = ref(false);
const rejectReason = ref("");

const tagType = (s: string): "success" | "warning" | "error" | "default" | "info" => {
  if (s === "fulfilled") return "success";
  if (s === "pending") return "info";
  if (s === "rejected") return "error";
  if (s === "cancelled") return "default";
  return "default";
};

async function load(id: string) {
  loading.value = true;
  try {
    detail.value = await getRequest(id);
  } catch {
    msg.error("Failed to load");
  } finally {
    loading.value = false;
  }
}

async function approve() {
  if (!detail.value) return;
  try {
    await approveRequest(detail.value.request.id);
    msg.success("已核准並配發 IP");
    await load(detail.value.request.id);
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? "Approve failed");
  }
}

async function reject() {
  if (!detail.value || !rejectReason.value.trim()) {
    msg.warning("請填寫拒絕原因");
    return;
  }
  try {
    await rejectRequest(detail.value.request.id, rejectReason.value);
    msg.success("已拒絕");
    showReject.value = false;
    rejectReason.value = "";
    await load(detail.value.request.id);
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? "Reject failed");
  }
}

async function cancel() {
  if (!detail.value) return;
  try {
    await cancelRequest(detail.value.request.id);
    msg.success("已取消");
    await load(detail.value.request.id);
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? "Cancel failed");
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
    <n-space v-if="detail" vertical :size="16">
      <n-card>
        <template #header>
          <n-space align="center">
            <span>IP Request</span>
            <n-tag :type="tagType(detail.request.status)">{{ detail.request.status }}</n-tag>
          </n-space>
        </template>
        <template #header-extra>
          <n-space>
            <n-popconfirm
              v-if="detail.request.status === 'pending' && me?.is_admin"
              @positive-click="approve"
            >
              <template #trigger>
                <n-button type="primary">核准（配發 IP）</n-button>
              </template>
              將立即配發 IP 並通知申請者。確定？
            </n-popconfirm>
            <n-button
              v-if="detail.request.status === 'pending' && me?.is_admin"
              type="error"
              @click="showReject = true"
            >
              拒絕
            </n-button>
            <n-popconfirm
              v-if="
                detail.request.status === 'pending' &&
                (me?.id === detail.request.requester_user_id || me?.is_admin)
              "
              @positive-click="cancel"
            >
              <template #trigger>
                <n-button>取消申請</n-button>
              </template>
              確定取消這項申請？
            </n-popconfirm>
          </n-space>
        </template>

        <n-descriptions bordered :column="2" label-style="width: 140px">
          <n-descriptions-item label="Subnet">
            {{ detail.request.subnet_id }}
          </n-descriptions-item>
          <n-descriptions-item label="Hostname">
            {{ detail.request.hostname ?? "—" }}
          </n-descriptions-item>
          <n-descriptions-item label="指定 IP">
            {{ detail.request.requested_ip ?? "（任意）" }}
          </n-descriptions-item>
          <n-descriptions-item label="Allocated IP">
            {{ detail.request.allocated_ip_id ?? "—" }}
          </n-descriptions-item>
          <n-descriptions-item label="Purpose" :span="2">
            {{ detail.request.purpose }}
          </n-descriptions-item>
          <n-descriptions-item label="Description" :span="2">
            {{ detail.request.description ?? "—" }}
          </n-descriptions-item>
          <n-descriptions-item v-if="detail.request.rejected_reason" label="Rejected reason" :span="2">
            {{ detail.request.rejected_reason }}
          </n-descriptions-item>
        </n-descriptions>
      </n-card>

      <n-card title="Timeline">
        <n-timeline>
          <n-timeline-item
            v-for="ev in detail.events"
            :key="ev.id"
            :title="ev.event_type"
            :content="ev.message ?? ''"
            :time="ev.created_at"
            :type="
              ev.event_type === 'approved_and_fulfilled'
                ? 'success'
                : ev.event_type === 'rejected'
                ? 'error'
                : ev.event_type === 'cancelled'
                ? 'warning'
                : 'info'
            "
          />
        </n-timeline>
      </n-card>
    </n-space>

    <n-modal v-model:show="showReject" preset="dialog" title="拒絕申請" :show-icon="false">
      <n-form>
        <n-form-item label="拒絕原因（必填）">
          <n-input
            v-model:value="rejectReason"
            type="textarea"
            :rows="3"
            placeholder="說明拒絕的具體理由（會通知申請者）"
          />
        </n-form-item>
      </n-form>
      <template #action>
        <n-space>
          <n-button @click="showReject = false">取消</n-button>
          <n-button type="error" @click="reject">確認拒絕</n-button>
        </n-space>
      </template>
    </n-modal>
  </n-spin>
</template>
