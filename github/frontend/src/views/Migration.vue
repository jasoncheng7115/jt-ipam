<script setup lang="ts">
import { computed, h, onMounted, onUnmounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NSpace, NIcon, NButton, NForm, NFormItem, NInput, NInputNumber, NSelect,
  NSwitch, NAlert, NCode, NDataTable, NCollapse, NCollapseItem, NModal, NTag,
  NRadioGroup, NRadioButton, NSteps, NStep, NGrid, NGi,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { migrationStatus, getMigrationConfig, saveMigrationConfig, type MappingStat } from "@/api/phase3";
import { apiClient } from "@/api/client";
import { getTask } from "@/api/tasks";
import {
  MigrationIcon, RefreshIcon, EyeIcon, SaveIcon, WarnIcon,
  CancelIcon, OkIcon,
} from "@/icons";
import { Lock as LockIcon } from "@iconoir/vue";

const { t } = useI18n();
const msg = useMessage();
const stats = ref<MappingStat[]>([]);
const loading = ref(false);
const running = ref(false);
const result = ref<string | null>(null);
// 背景作業 polling 狀態
const taskWaiting = ref(false);
const pollTaskId = ref<string | null>(null);
const pollTaskStatus = ref<string | null>(null);
const pollTaskProgress = ref<number>(0);
let pollTimer: ReturnType<typeof setInterval> | null = null;

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

function startPollingTask(taskId: string) {
  stopPolling();
  pollTimer = setInterval(async () => {
    try {
      const tk = await getTask(taskId);
      pollTaskStatus.value = tk.status;
      pollTaskProgress.value = tk.progress ?? 0;
      if (tk.status === "succeeded" || tk.status === "failed" || tk.status === "cancelled") {
        stopPolling();
        taskWaiting.value = false;
        // summary 是 MigrationReport JSON；直接塞進 result(同 dry-run 格式)
        if (tk.summary) {
          result.value = JSON.stringify(tk.summary, null, 2);
        } else if (tk.error) {
          result.value = JSON.stringify({ error: tk.error }, null, 2);
        }
        await refresh();
      }
    } catch {
      // 暫時失敗就下次再試；不停 polling
    }
  }, 2000);
}

onUnmounted(() => { stopPolling(); });

type TableRow = {
  name: string;
  inserted: number; updated: number; skipped: number; errored: number;
  errors: string[];
};
type MigrationResult = {
  started_at?: string;
  finished_at?: string | null;
  duration_seconds?: number | null;
  dry_run?: boolean;
  on_conflict?: string;
  tables?: Record<string, Omit<TableRow, "name">>;
  error?: string | null;
};
const resultObj = computed<MigrationResult | null>(() => {
  if (!result.value) return null;
  try { return JSON.parse(result.value); } catch { return null; }
});
const tableRows = computed<TableRow[]>(() => {
  const t = resultObj.value?.tables ?? {};
  return Object.entries(t).map(([name, v]) => ({ name, ...v }));
});
const totalErrored = computed(() =>
  tableRows.value.reduce((s, r) => s + (r.errored || 0), 0),
);
const totalInserted = computed(() =>
  tableRows.value.reduce((s, r) => s + (r.inserted || 0), 0),
);
const totalUpdated = computed(() =>
  tableRows.value.reduce((s, r) => s + (r.updated || 0), 0),
);
const totalSkipped = computed(() =>
  tableRows.value.reduce((s, r) => s + (r.skipped || 0), 0),
);
const allErrorMessages = computed<{ table: string; msg: string }[]>(() => {
  const out: { table: string; msg: string }[] = [];
  for (const row of tableRows.value) {
    for (const m of row.errors ?? []) out.push({ table: row.name, msg: m });
  }
  return out;
});

const form = ref<{
  // MySQL 連線模式：socket 走 Unix socket(推薦，免帳密)；tcp 走 127.0.0.1:3306
  mysql_via: "socket" | "tcp";
  mysql_socket_path: string;
  // MySQL 端 (tcp 模式才會用到 host/port；socket 模式無視)
  host: string; port: number; username: string; password: string; database: string;
  // SSH tunnel
  use_ssh: boolean;
  ssh_host: string; ssh_port: number; ssh_username: string;
  ssh_private_key: string;
  ssh_known_host: string;
  // 同步行為
  on_conflict: "skip" | "overwrite"; dry_run: boolean;
}>({
  mysql_via: "socket",     // 預設 socket：免 MySQL 帳密、auth_socket 認 SSH user
  mysql_socket_path: "/run/mysqld/mysqld.sock",
  host: "127.0.0.1",
  port: 3306,
  username: "",
  password: "",
  database: "phpipam",
  use_ssh: true,           // 預設 ON — phpIPAM MySQL 通常只 listen 127.0.0.1
  ssh_host: "",
  ssh_port: 22,
  ssh_username: "",
  ssh_private_key: "",
  ssh_known_host: "",
  on_conflict: "skip",
  dry_run: true,
});

// 連線設定改存伺服器端 (跨瀏覽器、私鑰加密)，見 loadServerConfig / saveServerConfig。
// 舊的 localStorage 記憶已移除；順手清掉殘留。
try { localStorage.removeItem("jt-ipam:migration:form"); } catch { /* ignore */ }

// TOFU 確認 modal
const tofuShow = ref(false);
const tofuFingerprint = ref("");
const tofuKnownHost = ref("");
const tofuFetching = ref(false);

const cols: DataTableColumns<MappingStat> = [
  { title: t("cols.object_type"), key: "object_type" },
  { title: t("cols.count"),       key: "count" },
];

async function refresh() {
  loading.value = true;
  try { stats.value = await migrationStatus(); }
  catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}

function buildPayload() {
  const p: Record<string, unknown> = {
    host: form.value.host,
    port: form.value.port,
    username: form.value.username || null,
    password: form.value.password || null,
    database: form.value.database,
    mysql_via: form.value.mysql_via,
    mysql_socket_path: form.value.mysql_socket_path,
    on_conflict: form.value.on_conflict,
    dry_run: form.value.dry_run,
  };
  if (form.value.use_ssh) {
    p.ssh_host = form.value.ssh_host;
    p.ssh_port = form.value.ssh_port;
    p.ssh_username = form.value.ssh_username;
    p.ssh_private_key = form.value.ssh_private_key;
    p.ssh_known_host = form.value.ssh_known_host || null;
  }
  return p;
}

// 第一步：探 SSH host fingerprint(TOFU)
async function fetchFingerprint() {
  if (!form.value.ssh_host) {
    msg.error(t("migration.error_ssh_host_required"));
    return;
  }
  tofuFetching.value = true;
  try {
    const { data } = await apiClient.post<{
      key_type: string; key_b64: string; known_host: string; fingerprint: string;
    }>("/api/v1/migration/phpipam/ssh-fingerprint", {
      ssh_host: form.value.ssh_host,
      ssh_port: form.value.ssh_port,
    }, { timeout: 30_000 });
    tofuFingerprint.value = data.fingerprint;
    tofuKnownHost.value = data.known_host;
    tofuShow.value = true;
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.server"));
  } finally { tofuFetching.value = false; }
}

function tofuAccept() {
  form.value.ssh_known_host = tofuKnownHost.value;
  tofuShow.value = false;
  msg.success(t("migration.tofu_accepted"));
}

// 各步驟完成度 (用來給 NSteps 顯示 / 阻擋按鈕)
const step1Ok = computed(() => !!form.value.mysql_via);
const step2Ok = computed(() =>
  !!form.value.ssh_host && !!form.value.ssh_username &&
  !!form.value.ssh_private_key.trim() && !!form.value.ssh_known_host.trim()
);
const step3Ok = computed(() => {
  if (form.value.mysql_via === "socket") return !!form.value.mysql_socket_path && !!form.value.database;
  return !!form.value.host && !!form.value.username && !!form.value.database;
});
const allOk = computed(() => step1Ok.value && step2Ok.value && step3Ok.value);
const currentStep = computed(() => {
  if (!step1Ok.value) return 0;
  if (!step2Ok.value) return 1;
  if (!step3Ok.value) return 2;
  return 3;
});

// 跑 sync
async function run() {
  // socket 模式：MySQL user 可空 (auth_socket plugin 認 SSH OS user)
  const userOptional = form.value.mysql_via === "socket";
  if (!userOptional && !form.value.username) {
    msg.error(t("migration.error_user_required"));
    return;
  }
  if (!form.value.ssh_host || !form.value.ssh_username) {
    msg.error(t("migration.error_ssh_required"));
    return;
  }
  if (!form.value.ssh_private_key.trim()) {
    msg.error(t("migration.error_private_key_required"));
    return;
  }
  if (!form.value.ssh_known_host.trim()) {
    msg.error(t("migration.error_known_host_required"));
    return;
  }
  running.value = true;
  result.value = null;
  try {
    const { data } = await apiClient.post("/api/v1/migration/phpipam/sync", buildPayload(),
      { timeout: 600_000 });
    // 非 dry-run：後端回 task_id 而非完整 report → 開 polling
    if (data?.task_id && data?.dry_run === false) {
      msg.info(t("migration.queued_waiting"));
      taskWaiting.value = true;
      result.value = null;
      pollTaskId.value = data.task_id;
      startPollingTask(data.task_id);
    } else {
      result.value = JSON.stringify(data, null, 2);
    }
    await refresh();
  } catch (e: any) {
    const detail = e?.response?.data?.detail;
    // 特殊情況：host key 不對
    if (typeof detail === "object" && detail?.error === "host_key_mismatch") {
      msg.error(t("migration.host_key_changed", { expected: detail.expected, actual: detail.actual }));
      // 提示重新確認
      form.value.ssh_known_host = "";
    } else {
      msg.error(typeof detail === "string" ? detail : t("errors.server"));
    }
  } finally { running.value = false; }
}

// ── 伺服器端設定 (跨瀏覽器共用；私鑰加密存) ──
const serverHasKey = ref(false);
const savingCfg = ref(false);

async function loadServerConfig() {
  try {
    const c = await getMigrationConfig();
    form.value.mysql_via = c.mysql_via;
    form.value.mysql_socket_path = c.mysql_socket_path;
    form.value.host = c.host;
    form.value.port = c.port;
    form.value.username = c.username ?? "";
    form.value.database = c.database;
    form.value.ssh_host = c.ssh_host ?? "";
    form.value.ssh_port = c.ssh_port;
    form.value.ssh_username = c.ssh_username ?? "";
    form.value.ssh_known_host = c.ssh_known_host ?? "";
    form.value.use_ssh = !!c.ssh_host || form.value.use_ssh;
    serverHasKey.value = c.has_private_key;
  } catch { /* 沒存過就維持預設 */ }
}

async function saveServerConfig() {
  savingCfg.value = true;
  try {
    await saveMigrationConfig({
      mysql_via: form.value.mysql_via,
      mysql_socket_path: form.value.mysql_socket_path,
      host: form.value.host, port: form.value.port,
      username: form.value.username || null, database: form.value.database,
      ssh_host: form.value.ssh_host || null, ssh_port: form.value.ssh_port,
      ssh_username: form.value.ssh_username || null,
      ssh_known_host: form.value.ssh_known_host || null,
      // 只有實際輸入私鑰才更新 (否則保留已存的)
      ...(form.value.ssh_private_key.trim() ? { ssh_private_key: form.value.ssh_private_key } : {}),
    });
    serverHasKey.value = serverHasKey.value || !!form.value.ssh_private_key.trim();
    msg.success(t("migration.cfg_saved"));
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.server"));
  } finally { savingCfg.value = false; }
}

onMounted(() => { void refresh(); void loadServerConfig(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><MigrationIcon /></n-icon>
        <span>{{ t("migration.title") }}</span>
      </n-space>
    </template>

    <!-- 簡短說明：合併原本兩個 alert -->
    <n-alert type="info" :show-icon="false" style="margin-bottom: 16px">
      <n-space align="center" :wrap-item="false">
        <n-icon :size="18"><LockIcon /></n-icon>
        <span>{{ t("migration.intro_combined") }}</span>
      </n-space>
    </n-alert>

    <!-- 步驟指示器 -->
    <n-steps :current="currentStep + 1" status="process" size="small" style="margin-bottom: 20px">
      <n-step :title="t('migration.step1_title')" :description="t('migration.step1_desc')" />
      <n-step :title="t('migration.step2_title')" :description="t('migration.step2_desc')" />
      <n-step :title="t('migration.step3_title')" :description="t('migration.step3_desc')" />
      <n-step :title="t('migration.step4_title')" :description="t('migration.step4_desc')" />
    </n-steps>

    <n-form label-placement="top">

      <!-- ─────── 步驟 1：連線方式 ─────── -->
      <div class="step-card">
        <div class="step-title">
          <span class="step-num">1</span>
          <span>{{ t("migration.step1_title") }}</span>
        </div>
        <n-radio-group v-model:value="form.mysql_via" size="large">
          <n-radio-button value="socket">{{ t("migration.via_socket") }}</n-radio-button>
          <n-radio-button value="tcp">{{ t("migration.via_tcp") }}</n-radio-button>
        </n-radio-group>
        <p class="step-hint">
          <span v-if="form.mysql_via === 'socket'">{{ t("migration.via_socket_hint") }}</span>
          <span v-else>{{ t("migration.via_tcp_hint") }}</span>
        </p>
      </div>

      <!-- ─────── 步驟 2：phpIPAM 主機 (SSH) ─────── -->
      <div class="step-card">
        <div class="step-title">
          <span class="step-num">2</span>
          <span>{{ t("migration.step2_title") }}</span>
        </div>

        <n-grid :cols="12" :x-gap="12" :y-gap="8">
          <n-gi :span="6">
            <n-form-item :label="t('migration.ssh_host')" :show-feedback="false">
              <n-input v-model:value="form.ssh_host" :placeholder="t('migration.ssh_host_ph')" />
            </n-form-item>
          </n-gi>
          <n-gi :span="2">
            <n-form-item :label="t('migration.ssh_port')" :show-feedback="false">
              <n-input-number v-model:value="form.ssh_port" :min="1" :max="65535" style="width: 100%" />
            </n-form-item>
          </n-gi>
          <n-gi :span="4">
            <n-form-item :label="t('migration.ssh_user')" :show-feedback="false">
              <n-input v-model:value="form.ssh_username" placeholder="root" />
            </n-form-item>
          </n-gi>
        </n-grid>

        <n-collapse style="margin: 8px 0 12px 0">
          <n-collapse-item :title="t('migration.ssh_keypair_title')" name="keypair">
            <ol style="margin: 4px 0; padding-left: 22px; line-height: 1.7">
              <li v-html="t('migration.ssh_step1_html')"></li>
              <li v-html="t('migration.ssh_step2_html')"></li>
              <li v-html="t('migration.ssh_step3_html')"></li>
              <li v-html="t('migration.ssh_step4_html')"></li>
            </ol>
          </n-collapse-item>
        </n-collapse>

        <n-form-item :label="t('migration.ssh_private_key')" :show-feedback="false">
          <n-input v-model:value="form.ssh_private_key" type="textarea" :rows="5"
                   :placeholder="t('migration.ssh_private_key_placeholder')"
                   show-password-on="click" />
        </n-form-item>

        <n-form-item style="margin-top: 8px" :show-feedback="false">
          <template #label>
            <n-space align="center" :wrap-item="false" :size="6">
              <span>{{ t("migration.ssh_known_host") }}</span>
              <n-tag v-if="form.ssh_known_host" type="success" size="small" :bordered="false">
                {{ t("migration.known_host_set_short") }}
              </n-tag>
            </n-space>
          </template>
          <n-input v-model:value="form.ssh_known_host" type="textarea" :rows="2" readonly
                   :placeholder="t('migration.ssh_known_host_placeholder')" />
        </n-form-item>
        <n-button :loading="tofuFetching" @click="fetchFingerprint" type="info" size="small">
          <template #icon><n-icon><LockIcon /></n-icon></template>
          {{ t("migration.fetch_fingerprint") }}
        </n-button>
      </div>

      <!-- ─────── 步驟 3：phpIPAM 資料庫 ─────── -->
      <div class="step-card">
        <div class="step-title">
          <span class="step-num">3</span>
          <span>{{ t("migration.step3_title") }}</span>
        </div>

        <template v-if="form.mysql_via === 'socket'">
          <n-grid :cols="12" :x-gap="12" :y-gap="8">
            <n-gi :span="8">
              <n-form-item :label="t('migration.socket_path')" :show-feedback="false">
                <n-input v-model:value="form.mysql_socket_path"
                         placeholder="/run/mysqld/mysqld.sock" />
              </n-form-item>
            </n-gi>
            <n-gi :span="4">
              <n-form-item :label="t('migration.database')" :show-feedback="false">
                <n-input v-model:value="form.database" placeholder="phpipam" />
              </n-form-item>
            </n-gi>
            <n-gi :span="6">
              <n-form-item :label="t('migration.username_optional')" :show-feedback="false">
                <n-input v-model:value="form.username"
                         :placeholder="t('migration.username_socket_placeholder')" />
              </n-form-item>
            </n-gi>
          </n-grid>
          <p class="step-hint">{{ t("migration.socket_path_hint") }}</p>
        </template>

        <template v-else>
          <n-grid :cols="12" :x-gap="12" :y-gap="8">
            <n-gi :span="5">
              <n-form-item :label="t('migration.host')" :show-feedback="false">
                <n-input v-model:value="form.host" placeholder="127.0.0.1" />
              </n-form-item>
            </n-gi>
            <n-gi :span="2">
              <n-form-item :label="t('migration.port')" :show-feedback="false">
                <n-input-number v-model:value="form.port" :min="1" :max="65535" style="width: 100%" />
              </n-form-item>
            </n-gi>
            <n-gi :span="5">
              <n-form-item :label="t('migration.database')" :show-feedback="false">
                <n-input v-model:value="form.database" placeholder="phpipam" />
              </n-form-item>
            </n-gi>
            <n-gi :span="6">
              <n-form-item :label="t('migration.username')" :show-feedback="false">
                <n-input v-model:value="form.username" :placeholder="t('migration.db_user_ph')" />
              </n-form-item>
            </n-gi>
            <n-gi :span="6">
              <n-form-item :label="t('migration.password')" :show-feedback="false">
                <n-input v-model:value="form.password" type="password" show-password-on="click"
                         :placeholder="t('migration.db_password_ph')" />
              </n-form-item>
            </n-gi>
          </n-grid>
        </template>
      </div>

      <!-- ─────── 步驟 4：同步選項 ─────── -->
      <div class="step-card">
        <div class="step-title">
          <span class="step-num">4</span>
          <span>{{ t("migration.step4_title") }}</span>
        </div>
        <n-grid :cols="12" :x-gap="12" :y-gap="8">
          <n-gi :span="6">
            <n-form-item :label="t('migration.on_conflict')" :show-feedback="false">
              <n-select v-model:value="form.on_conflict"
                        :options="[
                          {label: t('migration.skip'), value: 'skip'},
                          {label: t('migration.overwrite'), value: 'overwrite'},
                        ]" />
            </n-form-item>
          </n-gi>
          <n-gi :span="3">
            <n-form-item :label="t('migration.dry_run')" :show-feedback="false">
              <n-switch v-model:value="form.dry_run" />
            </n-form-item>
          </n-gi>
        </n-grid>
      </div>

    </n-form>

    <p v-if="serverHasKey" class="step-hint" style="margin-top: 8px">{{ t("migration.key_stored") }}</p>
    <n-space style="margin-top: 12px">
      <n-button type="primary" size="large" :loading="running" :disabled="!allOk" @click="run">
        <template #icon>
          <n-icon><component :is="form.dry_run ? EyeIcon : SaveIcon" /></n-icon>
        </template>
        {{ form.dry_run ? t("migration.dry_run_btn") : t("migration.commit_btn") }}
      </n-button>
      <n-button size="large" :loading="savingCfg" @click="saveServerConfig">
        <template #icon><n-icon><SaveIcon /></n-icon></template>
        {{ t("migration.save_cfg") }}
      </n-button>
      <n-button size="large" @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
    </n-space>

    <h3 style="margin-top: 24px">{{ t("migration.existing_mappings") }}</h3>
    <n-data-table :columns="cols" :data="stats" :loading="loading" :bordered="false" />

    <n-alert v-if="taskWaiting" type="info" style="margin-top: 24px"
             :title="t('migration.task_running')">
      <template #icon><n-icon><RefreshIcon /></n-icon></template>
      {{ t("migration.task_running_body", { status: pollTaskStatus ?? "queued", progress: pollTaskProgress }) }}
      <span v-if="pollTaskId" style="margin-left: 8px; opacity: 0.7;">task_id: {{ pollTaskId.slice(0, 8) }}…</span>
    </n-alert>

    <template v-if="resultObj">
      <h3 style="margin-top: 24px">{{ t("migration.last_run_result") }}</h3>

      <n-alert v-if="resultObj.error" type="error" style="margin-bottom: 12px"
               :title="t('migration.result_error_title')">
        <template #icon><n-icon><WarnIcon /></n-icon></template>
        <pre style="white-space: pre-wrap; margin: 0">{{ resultObj.error }}</pre>
      </n-alert>

      <n-alert v-else-if="totalErrored > 0" type="error" style="margin-bottom: 12px"
               :title="t('migration.result_partial_failure_title')">
        <template #icon><n-icon><WarnIcon /></n-icon></template>
        {{ t('migration.result_partial_failure_body', { n: totalErrored }) }}
      </n-alert>

      <n-alert v-else type="success" style="margin-bottom: 12px"
               :title="t('migration.result_success_title')">
        <template #icon><n-icon><OkIcon /></n-icon></template>
        {{ t('migration.result_success_body', {
            inserted: totalInserted, updated: totalUpdated, skipped: totalSkipped,
            secs: (resultObj.duration_seconds ?? 0).toFixed(1),
        }) }}
        <span v-if="resultObj.dry_run" style="margin-left: 8px; font-weight: 600">
          ({{ t('migration.dry_run') }})
        </span>
      </n-alert>

      <n-data-table size="small" :bordered="false"
        :columns="[
          { title: t('migration.table'), key: 'name' },
          { title: t('migration.inserted'), key: 'inserted',
            render: (r) => h(NTag, { size: 'small', type: r.inserted > 0 ? 'success' : 'default', bordered: false }, () => String(r.inserted)) },
          { title: t('migration.updated'), key: 'updated',
            render: (r) => h(NTag, { size: 'small', type: r.updated > 0 ? 'info' : 'default', bordered: false }, () => String(r.updated)) },
          { title: t('migration.skipped'), key: 'skipped',
            render: (r) => h(NTag, { size: 'small', bordered: false }, () => String(r.skipped)) },
          { title: t('migration.errored'), key: 'errored',
            render: (r) => h(NTag, { size: 'small', type: r.errored > 0 ? 'error' : 'default', bordered: false }, () => String(r.errored)) },
        ]"
        :data="tableRows" />

      <n-collapse v-if="allErrorMessages.length" :default-expanded-names="['errors']"
                  style="margin-top: 12px">
        <n-collapse-item :name="'errors'"
                         :title="t('migration.errors_section') + ` (${allErrorMessages.length})`">
          <template #header-extra>
            <n-icon color="#d03050"><WarnIcon /></n-icon>
          </template>
          <ul style="margin: 0; padding-left: 18px; color: #d03050">
            <li v-for="(e, i) in allErrorMessages" :key="i" style="margin-bottom: 4px">
              <strong>{{ e.table }}</strong>: <code>{{ e.msg }}</code>
            </li>
          </ul>
        </n-collapse-item>
      </n-collapse>

      <n-collapse style="margin-top: 12px">
        <n-collapse-item :title="t('migration.raw_result')" name="raw">
          <n-code :code="result || ''" language="json" />
        </n-collapse-item>
      </n-collapse>
    </template>

    <!-- TOFU 確認 modal -->
    <n-modal v-model:show="tofuShow" preset="card" style="width: 580px">
      <template #header>
        <n-space align="center">
          <n-icon :size="20"><WarnIcon /></n-icon>
          <span>{{ t("migration.tofu_title") }}</span>
        </n-space>
      </template>
      <n-alert type="warning" style="margin-bottom: 12px">
        <template #icon><n-icon><WarnIcon /></n-icon></template>
        {{ t("migration.tofu_warn") }}
      </n-alert>
      <p>{{ t("migration.tofu_host") }}：<code>{{ form.ssh_host }}:{{ form.ssh_port }}</code></p>
      <p>{{ t("migration.tofu_fingerprint") }}：</p>
      <n-code :code="tofuFingerprint" language="plaintext" />
      <p style="margin-top: 12px; font-size: 13px; opacity: 0.8">
        {{ t("migration.tofu_compare") }}<br />
        <code>ssh-keyscan {{ form.ssh_host }} | ssh-keygen -lf -</code>
      </p>
      <n-space justify="end" style="margin-top: 16px">
        <n-button @click="tofuShow = false">
          <template #icon><n-icon><CancelIcon /></n-icon></template>
          {{ t("common.cancel") }}
        </n-button>
        <n-button type="primary" @click="tofuAccept">
          <template #icon><n-icon><OkIcon /></n-icon></template>
          {{ t("migration.tofu_trust") }}
        </n-button>
      </n-space>
    </n-modal>
  </n-card>
</template>

<style scoped>
.step-card {
  position: relative;
  border: 1px solid rgba(127, 127, 127, 0.25);
  border-left: 4px solid #18a058;
  border-radius: 8px;
  padding: 16px 20px 14px 20px;
  margin-bottom: 24px;
  background: rgba(127, 127, 127, 0.04);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
}
.step-card:last-child {
  margin-bottom: 0;
}
.step-title {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 16px;
  font-weight: 700;
  margin: -16px -20px 14px -20px;
  padding: 10px 20px;
  border-bottom: 1px solid rgba(127, 127, 127, 0.18);
  background: rgba(127, 127, 127, 0.06);
  border-top-right-radius: 8px;
}
.step-num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: #18a058;
  color: #fff;
  font-size: 14px;
  font-weight: 700;
  flex-shrink: 0;
}
.step-hint {
  margin: 10px 0 0 0;
  font-size: 12px;
  opacity: 0.7;
  line-height: 1.5;
}
</style>
