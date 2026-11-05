<script setup lang="ts">
// AI 判讀／AI 巡檢各自的 LLM Server（使用者 2026-11-05）。開關關著＝沿用基本設定（對話＋嵌入）那台；
// 打開才填供應商、位址與金鑰。關掉會把位址清空，後端連同供應商與金鑰一起清掉。
import { ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { NAlert, NInput, NSelect, NSwitch } from "naive-ui";
import type { LLMConfig, LLMConfigPatch } from "@/api/system";

const props = defineProps<{ role: "interpret" | "audit"; llm: LLMConfig; disabled?: boolean }>();
const emit = defineEmits<{ patch: [p: LLMConfigPatch] }>();
const { t } = useI18n();

const k = (field: "provider" | "url" | "api_key") => `ai_${props.role}_${field}` as keyof LLMConfigPatch;
const url = () => (props.llm[`ai_${props.role}_url`] as string) || "";
const provider = () => (props.llm[`ai_${props.role}_provider`] as string) || "ollama";
const keySet = () => !!props.llm[`ai_${props.role}_api_key_set`];

// 打開但還沒填位址時，畫面要維持打開（後端只在有位址時才算「有自己的 server」）
const separate = ref(!!url());
watch(() => url(), (v) => { if (v) separate.value = true; });

function toggle(on: boolean) {
  separate.value = on;
  if (!on && url()) emit("patch", { [k("url")]: "" });
}

const providerOptions = [
  { label: t("llm_settings.provider_ollama"), value: "ollama" },
  { label: t("llm_settings.provider_openai"), value: "openai" },
];
</script>

<template>
  <div :data-testid="`${role}-server`">
    <n-switch :value="separate" :disabled="disabled" :data-testid="`${role}-server-switch`"
              @update:value="toggle" />
    <span style="margin-left:10px">{{ t("llm_settings.role_server_separate") }}</span>
    <p v-if="!separate" class="hint">{{ t("llm_settings.role_server_inherit", { url: llm.url }) }}</p>
    <div v-else class="role-server">
      <div>
        <label>{{ t("llm_settings.provider") }}</label>
        <n-select :value="provider()" :options="providerOptions" :disabled="disabled"
                  @update:value="(v: string) => emit('patch', { [k('provider')]: v })" />
      </div>
      <n-alert v-if="provider() === 'openai'" type="warning" :bordered="false" :show-icon="true">
        {{ t("llm_settings.provider_egress_warn") }}
      </n-alert>
      <div>
        <label>{{ provider() === 'openai' ? t("llm_settings.api_base") : "Ollama URL" }}</label>
        <n-input :value="url()" :disabled="disabled" :data-testid="`${role}-server-url`"
                 :placeholder="provider() === 'openai' ? 'https://api.openai.com/v1' : 'http://127.0.0.1:11434'"
                 @update:value="(v: string) => emit('patch', { [k('url')]: v })" />
        <p class="hint">{{ t("llm_settings.role_server_url_hint") }}</p>
      </div>
      <div v-if="provider() === 'openai'">
        <label>{{ t("llm_settings.api_key") }}</label>
        <n-input type="password" show-password-on="click" :disabled="disabled"
                 :placeholder="keySet() ? t('llm_settings.api_key_set') : 'sk-…'"
                 @update:value="(v: string) => emit('patch', { [k('api_key')]: v })" />
        <p class="hint">{{ t("llm_settings.api_key_hint") }}</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.role-server { display: flex; flex-direction: column; gap: 12px; margin-top: 10px; }
/* 跟 LLMSettings.vue 的欄位標籤、說明文字一致 */
.role-server label { display: block; font-size: 12px; margin-bottom: 4px; opacity: 0.8; }
.hint { margin: 4px 0 0; font-size: 12px; opacity: 0.6; line-height: 1.5; }
</style>
