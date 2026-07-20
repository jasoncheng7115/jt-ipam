<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NAlert, NButton, NCard, NCheckbox, NCheckboxGroup, NCode, NDataTable, NDivider,
  NIcon, NInput, NPopconfirm, NRadio, NRadioGroup, NSpace, NSpin, NTag, useMessage,
} from "naive-ui";
import { AdminIcon, ExportIcon, ImportIcon } from "@/icons";
import { getTask } from "@/api/tasks";
import {
  analyzeImport, applyImport, downloadExport, getTransferSchema, startExport,
  type AnalyzeResult, type ImportReport, type TransferSchema,
} from "@/api/systemTransfer";

const { t } = useI18n();
const msg = useMessage();

// ─────────────────── schema / scope ───────────────────
const schema = ref<TransferSchema | null>(null);
const scope = ref<string[]>([]);

onMounted(async () => {
  try {
    schema.value = await getTransferSchema();
    scope.value = [...schema.value.default_scope];
  } catch {
    msg.error(t("errors.network"));
  }
});

function scopeCount(cat: string): number {
  return schema.value?.counts?.[cat] ?? 0;
}

// ─────────────────── export ───────────────────
const expPass = ref("");
const expPass2 = ref("");
const expBusy = ref(false);
const expTaskId = ref<string | null>(null);
const expReady = ref(false);
const expSummary = ref<Record<string, unknown> | null>(null);
let expTimer: ReturnType<typeof setInterval> | null = null;

const canExport = computed(
  () => scope.value.length > 0 && expPass.value.length >= 8 && expPass.value === expPass2.value,
);

async function runExport() {
  if (!canExport.value) return;
  expBusy.value = true;
  expReady.value = false;
  expSummary.value = null;
  try {
    const { task_id } = await startExport(scope.value, expPass.value);
    expTaskId.value = task_id;
    expTimer = setInterval(async () => {
      try {
        const tk = await getTask(task_id);
        if (tk.status === "succeeded") {
          stopExpTimer();
          expBusy.value = false;
          expReady.value = true;
          expSummary.value = tk.summary;
          msg.success(t("system_transfer.export_ready"));
        } else if (tk.status === "failed" || tk.status === "cancelled") {
          stopExpTimer();
          expBusy.value = false;
          msg.error(tk.error || t("system_transfer.export_failed"));
        }
      } catch { /* transient — retry next tick */ }
    }, 1500);
  } catch (e: any) {
    expBusy.value = false;
    msg.error(e?.response?.data?.detail ?? t("errors.network"));
  }
}

function stopExpTimer() {
  if (expTimer) { clearInterval(expTimer); expTimer = null; }
}

async function doDownload() {
  if (!expTaskId.value) return;
  try {
    const blob = await downloadExport(expTaskId.value);
    const fname = (expSummary.value?.filename as string) || `jt-ipam-export-${expTaskId.value}.json`;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = fname;
    a.click();
    URL.revokeObjectURL(url);
  } catch {
    msg.error(t("system_transfer.download_failed"));
  }
}

// ─────────────────── import ───────────────────
const impFile = ref<File | null>(null);
const impPass = ref("");
const impMode = ref<"merge" | "replace">("merge");
const impBusy = ref(false);
const analyzed = ref<AnalyzeResult | null>(null);
const report = ref<ImportReport | null>(null);
const impTaskId = ref<string | null>(null);
const impTaskStatus = ref<string | null>(null);
let impTimer: ReturnType<typeof setInterval> | null = null;

function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement;
  impFile.value = input.files?.[0] ?? null;
  analyzed.value = null;
  report.value = null;
}

async function doAnalyze() {
  if (!impFile.value || !impPass.value) return;
  impBusy.value = true;
  report.value = null;
  try {
    analyzed.value = await analyzeImport(impFile.value, impPass.value);
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("system_transfer.analyze_failed"));
  } finally {
    impBusy.value = false;
  }
}

const countRows = computed(() => {
  const src = report.value?.tables
    ? Object.entries(report.value.tables).map(([k, v]) => ({ table: k, ...v }))
    : Object.entries(analyzed.value?.counts ?? {}).map(([k, v]) => ({
        table: k, inserted: v, updated: 0, skipped: 0, errored: 0,
      }));
  return src.filter((r) => r.inserted || r.updated || r.skipped || r.errored);
});

