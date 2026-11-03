<script setup lang="ts">
/**
 * 管理 → 戰情室設定（2026-10-21）：播放清單、輪播、分組、門檻、語言、動畫，以及電視用的顯示權杖。
 * 權杖不隨設定回傳：按「顯示」或「複製電視網址」時才向後端取，每次都留稽核（與機櫃嵌入、Graylog DSV 一樣）。
 */
import { computed, h, onMounted, ref, watch } from "vue";
import { warroomKeyRows } from "@/utils/warroomKeys";
import { useI18n } from "vue-i18n";
import {
  NAlert, NButton, NCard, NFormItem, NIcon, NInputNumber, NPopconfirm, NSelect, NSpace, NSwitch,
  NTag, useMessage,
  type SelectOption,
} from "naive-ui";
import { AiIcon, CopyIcon, DeleteIcon, EyeIcon, RefreshIcon, SaveIcon, ScreensIcon } from "@/icons";
import { apiErrMsg } from "@/api/client";
import { listUsers } from "@/api/admin";
import {
  getWarroomSettings, putWarroomSettings, revealWarroomToken, revokeWarroomToken, rotateWarroomToken,
  searchWarroomScope, type ScopeKind, type ScopeOption, type WarroomSettings,
} from "@/api/warroom";
import { fmtDate } from "@/utils/datetime";
import { loadNumberFont, NUMBER_FONTS } from "@/views/warroom/warroomFonts";
import WrsIntelSettings from "@/views/warroom/WrsIntelSettings.vue";

const { t } = useI18n();
const msg = useMessage();

const s = ref<WarroomSettings | null>(null);
const saving = ref(false);
const token = ref("");
const tokenDays = ref(365);
const users = ref<{ label: string; value: string }[]>([]);

const keyRows = computed(() => warroomKeyRows(s.value?.pages?.length || 7));
const windowOpts = computed(() => ["24h", "7d", "30d", "90d"].map((v) => ({ label: t(`warroom_settings.aw_${v}`), value: v })));
const transOpts = computed(() => ["slide", "fade", "zoom", "rise", "wipe", "flip"]
  .map((v) => ({ label: t(`warroom_settings.tr_${v}`), value: v })));
// 字體選單：每個選項用它自己的字體畫一串數字，看得出長什麼樣子（字檔在選到或打開選單時才載）
const FONT_KEYS = ["inter", "orbitron", "exo2", "rajdhani", "share-tech-mono", "audiowide"];
const fontReady = ref(0);
async function preloadFonts() {
  await Promise.all(FONT_KEYS.map((k) => loadNumberFont(k)));
  fontReady.value++;
}
const fontOpts = computed(() => (void fontReady.value, FONT_KEYS.map((v) => ({ label: t(`warroom_settings.nf_${v}`), value: v }))));
function renderFontLabel(o: SelectOption) {
  const f = NUMBER_FONTS[String(o.value)] ?? NUMBER_FONTS.inter;
  return h("span", { style: "display:inline-flex;align-items:baseline;gap:12px" }, [
    h("span", { style: `font-family:"${f.family}",sans-serif;font-size:18px;letter-spacing:.02em` }, "1,367 78%"),
    h("span", { style: "opacity:.7;font-size:12px" }, String(o.label)),
  ]);
}
const groupOpts = computed(() => ["location", "section", "customer"].map((v) => ({ label: t(`warroom_settings.group_${v}`), value: v })));
const langOpts = computed(() => [
  { label: t("warroom_settings.language_follow"), value: "" },
  { label: "繁體中文", value: "zh-TW" }, { label: "English", value: "en-US" }, { label: "日本語", value: "ja-JP" },
]);
const dayOpts = computed(() => [30, 90, 180, 365].map((d) => ({ label: t("llm_settings.mcp_key_days_n", { n: d }), value: d })));
const tokenExpired = computed(() => !!s.value?.token_expires_at && new Date(s.value.token_expires_at).getTime() <= Date.now());
const tvUrl = computed(() => (token.value ? `${location.origin}/warroom?display=${encodeURIComponent(token.value)}` : ""));

/** 顯示範圍：每一種的選項＝已選的（名稱來自後端）＋目前搜尋結果 */
const SCOPE_KINDS: ScopeKind[] = ["customers", "sections", "subnets"];

