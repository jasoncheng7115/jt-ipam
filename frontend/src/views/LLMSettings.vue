<script setup lang="ts">
import { srvText } from "@/utils/wsError";
/**
 * LLM / AI 全域設定 (管理員)。
 *
 * 設定會覆寫環境變數，寫入 system_settings 表。所有 user 共用。
 */
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NSpace, NIcon, NAlert, NSwitch, NInput, NInputNumber, NSelect, NButton, NTag, NTimePicker,
  NPopconfirm, NModal, useMessage,
} from "naive-ui";
import {
  getLLMConfig, patchLLMConfig, listOllamaModels, revealMcpKey, rotateMcpKey, checkEmbedding,
  reindexEmbeddings, checkThinking, getIndexStatus,
  type LLMConfig, type LLMConfigPatch, type OllamaModel, type EmbeddingCheck, type ThinkingCheck,
  type ReindexResult, type IndexStatus,
} from "@/api/system";
import { listMcpTools, type McpTool } from "@/api/chat";
import { listSubnets, setAIAuditScope } from "@/api/subnets";
import type { Subnet } from "@/types";
import { SettingsIcon, RefreshIcon, ToolsIcon, KeyIcon, CopyIcon, EyeIcon, EyeOffIcon, AnomalyIcon, PlusIcon, DeleteIcon, TestIcon } from "@/icons";
import { apiErrMsg } from "@/api/client";
import { fmtDate, fmtDateTime } from "@/utils/datetime";
import LlmRoleServer from "@/components/LlmRoleServer.vue";

const { t, te } = useI18n();
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
// 嵌入模型可以接另一台（嵌入模型位址）：有填就向那一台要清單，沒填就用對話那台的（使用者 2026-11-07：
// 以前一律列對話那台的模型，挑不到另一台上的嵌入模型）
const embedModels = ref<OllamaModel[] | null>(null);
const embedModelsError = ref<string | null>(null);
let embedTimer: ReturnType<typeof setTimeout> | null = null;
async function loadEmbedModels() {
  if (!llm.value?.enabled || !llm.value?.embedding_base_url) { embedModels.value = null; embedModelsError.value = null; return; }
  try {
    const res = await listOllamaModels("embedding");
    embedModels.value = res.models;
    embedModelsError.value = res.error_detail || res.error ? srvText(res.error_detail, res.error ?? "") : null;
  } catch (e) { embedModels.value = []; embedModelsError.value = apiErrMsg(e); }
}
watch(() => llm.value?.embedding_base_url, () => {
  if (embedTimer) clearTimeout(embedTimer);
  embedTimer = setTimeout(() => { void loadEmbedModels(); }, 600);   // 位址是邊打邊存的，停一下再問
});
// 嵌入模型下拉：只有嵌入模型可選，其餘反灰（目前已選的仍保留可見）
const embeddingModelOptions = computed(() => {
  const src = embedModels.value
    ? embedModels.value.map((m) => ({ label: m.parameter_size ? `${m.name} (${m.parameter_size})` : m.name, value: m.name }))
    : modelOptions.value;
  const cur = llm.value?.embedding_model;
  const opts = cur && !src.some((o) => o.value === cur)
    ? [...src, { label: t("llm_settings.model_not_found", { model: cur }), value: cur }] : src;
  return opts.map((o) => ({ ...o, disabled: !isEmbedModel(o.value) && o.value !== cur }));
});
// 對話模型下拉：反過來把嵌入模型反灰
const chatModelOptions = computed(() =>
  modelOptions.value.map((o) => ({
    ...o,
    disabled: isEmbedModel(o.value) && o.value !== llm.value?.chat_model,
  })));
// 判讀／巡檢可以接自己的 LLM Server（2026-11-05）：有自己的位址時，模型清單要問那一台
type Role = "interpret" | "audit";
const roleModels = ref<Record<Role, OllamaModel[]>>({ interpret: [], audit: [] });
const roleModelsLoading = ref<Record<Role, boolean>>({ interpret: false, audit: false });
const roleModelsError = ref<Record<Role, string | null>>({ interpret: null, audit: null });
const hasOwnServer = (role: Role) => !!llm.value?.[`ai_${role}_url`];

async function loadRoleModels(role: Role) {
  if (!hasOwnServer(role)) { roleModels.value[role] = []; roleModelsError.value[role] = null; return; }
  roleModelsLoading.value[role] = true;
  roleModelsError.value[role] = null;
  try {
    const res = await listOllamaModels(role);
    roleModels.value[role] = res.models;
    if (res.error_detail || res.error) roleModelsError.value[role] = srvText(res.error_detail, res.error ?? "");
  } catch (e: any) {
    roleModelsError.value[role] = e?.response?.data?.detail ?? String(e);
  } finally {
    roleModelsLoading.value[role] = false;
  }
}

// 判讀／巡檢的模型下拉：排除嵌入模型；清空＝沿用（對話模型或那台的預設，見提示）
function roleModelOptions(role: Role) {
  const current = llm.value?.[`ai_${role}_model`] ?? null;
  const base = hasOwnServer(role)
    ? roleModels.value[role].map((m) => ({
      label: m.parameter_size ? `${m.name} (${m.parameter_size})` : m.name, value: m.name }))
    : modelOptions.value;
  const opts = base.map((o) => ({ ...o, disabled: isEmbedModel(o.value) && o.value !== current }));
  if (current && !opts.find((o) => o.value === current)) {
    opts.push({ label: t("llm_settings.model_not_found", { model: current }), value: current, disabled: false });
  }
  return opts;
}
const auditModelOptions = computed(() => roleModelOptions("audit"));
const interpretModelOptions = computed(() => roleModelOptions("interpret"));

// 巡檢範圍。存的是每個子網路的 ai_audit_enabled 欄位（跟子網路編輯頁同一個），
// 不是另外存一份清單 —— 存兩份遲早會出現「這裡看是開的、那裡看是關的」。
const subnets = ref<Subnet[]>([]);
const subnetsLoading = ref(false);
const auditScope = ref<string[]>([]);
const subnetOptions = computed(() => subnets.value.map((s) => ({
  label: s.description ? `${s.cidr} — ${s.description}` : s.cidr,
  value: s.id,
})));

