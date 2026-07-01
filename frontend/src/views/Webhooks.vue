<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NIcon, NButton, NModal, NForm, NFormItem,
  NInput, NCheckbox, NCheckboxGroup, NPopconfirm, NTag, NAlert, NCode, NTooltip,
  useMessage, type DataTableColumns,
} from "naive-ui";
import {
  WebhooksIcon, PlusIcon, DeleteIcon, RefreshIcon, SaveIcon, CancelIcon, OkIcon, WarnIcon,
} from "@/icons";
import {
  listWebhooks, createWebhook, deleteWebhook, type Webhook,
} from "@/api/phase3";
import { autoSort } from "@/composables/useTableSort";
import ColumnPicker from "@/components/ColumnPicker.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
const { t } = useI18n();

const { visibleKeys: wbVis, setVisible: wbSet, reset: wbReset } = useColumnPrefs(
  "webhooks",
  ["name", "target_url", "events", "enabled", "failure_count", "last_error", "actions"],
  ["name", "target_url", "events", "enabled", "failure_count", "last_error", "actions"],
);
const wbPicker = computed(() => [
  { key: "name", label: t("cols.name") },
  { key: "target_url", label: "Target URL" },
  { key: "events", label: t("cols.event") },
  { key: "enabled", label: t("cols.status") },
  { key: "failure_count", label: t("cols.failed_login") },
  { key: "last_error", label: t("cols.last_error") },
  { key: "actions", label: t("cols.actions") },
]);

const msg = useMessage();
const rows = ref<Webhook[]>([]);
const loading = ref(false);
const show = ref(false);
const form = ref<{ name: string; target_url: string; events: string[] }>({
  name: "", target_url: "", events: ["*"],
});

// 後端實際會發送的事件（app/services/notification.deliver_event 比對；"*" = 全部）。
// 新增事件種類時這裡與 i18n 一起補。
const EVENT_CATALOG = [
  { value: "*", descKey: "webhooks.ev_all" },
  { value: "subnet.created", descKey: "webhooks.ev_subnet_created" },
  { value: "ip_request.created", descKey: "webhooks.ev_ipreq_created" },
  { value: "ip_request.fulfilled", descKey: "webhooks.ev_ipreq_fulfilled" },
  { value: "ip_request.rejected", descKey: "webhooks.ev_ipreq_rejected" },
  { value: "anomaly.detected", descKey: "webhooks.ev_anomaly" },
];
const eventOptions = computed(() =>
  EVENT_CATALOG.map((e) => ({ value: e.value, desc: t(e.descKey) })));
const showSecret = ref(false);
const newSecret = ref("");

async function refresh() {
  loading.value = true;
  try { rows.value = (await listWebhooks()).items; }
  catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}
async function submit() {
  try {
    const r = await createWebhook({
      name: form.value.name,
      target_url: form.value.target_url,
      events: form.value.events.length ? form.value.events : ["*"],
    });
    show.value = false;
    newSecret.value = r.secret;
    showSecret.value = true;
    form.value = { name: "", target_url: "", events: ["*"] };
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function del(r: Webhook) {
  try { await deleteWebhook(r.id); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

function iconAction(icon: any, label: string, onClick: () => void, type?: any) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}
const allCols = computed<DataTableColumns<Webhook>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 160, ellipsis: { tooltip: true } },
  { title: t("cols.target_url"), key: "target_url", minWidth: 220, ellipsis: { tooltip: true } },
  {
    title: t("cols.events"), key: "events", width: 160,
    render: (r) => h(NSpace, { size: 4 }, () =>
      r.events.map((e) => h(NTag, { size: "small" }, () => e))),
  },
  {
    title: t("common.status"), key: "enabled", width: 100,
    render: (r) => h(NTag, { size: "small", type: r.enabled ? "success" : "warning" },
      () => r.enabled ? t("common.enabled") : t("common.disabled")),
  },
  { title: t("cols.fail_count"), key: "failure_count", width: 100 },
  { title: t("cols.last_error"), key: "last_error", minWidth: 160, ellipsis: { tooltip: true }, render: (r) => r.last_error ?? "—" },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 56,
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      h(NPopconfirm, { onPositiveClick: () => del(r) }, {
        trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]));

const cols = computed<DataTableColumns<Webhook>>(() =>
  allCols.value.filter((c: any) => wbVis.value.includes(c.key)),
);

onMounted(() => { void refresh(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><WebhooksIcon /></n-icon>
        <span>{{ t("nav.webhooks") }}</span>
      </n-space>
    </template>
    <n-space style="margin-bottom: 12px">
      <n-button @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
      <n-button type="primary" @click="show = true">
        <template #icon><n-icon><PlusIcon /></n-icon></template>
        {{ t("common.create") }}
      </n-button>
      <ColumnPicker :all="wbPicker" :visible="wbVis"
                    @update:visible="wbSet" @reset="wbReset" />
    </n-space>
    <n-data-table :columns="cols" :data="rows" :loading="loading" :bordered="false" :scroll-x="956" />

    <n-modal v-model:show="show" preset="card" style="width: 480px">
      <template #header>
        <n-space align="center">
          <n-icon :size="20"><PlusIcon /></n-icon>
          <span>{{ t("common.create") }}</span>
        </n-space>
      </template>
      <n-form>
        <n-form-item :label="t('common.name')"><n-input v-model:value="form.name" /></n-form-item>
        <n-form-item label="target URL">
          <n-input v-model:value="form.target_url" placeholder="https://hook.example.com/path" />
        </n-form-item>
        <n-form-item :label="t('webhooks.events_label')">
          <n-checkbox-group v-model:value="form.events" style="width:100%">
            <n-space vertical size="small" style="width:100%">
              <div v-for="e in eventOptions" :key="e.value" class="ev-row">
                <n-checkbox :value="e.value">
                  <code class="ev-name">{{ e.value }}</code>
                </n-checkbox>
                <div class="ev-desc">{{ e.desc }}</div>
              </div>
            </n-space>
          </n-checkbox-group>
          <template #feedback>{{ t("webhooks.events_hint") }}</template>
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="show = false">
          <template #icon><n-icon><CancelIcon /></n-icon></template>
          {{ t("common.cancel") }}
        </n-button>
        <n-button type="primary" @click="submit">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </n-space>
    </n-modal>

    <n-modal v-model:show="showSecret" preset="card" style="width: 540px">
      <template #header>
        <n-space align="center">
          <n-icon :size="20"><WarnIcon /></n-icon>
          <span>{{ t("webhooks.secret_title") }}</span>
        </n-space>
      </template>
      <n-alert type="warning" style="margin-bottom: 12px">
        <template #icon><n-icon><WarnIcon /></n-icon></template>
        {{ t("webhooks.secret_warning") }}
      </n-alert>
      <n-code :code="newSecret" language="plaintext" word-wrap />
      <n-space justify="end" style="margin-top: 16px">
        <n-button type="primary" @click="showSecret = false">
          <template #icon><n-icon><OkIcon /></n-icon></template>
          {{ t("common.ok") }}
        </n-button>
      </n-space>
    </n-modal>
  </n-card>
</template>

<style scoped>
.ev-row { line-height: 1.4; }
.ev-name { font-size: 12px; }
.ev-desc { font-size: 12px; opacity: .6; margin-left: 24px; margin-top: 1px; }
</style>
