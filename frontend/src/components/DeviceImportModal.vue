<script setup lang="ts">
/**
 * 裝置匯入（issue #46）：選檔（CSV／Excel）→ 預覽每一列會新增、更新、略過或有什麼錯 → 確認才匯入。
 * 匯出的檔案（任何介面語言）改完可以直接匯回來；範本可以帶出全部現有裝置，改完用「更新」模式匯回。
 */
import { computed, h, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  NAlert, NButton, NDataTable, NIcon, NModal, NRadio, NRadioGroup, NSpace, NTag, useMessage,
  type DataTableColumns,
} from "naive-ui";
import { apiErrMsg } from "@/api/client";
import {
  downloadDeviceTemplate, previewDeviceImport, runDeviceImport,
  type DeviceImportMessage, type DeviceImportPreview, type DeviceImportRow,
} from "@/api/deviceImport";
import { DownloadIcon, UploadIcon } from "@/icons";

const props = defineProps<{ show: boolean }>();
const emit = defineEmits<{ (e: "update:show", v: boolean): void; (e: "queued", taskId: string): void }>();
const { t, te } = useI18n();
const msg = useMessage();

const file = ref<File | null>(null);
const onExisting = ref<"skip" | "update">("skip");
const preview = ref<DeviceImportPreview | null>(null);
const busy = ref(false);
const fileInput = ref<HTMLInputElement | null>(null);

watch(() => props.show, (v) => {
  if (v) { file.value = null; preview.value = null; onExisting.value = "skip"; }
});
// 模式改了，舊的預覽就不準了
watch(onExisting, () => { if (file.value) void runPreview(); });

function pick() { fileInput.value?.click(); }
async function onFile(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0];
  (e.target as HTMLInputElement).value = "";
  if (!f) return;
  file.value = f;
  await runPreview();
}

async function runPreview() {
  if (!file.value) return;
  busy.value = true;
  preview.value = null;
  try {
    preview.value = await previewDeviceImport(file.value, onExisting.value);
  } catch (e) {
    msg.error(apiErrMsg(e), { duration: 8000, closable: true });
  } finally {
    busy.value = false;
  }
}

const canRun = computed(() => !!preview.value && !preview.value.fatal
  && preview.value.created + preview.value.updated > 0);

async function run() {
  if (!file.value || !canRun.value) return;
  busy.value = true;
  try {
    const r = await runDeviceImport(file.value, onExisting.value);
    emit("queued", r.task_id);
    emit("update:show", false);
  } catch (e) {
    msg.error(apiErrMsg(e), { duration: 8000, closable: true });
  } finally {
    busy.value = false;
  }
}

async function template(withData: boolean) {
  try { await downloadDeviceTemplate(withData); } catch (e) { msg.error(apiErrMsg(e)); }
}

function msgText(m: DeviceImportMessage): string {
  const key = `device_import.msg.${m.code}`;
  return te(key) ? t(key, m.params as Record<string, unknown>) : m.code;
}
const ACTION_TYPE = { create: "success", update: "info", skip: "default", error: "error" } as const;

const cols = computed<DataTableColumns<DeviceImportRow>>(() => [
  { title: t("device_import.col_line"), key: "line", width: 70 },
  { title: t("device_import.col_name"), key: "name", width: 200, ellipsis: { tooltip: true },
    render: (r) => r.name ?? "—" },
  { title: t("device_import.col_action"), key: "action", width: 100,
    render: (r) => h(NTag, { size: "small", bordered: false, type: ACTION_TYPE[r.action] },
      () => t(`device_import.action_${r.action}`)) },
  { title: t("device_import.col_messages"), key: "messages", minWidth: 260,
    render: (r) => (r.messages.length
      ? h("div", { class: "di-msgs" }, r.messages.map((m) =>
          h("div", { class: m.code.startsWith("warn_") ? "di-warn" : r.action === "error" ? "di-err" : "" }, msgText(m))))
      : "—") },
]);

const fatalText = computed(() => {
  const f = preview.value?.fatal;
  if (!f) return "";
  const key = `device_import.fatal_${f.code}`;
  return te(key) ? t(key, (f.params ?? {}) as Record<string, unknown>) : f.code;
});
</script>