async function loadSubnets() {
  subnetsLoading.value = true;
  try {
    const r = await listSubnets({ pageSize: 500 });
    subnets.value = r.items;
    auditScope.value = r.items.filter((s) => s.ai_audit_enabled).map((s) => s.id);
  } catch { /* 沒權限或載入失敗 → 這一格留空，不擋整頁 */ } finally { subnetsLoading.value = false; }
}

async function saveScope(ids: string[]) {
  try {
    await setAIAuditScope(ids);
    msg.success(t("common.saved"));
  } catch (e) { msg.error(apiErrMsg(e)); await loadSubnets(); }
}

// 排程的「哪幾天」。時刻（下面的 auditTimes）是另一個維度：兩者相乘才是實際的執行時機。
const freqOptions = computed(() => (["daily", "weekly", "monthly"] as const).map((f) => ({
  label: t(`llm_settings.audit_freq_${f}`), value: f,
})));
// 1=週一 … 7=週日（ISO），與後端一致
const weekdayOptions = computed(() => [1, 2, 3, 4, 5, 6, 7].map((d) => ({
  label: t(`llm_settings.weekday_${d}`), value: d,
})));
const auditFreq = computed(() => llm.value?.ai_audit_frequency || "daily");
const auditWeekdays = computed(() => (llm.value?.ai_audit_weekdays?.length
  ? [...llm.value.ai_audit_weekdays] : [1]));
const auditMonthDay = computed(() => llm.value?.ai_audit_month_day || 1);

// 排程時刻。n-time-picker 吃毫秒時間戳，設定存的是 "HH:MM" —— 兩邊在這裡轉換。
const auditTimes = computed(() => llm.value?.ai_audit_times?.length
  ? [...llm.value.ai_audit_times] : ["03:30"]);

function hhmmToMs(hhmm: string): number {
  const [h, m] = hhmm.split(":").map((x) => Number(x) || 0);
  const d = new Date();
  d.setHours(h, m, 0, 0);
  return d.getTime();
}

function setTime(index: number, formatted: string | null) {
  if (!formatted) return;
  const next = [...auditTimes.value];
  next[index] = formatted;
  patch({ ai_audit_times: next });
}

function addTime() {
  patch({ ai_audit_times: [...auditTimes.value, "12:00"] });
}

function removeTime(index: number) {
  const next = auditTimes.value.filter((_, i) => i !== index);
  // 一個都不留等於安靜地把排程關掉，但開關還是開著 —— 後端也會擋，這裡先不讓它發生
  if (next.length) patch({ ai_audit_times: next });
}

async function loadModels() {
  modelsLoading.value = true;
  modelsError.value = null;
  try {
    const res = await listOllamaModels();
    models.value = res.models;
    // 後端只給代碼與參數（error_detail），句子在這裡組 —— 伺服器產生的文字沒有辦法
    // 跟著使用者的語言走。舊欄位 error 留著當退路。
    if (res.error_detail || res.error) {
      modelsError.value = srvText(res.error_detail, res.error ?? "");
    }
  } catch (e: any) {
    modelsError.value = e?.response?.data?.detail ?? String(e);
  } finally {
    modelsLoading.value = false;
  }
}

const providerOptions = computed(() => [
  { label: t("llm_settings.provider_ollama"), value: "ollama" },
  { label: t("llm_settings.provider_openai"), value: "openai" },
]);

// 嵌入維度檢查：模型回幾維、資料庫要幾維、通不通
const embedBusy = ref(false);
const embedResult = ref<EmbeddingCheck | null>(null);
const reindexBusy = ref(false);
const reindexResult = ref<ReindexResult | null>(null);
async function doReindex() {
  reindexBusy.value = true;
  reindexResult.value = null;
  try { reindexResult.value = await reindexEmbeddings(); }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { reindexBusy.value = false; void loadIndexStatus(); }
}
/** 重建失敗的原因：有代碼就翻譯（維度不合會說模型回幾維、需要幾維），沒有才用原文 */
function reindexError(r: ReindexResult): string {
  if (r.error_code && te(`errors.${r.error_code}`)) return t(`errors.${r.error_code}`, r.error_params ?? {});
  return String(r.error ?? "").slice(0, 100);
}

// 索引狀態（使用者 2026-10-13：「要檢查是不是需要重建索引，因為看不出來要不要按」）：
// 只看資料庫（不呼叫模型），換了嵌入模型、重建完都重新讀一次
const indexStatus = ref<IndexStatus | null>(null);
async function loadIndexStatus() {
  try { indexStatus.value = await getIndexStatus(); } catch { indexStatus.value = null; }
}
const indexLine = computed(() => {
  const s = indexStatus.value;
  if (!s) return null;
  if (!s.described) return { ok: true, text: t("llm_settings.index_nothing") };
  if (s.reason === "never") return { ok: false, text: t("llm_settings.index_never", { n: s.described }) };
  if (s.reason === "model_changed") {
    return { ok: false, text: t("llm_settings.index_model_changed", { old: s.last?.model ?? "", now: s.model }) };
  }
  if (s.reason === "missing") return { ok: false, text: t("llm_settings.index_missing", { n: s.missing }) };
  return { ok: true, text: t("llm_settings.index_ok", {
    n: s.described - s.missing, total: s.described, model: s.model, at: s.last?.at ? fmtDateTime(s.last.at) : "" }) };
});

// 思考檢查：AI 巡檢與判讀一律送「關閉思考」的參數，伺服器或閘道沒照做時只會變慢、
// 答案被思考吃掉額度，不會報錯 —— 所以要能當場問一次（不自動跑：每個模型要真的問一句）
const thinkBusy = ref(false);
const thinkResults = ref<ThinkingCheck[] | null>(null);
async function doCheckThinking() {
  thinkBusy.value = true;
  try { thinkResults.value = await checkThinking(); }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { thinkBusy.value = false; }
}
function thinkLine(r: ThinkingCheck): string {
  const who = `${t(`llm_settings.think_role_${r.role}`)} ${r.model}`;
  if (!r.ok) return t("llm_settings.think_failed", { who, err: String(r.error ?? "").slice(0, 160) });
  if (!r.thinking) return t("llm_settings.think_off", { who, sec: r.seconds });
  const why = r.reasoning_chars ? t("llm_settings.think_why_chars", { n: r.reasoning_chars })
    : r.think_tag ? t("llm_settings.think_why_tag") : t("llm_settings.think_why_empty");
  return t("llm_settings.think_on", { who, sec: r.seconds, why });
}