const reportColumns = computed(() => [
  { title: t("system_transfer.col_table"), key: "table" },
  { title: "+", key: "inserted", width: 70 },
  { title: "~", key: "updated", width: 70 },
  { title: "skip", key: "skipped", width: 70 },
  { title: t("system_transfer.col_errored"), key: "errored", width: 80 },
]);

async function doApply(dryRun: boolean) {
  if (!analyzed.value || !impPass.value) return;
  impBusy.value = true;
  report.value = null;
  try {
    const res = await applyImport(analyzed.value.token, impPass.value, impMode.value, dryRun);
    if (res.dry_run && res.report) {
      report.value = res.report;
      msg.info(t("system_transfer.dry_run_done"));
    } else if (res.task_id) {
      impTaskId.value = res.task_id;
      impTaskStatus.value = "running";
      impTimer = setInterval(async () => {
        try {
          const tk = await getTask(res.task_id!);
          impTaskStatus.value = tk.status;
          if (tk.status === "succeeded") {
            stopImpTimer();
            impBusy.value = false;
            report.value = (tk.summary as unknown as ImportReport) ?? null;
            msg.success(t("system_transfer.import_done"));
          } else if (tk.status === "failed" || tk.status === "cancelled") {
            stopImpTimer();
            impBusy.value = false;
            msg.error(tk.error || t("system_transfer.import_failed"));
          }
        } catch { /* retry */ }
      }, 1500);
      return; // keep busy until poll resolves
    }
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("system_transfer.analyze_failed"));
  } finally {
    if (dryRun) impBusy.value = false;
  }
}

function stopImpTimer() {
  if (impTimer) { clearInterval(impTimer); impTimer = null; }
}

onUnmounted(() => { stopExpTimer(); stopImpTimer(); });
</script>

