<script setup lang="ts">
/**
 * 使用者設定頁。
 *
 * 比 phpIPAM 改進：
 *  - 三個 tab，每個 tab 不超過 ~5-7 個選項，不堆一頁
 *  - TOTP 啟用流程內嵌 SVG QR code(不要逼使用者貼 URI)
 *  - Preferences 即時儲存到 /api/v1/me/preferences，不需手動按 save
 */
import { computed, h, onMounted, ref } from "vue";
import { apiErrMsg } from "@/api/client";
import { useI18n } from "vue-i18n";
import {
  NCard,
  NTabs,
  NTabPane,
  NSpace,
  NDescriptions,
  NDescriptionsItem,
  NSelect,
  NInputNumber,
  NInput,
  NButton,
  NAlert,
  NModal,
  NForm,
  NFormItem,
  NTag,
  NDataTable,
  NPopconfirm,
  useMessage,
  type DataTableColumns,
} from "naive-ui";
import { NIcon } from "naive-ui";
import { SettingsIcon, UsersIcon, LockIcon, KeyIcon, LogoutIcon, CancelIcon, DeleteIcon, CheckIcon } from "@/icons";
import TotpEnroll from "@/components/auth/TotpEnroll.vue";
import RecoveryCodes from "@/components/auth/RecoveryCodes.vue";
import { describeAgent, listMySessions, revokeMyOtherSessions, revokeMySession, type LoginSession } from "@/api/sessions";
import { fmtDateTime } from "@/utils/datetime";
import { autoSort } from "@/composables/useTableSort";
import { cmpNatural } from "@/utils/sort";
import { storeToRefs } from "pinia";
import { useAuthStore } from "@/stores/auth";
import { useUiStore } from "@/stores/ui";
import {
  getPreferences,
  updatePreferences,
} from "@/api/preferences";
import {
  type UserPreferences,
} from "@/api/preferences";
import * as totpApi from "@/api/totp";

const { t } = useI18n();
const auth = useAuthStore();
const ui = useUiStore();
const { me } = storeToRefs(auth);
const msg = useMessage();

// ── Preferences ──
const prefs = ref<UserPreferences | null>(null);
const prefsLoading = ref(false);

async function loadPrefs() {
  prefsLoading.value = true;
  try {
    prefs.value = await getPreferences();
    // locale 同步到 ui store；theme 不在這裡覆寫——以 ui store(localStorage) 為準，
    // 否則使用者剛在右上切換的佈景會在開設定頁時被後端舊值蓋回去。
    ui.setLocale(prefs.value.locale);
  } catch {
    msg.error(t("errors.network"));
  } finally {
    prefsLoading.value = false;
  }
}

async function patchPref<K extends keyof UserPreferences>(
  key: K,
  value: UserPreferences[K],
) {
  if (!prefs.value) return;
  prefs.value[key] = value;
  try {
    const updated = await updatePreferences({ [key]: value } as Partial<UserPreferences>);
    prefs.value = updated;
    if (key === "locale") ui.setLocale(value as "zh-TW" | "en-US" | "ja-JP");
    if (key === "theme") ui.setTheme(value as "light" | "dark" | "auto");
  } catch {
    msg.error(t("errors.network"));
  }
}

// ── TOTP enrollment ──
const enrollment = ref<{ secret: string; otpauth_uri: string } | null>(null);
const totpBusy = ref(false);
// 目前是否已啟用 TOTP（來自 /me；enroll confirm / disable 後會 fetchMe 更新）
const totpEnabled = computed(() => me.value?.totp_enabled ?? false);
// 管理員的政策要求這個帳號一定要開 → 不能停用
const totpRequired = computed(() => me.value?.mfa_required_by_policy ?? false);
// 剛產生的復原碼（啟用或重新產生之後顯示一次）
const shownCodes = ref<string[] | null>(null);

async function startEnroll() {
  totpBusy.value = true;
  try {
    enrollment.value = await totpApi.enroll();
  } catch {
    msg.error(t("errors.network"));
  } finally {
    totpBusy.value = false;
  }
}

async function confirmEnroll(code: string) {
  if (!enrollment.value) return;
  totpBusy.value = true;
  try {
    shownCodes.value = await totpApi.confirm(enrollment.value.secret, code);
    enrollment.value = null;
    await auth.fetchMe();
    msg.success(t("settings.security.totp_enabled_msg"));
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("settings.security.totp_invalid"));
  } finally {
    totpBusy.value = false;
  }
}

