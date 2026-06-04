<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
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

const { t } = useI18n();
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
    msg.success(t("request_detail.approved_ok"));
    await load(detail.value.request.id);
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? "Approve failed");
  }
}

async function reject() {
  if (!detail.value || !rejectReason.value.trim()) {
    msg.warning(t("request_detail.reject_reason_required"));
    return;
  }
  try {
    await rejectRequest(detail.value.request.id, rejectReason.value);
    msg.success(t("request_detail.rejected_ok"));
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
    msg.success(t("request_detail.cancelled_ok"));
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
                <n-button type="primary">{{ t("request_detail.approve_btn") }}</n-button>
              </template>
              {{ t("request_detail.approve_confirm") }}
            </n-popconfirm>
            <n-button
              v-if="detail.request.status === 'pending' && me?.is_admin"
              type="error"
              @click="showReject = true"
            >
              {{ t("request_detail.reject_btn") }}
            </n-button>
            <n-popconfirm
              v-if="
                detail.request.status === 'pending' &&
                (me?.id === detail.request.requester_user_id || me?.is_admin)
              "
              @positive-click="cancel"
            >
              <template #trigger>
                <n-button>{{ t("request_detail.cancel_btn") }}</n-button>
              </template>
              {{ t("request_detail.cancel_confirm") }}
            </n-popconfirm>
          </n-space>
        </template>

        <n-descriptions bordered :column="2" label-style="width: 140px">
          <n-descriptions-item label="Subnet">
            {{ detail.request.subnet_id }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('requests.col_hostname')">
            {{ detail.request.hostname ?? "—" }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('request_detail.requested_ip')">
            {{ detail.request.requested_ip ?? t("common.any") }}
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

    <n-modal v-model:show="showReject" preset="dialog" :title="t('request_detail.reject_title')" :show-icon="false">
      <n-form>
        <n-form-item :label="t('request_detail.reject_reason_label')">
          <n-input
            v-model:value="rejectReason"
            type="textarea"
            :rows="3"
            :placeholder="t('request_detail.reject_reason_ph')"
          />
        </n-form-item>
      </n-form>
      <template #action>
        <n-space>
          <n-button @click="showReject = false">{{ t("common.cancel") }}</n-button>
          <n-button type="error" @click="reject">{{ t("request_detail.confirm_reject") }}</n-button>
        </n-space>
      </template>
    </n-modal>
  </n-spin>
</template>