<template>
  <div class="st-wrap">
    <n-card>
      <template #header>
        <n-space align="center" :wrap-item="false">
          <n-icon :size="22"><AdminIcon /></n-icon>
          <span>{{ t("system_transfer.title") }}</span>
        </n-space>
      </template>
      <n-alert type="info" :bordered="false" style="margin-bottom: 4px">
        {{ t("system_transfer.intro") }}
      </n-alert>
    </n-card>

    <div class="st-cols">
    <!-- ═══════════ 匯出 ═══════════ -->
    <n-card>
      <template #header>
        <n-space align="center" :wrap-item="false">
          <n-icon :size="18"><ExportIcon /></n-icon>
          <span>{{ t("system_transfer.export_title") }}</span>
        </n-space>
      </template>

      <div class="st-group">
        <div class="st-label">{{ t("system_transfer.scope_label") }}</div>
        <n-checkbox-group v-model:value="scope">
          <div class="st-scope-grid">
            <n-checkbox v-for="cat in schema?.scopes ?? []" :key="cat" :value="cat">
              {{ t(`system_transfer.scope.${cat}`) }}
              <n-tag size="small" :bordered="false" style="margin-left: 6px">{{ scopeCount(cat) }}</n-tag>
            </n-checkbox>
          </div>
        </n-checkbox-group>
        <div class="st-hint">{{ t(`system_transfer.scope_hint`) }}</div>
      </div>

      <div class="st-group">
        <div class="st-label">{{ t("system_transfer.passphrase") }}</div>
        <n-space vertical style="max-width: 420px">
          <n-input v-model:value="expPass" type="password" show-password-on="click"
                   :placeholder="t('system_transfer.passphrase_ph')" />
          <n-input v-model:value="expPass2" type="password" show-password-on="click"
                   :placeholder="t('system_transfer.passphrase_confirm')" />
        </n-space>
        <div class="st-hint">{{ t("system_transfer.passphrase_hint") }}</div>
      </div>

      <n-space align="center">
        <n-button type="primary" :disabled="!canExport" :loading="expBusy" @click="runExport">
          <template #icon><n-icon><ExportIcon /></n-icon></template>
          {{ t("system_transfer.generate") }}
        </n-button>
        <n-button v-if="expReady" type="success" @click="doDownload">
          {{ t("system_transfer.download") }}
        </n-button>
        <n-spin v-if="expBusy" :size="16" />
      </n-space>
    </n-card>

    <!-- ═══════════ 匯入 ═══════════ -->
    <n-card>
      <template #header>
        <n-space align="center" :wrap-item="false">
          <n-icon :size="18"><ImportIcon /></n-icon>
          <span>{{ t("system_transfer.import_title") }}</span>
        </n-space>
      </template>

      <div class="st-group">
        <div class="st-label">{{ t("system_transfer.select_file") }}</div>
        <input type="file" accept=".json,application/json" @change="onFileChange" />
      </div>

      <div class="st-group">
        <div class="st-label">{{ t("system_transfer.passphrase") }}</div>
        <n-input v-model:value="impPass" type="password" show-password-on="click"
                 style="max-width: 420px" :placeholder="t('system_transfer.passphrase_ph')" />
      </div>

      <n-space align="center" style="margin-bottom: 12px">
        <n-button type="primary" :disabled="!impFile || !impPass" :loading="impBusy" @click="doAnalyze">
          {{ t("system_transfer.analyze") }}
        </n-button>
      </n-space>

      <template v-if="analyzed">
        <n-divider style="margin: 8px 0" />
        <n-alert v-for="(w, i) in analyzed.warnings" :key="i" type="warning"
                 :bordered="false" style="margin-bottom: 8px">{{ w }}</n-alert>

        <div class="st-meta">
          <span>{{ t("system_transfer.source_version") }}:
            <n-code :code="analyzed.metadata.app_version ?? '—'" inline /></span>
          <span>schema: <n-code :code="analyzed.metadata.schema_version ?? '—'" inline /></span>
          <span>{{ t("system_transfer.source_scope") }}: {{ (analyzed.metadata.scope || []).join(", ") }}</span>
        </div>

        <div class="st-group">
          <div class="st-label">{{ t("system_transfer.mode_label") }}</div>
          <n-radio-group v-model:value="impMode">
            <n-space>
              <n-radio value="merge">{{ t("system_transfer.mode_merge") }}</n-radio>
              <n-radio value="replace">{{ t("system_transfer.mode_replace") }}</n-radio>
            </n-space>
          </n-radio-group>
          <div class="st-hint">
            {{ impMode === "replace" ? t("system_transfer.mode_replace_hint") : t("system_transfer.mode_merge_hint") }}
          </div>
        </div>

        <n-data-table v-if="countRows.length" size="small" :columns="reportColumns"
                      :data="countRows" :max-height="320" style="margin: 8px 0" />

        <n-space align="center">
          <n-button :loading="impBusy" @click="doApply(true)">
            {{ t("system_transfer.dry_run") }}
          </n-button>
          <n-popconfirm @positive-click="doApply(false)">
            <template #trigger>
              <n-button type="error" :loading="impBusy && !!impTaskId">
                {{ t("system_transfer.apply") }}
              </n-button>
            </template>
            {{ impMode === "replace" ? t("system_transfer.confirm_replace") : t("system_transfer.confirm_merge") }}
          </n-popconfirm>
          <span v-if="impTaskId && impBusy" style="opacity: 0.7">
            {{ t("system_transfer.importing", { status: impTaskStatus ?? "queued" }) }}
          </span>
        </n-space>

        <n-alert v-if="report && !report.dry_run" type="success" :bordered="false" style="margin-top: 12px">
          {{ t("system_transfer.import_done") }}
        </n-alert>
      </template>
    </n-card>
    </div>
  </div>
</template>

<style scoped>
.st-wrap { display: flex; flex-direction: column; gap: 16px; }
/* 匯出 / 匯入 並排；寬螢幕兩欄用滿版面，窄螢幕自動堆疊 */
.st-cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(460px, 1fr)); gap: 16px; align-items: start; }
.st-group { margin-bottom: 18px; }
.st-label { font-weight: 600; margin-bottom: 8px; }
.st-hint { font-size: 12px; opacity: 0.65; margin-top: 6px; }
.st-scope-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 10px 16px; }
.st-meta { display: flex; flex-wrap: wrap; gap: 16px; font-size: 13px; margin-bottom: 14px; opacity: 0.85; }
</style>
