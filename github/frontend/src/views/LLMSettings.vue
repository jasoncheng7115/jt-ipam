<script setup lang="ts">
/**
 * LLM / AI 全域設定 (管理員)。
 *
 * 設定會覆寫環境變數，寫入 system_settings 表。所有 user 共用。
 */
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NSpace, NIcon, NAlert, NSwitch, NInput, NInputNumber, NSelect, NButton,
  useMessage,
} from "naive-ui";
import {
  getLLMConfig, patchLLMConfig, listOllamaModels,
  type LLMConfig, type LLMConfigPatch, type OllamaModel,
} from "@/api/system";
import { SettingsIcon, RefreshIcon } from "@/icons";

const { t } = useI18n();
const msg = useMessage();
const llm = ref<LLMConfig | null>(null);
const models = ref<OllamaModel[]>([]);
const modelsLoading = ref(false);
const modelsError = ref<string | null>(null);

const modelOptions = computed(() => {
  // 已 pull 的 model；若目前設定的不在清單裡也補上去避免變空
  const opts = models.value.map((m) => ({
    label: m.parameter_size ? `${m.name} (${m.parameter_size})` : m.name,
    value: m.name,
  }));
  for (const v of [llm.value?.chat_model, llm.value?.embedding_model]) {
    if (v && !opts.find((o) => o.value === v)) {
      opts.push({ label: t("llm_settings.model_not_found", { model: v }), value: v });
    }
  }
  return opts;
});

// 嵌入模型名稱慣例含 "embed"（qwen3-embedding / nomic-embed-text / mxbai-embed…）
const isEmbedModel = (name: string) => /embed/i.test(name);
// 嵌入模型下拉：只有嵌入模型可選，其餘反灰（目前已選的仍保留可見）
const embeddingModelOptions = computed(() =>
  modelOptions.value.map((o) => ({
    ...o,
    disabled: !isEmbedModel(o.value) && o.value !== llm.value?.embedding_model,
  })));
// 對話模型下拉：反過來把嵌入模型反灰
const chatModelOptions = computed(() =>
  modelOptions.value.map((o) => ({
    ...o,
    disabled: isEmbedModel(o.value) && o.value !== llm.value?.chat_model,
  })));

async function loadModels() {
  modelsLoading.value = true;
  modelsError.value = null;
  try {
    const res = await listOllamaModels();
    models.value = res.models;
    if (res.error) modelsError.value = res.error;
  } catch (e: any) {
    modelsError.value = e?.response?.data?.detail ?? String(e);
  } finally {
    modelsLoading.value = false;
  }
}

async function load() {
  try { llm.value = await getLLMConfig(); }
  catch { msg.error(t("errors.network")); }
  void loadModels();
}

// URL 改了也重新拉 model 清單 (換 Ollama 機器時可能不同)
watch(() => llm.value?.url, () => { if (llm.value?.url) void loadModels(); });

let debounce: ReturnType<typeof setTimeout> | null = null;
function patch(p: LLMConfigPatch) {
  if (!llm.value) return;
  llm.value = { ...llm.value, ...p } as LLMConfig;
  if (debounce) clearTimeout(debounce);
  debounce = setTimeout(async () => {
    try {
      llm.value = await patchLLMConfig(p);
      msg.success(t("common.saved"));
    } catch (e: any) {
      msg.error(e?.response?.data?.detail ?? t("errors.server"));
    }
  }, 600);
}

onMounted(load);
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><SettingsIcon /></n-icon>
        <span>{{ t("llm_settings.title") }}</span>
      </n-space>
    </template>
    <n-space v-if="llm" vertical :size="16" style="max-width: 640px">
      <n-alert type="info" size="small">
        <span v-html="t('llm_settings.admin_note', { strong: `<strong>${t('llm_settings.admin_global_settings')}</strong>` })" />
      </n-alert>
      <div>
        <label>{{ t("llm_settings.enable_ollama") }}</label>
        <n-switch :value="llm.enabled" @update:value="(v: boolean) => patch({ enabled: v })" />
      </div>
      <div>
        <label>Ollama URL</label>
        <n-input
          :value="llm.url"
          placeholder="http://127.0.0.1:11434"
          @update:value="(v: string) => patch({ url: v })"
        />
      </div>
      <div>
        <n-space align="center" style="margin-bottom: 4px">
          <label style="margin: 0">{{ t("llm_settings.chat_model") }}</label>
          <n-button text size="tiny" @click="loadModels" :loading="modelsLoading">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>
            {{ t("llm_settings.reload_list") }}
          </n-button>
          <span v-if="modelsError" style="color: var(--err-color, #e88080); font-size: 11px;">
            {{ t("llm_settings.ollama_unreachable", { err: modelsError.slice(0, 80) }) }}
          </span>
        </n-space>
        <n-select
          :value="llm.chat_model"
          :options="chatModelOptions"
          :loading="modelsLoading"
          :placeholder="t('llm_settings.pick_model')"
          filterable
          @update:value="(v: string) => patch({ chat_model: v })"
        />
      </div>
      <div>
        <label>{{ t("llm_settings.embedding_model") }}</label>
        <n-select
          :value="llm.embedding_model"
          :options="embeddingModelOptions"
          :loading="modelsLoading"
          :placeholder="t('llm_settings.pick_model')"
          filterable
          @update:value="(v: string) => patch({ embedding_model: v })"
        />
      </div>
      <div>
        <label>{{ t("llm_settings.timeout_sec") }}</label>
        <n-input-number
          :value="llm.timeout"
          :min="1"
          :max="600"
          @update:value="(v: any) => patch({ timeout: v })"
        />
      </div>
    </n-space>
    <p v-else style="opacity: 0.7">{{ t("common.loading") }}</p>
  </n-card>
</template>

<style scoped>
label {
  display: block;
  font-size: 12px;
  margin-bottom: 4px;
  opacity: 0.8;
}
</style>
