<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { NCard, NSpace, NSelect, NButton, NIcon, NInputNumber, useMessage } from "naive-ui";
import { apiClient } from "@/api/client";
import { RefreshIcon, AdminIcon, ExportIcon } from "@/icons";

const { t } = useI18n();
const msg = useMessage();

const services = ref<string[]>([]);
const service = ref<string>("backend");
const lines = ref<number>(300);
const text = ref<string>("");
const loading = ref(false);

const serviceOpts = () => services.value.map((s) => ({ label: s, value: s }));

async function loadServices() {
  try {
    const { data } = await apiClient.get<{ services: string[] }>("/api/v1/system/logs/services");
    services.value = data.services;
    if (data.services.length && !data.services.includes(service.value)) service.value = data.services[0];
  } catch { /* ignore */ }
}

async function load() {
  loading.value = true;
  try {
    const { data } = await apiClient.get<{ text: string }>("/api/v1/system/logs", {
      params: { service: service.value, lines: lines.value },
    });
    text.value = data.text || t("system_logs.empty");
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.network"));
  } finally {
    loading.value = false;
  }
}

function downloadLog() {
  const blob = new Blob([text.value], { type: "text/plain;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${service.value}.log`;
  a.click();
  URL.revokeObjectURL(a.href);
}

onMounted(async () => { await loadServices(); await load(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><AdminIcon /></n-icon>
        <span>{{ t("system_logs.title") }}</span>
      </n-space>
    </template>
    <n-space align="center" style="margin-bottom: 12px" :wrap-item="false">
      <n-select v-model:value="service" :options="serviceOpts()" style="width: 200px" @update:value="load" />
      <n-input-number v-model:value="lines" :min="10" :max="5000" :step="100" style="width: 130px"
                      @update:value="load" />
      <span style="font-size:12px;opacity:.65">{{ t("system_logs.lines") }}</span>
      <n-button type="primary" :loading="loading" @click="load">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
      <n-button :disabled="!text" @click="downloadLog">
        <template #icon><n-icon><ExportIcon /></n-icon></template>
        {{ t("system_logs.download") }}
      </n-button>
    </n-space>
    <pre class="logbox">{{ text }}</pre>
  </n-card>
</template>

<style scoped>
.logbox {
  margin: 0; padding: 14px 16px; border-radius: 10px;
  background: #0b1020; color: #d6e2f0;
  font-family: "SFMono-Regular", Menlo, Consolas, monospace;
  font-size: 12.5px; line-height: 1.6;
  white-space: pre-wrap; word-break: break-all;
  max-height: 70vh; overflow: auto;
}
</style>
