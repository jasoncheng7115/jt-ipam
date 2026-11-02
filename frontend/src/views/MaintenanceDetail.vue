<script setup lang="ts">
/** 維護排程詳細資料：時間、範圍與影響、被靜音的告警；進行中可延長或提前結束。 */
import { computed, h, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import {
  NButton, NCard, NDataTable, NDescriptions, NDescriptionsItem, NDropdown, NEmpty, NIcon, NPopconfirm, NSpace, NSpin,
  NTag, useMessage, type DataTableColumns,
} from "naive-ui";
import { ArrowLeft as ArrowLeftIcon } from "@iconoir/vue";
import { DeleteIcon, EditIcon, EndNowIcon, ExtendIcon, MaintenanceIcon, MuteIcon } from "@/icons";
import { apiErrMsg } from "@/api/client";
import { Maintenance, type Window } from "@/api/maintenance";
import MaintenanceEditModal from "@/components/MaintenanceEditModal.vue";
import { fmtDateTime } from "@/utils/datetime";
import { scheduleText, statusLine } from "@/utils/maintenance";

const route = useRoute();
const router = useRouter();
const { t, te } = useI18n();
const msg = useMessage();
const data = ref<Window | null>(null);
const sup = ref<{ event: string; label: string | null; first_at: string; count: number }[]>([]);
const loading = ref(false);
const editShow = ref(false);

async function load() {
  loading.value = true;
  try {
    const id = String(route.params.id);
    [data.value, sup.value] = await Promise.all([Maintenance.get(id), Maintenance.suppressed(id)]);
  } catch (e) { msg.error(apiErrMsg(e)); data.value = null; } finally { loading.value = false; }
}
async function act(fn: () => Promise<unknown>) {
  try { await fn(); await load(); } catch (e) { msg.error(apiErrMsg(e)); }
}
async function remove() {
  try { await Maintenance.remove(data.value!.id); router.push({ name: "maintenance" }); }
  catch (e) { msg.error(apiErrMsg(e)); }
}
function eventText(ev: string): string {
  const k = `notify_ch.ev.${ev.replace(/\./g, "_")}`;
  return te(k) ? t(k) : ev;
}
const supCols = computed<DataTableColumns<(typeof sup.value)[number]>>(() => [
  { title: t("maint.sup_event"), key: "event", width: 220, render: (r) => eventText(r.event) },
  { title: t("maint.sup_target"), key: "label", minWidth: 200, render: (r) => r.label || "—" },
  { title: t("maint.sup_first"), key: "first_at", width: 170, render: (r) => fmtDateTime(r.first_at) },
  { title: t("maint.sup_count"), key: "count", width: 80 },
]);
const ico = (c: typeof EditIcon) => () => h(NIcon, null, () => h(c));
const adjustOpts = computed(() => [
  ...[1, 2, 4].map((n) => ({ key: String(n * 60), label: t("maint.extend_n", { n }), icon: ico(ExtendIcon) })),
  { type: "divider", key: "d" },
  { key: "end", label: t("maint.end_now"), icon: ico(EndNowIcon) }]);
const manual = computed(() => data.value?.source === "manual" && data.value?.can_manage);

watch(() => route.params.id, (id) => { if (id) void load(); });
onMounted(load);
</script>

<template>
  <n-spin :show="loading">
    <n-space vertical :size="16">
      <n-card v-if="data">
        <template #header>
          <n-space align="center" :wrap-item="false">
            <n-icon :size="22"><MaintenanceIcon /></n-icon><span data-testid="maint-detail-title">{{ data.title }}</span>
            <n-tag size="small" :bordered="false" :type="data.status === 'active' ? 'warning' : data.status === 'upcoming' ? 'info' : 'default'">
              {{ t(`maint.status_${data.status}`) }}
            </n-tag>
          </n-space>
        </template>
        <n-space align="center" justify="end" style="margin-bottom: 10px">
          <template v-if="manual">
            <n-dropdown v-if="data.status === 'active'" trigger="click" :options="adjustOpts"
                        @select="(k: string) => act(() => (k === 'end' ? Maintenance.end(data!.id) : Maintenance.extend(data!.id, Number(k))))">
              <n-button size="small" type="warning" ghost data-testid="maint-adjust">
                <template #icon><n-icon><ExtendIcon /></n-icon></template>{{ t("maint.adjust") }}
              </n-button>
            </n-dropdown>
            <n-button size="small" type="primary" @click="editShow = true">
              <template #icon><n-icon><EditIcon /></n-icon></template>{{ t("common.edit") }}
            </n-button>
            <n-popconfirm @positive-click="remove">
              <template #trigger>
                <n-button size="small" type="error" ghost>
                  <template #icon><n-icon><DeleteIcon /></n-icon></template>{{ t("common.delete") }}
                </n-button>
              </template>
              {{ t("maint.delete_confirm", { title: data.title }) }}
            </n-popconfirm>
          </template>
          <n-button size="small" @click="router.back()">
            <template #icon><n-icon><ArrowLeftIcon /></n-icon></template>{{ t("common.back") }}
          </n-button>
        </n-space>
        <n-descriptions label-placement="left" :column="2" size="small" bordered>
          <n-descriptions-item :label="t('maint.status')">{{ statusLine(data, t) }}</n-descriptions-item>
          <n-descriptions-item :label="t('maint.when')">{{ scheduleText(data, t) }}</n-descriptions-item>
          <n-descriptions-item :label="t('maint.behavior')">{{ t(`maint.behavior_${data.behavior}`) }}</n-descriptions-item>
          <n-descriptions-item :label="t('maint.ticket')">{{ data.ticket || "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('maint.scopes')" :span="2">
            <n-space :size="4">
              <n-tag v-for="(s, i) in data.scopes" :key="i" size="small" :bordered="false">{{ t(`maint.scope_${s.scope_type}`) }} {{ s.label }}</n-tag>
            </n-space>
            <div v-if="data.counts" class="sub" style="margin-top: 6px">
              {{ t("maint.preview", { ips: data.counts.ips, subnets: data.counts.subnets, devices: data.counts.devices,
                                      integrations: data.counts.integrations, agents: data.counts.agents }) }}
            </div>
          </n-descriptions-item>
          <n-descriptions-item :label="t('maint.note')" :span="2"><span class="pre">{{ data.note || "—" }}</span></n-descriptions-item>
        </n-descriptions>
      </n-card>
      <n-card v-if="data" data-testid="maint-suppressed">
        <template #header>
          <span style="display:inline-flex;align-items:center;gap:8px"><n-icon :size="18"><MuteIcon /></n-icon>{{ t("maint.suppressed_title", { n: sup.length }) }}</span>
        </template>
        <div class="sub" style="margin-bottom: 8px">{{ t("maint.suppressed_hint") }}</div>
        <n-empty v-if="!sup.length" :description="t('maint.suppressed_empty')" />
        <n-data-table v-else size="small" :columns="supCols" :data="sup" :bordered="false" />
      </n-card>
    </n-space>
    <MaintenanceEditModal v-model:show="editShow" :window="data" @saved="load" />
  </n-spin>
</template>

<style scoped>
.sub { font-size: 12.5px; opacity: .7; }
.pre { white-space: pre-wrap; }
</style>
