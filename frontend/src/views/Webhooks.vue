<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NButton, NModal, NForm, NFormItem,
  NInput, NPopconfirm, NTag, NAlert, NCode,
  useMessage, type DataTableColumns,
} from "naive-ui";
import {
  listWebhooks, createWebhook, deleteWebhook, type Webhook,
} from "@/api/phase3";

const { t } = useI18n();
const msg = useMessage();
const rows = ref<Webhook[]>([]);
const loading = ref(false);
const show = ref(false);
const form = ref({ name: "", target_url: "https://", events_csv: "*" });
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
    const events = form.value.events_csv.split(",").map((s) => s.trim()).filter(Boolean);
    const r = await createWebhook({
      name: form.value.name,
      target_url: form.value.target_url,
      events: events.length ? events : ["*"],
    });
    show.value = false;
    newSecret.value = r.secret;
    showSecret.value = true;
    form.value = { name: "", target_url: "https://", events_csv: "*" };
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function del(r: Webhook) {
  try { await deleteWebhook(r.id); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

const cols = computed<DataTableColumns<Webhook>>(() => [
  { title: t("common.name"), key: "name" },
  { title: "target URL", key: "target_url" },
  {
    title: "events", key: "events",
    render: (r) => h(NSpace, { size: 4 }, () =>
      r.events.map((e) => h(NTag, { size: "small" }, () => e))),
  },
  {
    title: t("common.status"), key: "enabled",
    render: (r) => h(NTag, { size: "small", type: r.enabled ? "success" : "warning" },
      () => r.enabled ? t("common.enabled") : t("common.disabled")),
  },
  { title: "fail count", key: "failure_count" },
  { title: "last error", key: "last_error", render: (r) => r.last_error ?? "—" },
  {
    title: t("common.actions"), key: "actions", width: 100,
    render: (r) => h(NPopconfirm, { onPositiveClick: () => del(r) }, {
      trigger: () => h(NButton, { size: "small", type: "error" }, () => t("common.delete")),
      default: () => t("common.confirm_delete"),
    }),
  },
]);
onMounted(() => { void refresh(); });
</script>

<template>
  <n-card :title="t('nav.webhooks')">
    <n-space style="margin-bottom: 12px">
      <n-button @click="refresh" :loading="loading">{{ t("common.refresh") }}</n-button>
      <n-button type="primary" @click="show = true">{{ t("common.create") }}</n-button>
    </n-space>
    <n-data-table :columns="cols" :data="rows" :loading="loading" :bordered="false" />

    <n-modal v-model:show="show" preset="card" :title="t('common.create')" style="width: 480px">
      <n-form>
        <n-form-item :label="t('common.name')"><n-input v-model:value="form.name" /></n-form-item>
        <n-form-item label="target URL">
          <n-input v-model:value="form.target_url" placeholder="https://hook.example.com/path" />
        </n-form-item>
        <n-form-item :label="t('webhooks.events_label')">
          <n-input v-model:value="form.events_csv"
                   placeholder="* 或 ip.create,subnet.update（逗號分隔）" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="show = false">{{ t("common.cancel") }}</n-button>
        <n-button type="primary" @click="submit">{{ t("common.save") }}</n-button>
      </n-space>
    </n-modal>

    <n-modal v-model:show="showSecret" preset="card"
             :title="t('webhooks.secret_title')" style="width: 540px">
      <n-alert type="warning" style="margin-bottom: 12px">
        {{ t("webhooks.secret_warning") }}
      </n-alert>
      <n-code :code="newSecret" language="plaintext" word-wrap />
      <n-space justify="end" style="margin-top: 16px">
        <n-button type="primary" @click="showSecret = false">{{ t("common.ok") }}</n-button>
      </n-space>
    </n-modal>
  </n-card>
</template>