const scopeOpts = ref<Record<ScopeKind, ScopeOption[]>>({ customers: [], sections: [], subnets: [] });
const scopeLoading = ref<Record<ScopeKind, boolean>>({ customers: false, sections: false, subnets: false });
const scopeTimers: Partial<Record<ScopeKind, ReturnType<typeof setTimeout>>> = {};
function mergeOpts(a: ScopeOption[], b: ScopeOption[]): ScopeOption[] {
  const seen = new Set<string>();
  return [...a, ...b].filter((o) => (seen.has(o.value) ? false : (seen.add(o.value), true)));
}
function selectedOpts(kind: ScopeKind): ScopeOption[] {
  const ids = new Set(s.value?.scope?.[kind] ?? []);
  return scopeOpts.value[kind].filter((o) => ids.has(o.value));
}
function searchScope(kind: ScopeKind, q: string) {
  if (scopeTimers[kind]) clearTimeout(scopeTimers[kind]);
  scopeTimers[kind] = setTimeout(async () => {
    scopeLoading.value[kind] = true;
    try {
      scopeOpts.value[kind] = mergeOpts(selectedOpts(kind), await searchWarroomScope(kind, q));
    } catch (e) { msg.error(apiErrMsg(e)); }
    finally { scopeLoading.value[kind] = false; }
  }, 250);
}
function applyLabels(v: WarroomSettings) {
  for (const k of SCOPE_KINDS) scopeOpts.value[k] = mergeOpts(v.scope_labels?.[k] ?? [], scopeOpts.value[k]);
}
const savedDisplay = ref(false);
const needPrincipal = computed(() => !!s.value?.display_enabled && !s.value?.principal_user_id);
const scopeOn = computed(() => SCOPE_KINDS.some((k) => (s.value?.scope?.[k] ?? []).length));

async function load() {
  try {
    s.value = await getWarroomSettings();
    savedDisplay.value = s.value.display_enabled;
    applyLabels(s.value);
    for (const k of SCOPE_KINDS) searchScope(k, "");
    const page = await listUsers("", "", 200, 0);
    users.value = page.items.filter((u) => u.is_active).map((u) => ({
      label: u.display_name ? `${u.display_name}（${u.username}）` : u.username, value: String(u.id),
    }));
  } catch (e) { msg.error(apiErrMsg(e)); }
}

/* 播放清單：拖拉排序＋開關（10-22 使用者：「清單要可以改變順序，參考別的頁面」→ 照主機名稱來源順序頁）。
 * order 是畫面上的順序（要播的在前、照存的順序；不播的接在後面），存檔時只送要播的、照這個順序。
 * 電視上的編號與數字鍵都照播放順序（01＝第一個播的）。 */
const order = ref<string[]>([]);
const dragIndex = ref<number | null>(null);
watch(() => s.value?.all_pages, () => {
  if (s.value) order.value = [...s.value.pages, ...s.value.all_pages.filter((k) => !s.value!.pages.includes(k))];
}, { immediate: true });
function togglePage(key: string, on: boolean) {
  if (!s.value) return;
  const cur = s.value.pages;
  s.value.pages = order.value.filter((k) => (k === key ? on : cur.includes(k)));
}
function onDropPage(i: number) {
  if (!s.value || dragIndex.value === null || dragIndex.value === i) return;
  const arr = [...order.value];
  const [moved] = arr.splice(dragIndex.value, 1);
  arr.splice(i, 0, moved);
  order.value = arr;
  const cur = s.value.pages;
  s.value.pages = arr.filter((k) => cur.includes(k));
  dragIndex.value = null;
}
/** 資訊展示模式下不播放的主題（整頁都是問題；後端 warroom/info_mode.HIDDEN_PAGES） */
const INFO_HIDDEN = ["anomalies", "collection", "availability", "ai_audit"];
const infoHidden = (k: string) => !!s.value?.info_mode && INFO_HIDDEN.includes(k);
const playNumber = (k: string) => {
  // 編號照電視上的順序：資訊展示模式下不播放的主題不佔號碼
  const shown = (s.value?.pages ?? []).filter((p) => !infoHidden(p));
  const i = shown.indexOf(k);
  return i >= 0 ? String(i + 1).padStart(2, "0") : "–";
};

