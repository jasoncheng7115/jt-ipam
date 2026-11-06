<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NAlert, NButton, NCard, NCheckbox, NCheckboxGroup, NCode, NDataTable, NDivider,
  NIcon, NInput, NPopconfirm, NRadio, NRadioGroup, NSpace, NSpin, useMessage,
} from "naive-ui";
import { AdminIcon, DownloadIcon, ExportIcon, ImportIcon, RefreshIcon, SearchIcon } from "@/icons";
import { apiErrMsg } from "@/api/client";
import { srvText } from "@/utils/wsError";
import { getTask } from "@/api/tasks";
import {
  analyzeImport, applyImport, downloadExport, getTransferSchema, startExport,
  type AnalyzeResult, type ImportReport, type TransferSchema,
} from "@/api/systemTransfer";
import { saveBlob } from "@/utils/saveFile";

const { t } = useI18n();
const msg = useMessage();

// ─────────────────── schema / scope ───────────────────
const schema = ref<TransferSchema | null>(null);
const scope = ref<string[]>([]);
// 剛進頁面時各類筆數還在統計（大站要好幾秒），按鈕反灰卻沒說為什麼，使用者以為壞了（2026-10-15）
const counting = ref(true);
const countError = ref("");

async function loadSchema() {
  counting.value = true;
  countError.value = "";
  try {
    schema.value = await getTransferSchema();
    scope.value = [...schema.value.default_scope];
  } catch (e) {
    countError.value = apiErrMsg(e);
  } finally {
    counting.value = false;
  }
}
onMounted(loadSchema);

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

/** 「產生匯出檔」不能按的原因（按鈕旁邊直接講，見 feedback_busy_buttons） */
const exportBlocker = computed(() => {
  if (counting.value) return t("system_transfer.why_counting");
  if (countError.value) return t("system_transfer.why_count_failed");
  if (!scope.value.length) return t("system_transfer.why_no_scope");
  if (expPass.value.length < 8) return t("system_transfer.why_pass_short");
  if (expPass.value !== expPass2.value) return t("system_transfer.why_pass_mismatch");
  return "";
});
const canExport = computed(() => !exportBlocker.value);

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
    // 伺服器端每次下載都記稽核（system_export_download）
    saveBlob(fname, blob, blob.type, { source: "system-export", audited: true });
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

/** 匯入時整列寫不進去的筆數。這個不能只當成表格裡的一欄 —— 客戶就是因為畫面
 *  一律顯示綠色「匯入完成」，事後才發現裝置少了一半。 */