// 停用 2FA、重新產生復原碼都需要升級驗證（後端強制）：本機帳號輸密碼，外部認證帳號輸當前驗證碼。
const reauthShow = ref(false);
const reauthFor = ref<"disable" | "regenerate">("disable");
const reauthPw = ref("");
const reauthCode = ref("");
const isLocalAccount = computed(() => auth.me?.auth_provider === "local");
function openReauth(kind: "disable" | "regenerate") {
  reauthFor.value = kind;
  reauthPw.value = "";
  reauthCode.value = "";
  reauthShow.value = true;
}

async function submitReauth() {
  const body = isLocalAccount.value ? { password: reauthPw.value } : { code: reauthCode.value };
  if (!(isLocalAccount.value ? reauthPw.value : reauthCode.value)) {
    msg.warning(t("settings.security.disable_reauth_required"));
    return;
  }
  totpBusy.value = true;
  try {
    if (reauthFor.value === "disable") {
      await totpApi.disable(body);
      msg.success(t("settings.security.totp_disabled_msg"));
    } else {
      shownCodes.value = await totpApi.regenerateRecoveryCodes(body);
    }
    await auth.fetchMe();
    reauthShow.value = false;
  } catch (e) {
    msg.error(apiErrMsg(e));
  } finally {
    totpBusy.value = false;
  }
}

function cancelEnroll() {
  enrollment.value = null;
}

// ── 登入中的裝置（伺服器端工作階段）──
const loginSessions = ref<LoginSession[]>([]);
const sessionsLoading = ref(false);
async function loadSessions() {
  sessionsLoading.value = true;
  try { loginSessions.value = await listMySessions(); }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { sessionsLoading.value = false; }
}
async function revokeOne(s: LoginSession) {
  try { await revokeMySession(s.id); msg.success(t("settings.sessions.revoked")); await loadSessions(); }
  catch (e) { msg.error(apiErrMsg(e)); }
}
async function revokeOthers() {
  try {
    const n = await revokeMyOtherSessions();
    msg.success(t("settings.sessions.revoked_n", { n }));
    await loadSessions();
  } catch (e) { msg.error(apiErrMsg(e)); }
}
const sessionColumns = computed<DataTableColumns<LoginSession>>(() => autoSort<LoginSession>([
  { title: t("settings.sessions.device"), key: "user_agent", minWidth: 160,
    sorter: (a, b) => cmpNatural(describeAgent(a.user_agent), describeAgent(b.user_agent)),
    render: (r) => h("span", [describeAgent(r.user_agent),
      r.current ? h(NTag, { size: "small", type: "success", bordered: false, style: "margin-left:6px" },
                    () => t("settings.sessions.current")) : null]) },
  { title: "IP", key: "ip", width: 140, render: (r) => r.ip || "—" },
  { title: t("settings.sessions.method"), key: "method", width: 110,
    render: (r) => `${r.method}${r.mfa ? " + MFA" : ""}` },
  { title: t("settings.sessions.created"), key: "created_at", width: 170, render: (r) => fmtDateTime(r.created_at) },
  { title: t("settings.sessions.last_used"), key: "last_used_at", width: 170, render: (r) => fmtDateTime(r.last_used_at) },
  { title: t("common.actions"), key: "actions", width: 110, fixed: "right",
    render: (r) => r.current ? null : h(NPopconfirm, { onPositiveClick: () => revokeOne(r) }, {
      trigger: () => h(NButton, { size: "small", type: "error", ghost: true },
                       { default: () => t("settings.sessions.revoke"), icon: () => h(NIcon, { component: LogoutIcon }) }),
      default: () => t("settings.sessions.revoke_confirm"),
    }) },
]));

const localeOptions = [
  { label: "繁體中文", value: "zh-TW" },
  { label: "English", value: "en-US" },
  { label: "日本語", value: "ja-JP" },
];
const themeOptions = computed(() => [
  { label: t("settings.prefs.theme_light"), value: "light" },
  { label: t("settings.prefs.theme_dark"),  value: "dark"  },
  { label: t("settings.prefs.theme_auto"),  value: "auto"  },
]);
const calendarOptions = computed(() => [
  { label: t("settings.prefs.calendar_gregorian"), value: "gregorian" },
  { label: t("settings.prefs.calendar_minguo"),    value: "minguo"    },
]);