async function save() {
  if (!s.value) return;
  saving.value = true;
  try {
    const { all_pages: _a, ready_pages: _r, token_set: _ts, token_expires_at: _te, scope_labels: _sl, ...body } = s.value;
    s.value = await putWarroomSettings(body);
    savedDisplay.value = s.value.display_enabled;
    applyLabels(s.value);
    msg.success(t("common.saved"));
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { saving.value = false; }
}

async function rotate() {
  try {
    const r = await rotateWarroomToken(tokenDays.value);
    token.value = r.token;
    if (s.value) { s.value.token_set = true; s.value.token_expires_at = r.expires_at; }
    msg.success(t("warroom_settings.token_rotated"));
  } catch (e) { msg.error(apiErrMsg(e)); }
}
async function reveal() {
  try { token.value = (await revealWarroomToken()).token; } catch (e) { msg.error(apiErrMsg(e)); }
}
async function revoke() {
  try {
    await revokeWarroomToken();
    token.value = "";
    await load();
  } catch (e) { msg.error(apiErrMsg(e)); }
}
async function copyUrl() {
  if (!token.value) await reveal();
  if (!tvUrl.value) return;
  await navigator.clipboard.writeText(tvUrl.value);
  msg.success(t("common.copied"));
}
function openWarroom() {
  window.open("/warroom", "_blank", "noopener");
}

onMounted(load);
</script>

<template>
  <div class="wrs">
    <n-card size="small">
      <template #header>
        <n-space align="center" :wrap-item="false">
          <n-icon :size="22"><ScreensIcon /></n-icon>
          <span>{{ t("nav.warroom_settings") }}</span>
        </n-space>
      </template>
      <template #header-extra>
        <n-button size="small" type="info" @click="openWarroom">
          <template #icon><n-icon :component="ScreensIcon" /></template>{{ t("warroom_settings.open") }}
        </n-button>
      </template>
      <p class="wrs-intro">{{ t("warroom_settings.intro") }}</p>
      <template v-if="s">
        <n-form-item :label="t('warroom_settings.enabled')" label-placement="left">
          <n-switch v-model:value="s.enabled" />
          <span class="wrs-hint">{{ t("warroom_settings.enabled_hint") }}</span>
        </n-form-item>

        <!-- 資訊展示模式（11-03）：有的單位不允許看到紅字或問題 -->
        <n-form-item :label="t('warroom_settings.info_mode')" label-placement="left">
          <n-switch v-model:value="s.info_mode" data-testid="wrs-info-mode" />
          <span class="wrs-hint">{{ t("warroom_settings.info_mode_hint") }}</span>
        </n-form-item>

        <n-form-item :label="t('warroom_settings.pages')">
          <div class="wrs-playlist" data-testid="wrs-playlist">
            <div class="wrs-hint">{{ t("warroom_settings.pages_drag_hint") }}</div>
            <ul class="rank-list">
              <li v-for="(k, i) in order" :key="k" class="rank-item" data-testid="wrs-page"
                  :class="{ dragging: dragIndex === i, disabled: !s.pages.includes(k) || infoHidden(k) }"
                  draggable="true" @dragstart="dragIndex = i" @dragover.prevent @drop="onDropPage(i)"
                  @dragend="dragIndex = null">
                <span class="rank-num">{{ playNumber(k) }}</span>
                <span class="rank-handle">⠿</span>
                <span class="rank-label">{{ t(`warroom.nav_${k}`) }}</span>
                <n-tag v-if="!s.ready_pages.includes(k)" size="tiny" :bordered="false">{{ t("warroom_settings.not_ready") }}</n-tag>
                <n-tag v-if="infoHidden(k)" size="tiny" :bordered="false" data-testid="wrs-info-hidden">{{ t("warroom_settings.info_mode_hidden") }}</n-tag>
                <n-switch :value="s.pages.includes(k)" :disabled="!s.ready_pages.includes(k)" size="small"
                          style="margin-left: auto" @update:value="(v: boolean) => togglePage(k, v)">
                  <template #checked>{{ t("warroom_settings.page_on") }}</template>
                  <template #unchecked>{{ t("warroom_settings.page_off") }}</template>
                </n-switch>
              </li>
            </ul>
          </div>
        </n-form-item>

        <n-form-item :label="t('warroom_settings.scope')">
          <div class="wrs-scope">
            <div v-for="k in SCOPE_KINDS" :key="k" class="wrs-scope__row">
              <span class="wrs-scope__k">{{ t(`warroom_settings.scope_${k}`) }}</span>
              <n-select v-model:value="s.scope[k]" multiple filterable remote clearable
                        :options="scopeOpts[k]" :loading="scopeLoading[k]" max-tag-count="responsive"
                        :placeholder="t('warroom_settings.scope_all')" :data-testid="`wrs-scope-${k}`"
                        @search="(q: string) => searchScope(k, q)" />
            </div>
            <n-alert v-if="scopeOn" type="info" :show-icon="false" class="wrs-scope__note">{{ t("warroom_settings.scope_note") }}</n-alert>
            <p v-else class="wrs-hint wrs-scope__hint">{{ t("warroom_settings.scope_hint") }}</p>
          </div>
        </n-form-item>

        <div class="wrs-grid">
          <n-form-item :label="t('warroom_settings.dwell')">
            <n-input-number v-model:value="s.dwell_seconds" :min="5" :max="600">
              <template #suffix>{{ t("warroom_settings.seconds") }}</template>
            </n-input-number>
          </n-form-item>
          <n-form-item :label="t('warroom_settings.max_subpages')">
            <n-input-number v-model:value="s.max_subpages" :min="1" :max="50" />
          </n-form-item>
          <n-form-item :label="t('warroom_settings.group_by')">
            <n-select v-model:value="s.group_by" :options="groupOpts" />
          </n-form-item>
          <n-form-item :label="t('warroom_settings.availability_window')">
            <n-select v-model:value="s.availability_window" :options="windowOpts" data-testid="wrs-availability-window" />
          </n-form-item>
          <n-form-item :label="t('warroom_settings.transition')">
            <n-select v-model:value="s.transition" :options="transOpts" data-testid="wrs-transition" />
          </n-form-item>
          <n-form-item :label="t('warroom_settings.number_font')">
            <n-select v-model:value="s.number_font" :options="fontOpts" :render-label="renderFontLabel"
                      data-testid="wrs-number-font" @update:show="(v: boolean) => v && preloadFonts()" />
          </n-form-item>
          <n-form-item :label="t('warroom_settings.language')">
            <n-select v-model:value="s.language" :options="langOpts" />
          </n-form-item>
          <n-form-item :label="t('warroom_settings.warn')">
            <n-input-number v-model:value="s.capacity_warn" :min="1" :max="99"><template #suffix>%</template></n-input-number>
          </n-form-item>
          <n-form-item :label="t('warroom_settings.crit')">
            <n-input-number v-model:value="s.capacity_crit" :min="2" :max="100"><template #suffix>%</template></n-input-number>
          </n-form-item>
        </div>
        <p class="wrs-hint wrs-hint--block">{{ t("warroom_settings.dwell_hint") }}</p>
        <p class="wrs-hint wrs-hint--block">{{ t("warroom_settings.max_subpages_hint") }}</p>
        <!-- 現場按鍵：跟電視上按 H 看到的是同一份（utils/warroomKeys.ts），不另寫一套 -->
        <div class="wrs-keys">
          <div class="wrs-keys__t">{{ t("warroom_settings.keys_title") }}</div>
          <div class="wrs-keys__grid">
            <template v-for="row in keyRows" :key="row.label">
              <span class="wrs-keys__k"><template v-for="(k, i) in row.keys" :key="k"><span v-if="row.range && i">–</span><kbd>{{ k }}</kbd></template></span>
              <span>{{ t(row.label) }}</span>
            </template>
          </div>
          <div class="wrs-hint wrs-keys__note">{{ t("warroom_settings.keys_clicker") }}</div>
        </div>

        <n-form-item :label="t('warroom_settings.reduce_motion')" label-placement="left">
          <n-switch v-model:value="s.reduce_motion" />
        </n-form-item>

        <n-button type="primary" :loading="saving" @click="save">
          <template #icon><n-icon :component="SaveIcon" /></template>{{ t("common.save") }}
        </n-button>
      </template>
    </n-card>

    <!-- 12 AI 情報：設定做在這裡（使用者：不要另做設計稿的設定頁），跟其他設定一起存 -->
    <n-card v-if="s?.intel" size="small" class="wrs-card">
      <template #header>
        <n-space align="center" :wrap-item="false">
          <n-icon :size="22"><AiIcon /></n-icon>
          <span data-testid="wrs-intel-title">{{ t("warroom_settings.intel_title") }}</span>
        </n-space>
      </template>
      <WrsIntelSettings v-model="s.intel" :info-mode="s.info_mode" />
      <n-button type="primary" :loading="saving" data-testid="wrs-intel-save" @click="save">
        <template #icon><n-icon :component="SaveIcon" /></template>{{ t("common.save") }}
      </n-button>
    </n-card>

    <n-card v-if="s" :title="t('warroom_settings.display_title')" size="small" class="wrs-card">
      <n-alert type="warning" :bordered="false" class="wrs-alert">{{ t("warroom_settings.display_hint") }}</n-alert>
      <n-form-item :label="t('warroom_settings.display_enabled')" label-placement="left">
        <n-space :size="10" align="center">
          <n-switch v-model:value="s.display_enabled" />
          <!-- 開關是畫面上的值；實際生效的是存下來的值（10-21 使用者打開開關但沒存成功，電視顯示「已停用」看不出原因） -->
          <n-tag size="small" :bordered="false" :type="savedDisplay ? 'success' : 'default'" data-testid="wrs-display-saved">
            {{ savedDisplay ? t("warroom_settings.display_saved_on") : t("warroom_settings.display_saved_off") }}
          </n-tag>
          <n-tag v-if="s.display_enabled !== savedDisplay" size="small" :bordered="false" type="warning">
            {{ t("warroom_settings.unsaved") }}
          </n-tag>
        </n-space>
      </n-form-item>
      <n-form-item :label="t('warroom_settings.principal')">
        <n-select v-model:value="s.principal_user_id" :options="users" filterable clearable
                  :placeholder="t('warroom_settings.principal_ph')" style="max-width: 420px" />
      </n-form-item>
      <p class="wrs-hint wrs-hint--block">{{ t("warroom_settings.principal_hint") }}</p>
      <n-alert v-if="needPrincipal" type="error" :bordered="false" class="wrs-alert" data-testid="wrs-need-principal">
        {{ t("warroom_settings.principal_required") }}
      </n-alert>
      <n-button type="primary" :loading="saving" :disabled="needPrincipal" @click="save">
        <template #icon><n-icon :component="SaveIcon" /></template>{{ t("common.save") }}
      </n-button>

      <div class="wrs-token">
        <div class="wrs-token__row">
          <span class="wrs-token__k">{{ t("warroom_settings.token") }}</span>
          <code v-if="token" class="wrs-token__v">{{ token }}</code>
          <span v-else class="wrs-token__v wrs-muted">{{ s.token_set ? "••••••••••••" : t("warroom_settings.token_none") }}</span>
          <n-tag v-if="s.token_set && s.token_expires_at" size="small" :bordered="false" :type="tokenExpired ? 'error' : 'default'">
            {{ tokenExpired ? t("warroom_settings.token_expired", { date: fmtDate(s.token_expires_at) })
                            : t("warroom_settings.token_expires", { date: fmtDate(s.token_expires_at) }) }}
          </n-tag>
        </div>
        <n-space :size="8" align="center">
          <n-select v-model:value="tokenDays" :options="dayOpts" size="small" style="width: 130px" />
          <n-popconfirm v-if="s.token_set" @positive-click="rotate">
            <template #trigger>
              <n-button size="small" type="warning" ghost>
                <template #icon><n-icon :component="RefreshIcon" /></template>{{ t("warroom_settings.regenerate") }}
              </n-button>
            </template>
            {{ t("warroom_settings.regenerate_confirm") }}
          </n-popconfirm>
          <n-button v-else size="small" type="primary" @click="rotate">
            <template #icon><n-icon :component="RefreshIcon" /></template>{{ t("warroom_settings.generate") }}
          </n-button>
          <n-button v-if="s.token_set && !token" size="small" @click="reveal">
            <template #icon><n-icon :component="EyeIcon" /></template>{{ t("warroom_settings.reveal") }}
          </n-button>
          <n-button v-if="s.token_set" size="small" @click="copyUrl">
            <template #icon><n-icon :component="CopyIcon" /></template>{{ t("warroom_settings.copy_url") }}
          </n-button>
          <n-popconfirm v-if="s.token_set" @positive-click="revoke">
            <template #trigger>
              <n-button size="small" type="error" ghost>
                <template #icon><n-icon :component="DeleteIcon" /></template>{{ t("warroom_settings.revoke") }}
              </n-button>
            </template>
            {{ t("warroom_settings.revoke_confirm") }}
          </n-popconfirm>
        </n-space>
        <code v-if="tvUrl" class="wrs-url">{{ tvUrl }}</code>
        <!-- 電視每天關機（10-23）：用網址開一次就記住，隔天開機不用再帶權杖 -->
        <p v-if="s.token_set" class="wrs-hint wrs-hint--block" data-testid="wrs-url-hint">{{ t("warroom_settings.url_hint") }}</p>
      </div>
    </n-card>
  </div>
</template>

<style scoped>
.wrs { display: flex; flex-direction: column; gap: 14px; }
.wrs-intro { margin: 0 0 12px; opacity: .8; line-height: 1.7; }
.wrs-hint { margin-left: 12px; opacity: .7; font-size: 13px; }
.wrs-keys { margin: 4px 0 16px; padding: 12px 14px; border: 1px solid rgba(128, 128, 128, .22); border-radius: 8px; max-width: 760px; }
.wrs-keys__t { font-weight: 600; font-size: 13px; margin-bottom: 8px; }
.wrs-keys__grid { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 7px 16px; align-items: center; font-size: 13px; }
.wrs-keys__k { display: inline-flex; gap: 4px; align-items: center; white-space: nowrap; }
.wrs-keys kbd { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; min-width: 22px; text-align: center;
  padding: 1px 6px; border: 1px solid rgba(128, 128, 128, .45); border-bottom-width: 2px; border-radius: 4px; }
.wrs-keys__note { margin: 8px 0 0; }
.wrs-scope { display: grid; gap: 8px; width: 100%; max-width: 760px; }
.wrs-scope__row { display: grid; grid-template-columns: 72px minmax(0, 1fr); gap: 10px; align-items: center; }
.wrs-scope__k { font-size: 13px; opacity: .8; }
.wrs-scope__hint { margin: 0; }
@media (max-width: 640px) { .wrs-scope__row { grid-template-columns: 1fr; gap: 4px; } }
.wrs-hint--block { margin: -6px 0 14px; display: block; }
.wrs-playlist { width: 100%; max-width: 460px; }
/* 照主機名稱來源順序頁（HostnamePrecedence.vue）的拖拉清單 */
.rank-list { list-style: none; padding: 0; margin: 6px 0 0; }
.rank-item { display: flex; align-items: center; gap: 12px; padding: 9px 14px; margin: 6px 0;
  border: 1px solid var(--n-border-color, rgba(127,127,127,.2)); border-radius: 8px;
  background: var(--n-card-color, rgba(127,127,127,.04)); cursor: grab; user-select: none; }
.rank-item.dragging { opacity: .5; }
.rank-item.disabled { opacity: .5; }
.rank-item.disabled .rank-num { background: var(--n-text-color-3, #888); }
.rank-item:active { cursor: grabbing; }
.rank-num { min-width: 26px; height: 22px; padding: 0 4px; display: inline-flex; align-items: center; justify-content: center;
  border-radius: 11px; background: var(--primary-color, #18a058); color: #fff; font-size: 12px; font-weight: 600; flex: 0 0 auto; }
.rank-handle { opacity: .5; font-size: 16px; }
.rank-label { font-weight: 500; }
.wrs-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 0 18px; }
.wrs-card { margin-top: 0; }
.wrs-alert { margin-bottom: 14px; }
.wrs-token { margin-top: 18px; padding-top: 14px; border-top: 1px dashed rgba(128, 128, 128, .3); display: grid; gap: 10px; }
.wrs-token__row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.wrs-token__k { font-weight: 600; }
.wrs-token__v { font-family: ui-monospace, monospace; word-break: break-all; }
.wrs-muted { opacity: .6; }
.wrs-url { display: block; font-family: ui-monospace, monospace; font-size: 12.5px; word-break: break-all;
  background: rgba(128, 128, 128, .1); padding: 8px 10px; border-radius: 6px; }
</style>