const failedRows = computed(() => {
  const r = report.value;
  if (!r) return 0;
  const t = Object.values(r.tables ?? {}).reduce((a, v) => a + (v.errored ?? 0), 0);
  return t + (r.central_secrets?.errored ?? 0) + (r.deferred_refs?.errors?.length ?? 0);
});
const failureReasons = computed(() => {
  const r = report.value;
  if (!r) return [] as string[];
  const out: string[] = [];
  for (const v of Object.values(r.tables ?? {})) out.push(...(v.errors ?? []));
  out.push(...(r.deferred_refs?.errors ?? []));
  return out.slice(0, 5);
});
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
        <!-- 一項一列：名稱與筆數同一行（筆數靠右對齊），內容說明在下一行。以前三欄格子裡
             名稱一長就把筆數擠到下一行，看起來亂（使用者回報） -->
        <div v-if="counting" class="st-counting" data-testid="st-counting">
          <n-spin :size="16" /><span>{{ t("system_transfer.counting") }}</span>
        </div>
        <n-alert v-else-if="countError" type="error" :bordered="false" data-testid="st-count-error">
          <n-space align="center" :wrap-item="false">
            <span>{{ t("system_transfer.count_failed", { reason: countError }) }}</span>
            <n-button size="small" @click="loadSchema">
              <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("system_transfer.retry") }}
            </n-button>
          </n-space>
        </n-alert>
        <n-checkbox-group v-model:value="scope">
          <div class="st-scope-list">
            <n-checkbox v-for="cat in schema?.scopes ?? []" :key="cat" :value="cat" class="st-scope-row"
                        :data-testid="`st-scope-${cat}`">
              <span class="st-scope-line">
                <span class="st-scope-name">{{ t(`system_transfer.scope.${cat}`) }}</span>
                <span class="st-scope-count">{{ scopeCount(cat).toLocaleString() }}</span>
              </span>
              <span class="st-scope-desc">{{ t(`system_transfer.scope_desc.${cat}`) }}</span>
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
          <template #icon><n-icon><DownloadIcon /></n-icon></template>
          {{ t("system_transfer.download") }}
        </n-button>
        <n-spin v-if="expBusy" :size="16" />
        <span v-if="exportBlocker && !expBusy" class="st-why" data-testid="st-export-why">{{ exportBlocker }}</span>
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
          <template #icon><n-icon><SearchIcon /></n-icon></template>
          {{ t("system_transfer.analyze") }}
        </n-button>
        <span v-if="!impBusy && (!impFile || !impPass)" class="st-why" data-testid="st-import-why">
          {{ !impFile ? t("system_transfer.why_no_file") : t("system_transfer.why_no_pass") }}
        </span>
      </n-space>

      <template v-if="analyzed">
        <n-divider style="margin: 8px 0" />
        <n-alert v-for="(w, i) in analyzed.warnings" :key="i" type="warning"
                 :bordered="false" style="margin-bottom: 8px">{{ srvText(w) }}</n-alert>

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

        <n-alert v-if="report && !report.dry_run && !failedRows" type="success"
                 :bordered="false" style="margin-top: 12px">
          {{ t("system_transfer.import_done") }}
        </n-alert>
        <!-- 有寫不進去的列就不可以說「完成」：那些列是整筆不見，不是少一個欄位 -->
        <n-alert v-else-if="report && !report.dry_run" type="warning"
                 :bordered="false" style="margin-top: 12px"
                 :title="t('system_transfer.import_partial', { n: failedRows })">
          <div>{{ t("system_transfer.import_partial_hint") }}</div>
          <ul v-if="failureReasons.length" class="fail-list">
            <li v-for="(e, i) in failureReasons" :key="i">{{ e }}</li>
          </ul>
        </n-alert>
      </template>
    </n-card>
    </div>
  </div>
</template>

<style scoped>
.st-counting { display: flex; align-items: center; gap: 8px; font-size: 13px; opacity: .8; margin: 4px 0 10px; }
.st-why { font-size: 12.5px; opacity: .7; }
.fail-list { margin: 6px 0 0; padding-left: 18px; font-size: 12px; opacity: 0.85; word-break: break-all; }
.fail-list li { margin-bottom: 2px; }
.st-wrap { display: flex; flex-direction: column; gap: 16px; }
/* 匯出 / 匯入 並排；寬螢幕兩欄用滿版面，窄螢幕自動堆疊 */
.st-cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(460px, 100%), 1fr)); gap: 16px; align-items: start; }
.st-group { margin-bottom: 18px; }
.st-label { font-weight: 600; margin-bottom: 8px; }
.st-hint { font-size: 12px; opacity: 0.65; margin-top: 6px; }
.st-scope-list { display: flex; flex-direction: column; border: 1px solid var(--n-border-color, rgba(128,128,128,.22)); border-radius: 8px; }
.st-scope-row { display: flex; width: 100%; box-sizing: border-box; padding: 9px 12px; align-items: flex-start; margin-right: 0; }
.st-scope-row + .st-scope-row { border-top: 1px solid var(--n-border-color, rgba(128,128,128,.16)); }
.st-scope-row :deep(.n-checkbox__label) { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.st-scope-line { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.st-scope-name { font-weight: 500; }
.st-scope-count { font-variant-numeric: tabular-nums; font-size: 12.5px; opacity: .7; white-space: nowrap; }
.st-scope-desc { font-size: 12px; opacity: .6; line-height: 1.5; }
.st-meta { display: flex; flex-wrap: wrap; gap: 16px; font-size: 13px; margin-bottom: 14px; opacity: 0.85; }
</style>
