<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NSpace, NIcon, NButton, NForm, NFormItem, NInput, NInputNumber, NSelect,
  NSwitch, NAlert, NCode, NDataTable, NCollapse, NCollapseItem, NModal,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { migrationStatus, type MappingStat } from "@/api/phase3";
import { apiClient } from "@/api/client";
import {
  MigrationIcon, RefreshIcon, EyeIcon, SaveIcon, InfoIcon, WarnIcon,
  CancelIcon, OkIcon,
} from "@/icons";
import { Lock as LockIcon } from "@iconoir/vue";

const { t } = useI18n();
const msg = useMessage();
const stats = ref<MappingStat[]>([]);
const loading = ref(false);
const running = ref(false);
const result = ref<string | null>(null);

const form = ref<{
  // MySQL 端（從 SSH 主機看過去的位址；走 tunnel 時通常是 127.0.0.1）
  host: string; port: number; username: string; password: string; database: string;
  // SSH tunnel
  use_ssh: boolean;
  ssh_host: string; ssh_port: number; ssh_username: string;
  ssh_private_key: string;
  ssh_known_host: string;
  // 同步行為
  on_conflict: "skip" | "overwrite"; dry_run: boolean;
}>({
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

// TOFU 確認 modal
const tofuShow = ref(false);
const tofuFingerprint = ref("");
const tofuKnownHost = ref("");
const tofuFetching = ref(false);

const cols: DataTableColumns<MappingStat> = [
  { title: "object_type", key: "object_type" },
  { title: "count",       key: "count" },
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

// 第一步：探 SSH host fingerprint（TOFU）
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
    });
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

// 第二步：跑 sync
async function run() {
  if (!form.value.username) {
    msg.error(t("migration.error_user_required"));
    return;
  }
  if (form.value.use_ssh) {
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
  }
  running.value = true;
  result.value = null;
  try {
    const { data } = await apiClient.post("/api/v1/migration/phpipam/sync", buildPayload());
    result.value = JSON.stringify(data, null, 2);
    await refresh();
  } catch (e: any) {
    const detail = e?.response?.data?.detail;
    // 特殊情況：host key 不對
    if (typeof detail === "object" && detail?.error === "host_key_mismatch") {
      msg.error(`SSH host key 變了！expected=${detail.expected}, actual=${detail.actual}`);
      // 提示重新確認
      form.value.ssh_known_host = "";
    } else {
      msg.error(typeof detail === "string" ? detail : t("errors.server"));
    }
  } finally { running.value = false; }
}

onMounted(() => { void refresh(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><MigrationIcon /></n-icon>
        <span>{{ t("migration.title") }}</span>
      </n-space>
    </template>

    <n-alert type="info" style="margin-bottom: 16px">
      <template #icon><n-icon><InfoIcon /></n-icon></template>
      {{ t("migration.help") }}
    </n-alert>

    <n-alert v-if="form.use_ssh" type="warning" style="margin-bottom: 16px">
      <template #icon><n-icon><LockIcon /></n-icon></template>
      {{ t("migration.ssh_help") }}
    </n-alert>

    <n-form label-placement="top">
      <h3 style="margin: 0 0 8px 0">{{ t("migration.mysql_section") }}</h3>
      <n-space>
        <n-form-item :label="t('migration.host')" style="min-width: 240px">
          <n-input v-model:value="form.host"
                   :placeholder="form.use_ssh ? '127.0.0.1（從 SSH 主機看）' : 'phpipam.example.com'" />
        </n-form-item>
        <n-form-item :label="t('migration.port')">
          <n-input-number v-model:value="form.port" :min="1" :max="65535" style="width: 110px" />
        </n-form-item>
        <n-form-item :label="t('migration.database')" style="min-width: 180px">
          <n-input v-model:value="form.database" placeholder="phpipam" />
        </n-form-item>
      </n-space>
      <n-space>
        <n-form-item :label="t('migration.username')" style="min-width: 200px">
          <n-input v-model:value="form.username" placeholder="phpipam DB 帳號（如 root）" />
        </n-form-item>
        <n-form-item :label="t('migration.password')" style="min-width: 240px">
          <n-input v-model:value="form.password" type="password" show-password-on="click"
                   placeholder="phpipam DB 密碼" />
        </n-form-item>
      </n-space>

      <n-form-item :label="t('migration.use_ssh')" style="margin-top: 8px">
        <n-switch v-model:value="form.use_ssh" />
        <span style="margin-left: 8px; opacity: 0.7; font-size: 12px">
          {{ t("migration.use_ssh_hint") }}
        </span>
      </n-form-item>

      <template v-if="form.use_ssh">
        <h3 style="margin: 16px 0 8px 0">{{ t("migration.ssh_section") }}</h3>
        <n-space>
          <n-form-item :label="t('migration.ssh_host')" style="min-width: 280px">
            <n-input v-model:value="form.ssh_host" placeholder="phpipam-server.example.com 或 IP" />
          </n-form-item>
          <n-form-item :label="t('migration.ssh_port')">
            <n-input-number v-model:value="form.ssh_port" :min="1" :max="65535" style="width: 110px" />
          </n-form-item>
          <n-form-item :label="t('migration.ssh_user')" style="min-width: 200px">
            <n-input v-model:value="form.ssh_username" placeholder="root 或可以 sudo 的帳號" />
          </n-form-item>
        </n-space>
        <n-form-item :label="t('migration.ssh_private_key')">
          <n-input v-model:value="form.ssh_private_key" type="textarea" :rows="6"
                   :placeholder="t('migration.ssh_private_key_placeholder')"
                   show-password-on="click" />
        </n-form-item>
        <n-form-item :label="t('migration.ssh_known_host')">
          <n-input v-model:value="form.ssh_known_host" type="textarea" :rows="2" readonly
                   :placeholder="t('migration.ssh_known_host_placeholder')" />
        </n-form-item>
        <n-space>
          <n-button :loading="tofuFetching" @click="fetchFingerprint" type="info">
            <template #icon><n-icon><LockIcon /></n-icon></template>
            {{ t("migration.fetch_fingerprint") }}
          </n-button>
          <span v-if="form.ssh_known_host" style="line-height: 34px; color: #18a058">
            {{ t("migration.known_host_set") }}
          </span>
        </n-space>
      </template>

      <h3 style="margin: 16px 0 8px 0">{{ t("migration.options_section") }}</h3>
      <n-space>
        <n-form-item :label="t('migration.on_conflict')" style="min-width: 280px">
          <n-select v-model:value="form.on_conflict"
                    :options="[
                      {label: t('migration.skip'), value: 'skip'},
                      {label: t('migration.overwrite'), value: 'overwrite'},
                    ]" />
        </n-form-item>
        <n-form-item :label="t('migration.dry_run')">
          <n-switch v-model:value="form.dry_run" />
        </n-form-item>
      </n-space>
    </n-form>

    <n-space style="margin-top: 12px">
      <n-button type="primary" :loading="running" @click="run">
        <template #icon>
          <n-icon><component :is="form.dry_run ? EyeIcon : SaveIcon" /></n-icon>
        </template>
        {{ form.dry_run ? t("migration.dry_run_btn") : t("migration.commit_btn") }}
      </n-button>
      <n-button @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
    </n-space>

    <h3 style="margin-top: 24px">{{ t("migration.existing_mappings") }}</h3>
    <n-data-table :columns="cols" :data="stats" :loading="loading" :bordered="false" />

    <template v-if="result">
      <h3 style="margin-top: 24px">{{ t("migration.last_run_result") }}</h3>
      <n-code :code="result" language="json" />
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