async function doCheckEmbedding() {
  embedBusy.value = true;
  try { embedResult.value = await checkEmbedding(); }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { embedBusy.value = false; }
}

async function load() {
  try { llm.value = await getLLMConfig(); }
  catch (e) { msg.error(apiErrMsg(e)); }
  // 未啟用連接時不要自動去抓清單（否則會冒出「無法連上 LLM 伺服器」錯誤）
  if (llm.value?.enabled) {
    void loadModels(); void doCheckEmbedding(); void loadIndexStatus(); void loadEmbedModels();
    void loadRoleModels("interpret"); void loadRoleModels("audit");
  }
}

// URL 改了也重新拉 model 清單 (換 LLM 伺服器時可能不同)；僅在已啟用時
watch(() => llm.value?.url, () => { if (llm.value?.url && llm.value?.enabled) void loadModels(); });
// 換供應商等於換端點（/api/tags ↔ /v1/models），清單一定要重拉，否則下拉會留著上一家的模型
watch(() => llm.value?.provider, () => { if (llm.value?.url && llm.value?.enabled) void loadModels(); });
// 啟用切換：開啟才去抓清單；關閉時清掉清單與錯誤（避免殘留「無法連」）
watch(() => llm.value?.enabled, (en) => {
  if (en) { if (llm.value?.url) void loadModels(); }
  else { models.value = []; modelsError.value = null; }
});

let debounce: ReturnType<typeof setTimeout> | null = null;
function patch(p: LLMConfigPatch) {
  if (!llm.value) return;
  llm.value = { ...llm.value, ...p } as LLMConfig;
  if (debounce) clearTimeout(debounce);
  debounce = setTimeout(async () => {
    try {
      llm.value = await patchLLMConfig(p);
      // 換了嵌入模型：既有向量是舊模型的，索引狀態要跟著變成「建議重建」
      if ("embedding_model" in p) void loadIndexStatus();
      // 判讀／巡檢換了 LLM Server：模型清單要重問那一台
      for (const role of ["interpret", "audit"] as const) {
        if ([`ai_${role}_url`, `ai_${role}_provider`, `ai_${role}_api_key`].some((x) => x in p)) void loadRoleModels(role);
      }
      msg.success(t("common.saved"));
    } catch (e) {
      msg.error(apiErrMsg(e));
    }
  }, 600);
}

// 對外提供 MCP（讓其它系統呼叫）
const mcpKey = ref<string | null>(null);   // 已揭示的明文（null＝未揭示）
const mcpKeyBusy = ref(false);
const mcpUrl = computed(() => `${window.location.origin}/api/mcp`);
async function doRevealKey() {
  mcpKeyBusy.value = true;
  try { mcpKey.value = await revealMcpKey(); }
  catch { msg.error(t("errors.server")); }
  finally { mcpKeyBusy.value = false; }
}
// 金鑰會過期：產生／更換時選有效天數，到期前 14／7／1 天會通知管理員
const mcpKeyDays = ref(90);
const mcpKeyDayOptions = computed(() => [30, 90, 180, 365].map((d) => ({ label: t("llm_settings.mcp_key_days_n", { n: d }), value: d })));
/** 7 天內有 MCP 用戶端用過這把金鑰：重產會讓它立刻斷線，確認框要特別說 */
const mcpKeyRecentlyUsed = computed(() => {
  const at = llm.value?.mcp_api_key_last_used_at;
  return !!at && Date.now() - new Date(at).getTime() < 7 * 86400 * 1000;
});
const mcpKeyExpired = computed(() => {
  const s = llm.value?.mcp_api_key_expires_at;
  return !!s && new Date(s).getTime() <= Date.now();
});
async function doRotateKey() {
  mcpKeyBusy.value = true;
  try {
    const out = await rotateMcpKey(mcpKeyDays.value);
    mcpKey.value = out.api_key;
    if (llm.value) {
      llm.value.mcp_api_key_set = true;
      llm.value.mcp_api_key_expires_at = out.expires_at;
    }
    msg.success(t("common.saved"));
  } catch { msg.error(t("errors.server")); }
  finally { mcpKeyBusy.value = false; }
}
async function copyText(s: string | null) {
  if (!s) return;
  try { await navigator.clipboard.writeText(s); msg.success(t("common.copied_clipboard")); }
  catch { /* ignore */ }
}

// 產生各用戶端的 MCP 設定（方便對方直接貼上）
const cfgShow = ref(false);
async function openClientConfigs() {
  // 已設金鑰但尚未揭示 → 先揭示，讓產生的設定直接帶上真正的金鑰
  if (!mcpKey.value && llm.value?.mcp_api_key_set) await doRevealKey();
  cfgShow.value = true;
}
const clientConfigs = computed(() => {
  const url = mcpUrl.value;
  const key = mcpKey.value || "<API KEY>";
  return [
    {
      id: "claude", name: "Claude Desktop", file: "claude_desktop_config.json",
      code: JSON.stringify({ mcpServers: { "jt-ipam": {
        command: "npx", args: ["-y", "mcp-remote", url, "--header", `X-Auth-Token: ${key}`],
      } } }, null, 2),
    },
    {
      id: "opencode", name: "opencode", file: "opencode.json",
      code: JSON.stringify({ mcp: { "jt-ipam": {
        type: "remote", url, enabled: true, headers: { "X-Auth-Token": key },
      } } }, null, 2),
    },
    {
      id: "mcpo", name: "mcpo", file: "config.json（mcpo --config config.json）",
      code: JSON.stringify({ mcpServers: { "jt-ipam": {
        type: "streamablehttp", url, headers: { "X-Auth-Token": key },
      } } }, null, 2),
    },
    {
      id: "generic", name: "Cursor / Cline / VS Code", file: "mcp.json",
      code: JSON.stringify({ mcpServers: { "jt-ipam": { url, headers: { "X-Auth-Token": key } } } }, null, 2),
    },
  ];
});

