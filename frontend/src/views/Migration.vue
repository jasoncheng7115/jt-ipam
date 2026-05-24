<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NSpace, NButton, NForm, NFormItem, NInput, NInputNumber, NSelect,
  NSwitch, NAlert, NCode, NDataTable, NIcon,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { migrationStatus, type MappingStat } from "@/api/phase3";
import { apiClient } from "@/api/client";
import {
  MigrationIcon, RefreshIcon, EyeIcon, SaveIcon, InfoIcon,
} from "@/icons";

const { t } = useI18n();
const msg = useMessage();
const stats = ref<MappingStat[]>([]);
const loading = ref(false);
const running = ref(false);
const result = ref<string | null>(null);

const form = ref<{
  host: string; port: number; username: string; password: string; database: string;
  on_conflict: "skip" | "overwrite"; dry_run: boolean;
}>({
  host: "phpipam-mysql",
  port: 3306,
  username: "root",
  password: "",
  database: "phpipam",
  on_conflict: "skip",
  dry_run: true,
});

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

async function run() {
  if (!form.value.host) {
    msg.error(t("migration.error_host_required"));
    return;
  }
  running.value = true;
  result.value = null;
  try {
    const { data } = await apiClient.post("/api/v1/migration/phpipam/sync", {
      host: form.value.host,
      port: form.value.port,
      username: form.value.username,
      password: form.value.password,
      database: form.value.database,
      on_conflict: form.value.on_conflict,
      dry_run: form.value.dry_run,
    });
    result.value = JSON.stringify(data, null, 2);
    await refresh();
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.server"));
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

    <n-form label-placement="top">
      <n-space>
        <n-form-item :label="t('migration.host')" style="min-width: 280px">
          <n-input v-model:value="form.host" placeholder="phpipam.example.com" />
        </n-form-item>
        <n-form-item :label="t('migration.port')">
          <n-input-number v-model:value="form.port" :min="1" :max="65535" style="width: 110px" />
        </n-form-item>
      </n-space>
      <n-space>
        <n-form-item :label="t('migration.username')" style="min-width: 200px">
          <n-input v-model:value="form.username" placeholder="root" />
        </n-form-item>
        <n-form-item :label="t('migration.password')" style="min-width: 220px">
          <n-input v-model:value="form.password" type="password" show-password-on="click" />
        </n-form-item>
        <n-form-item :label="t('migration.database')" style="min-width: 180px">
          <n-input v-model:value="form.database" placeholder="phpipam" />
        </n-form-item>
      </n-space>
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
  </n-card>
</template>
