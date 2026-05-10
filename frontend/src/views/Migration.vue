<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NSpace, NButton, NForm, NFormItem, NInput, NSelect, NSwitch,
  NAlert, NCode, NDataTable, NTag,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { migrationStatus, migrationSync, type MappingStat } from "@/api/phase3";

const { t } = useI18n();
const msg = useMessage();
const stats = ref<MappingStat[]>([]);
const loading = ref(false);
const running = ref(false);
const result = ref<string | null>(null);
const form = ref({
  mysql_url: "mysql://root:secret@phpipam-mysql:3306/phpipam",
  on_conflict: "skip" as "skip" | "overwrite",
  dry_run: true,
});

const cols: DataTableColumns<MappingStat> = [
  { title: "object_type", key: "object_type",
    render: (r) => r.object_type },
  { title: "count", key: "count" },
];

async function refresh() {
  loading.value = true;
  try { stats.value = await migrationStatus(); }
  catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}
async function run() {
  running.value = true;
  result.value = null;
  try {
    const r = await migrationSync({
      mysql_url: form.value.mysql_url,
      on_conflict: form.value.on_conflict,
      dry_run: form.value.dry_run,
    });
    result.value = JSON.stringify(r, null, 2);
    await refresh();
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.server"));
  } finally { running.value = false; }
}

onMounted(() => { void refresh(); });
</script>

<template>
  <n-card :title="t('migration.title')">
    <n-alert type="info" style="margin-bottom: 16px">
      {{ t("migration.help") }}
    </n-alert>

    <n-form>
      <n-form-item :label="t('migration.mysql_url')">
        <n-input v-model:value="form.mysql_url" type="password" show-password-on="click"
                 placeholder="mysql://user:pass@host:3306/phpipam" />
      </n-form-item>
      <n-form-item :label="t('migration.on_conflict')">
        <n-select v-model:value="form.on_conflict"
                  :options="[
                    {label: t('migration.skip'), value: 'skip'},
                    {label: t('migration.overwrite'), value: 'overwrite'},
                  ]" />
      </n-form-item>
      <n-form-item :label="t('migration.dry_run')">
        <n-switch v-model:value="form.dry_run" />
      </n-form-item>
    </n-form>
    <n-space style="margin-top: 12px">
      <n-button type="primary" :loading="running" @click="run">
        {{ form.dry_run ? t("migration.dry_run_btn") : t("migration.commit_btn") }}
      </n-button>
      <n-button @click="refresh" :loading="loading">{{ t("common.refresh") }}</n-button>
    </n-space>

    <h3 style="margin-top: 24px">{{ t("migration.existing_mappings") }}</h3>
    <n-data-table :columns="cols" :data="stats" :loading="loading" :bordered="false" />

    <template v-if="result">
      <h3 style="margin-top: 24px">{{ t("migration.last_run_result") }}</h3>
      <n-code :code="result" language="json" />
    </template>
  </n-card>
</template>