// MCP / AI 工具清單
const mcpTools = ref<McpTool[]>([]);
const mcpMutating = ref(0);
async function loadTools() {
  try { const r = await listMcpTools(); mcpTools.value = r.tools; mcpMutating.value = r.mutating_count; }
  catch { /* ignore */ }
}

onMounted(() => { void load(); void loadTools(); void loadSubnets(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><SettingsIcon /></n-icon>
        <span>{{ t("llm_settings.title") }}</span>
      </n-space>
    </template>
    <!-- 不限寬：說明文字在寬螢幕上只佔一半版面、右邊整片空白，讀起來被硬折行 -->
    <n-space v-if="llm" vertical :size="16" style="width: 100%">
      <n-alert type="info" size="small">
        <span v-html="t('llm_settings.admin_note', { strong: `<strong>${t('llm_settings.admin_global_settings')}</strong>` })" />
      </n-alert>
      <div>
        <label>{{ t("llm_settings.enable_ollama") }}</label>
        <n-switch :value="llm.enabled" @update:value="(v: boolean) => patch({ enabled: v })" />
      </div>
      <div>
        <label>{{ t("llm_settings.provider") }}</label>
        <n-select
          :value="llm.provider ?? 'ollama'"
          :options="providerOptions"
          @update:value="(v: string) => patch({ provider: v })"
        />
        <p class="hint">{{ t("llm_settings.provider_hint") }}</p>
      </div>
      <!-- 選了外部供應商就要講清楚資料會離開自己的網路。
           這個專案的賣點是「自架 LLM、資料不外流」，改成雲端是使用者的選擇，
           但不能讓它悄悄發生。 -->
      <n-alert v-if="(llm.provider ?? 'ollama') === 'openai'" type="warning"
               :bordered="false" :show-icon="true">
        {{ t("llm_settings.provider_egress_warn") }}
      </n-alert>
      <div>
        <label>{{ (llm.provider ?? 'ollama') === 'openai' ? t("llm_settings.api_base") : "Ollama URL" }}</label>
        <n-input
          :value="llm.url"
          :placeholder="(llm.provider ?? 'ollama') === 'openai'
            ? 'https://api.openai.com/v1' : 'http://127.0.0.1:11434'"
          @update:value="(v: string) => patch({ url: v })"
        />
        <p v-if="(llm.provider ?? 'ollama') === 'openai'" class="hint">
          {{ t("llm_settings.api_base_hint") }}
        </p>
      </div>
      <div v-if="(llm.provider ?? 'ollama') === 'openai'">
        <label>{{ t("llm_settings.api_key") }}</label>
        <n-input type="password" show-password-on="click"
                 :placeholder="llm.api_key_set ? t('llm_settings.api_key_set') : 'sk-…'"
                 @update:value="(v: string) => patch({ api_key: v })" />
        <p class="hint">{{ t("llm_settings.api_key_hint") }}</p>
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
          <n-button text size="tiny" :loading="thinkBusy" data-testid="think-check" @click="doCheckThinking">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>
            {{ t("llm_settings.think_check") }}
          </n-button>
        </n-space>
        <n-select
          :value="llm.chat_model"
          :options="chatModelOptions"
          :loading="modelsLoading"
          :placeholder="t('llm_settings.pick_model')"
          filterable
          @update:value="(v: string) => patch({ chat_model: v })"
        />
        <div v-if="thinkResults" data-testid="think-results" style="margin-top: 6px">
          <p v-for="r in thinkResults" :key="r.role" class="hint"
             :style="r.ok && !r.thinking ? 'color:#18a058' : 'color:#e88080'">
            {{ thinkLine(r) }}
            <template v-if="r.rejected_params.length">
              {{ t("llm_settings.think_rejected", { params: r.rejected_params.join(", ") }) }}
            </template>
          </p>
          <p v-if="thinkResults.some((r) => r.ok && r.thinking)" class="hint">{{ t("llm_settings.think_on_hint") }}</p>
        </div>
      </div>
      <div>
        <!-- 嵌入與對話模型常常是分開部署的（GitHub issue #33），位址自然不同。
             留空＝沿用上面那個位址，既有安裝升上來行為不變。先選位址再選模型（使用者 2026-11-07），
             模型清單也是向這個位址要的。 -->
        <div style="margin-bottom: 12px">
          <label>{{ t("llm_settings.embedding_url") }}</label>
          <n-input
            :value="llm.embedding_base_url ?? ''"
            :placeholder="llm.url"
            data-testid="embedding-url"
            @update:value="(v: string) => patch({ embedding_base_url: v })"
          />
          <p class="hint">{{ t("llm_settings.embedding_url_hint") }}</p>
          <p v-if="embedModelsError" class="hint" style="color:#e88080">{{ embedModelsError }}</p>
        </div>
        <n-space align="center" style="margin-bottom: 4px">
          <label style="margin: 0">{{ t("llm_settings.embedding_model") }}</label>
          <n-button text size="tiny" :loading="embedBusy" @click="doCheckEmbedding">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>
            {{ t("llm_settings.embed_check") }}
          </n-button>
        </n-space>
        <n-select
          :value="llm.embedding_model"
          :options="embeddingModelOptions"
          data-testid="embedding-model"
          :loading="modelsLoading"
          :placeholder="t('llm_settings.pick_model')"
          filterable
          @update:value="(v: string) => patch({ embedding_model: v })"
        />
        <!-- 選錯嵌入模型時唯一的症狀是「語意搜尋永遠沒有結果」，畫面上沒有任何線索
             指向維度不合 —— 所以要能當場問一次、把實際維度講出來。 -->
        <p v-if="embedResult" class="hint" :style="embedResult.ok ? 'color:#18a058' : 'color:#e88080'">
          {{ embedResult.ok
            ? t("llm_settings.embed_ok", { dim: embedResult.dim })
            : embedResult.dim
              ? t("llm_settings.embed_dim_mismatch", { dim: embedResult.dim, expected: embedResult.expected })
              : t("llm_settings.embed_failed", { err: String(embedResult.error).slice(0, 120) }) }}
        </p>
        <p class="hint">{{ t("llm_settings.embed_hint", { expected: embedResult?.expected ?? 768 }) }}</p>
        <!-- 換過模型之後，既有物件的向量還是空的 —— 沒有這顆按鈕，使用者無從讓
             語意搜尋真的開始運作（端點一直都在，只是畫面上沒有入口）。 -->
        <n-space align="center" style="margin-top:8px">
          <n-button size="small" :loading="reindexBusy" @click="doReindex">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>
            {{ t("llm_settings.reindex") }}
          </n-button>
          <span v-if="reindexResult" class="hint" style="margin:0"
                :style="reindexResult.failed ? 'color:#e88080' : 'color:#18a058'">
            {{ reindexResult.failed
              ? t("llm_settings.reindex_failed", { n: reindexResult.failed, err: reindexError(reindexResult) })
              : t("llm_settings.reindex_ok", { n: reindexResult.subnets
                    + reindexResult.ip_addresses + reindexResult.devices }) }}
          </span>
        </n-space>
        <p v-if="indexLine" class="hint index-status" data-testid="llm-index-status"
           :style="indexLine.ok ? 'color:#18a058' : 'color:#f0a020'">
          {{ indexLine.text }}
        </p>
        <p class="hint">{{ t("llm_settings.reindex_hint") }}</p>
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
      <div>
        <label>{{ t("llm_settings.num_ctx") }}</label>
        <n-input-number
          :value="llm.num_ctx ?? null"
          :min="0"
          :max="131072"
          :step="1024"
          clearable
          :placeholder="t('llm_settings.num_ctx_placeholder')"
          style="width: 220px"
          @update:value="(v: any) => patch({ num_ctx: v ?? 0 })"
        />
        <p class="hint">{{ t("llm_settings.num_ctx_hint") }}</p>
      </div>
      <div data-testid="chat-thinking">
        <n-switch :value="llm.chat_thinking"
                  @update:value="(v: boolean) => patch({ chat_thinking: v })" />
        <span style="margin-left:10px">{{ t("llm_settings.chat_thinking") }}</span>
        <p class="hint">{{ t("llm_settings.chat_thinking_hint") }}</p>
      </div>
    </n-space>
    <p v-else style="opacity: 0.7">{{ t("common.loading") }}</p>
  </n-card>

  <!-- AI 判讀：三個按需觸發的判讀功能共用一個模型設定（比照巡檢；留空＝沿用對話模型） -->
  <n-card v-if="llm" style="margin-top:16px" data-testid="interpret-card">
    <template #header>
      <n-space align="center" :size="8">
        <n-icon :size="18" :component="TestIcon" />
        <span>{{ t("llm_settings.interpret_title") }}</span>
      </n-space>
    </template>
    <p class="hint" style="margin: 0 0 14px">{{ t("llm_settings.interpret_scope") }}</p>
    <n-space vertical :size="14">
      <LlmRoleServer role="interpret" :llm="llm" @patch="patch" />
      <div>
        <n-space align="center" style="margin-bottom: 4px">
          <label style="margin: 0">{{ t("llm_settings.interpret_model") }}</label>
          <n-button v-if="hasOwnServer('interpret')" text size="tiny" :loading="roleModelsLoading.interpret"
                    @click="loadRoleModels('interpret')">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>
            {{ t("llm_settings.reload_list") }}
          </n-button>
          <span v-if="roleModelsError.interpret" style="color: var(--err-color, #e88080); font-size: 11px;">
            {{ t("llm_settings.ollama_unreachable", { err: roleModelsError.interpret.slice(0, 80) }) }}
          </span>
        </n-space>
        <n-select
          :value="llm.ai_interpret_model"
          :options="interpretModelOptions"
          :loading="hasOwnServer('interpret') ? roleModelsLoading.interpret : modelsLoading"
          :placeholder="t('llm_settings.audit_model_inherit', { model: llm.chat_model })"
          clearable
          filterable
          style="width: 100%"
          data-testid="interpret-model"
          @update:value="(v: string | null) => patch({ ai_interpret_model: v ?? '' })"
        />
        <p v-if="hasOwnServer('interpret') && !llm.ai_interpret_model" class="hint" style="color: #f0a020"
           data-testid="interpret-model-needed">
          {{ t("llm_settings.role_server_model_needed", { model: llm.chat_model }) }}
        </p>
        <p class="hint">{{ t("llm_settings.interpret_model_hint") }}</p>
      </div>
      <div>
        <label>{{ t("llm_settings.interpret_num_ctx") }}</label>
        <n-input-number :value="llm.ai_interpret_num_ctx" :min="0" :max="131072" :step="2048"
                        clearable style="width: 220px"
                        :placeholder="String(llm.num_ctx ?? 4096)"
                        @update:value="(v: number | null) => patch({ ai_interpret_num_ctx: v ?? 0 })" />
        <p class="hint">{{ t("llm_settings.interpret_num_ctx_hint", { n: llm.num_ctx ?? 4096 }) }}</p>
      </div>
    </n-space>
  </n-card>

  <!-- AI 巡檢排程 -->
  <n-card v-if="llm" style="margin-top:16px">
    <template #header>
      <n-space align="center" :size="8">
        <n-icon :size="18" :component="AnomalyIcon" />
        <span>{{ t("llm_settings.audit_title") }}</span>
      </n-space>
    </template>
    <n-alert type="warning" :bordered="false" :show-icon="true" style="margin-bottom:14px">
      {{ t("llm_settings.audit_warning") }}
    </n-alert>
    <n-space vertical :size="14">
      <div>
        <n-switch :value="llm.ai_audit_enabled"
                  @update:value="(v: boolean) => patch({ ai_audit_enabled: v })" />
        <span style="margin-left:10px">{{ t("llm_settings.audit_enabled") }}</span>
        <p class="hint">{{ t("llm_settings.audit_enabled_hint") }}</p>
      </div>
      <LlmRoleServer role="audit" :llm="llm" :disabled="!llm.ai_audit_enabled" @patch="patch" />
      <div>
        <n-space align="center" style="margin-bottom: 4px">
          <label style="margin: 0">{{ t("llm_settings.audit_model") }}</label>
          <n-button v-if="hasOwnServer('audit')" text size="tiny" :loading="roleModelsLoading.audit"
                    @click="loadRoleModels('audit')">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>
            {{ t("llm_settings.reload_list") }}
          </n-button>
          <span v-if="roleModelsError.audit" style="color: var(--err-color, #e88080); font-size: 11px;">
            {{ t("llm_settings.ollama_unreachable", { err: roleModelsError.audit.slice(0, 80) }) }}
          </span>
        </n-space>
        <n-select
          :value="llm.ai_audit_model"
          :options="auditModelOptions"
          :loading="hasOwnServer('audit') ? roleModelsLoading.audit : modelsLoading"
          :disabled="!llm.ai_audit_enabled"
          :placeholder="t('llm_settings.audit_model_inherit', { model: llm.chat_model })"
          clearable
          filterable
          style="width: 100%"
          @update:value="(v: string | null) => patch({ ai_audit_model: v ?? '' })"
        />
        <p v-if="hasOwnServer('audit') && !llm.ai_audit_model" class="hint" style="color: #f0a020"
           data-testid="audit-model-needed">
          {{ t("llm_settings.role_server_model_needed", { model: llm.chat_model }) }}
        </p>
        <p class="hint">{{ t("llm_settings.audit_model_hint") }}</p>
      </div>
      <div>
        <label>{{ t("llm_settings.audit_num_ctx") }}</label>
        <n-input-number :value="llm.ai_audit_num_ctx" :min="0" :max="131072" :step="2048"
                        :disabled="!llm.ai_audit_enabled" clearable style="width: 220px"
                        :placeholder="String(llm.num_ctx ?? 4096)"
                        @update:value="(v: number | null) => patch({ ai_audit_num_ctx: v ?? 0 })" />
        <p class="hint">{{ t("llm_settings.audit_num_ctx_hint", { n: llm.num_ctx ?? 4096 }) }}</p>
      </div>
      <div>
        <label>{{ t("llm_settings.audit_scope") }}</label>
        <n-select v-model:value="auditScope" multiple filterable clearable
                  :options="subnetOptions" :loading="subnetsLoading"
                  :disabled="!llm.ai_audit_enabled"
                  :placeholder="t('llm_settings.audit_scope_none')"
                  style="width: 100%" @update:value="saveScope" />
        <p class="hint">{{ t("llm_settings.audit_scope_hint") }}</p>
      </div>
      <div>
        <label>{{ t("llm_settings.audit_freq") }}</label>
        <n-space align="center" :size="12" :wrap="true">
          <n-select :value="auditFreq" :options="freqOptions" style="width: 160px"
                    :disabled="!llm.ai_audit_enabled"
                    @update:value="(v: string) => patch({ ai_audit_frequency: v })" />
          <!-- 每週：可複選；一天都不選等於排程永遠不觸發，所以後端不會存空清單 -->
          <n-select v-if="auditFreq === 'weekly'" :value="auditWeekdays" multiple
                    :options="weekdayOptions" style="min-width: 280px"
                    :disabled="!llm.ai_audit_enabled"
                    :placeholder="t('llm_settings.audit_weekdays_ph')"
                    @update:value="(v: number[]) => v.length && patch({ ai_audit_weekdays: v })" />
          <!-- 每月：1–31。設 31 遇到只有 30 天（或 2 月）的月份會落在該月最後一天，
               不是整個月都不跑 —— 提示文字要講出來，否則使用者以為排程壞了 -->
          <n-input-number v-if="auditFreq === 'monthly'" :value="auditMonthDay"
                          :min="1" :max="31" style="width: 130px"
                          :disabled="!llm.ai_audit_enabled"
                          @update:value="(v: number | null) => v && patch({ ai_audit_month_day: v })" />
        </n-space>
        <p v-if="auditFreq === 'monthly'" class="hint">
          {{ t("llm_settings.audit_month_day_hint") }}
        </p>
      </div>
      <div>
        <label>{{ t("llm_settings.audit_times") }}</label>
        <div class="times">
          <div v-for="(tm, i) in auditTimes" :key="i" class="time-row">
            <n-time-picker :value="hhmmToMs(tm)" format="HH:mm" :disabled="!llm.ai_audit_enabled"
                           style="width: 130px"
                           @update:formatted-value="(v: string | null) => setTime(i, v)" />
            <n-button quaternary size="small" :disabled="!llm.ai_audit_enabled || auditTimes.length <= 1"
                      @click="removeTime(i)">
              <template #icon><n-icon><DeleteIcon /></n-icon></template>
            </n-button>
          </div>
          <n-button size="small" dashed :disabled="!llm.ai_audit_enabled || auditTimes.length >= 12"
                    @click="addTime">
            <template #icon><n-icon><PlusIcon /></n-icon></template>
            {{ t("llm_settings.audit_add_time") }}
          </n-button>
        </div>
        <p class="hint">
          {{ t("llm_settings.audit_times_hint", { tz: llm.server_timezone || "—" }) }}
        </p>
      </div>
    </n-space>
  </n-card>

  <!-- 對外提供 MCP：讓其它系統（外部 LLM 客戶端 / 自動化）以 HTTP 呼叫本站 MCP -->
  <n-card v-if="llm" style="margin-top:16px">
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="20"><KeyIcon /></n-icon>
        <span>{{ t("llm_settings.mcp_ext_title") }}</span>
      </n-space>
    </template>

    <div class="row">
      <label>{{ t("llm_settings.mcp_ext_enable") }}</label>
      <n-switch :value="llm.mcp_external_enabled"
                @update:value="(v: boolean) => patch({ mcp_external_enabled: v })" />
    </div>
    <p class="hint">{{ t("llm_settings.mcp_ext_hint") }}</p>

    <template v-if="llm.mcp_external_enabled">
      <!-- 連線資訊：外部系統要填的 URL / 傳輸 / 認證標頭 -->
      <div class="mcp-info">
        <div class="mcp-info-row">
          <span class="mcp-k">{{ t("llm_settings.mcp_url") }}</span>
          <code class="mcp-v">{{ mcpUrl }}</code>
          <n-button size="tiny" @click="copyText(mcpUrl)">
            <template #icon><n-icon :component="CopyIcon" /></template>{{ t("common.copy") }}
          </n-button>
        </div>
        <div class="mcp-info-row">
          <span class="mcp-k">{{ t("llm_settings.mcp_transport") }}</span>
          <code class="mcp-v">Streamable HTTP（JSON-RPC 2.0, POST）</code>
          <span class="mcp-or">{{ t("llm_settings.mcp_transport_note") }}</span>
        </div>
        <div class="mcp-info-row mcp-auth-hd">
          <span class="mcp-k">{{ t("llm_settings.mcp_auth_header") }}</span>
          <span class="mcp-or">{{ t("llm_settings.mcp_auth_choose") }}</span>
        </div>
        <!-- 兩種寫法各一列，名稱與值各自對齊成欄；原本擠在同一行讀起來像一串亂碼 -->
        <div class="mcp-auth-grid">
          <span class="mcp-kn">{{ t("llm_settings.mcp_header_name") }}</span>
          <code class="mcp-v">X-Auth-Token</code>
          <span class="mcp-kn">{{ t("llm_settings.mcp_header_value") }}</span>
          <code class="mcp-v">&lt;API KEY&gt;</code>

          <span class="mcp-kn">{{ t("llm_settings.mcp_header_name") }}</span>
          <code class="mcp-v">Authorization</code>
          <span class="mcp-kn">{{ t("llm_settings.mcp_header_value") }}</span>
          <code class="mcp-v">Bearer &lt;API KEY&gt;</code>
        </div>
        <div class="mcp-info-row mcp-gen-row">
          <n-button size="small" type="primary" ghost @click="openClientConfigs">
            <template #icon><n-icon :component="CopyIcon" /></template>{{ t("llm_settings.mcp_gen_config") }}
          </n-button>
          <span class="mcp-or">{{ t("llm_settings.mcp_gen_inline_hint") }}</span>
        </div>
      </div>

      <!-- API 金鑰（唯讀範圍）：標籤獨立一行，下方為金鑰值與操作 -->
      <div class="mcp-key">
        <label>{{ t("llm_settings.mcp_key") }}</label>
        <n-space align="center" :size="8" :wrap-item="false" style="flex-wrap:wrap">
          <code v-if="mcpKey" class="mcp-keybox mcp-keybox--val">{{ mcpKey }}</code>
          <span v-else-if="llm.mcp_api_key_set" class="mcp-keybox">••••••••••••（{{ t("llm_settings.mcp_key_hidden") }}）</span>
          <span v-else class="mcp-keybox mcp-keybox--none">{{ t("llm_settings.mcp_key_none") }}</span>
          <n-tag v-if="llm.mcp_api_key_set && llm.mcp_api_key_expires_at" size="small" :bordered="false"
                 :type="mcpKeyExpired ? 'error' : 'default'" data-testid="mcp-key-expiry">
            {{ mcpKeyExpired ? t("llm_settings.mcp_key_expired", { date: fmtDate(llm.mcp_api_key_expires_at) })
                             : t("llm_settings.mcp_key_expires", { date: fmtDate(llm.mcp_api_key_expires_at) }) }}
          </n-tag>

          <!-- 最後使用：重產前看得出還有沒有用戶端在用（使用者 2026-10-21：重產後自己的 Claude 斷線一週才發現） -->
          <n-tag v-if="llm.mcp_api_key_set" size="small" :bordered="false" :type="mcpKeyRecentlyUsed ? 'info' : 'default'"
                 data-testid="mcp-key-last-used">
            {{ llm.mcp_api_key_last_used_at
              ? t("llm_settings.mcp_key_last_used", { time: fmtDateTime(llm.mcp_api_key_last_used_at), ip: llm.mcp_api_key_last_used_ip || "—" })
              : t("llm_settings.mcp_key_never_used") }}
          </n-tag>
          <n-button v-if="llm.mcp_api_key_set && !mcpKey" size="small" :loading="mcpKeyBusy" @click="doRevealKey">
            <template #icon><n-icon :component="EyeIcon" /></template>{{ t("llm_settings.mcp_key_reveal") }}
          </n-button>
          <n-button v-if="mcpKey" size="small" @click="copyText(mcpKey)">
            <template #icon><n-icon :component="CopyIcon" /></template>{{ t("common.copy") }}
          </n-button>
          <n-button v-if="mcpKey" size="small" @click="mcpKey = null">
            <template #icon><n-icon :component="EyeOffIcon" /></template>{{ t("llm_settings.mcp_key_hide") }}
          </n-button>

          <n-select v-model:value="mcpKeyDays" :options="mcpKeyDayOptions" size="small" style="width: 130px"
                    :aria-label="t('llm_settings.mcp_key_days')" data-testid="mcp-key-days" />
          <n-popconfirm v-if="llm.mcp_api_key_set" @positive-click="doRotateKey">
            <template #trigger>
              <n-button size="small" type="warning" ghost :loading="mcpKeyBusy">
                <template #icon><n-icon :component="RefreshIcon" /></template>{{ t("llm_settings.mcp_key_rotate") }}
              </n-button>
            </template>
            <div style="max-width: 360px">
              <div v-if="mcpKeyRecentlyUsed" style="font-weight: 600; margin-bottom: 6px">
                {{ t("llm_settings.mcp_key_rotate_in_use", { time: fmtDateTime(llm.mcp_api_key_last_used_at), ip: llm.mcp_api_key_last_used_ip || "—" }) }}
              </div>
              {{ t("llm_settings.mcp_key_rotate_confirm") }}
            </div>
          </n-popconfirm>
          <n-button v-else size="small" type="primary" :loading="mcpKeyBusy" @click="doRotateKey">
            <template #icon><n-icon :component="KeyIcon" /></template>{{ t("llm_settings.mcp_key_generate") }}
          </n-button>
        </n-space>
        <p class="hint">{{ t("llm_settings.mcp_key_hint") }}</p>
      </div>
    </template>

    <n-modal v-model:show="cfgShow" preset="card"
             :title="t('llm_settings.mcp_gen_title')" style="width: 760px; max-width: 94vw">
      <p class="hint" style="margin-top:0">{{ t("llm_settings.mcp_gen_hint") }}</p>
      <div v-for="cc in clientConfigs" :key="cc.id" class="cfg-block">
        <div class="cfg-head">
          <span class="cfg-name">{{ cc.name }}</span>
          <code class="cfg-file">{{ cc.file }}</code>
          <n-button size="tiny" @click="copyText(cc.code)">
            <template #icon><n-icon :component="CopyIcon" /></template>{{ t("common.copy") }}
          </n-button>
        </div>
        <pre class="cfg-code">{{ cc.code }}</pre>
      </div>
    </n-modal>
  </n-card>

  <n-card style="margin-top:16px">
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="20"><ToolsIcon /></n-icon>
        <span>{{ t("llm_settings.tools_title") }}</span>
      </n-space>
    </template>
    <p class="tools-cap">{{ t("llm_settings.tools_intro", { n: mcpTools.length, m: mcpMutating }) }}</p>
    <div class="tool-list">
      <div v-for="tool in mcpTools" :key="tool.name" class="tool-item">
        <div class="tool-head">
          <code class="tool-name">{{ tool.name }}</code>
          <n-tag v-if="tool.mutating" size="tiny" type="warning" :bordered="false">
            {{ t("llm_settings.tools_mutating") }}
          </n-tag>
          <n-tag v-else size="tiny" type="success" :bordered="false">
            {{ t("llm_settings.tools_readonly") }}
          </n-tag>
        </div>
        <div class="tool-desc">{{ tool.description }}</div>
        <div v-if="tool.params.length" class="tool-params">
          <span v-for="p in tool.params" :key="p.name" class="tool-param"
                :title="p.description">
            {{ p.name }}<span v-if="p.required" class="req">*</span>
            <em>{{ p.type }}</em>
          </span>
        </div>
      </div>
    </div>
  </n-card>
</template>

<style scoped>
label {
  display: block;
  font-size: 12px;
  margin-bottom: 4px;
  opacity: 0.8;
}
.times { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.time-row { display: flex; align-items: center; gap: 2px; }
.hint { margin: 4px 0 0; font-size: 12px; opacity: 0.6; line-height: 1.5; }
.tools-cap { margin: 0 0 12px; font-size: 13px; opacity: 0.7; }
.tool-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 10px; }
.tool-item { border: 1px solid var(--n-border-color, #eee); border-radius: 8px; padding: 10px 12px; }
.tool-head { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.tool-name { font-weight: 700; font-size: 13px; }
.tool-desc { font-size: 12.5px; opacity: 0.85; line-height: 1.5; }
.tool-params { margin-top: 6px; display: flex; flex-wrap: wrap; gap: 6px; }
.tool-param { font-size: 11px; background: rgba(128,128,128,0.1); border-radius: 5px; padding: 1px 6px; font-family: ui-monospace, monospace; }
.tool-param .req { color: #d03050; margin-left: 1px; }
.tool-param em { opacity: 0.55; font-style: normal; margin-left: 4px; }
/* 對外 MCP 卡片 */
.row { display: flex; align-items: center; gap: 12px; }
.row > label { margin-bottom: 0; }
.mcp-info { margin: 14px 0 4px; border: 1px solid var(--n-border-color, #eee); border-radius: 8px;
  padding: 10px 12px; background: rgba(128,128,128,0.04); }
.mcp-info-row { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; padding: 4px 0; font-size: 13px; }
.mcp-auth-hd { margin-bottom: 2px; }
/* 名稱／值 兩組欄位；auto 讓標籤只佔文字寬度，值欄可伸展 */
.mcp-auth-grid {
  display: grid; grid-template-columns: auto auto auto minmax(0, 1fr);
  gap: 6px 8px; align-items: center; margin: 2px 0 8px 12px;
}
.mcp-k { min-width: 96px; opacity: 0.65; font-size: 12.5px; }
.mcp-v { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12.5px;
  background: rgba(128,128,128,0.12); border-radius: 5px; padding: 2px 7px; word-break: break-all; }
.mcp-or { opacity: 0.5; font-size: 12px; }
.mcp-info-sub { padding-left: 14px; }
.mcp-kn { opacity: 0.6; font-size: 11.5px; }
.mcp-key { margin-top: 14px; }
/* 金鑰值/狀態框：與旁邊 small 按鈕同高（28px） */
.mcp-keybox { display: inline-flex; align-items: center; min-height: 28px; box-sizing: border-box;
  padding: 0 10px; border: 1px solid var(--n-border-color, rgba(128,128,128,0.3)); border-radius: 6px;
  background: rgba(128,128,128,0.06); font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12.5px; line-height: 1.4; word-break: break-all; }
.mcp-keybox--val { background: rgba(24,160,88,0.12); border-color: rgba(24,160,88,0.35); }
.mcp-keybox--none { color: #d0a215; background: rgba(208,162,21,0.08); border-color: rgba(208,162,21,0.4); }
.mcp-gen-row { margin-top: 8px; padding-top: 10px; align-items: center; gap: 10px;
  border-top: 1px dashed var(--n-border-color, rgba(128,128,128,.22)); }
.mcp-gen-row .mcp-or { font-size: 12px; opacity: .6; }
.cfg-block { margin-bottom: 14px; }
.cfg-head { display: flex; align-items: center; gap: 10px; margin-bottom: 5px; }
.cfg-name { font-weight: 600; font-size: 13px; }
.cfg-file { font-size: 11.5px; opacity: .6; }
.cfg-head .n-button { margin-left: auto; }
.cfg-code { margin: 0; padding: 10px 12px; border-radius: 8px; font-size: 12px; line-height: 1.5;
  background: rgba(128,128,128,.1); overflow-x: auto; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
</style>