onMounted(() => {
  void loadPrefs();
  void loadSessions();
});
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><SettingsIcon /></n-icon>
        <span>{{ t("settings.title") }}</span>
      </n-space>
    </template>
    <n-tabs type="line" default-value="profile">
      <!-- Profile -->
      <n-tab-pane name="profile">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><UsersIcon /></n-icon>{{ t('settings.profile.tab') }}</span>
        </template>
        <n-descriptions v-if="me" bordered :column="1" label-placement="left"
                        label-style="width: 160px">
          <n-descriptions-item :label="t('settings.profile.username')">{{ me.username }}</n-descriptions-item>
          <n-descriptions-item :label="t('settings.profile.email')">{{ me.email }}</n-descriptions-item>
          <n-descriptions-item :label="t('settings.profile.display_name')">
            {{ me.display_name ?? "—" }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('settings.profile.auth_provider')">{{ me.auth_provider }}</n-descriptions-item>
          <n-descriptions-item :label="t('settings.profile.admin')">
            {{ me.is_admin ? t("common.yes") : t("common.no") }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('settings.profile.last_login')">
            {{ me.last_login_at ?? "—" }}
          </n-descriptions-item>
        </n-descriptions>
      </n-tab-pane>

      <!-- Security: TOTP -->
      <n-tab-pane name="security">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><LockIcon /></n-icon>{{ t('settings.security.tab') }}</span>
        </template>
        <n-space vertical :size="16">
          <n-alert type="info">
            <strong>{{ t("settings.security.totp_title") }}</strong>
            <span v-html="t('settings.security.totp_intro_html')"></span>
          </n-alert>

          <!-- 未在 enrollment 流程中：顯示目前狀態 + 對應的啟用 / 停用按鈕 -->
          <n-space v-if="!enrollment" align="center" :size="12">
            <span>{{ t("settings.security.totp_status") }}：</span>
            <n-tag :type="totpEnabled ? 'success' : 'default'" size="small" round :bordered="false">
              {{ totpEnabled ? t("settings.security.status_enabled") : t("settings.security.status_disabled") }}
            </n-tag>
            <n-tag v-if="totpRequired" size="small" type="warning" round :bordered="false">
              {{ t("settings.security.required_by_policy") }}
            </n-tag>
            <n-button v-if="!totpEnabled" type="primary" :loading="totpBusy" @click="startEnroll">
              <template #icon><n-icon :component="KeyIcon" /></template>{{ t("settings.security.enable_totp") }}
            </n-button>
            <n-button v-else type="error" ghost :loading="totpBusy" :disabled="totpRequired"
                      :title="totpRequired ? t('settings.security.required_by_policy_hint') : ''"
                      @click="openReauth('disable')">
              <template #icon><n-icon :component="DeleteIcon" /></template>{{ t("settings.security.disable_totp") }}
            </n-button>
          </n-space>

          <!-- 復原碼：剩幾組、重新產生 -->
          <n-space v-if="totpEnabled && !enrollment" align="center" :size="12" data-testid="recovery-status">
            <span>{{ t("recovery.remaining") }}：</span>
            <n-tag :type="(me?.recovery_codes_remaining ?? 0) > 2 ? 'default' : 'warning'" size="small" round
                   :bordered="false">{{ me?.recovery_codes_remaining ?? 0 }} / 10</n-tag>
            <n-button :loading="totpBusy" @click="openReauth('regenerate')">
              <template #icon><n-icon :component="KeyIcon" /></template>{{ t("recovery.regenerate") }}
            </n-button>
          </n-space>

          <!-- enrollment 流程中：顯示 QR + 驗證碼輸入（與登入頁的首次設定共用同一個元件） -->
          <totp-enroll v-else-if="enrollment" :enrollment="enrollment" :busy="totpBusy" cancellable
                       @confirm="confirmEnroll" @cancel="cancelEnroll" />

          <!-- 登入中的裝置 -->
          <n-card size="small" :title="t('settings.sessions.title')" data-testid="my-sessions">
            <template #header-extra>
              <n-popconfirm v-if="loginSessions.length > 1" @positive-click="revokeOthers">
                <template #trigger>
                  <n-button size="small" type="error" ghost data-testid="revoke-other-sessions">
                    <template #icon><n-icon :component="LogoutIcon" /></template>{{ t("settings.sessions.revoke_others") }}
                  </n-button>
                </template>
                {{ t("settings.sessions.revoke_others_confirm") }}
              </n-popconfirm>
            </template>
            <p class="hint">{{ t("settings.sessions.hint") }}</p>
            <n-data-table :columns="sessionColumns" :data="loginSessions" :loading="sessionsLoading" size="small"
                          :row-key="(r: LoginSession) => r.id" :scroll-x="820" />
          </n-card>
        </n-space>

        <n-modal v-model:show="reauthShow" preset="card" style="max-width: 460px"
                 :title="reauthFor === 'disable' ? t('settings.security.disable_totp') : t('recovery.regenerate')">
          <n-alert type="warning" :show-icon="true" style="margin-bottom: 12px">
            {{ reauthFor === "disable" ? t("settings.security.disable_confirm") : t("recovery.regenerate_confirm") }}
          </n-alert>
          <n-form label-placement="top">
            <n-form-item v-if="isLocalAccount" :label="t('account.current_pw')">
              <n-input v-model:value="reauthPw" type="password" show-password-on="click"
                       :placeholder="t('settings.security.disable_pw_ph')" @keyup.enter="submitReauth" />
            </n-form-item>
            <n-form-item v-else :label="t('settings.security.disable_code_label')">
              <n-input v-model:value="reauthCode" :maxlength="6"
                       :placeholder="t('settings.security.disable_code_ph')" @keyup.enter="submitReauth" />
            </n-form-item>
          </n-form>
          <template #footer>
            <n-space justify="end">
              <n-button type="error" ghost @click="reauthShow = false">
                <template #icon><n-icon :component="CancelIcon" /></template>{{ t("common.cancel") }}
              </n-button>
              <n-button :type="reauthFor === 'disable' ? 'error' : 'primary'" :loading="totpBusy" @click="submitReauth">
                <template #icon><n-icon :component="CheckIcon" /></template>
                {{ reauthFor === "disable" ? t("settings.security.disable_totp") : t("recovery.regenerate") }}
              </n-button>
            </n-space>
          </template>
        </n-modal>

        <n-modal :show="!!shownCodes" preset="card" style="max-width: 520px" :title="t('recovery.title')"
                 :closable="false" :mask-closable="false">
          <recovery-codes v-if="shownCodes" :codes="shownCodes" :account="me?.username" @done="shownCodes = null" />
        </n-modal>
      </n-tab-pane>

      <!-- Preferences -->
      <n-tab-pane name="preferences">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><SettingsIcon /></n-icon>{{ t('settings.prefs.tab') }}</span>
        </template>
        <n-space v-if="prefs" vertical :size="16" style="max-width: 480px">
          <div>
            <label>{{ t("settings.prefs.language") }}</label>
            <n-select
              :value="prefs.locale"
              :options="localeOptions"
              @update:value="(v: any) => patchPref('locale', v)"
            />
          </div>
          <div>
            <label>{{ t("settings.prefs.theme") }}</label>
            <n-select
              :value="ui.theme"
              :options="themeOptions"
              @update:value="(v: any) => patchPref('theme', v)"
            />
          </div>
          <div>
            <label>{{ t("settings.prefs.calendar") }}</label>
            <n-select
              :value="prefs.calendar"
              :options="calendarOptions"
              @update:value="(v: any) => patchPref('calendar', v)"
            />
          </div>
          <div>
            <label>{{ t("settings.prefs.timezone") }}</label>
            <n-input
              :value="prefs.timezone"
              placeholder="Asia/Taipei"
              @update:value="(v: any) => patchPref('timezone', v)"
            />
          </div>
          <div>
            <label>{{ t("settings.prefs.page_size") }}</label>
            <n-input-number
              :value="prefs.page_size"
              :min="10"
              :max="500"
              @update:value="(v: any) => patchPref('page_size', v)"
            />
          </div>
        </n-space>
        <p v-else style="opacity: 0.7">{{ t("common.loading") }}</p>
      </n-tab-pane>

      <!-- LLM (admin only) -->
    </n-tabs>
  </n-card>
</template>

<style scoped>
.hint {
  margin: 0 0 8px;
  font-size: 12px;
  opacity: 0.7;
}
label {
  display: block;
  font-size: 12px;
  margin-bottom: 4px;
  opacity: 0.8;
}
</style>