<template>
  <n-modal :show="show" preset="card" :title="t('device_import.title')" style="width: min(860px, 100%)"
           @update:show="(v: boolean) => emit('update:show', v)">
    <n-alert type="info" :bordered="false" style="margin-bottom: 12px">
      <div>{{ t("device_import.intro") }}</div>
      <div class="di-cols">{{ t("device_import.columns") }}</div>
      <n-space :size="8" style="margin-top: 8px">
        <n-button size="small" data-testid="device-import-template" @click="template(false)">
          <template #icon><n-icon><DownloadIcon /></n-icon></template>{{ t("device_import.template") }}
        </n-button>
        <n-button size="small" data-testid="device-import-template-data" @click="template(true)">
          <template #icon><n-icon><DownloadIcon /></n-icon></template>{{ t("device_import.template_with_data") }}
        </n-button>
      </n-space>
    </n-alert>

    <div class="di-row">
      <span class="di-label">{{ t("device_import.on_existing") }}</span>
      <n-radio-group v-model:value="onExisting" size="small">
        <n-radio value="skip" data-testid="device-import-skip">{{ t("device_import.mode_skip") }}</n-radio>
        <n-radio value="update" data-testid="device-import-update">{{ t("device_import.mode_update") }}</n-radio>
      </n-radio-group>
    </div>

    <div class="di-row">
      <input ref="fileInput" type="file" accept=".csv,.xlsx,text/csv" class="di-file"
             data-testid="device-import-file" @change="onFile" />
      <n-button :loading="busy && !preview" @click="pick">
        <template #icon><n-icon><UploadIcon /></n-icon></template>{{ t("device_import.choose") }}
      </n-button>
      <span v-if="file" class="di-fname">{{ file.name }}</span>
    </div>

    <template v-if="preview">
      <n-alert v-if="preview.fatal" type="error" :bordered="false" data-testid="device-import-fatal">{{ fatalText }}</n-alert>
      <template v-else>
        <n-space :size="6" style="margin: 4px 0 8px" data-testid="device-import-summary">
          <n-tag type="success" :bordered="false">{{ t("device_import.sum_create", { n: preview.created }) }}</n-tag>
          <n-tag type="info" :bordered="false">{{ t("device_import.sum_update", { n: preview.updated }) }}</n-tag>
          <n-tag :bordered="false">{{ t("device_import.sum_skip", { n: preview.skipped }) }}</n-tag>
          <n-tag type="error" :bordered="false">{{ t("device_import.sum_error", { n: preview.errored }) }}</n-tag>
          <n-tag v-if="preview.warnings" type="warning" :bordered="false">{{ t("device_import.sum_warn", { n: preview.warnings }) }}</n-tag>
        </n-space>
        <n-alert v-if="preview.ignored_columns.length" type="warning" :bordered="false" style="margin-bottom: 8px">
          {{ t("device_import.ignored_columns", { cols: preview.ignored_columns.join("、") }) }}
        </n-alert>
        <n-data-table :columns="cols" :data="preview.rows" size="small" :max-height="340" :scroll-x="640"
                      :row-key="(r: DeviceImportRow) => r.line" data-testid="device-import-rows" />
        <div v-if="preview.errored" class="di-note">{{ t("device_import.errors_not_imported") }}</div>
      </template>
    </template>

    <template #footer>
      <n-space justify="end">
        <n-button @click="emit('update:show', false)">{{ t("common.cancel") }}</n-button>
        <n-button type="primary" :disabled="!canRun" :loading="busy && !!preview" data-testid="device-import-run" @click="run">
          {{ preview && canRun ? t("device_import.run_n", { n: preview.created + preview.updated }) : t("device_import.run") }}
        </n-button>
      </n-space>
    </template>
  </n-modal>
</template>

<style scoped>
.di-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
.di-label { font-size: 13px; opacity: .8; }
.di-file { display: none; }
.di-fname { font-size: 13px; opacity: .8; word-break: break-all; }
.di-cols { font-size: 12px; opacity: .8; margin-top: 4px; line-height: 1.6; }
.di-msgs { display: flex; flex-direction: column; gap: 2px; font-size: 12.5px; }
.di-err { color: var(--n-error-color, #d03050); }
.di-warn { color: #d0a300; }
.di-note { font-size: 12px; opacity: .75; margin-top: 6px; }
</style>
