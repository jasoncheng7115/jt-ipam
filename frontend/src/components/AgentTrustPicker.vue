<script setup lang="ts">
// 代理安裝指令的「伺服器驗證」選擇（釘選／系統 CA／不驗證）＋說明。狀態在 useAgentTrust，三個代理頁共用。
import { NRadioButton, NRadioGroup } from "naive-ui";
import { useI18n } from "vue-i18n";
import type { AgentTrust } from "@/composables/useAgentTrust";

const props = withDefaults(defineProps<{ trust: AgentTrust; testid?: string }>(), { testid: "cert-trust" });
const { t } = useI18n();
const mode = props.trust.mode;
const serverId = props.trust.serverId;
const options = props.trust.options;
</script>

<template>
  <div>
    <div style="font-weight: 600; margin: 4px 0 6px">{{ t("certTrust.title") }}</div>
    <n-radio-group v-model:value="mode" size="small" class="trust-modes" :data-testid="`${props.testid}-mode`">
      <n-radio-button v-for="o in options" :key="o.value" :value="o.value" :disabled="o.disabled">{{ o.label }}</n-radio-button>
    </n-radio-group>
    <div class="trust-note" :data-testid="`${props.testid}-hint`">
      <template v-if="mode === 'pinned' && serverId?.available">
        {{ t("certTrust.hint_mode_pinned", { fp: (serverId.cert_sha256 || "").slice(0, 16) }) }}
        <div v-if="serverId.chain_valid" style="color: var(--warning-color, #f0a020); margin-top: 4px"
             :data-testid="`${props.testid}-public-warn`">{{ t("certTrust.hint_pinned_public") }}</div>
      </template>
      <template v-else-if="mode === 'ca'">
        {{ serverId?.available === false ? t("certTrust.hint_unavailable") : t("certTrust.hint_mode_ca") }}
      </template>
      <span v-else style="color: var(--error-color, #d03050)">{{ t("certTrust.hint_mode_insecure") }}</span>
    </div>
  </div>
</template>

<style scoped>
.trust-note { margin: 6px 0 10px; font-size: 12px; opacity: .68; line-height: 1.6; }
/* 手機寬度三個選項排不下一行：換行而不是被切掉 */
.trust-modes { display: flex; flex-wrap: wrap; row-gap: 4px; max-width: 100%; height: auto !important; }
</style>
