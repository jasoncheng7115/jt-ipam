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
// 審核人核准時可改配發的 IP（預設帶入 target_ip：申請指定 or 系統自動）
const approveIp = ref("");

const statusLabel = (s: string): string => t(`requests.status_${s}`, s) as string;
const eventLabel = (s: string): string => t(`request_events.${s}`, s) as string;

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
    approveIp.value = detail.value.target_ip ?? "";
  } catch {
    msg.error(t("errors.network"));
  } finally {
    loading.value = false;
  }
}

async function approve() {
  if (!detail.value) return;
  try {
    await approveRequest(detail.value.request.id, approveIp.value.trim() || undefined);
    msg.success(t("request_detail.approved_ok"));
    await load(detail.value.request.id);
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.network"));
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
    msg.error(e?.response?.data?.detail ?? t("errors.network"));
  }
}

async function cancel() {
  if (!detail.value) return;
  try {
    await cancelRequest(detail.value.request.id);
    msg.success(t("request_detail.cancelled_ok"));
    await load(detail.value.request.id);
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.network"));
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
            <span>{{ t("request_detail.title") }}</span>
            <n-tag :type="tagType(detail.request.status)">{{ statusLabel(detail.request.status) }}</n-tag>
          </n-space>
        </template>
        <template #header-extra>
          <n-space>
            <n-popconfirm
              v-if="detail.request.can_approve"
              @positive-click="approve"
            >
              <template #trigger>
                <n-button type="primary">{{ t("request_detail.approve_btn") }}</n-button>
              </template>
              {{ t("request_detail.approve_confirm") }}
            </n-popconfirm>
            <n-button
              v-if="detail.request.can_approve"
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

        <n-descriptions bordered :column="2" label-style="width: 160px">
          <n-descriptions-item :label="t('requests.col_subnet')">
            <router-link :to="{ name: 'subnet-detail', params: { id: detail.request.subnet_id } }"
                         style="font-family: monospace">
              {{ detail.subnet_cidr || (detail.request.subnet_id.slice(0,8) + "…") }}
            </router-link>
          </n-descriptions-item>
          <n-descriptions-item :label="t('requests.col_hostname')">
            {{ detail.request.hostname ?? "—" }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('request_detail.requested_ip')">
            <span style="font-family: monospace">{{ detail.request.requested_ip ?? t("common.any") }}</span>
          </n-descriptions-item>
          <!-- pending：顯示「實際會配發的 IP」；審核人可改 -->
          <n-descriptions-item v-if="detail.request.status === 'pending'" :label="t('request_detail.target_ip')">
            <template v-if="detail.request.can_approve">
              <n-input v-model:value="approveIp" size="small" style="max-width: 200px; font-family: monospace"
                       :placeholder="t('request_detail.target_ip_ph')" />
              <span v-if="detail.target_auto" style="margin-left: 8px; font-size: 12px; opacity: .65">
                {{ t("request_detail.target_auto_hint") }}
              </span>
            </template>
            <template v-else>
              <span style="font-family: monospace">{{ detail.target_ip ?? "—" }}</span>
              <n-tag v-if="detail.target_auto" size="tiny" :bordered="false" style="margin-left: 8px">
                {{ t("request_detail.auto_tag") }}
              </n-tag>
            </template>
          </n-descriptions-item>
          <n-descriptions-item v-else :label="t('request_detail.allocated_ip')">
            <span style="font-family: monospace">{{ detail.allocated_ip ?? "—" }}</span>
          </n-descriptions-item>
          <n-descriptions-item :label="t('requests.purpose_label')" :span="2">
            {{ detail.request.purpose }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('common.description')" :span="2">
            {{ detail.request.description ?? "—" }}
          </n-descriptions-item>
          <n-descriptions-item v-if="detail.request.rejected_reason" :label="t('request_detail.rejected_reason')" :span="2">
            {{ detail.request.rejected_reason }}
          </n-descriptions-item>
        </n-descriptions>
      </n-card>

      <n-card v-if="detail.stages && detail.stages.length" :title="t('request_detail.stages')">
        <n-space vertical :size="8">
          <div v-for="st in detail.stages" :key="st.index" class="stage-row">
            <n-tag size="small" :type="st.approved ? 'success' : (st.is_current ? 'warning' : 'default')" :bordered="false">
              {{ st.approved ? "✓" : (st.is_current ? "▶" : "•") }} {{ st.index + 1 }}
            </n-tag>
            <span :style="{ fontWeight: st.is_current ? 600 : 400, opacity: st.approved ? .7 : 1 }">{{ st.name }}</span>
            <span v-if="st.approved" style="font-size:12px; color: var(--ok,#36ad6a)">{{ t("request_events.approved_and_fulfilled") }}</span>
            <span v-else-if="st.is_current" style="font-size:12px; opacity:.7">{{ t("request_detail.stage_current") }}</span>
            <span v-else style="font-size:12px; opacity:.5">{{ t("request_detail.stage_waiting") }}</span>
          </div>
        </n-space>
      </n-card>

      <n-card :title="t('request_detail.timeline')">
        <n-timeline>
          <n-timeline-item
            v-for="ev in detail.events"
            :key="ev.id"
            :title="eventLabel(ev.event_type)"
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
