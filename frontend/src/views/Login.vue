<script setup lang="ts">
import { safeNextPath } from "@/utils/safeRedirect";
import { computed, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { useI18n } from "vue-i18n";
import {
  NCard,
  NForm,
  NFormItem,
  NInput,
  NSelect,
  NButton,
  NIcon,
  NSpace,
  NAlert,
  NDivider,
  NDropdown,
} from "naive-ui";
import { storeToRefs } from "pinia";
import { useAuthStore } from "@/stores/auth";
import { useUiStore } from "@/stores/ui";
import { apiClient } from "@/api/client";
import { CancelIcon, CheckIcon, LoginIcon } from "@/icons";
import TotpEnroll from "@/components/auth/TotpEnroll.vue";
import RecoveryCodes from "@/components/auth/RecoveryCodes.vue";
import { ShieldCheck, Globe } from "@iconoir/vue";

const { t, te } = useI18n();
const route = useRoute();
const auth = useAuthStore();
const { mfaToken, mfaSetup, pendingRecoveryCodes } = storeToRefs(auth);

// 登入頁語言切換（未登入，不寫回後端偏好）：點開下拉再選，不是一按就切
const ui = useUiStore();
const { locale } = storeToRefs(ui);
const localeMenuOptions = [
  { label: "繁體中文", key: "zh-TW" },
  { label: "English", key: "en-US" },
  { label: "日本語", key: "ja-JP" },
];
const currentLocaleLabel = computed(
  () => localeMenuOptions.find((o) => o.key === locale.value)?.label ?? "",
);
function pickLocale(k: string | number) {
  ui.setLocale(String(k) as "zh-TW" | "en-US" | "ja-JP", false);
}

const username = ref("");
const password = ref("");
const code = ref("");
const loading = ref(false);
const errorMsg = ref<string | null>(null);

// 領域（PVE 風）：本機 / LDAP，預設本機
const realm = ref("local");
const realms = ref<{ label: string; value: string; label_key?: string }[]>(
  [{ label: "本機", value: "local", label_key: "login.realm_local" }]);
// 登入頁可以切語言，所以顯示用的標籤要是 computed。後端給的 `label` 只是沒有翻譯鍵時的退路
const realmOptions = computed(() => realms.value.map(
  (r) => ({ value: r.value, label: r.label_key && te(r.label_key) ? t(r.label_key) : r.label })));
// 只在後端回報該 SSO 供應商已啟用時才顯示對應按鈕，避免點了未設定的 SSO 跳出原始錯誤
const ssoAvail = ref<{ oidc: boolean; saml: boolean }>({ oidc: false, saml: false });
// 先用上次快取的 realms / sso 立刻渲染（避免冷啟動時一閃才出現／沒出現），再向後端刷新
try {
  const cached = JSON.parse(localStorage.getItem("jtipam.realms") || "null");
  if (Array.isArray(cached) && cached.length) realms.value = cached;
  const cachedSso = JSON.parse(localStorage.getItem("jtipam.sso") || "null");
  if (cachedSso && typeof cachedSso === "object") {
    ssoAvail.value = { oidc: !!cachedSso.oidc, saml: !!cachedSso.saml };
  }
} catch { /* ignore */ }
onMounted(async () => {
  // SSO（OIDC / SAML）回來：後端已經把工作階段放進 HttpOnly Cookie，網址只帶 #sso=1
  // （以前把權杖放在網址上）。MFA 政策套用到 SSO 時帶回的是 MFA 挑戰（#mfa_token=…）。
  const frag = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  if (frag.has("sso") || frag.has("mfa_token")) {
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
  }
  if (frag.get("mfa_token")) {
    auth.startSecondStep({
      access_token: null, refresh_token: null, token_type: "bearer", expires_in: null,
      mfa_required: frag.get("mfa_setup") !== "1", mfa_token: frag.get("mfa_token"),
      mfa_setup_required: frag.get("mfa_setup") === "1",
    });
  } else if (frag.get("sso") === "1") {
    try {
      if (await auth.ensureSession()) {
        await auth.fetchMe();
        window.location.assign(targetAfterLogin());
        return;
      }
      errorMsg.value = t("login.failed");
    } catch {
      errorMsg.value = t("login.failed");
    }
  }
  try {
    const { data } = await apiClient.get<{
      realms: { label: string; value: string; label_key?: string }[];
      sso?: { oidc: boolean; saml: boolean };
    }>("/api/v1/auth/realms");
    if (data.realms?.length) {
      realms.value = data.realms;
      localStorage.setItem("jtipam.realms", JSON.stringify(data.realms));
    }
    if (data.sso) {
      ssoAvail.value = { oidc: !!data.sso.oidc, saml: !!data.sso.saml };
      localStorage.setItem("jtipam.sso", JSON.stringify(ssoAvail.value));
    }
  } catch { /* 預設只有本機 */ }
});

// 只接受本站路徑：`//evil.example` 這類也以 / 開頭的會被當成別的網站（開放式轉址，見 utils/safeRedirect）
function targetAfterLogin(): string {
  return safeNextPath(route.query.next);
}


/**
 * 登入失敗的原因要說對。
 *
 * 後端整個掛掉（502／503）時，畫面原本仍顯示「請確認帳號密碼」—— 把伺服器故障
 * 說成使用者打錯密碼，於是人會一直重打、懷疑自己的帳號，而真正的問題在別處。
 * 只有後端明確說「憑證錯誤」（401）時才可以這樣講。
 */
function loginErrorMessage(err: unknown): string {
  const st = (err as { response?: { status?: number } })?.response?.status;
  if (st === 401 || st === 400) return t("login.failed");        // 後端真的驗過了
  if (st === 429) return t("login.too_many");
  if (st === 423) return t("login.locked");
  if (st === undefined) return t("login.unreachable");           // 連不上（網路／服務未啟動）
  if (st >= 500) return t("login.server_down", { code: st });    // 服務有問題，不是密碼問題
  return t("login.failed");
}

async function submitLogin() {
  errorMsg.value = null;
  loading.value = true;
  try {
    const res = await auth.login(username.value, password.value, realm.value);
    if (!res.mfa_required && !res.mfa_setup_required) {
      // 整頁載入(非 SPA 導向)：以新 token 全新啟動，清掉前一個 session 殘留的
      // 模組級快取 / loading 旗標，避免登入後某些功能因舊狀態出錯、要切頁才好。
      window.location.assign(targetAfterLogin());
    }
  } catch (err: unknown) {
    errorMsg.value = loginErrorMessage(err);
  } finally {
    loading.value = false;
  }
}

async function submitMfa() {
  errorMsg.value = null;
  loading.value = true;
  try {
    await auth.verifyMfa(code.value);
    window.location.assign(targetAfterLogin());
  } catch (err: unknown) {
    const st = (err as { response?: { status?: number; data?: { detail?: unknown } } })?.response;
    // 驗證碼錯太多次會鎖定帳號（跟密碼錯一樣）：講清楚，不要讓人以為只是打錯
    errorMsg.value = st?.data?.detail === "Account temporarily locked" ? t("login.locked") : t("login.mfa_failed");
  } finally {
    loading.value = false;
  }
}

// 管理員要求 MFA、還沒設定：登入時先設定（不會被鎖在外面），完成後顯示復原碼
const setupEnrollment = ref<{ secret: string; otpauth_uri: string } | null>(null);
async function loadSetup() {
  errorMsg.value = null;
  try {
    setupEnrollment.value = await auth.beginMfaSetup();
  } catch {
    errorMsg.value = t("login.mfa_setup_expired");
  }
}
watch([mfaToken, mfaSetup], ([tok, setup]) => {
  if (tok && setup && !setupEnrollment.value) void loadSetup();
}, { immediate: true });
async function confirmSetup(c: string) {
  if (!setupEnrollment.value) return;
  errorMsg.value = null;
  loading.value = true;
  try {
    await auth.confirmMfaSetup(setupEnrollment.value.secret, c);
    setupEnrollment.value = null;
  } catch {
    errorMsg.value = t("settings.security.totp_invalid");
  } finally {
    loading.value = false;
  }
}
function recoveryDone() {
  pendingRecoveryCodes.value = null;
  window.location.assign(targetAfterLogin());
}
function cancelSecondStep() {
  setupEnrollment.value = null;
  auth.clearTokens();
}

function ssoOidc() {
  // backend 處理重導與 cookie
  window.location.assign("/api/v1/auth/oidc/login");
}

function ssoSaml() {
  const returnTo = safeNextPath(route.query.next);
  window.location.assign(`/api/v1/auth/saml/login?return_to=${encodeURIComponent(returnTo)}`);
}
</script>

<template>
  <div class="login-shell">
    <n-card class="login-card"
            :style="{ width: pendingRecoveryCodes || mfaSetup ? '440px' : '380px', maxWidth: 'calc(100vw - 32px)' }">
      <template #header>
        <div class="login-brand">
          <span class="login-brand-name">
            <img src="/favicon.svg" alt="jt-ipam" class="login-logo" />
            <span>{{ t('login.title') }}</span>
          </span>
          <n-dropdown trigger="click" :options="localeMenuOptions" @select="pickLocale">
            <n-button text size="small" class="login-lang">
              <template #icon><n-icon><Globe /></n-icon></template>
              {{ currentLocaleLabel }}
            </n-button>
          </n-dropdown>
        </div>
      </template>
      <n-alert v-if="errorMsg" type="error" style="margin-bottom: 12px">
        {{ errorMsg }}
      </n-alert>

      <!-- 首次設定完成：顯示復原碼，確認存好才進系統 -->
      <recovery-codes v-if="pendingRecoveryCodes" :codes="pendingRecoveryCodes" :account="auth.me?.username"
                      @done="recoveryDone" />

      <!-- 管理員要求 MFA、還沒設定：先設定 -->
      <div v-else-if="mfaToken && mfaSetup" data-testid="login-mfa-setup">
        <n-alert type="info" style="margin-bottom: 12px">{{ t("login.mfa_setup_intro") }}</n-alert>
        <totp-enroll v-if="setupEnrollment" :enrollment="setupEnrollment" :busy="loading" @confirm="confirmSetup" />
        <n-space justify="start" style="margin-top: 12px">
          <n-button type="error" ghost @click="cancelSecondStep">
            <template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}
          </n-button>
        </n-space>
      </div>

      <!-- Step 1: 帳密 -->
      <n-form v-else-if="!mfaToken" @submit.prevent="submitLogin">
        <n-form-item :label="t('login.username')">
          <n-input
            v-model:value="username"
            :placeholder="t('login.username')"
            autocomplete="username"
            :disabled="loading"
          />
        </n-form-item>
        <n-form-item :label="t('login.password')">
          <n-input
            v-model:value="password"
            type="password"
            show-password-on="click"
            :placeholder="t('login.password')"
            autocomplete="current-password"
            :disabled="loading"
            @keyup.enter="submitLogin"
          />
        </n-form-item>
        <n-form-item v-if="realms.length > 1" :label="t('login.realm')">
          <n-select v-model:value="realm" :options="realmOptions" :disabled="loading" />
        </n-form-item>
        <n-space justify="end">
          <n-button type="primary" :loading="loading" @click="submitLogin">
            <template #icon><n-icon><LoginIcon /></n-icon></template>
            {{ t("login.submit") }}
          </n-button>
        </n-space>

        <template v-if="ssoAvail.oidc || ssoAvail.saml">
          <n-divider style="margin: 16px 0 12px 0">{{ t("login.or_sso") }}</n-divider>
          <n-space vertical size="small">
            <n-button v-if="ssoAvail.oidc" block @click="ssoOidc">
              <template #icon><n-icon><Globe /></n-icon></template>
              {{ t("login.sso_oidc") }}
            </n-button>
            <n-button v-if="ssoAvail.saml" block @click="ssoSaml">
              <template #icon><n-icon><ShieldCheck /></n-icon></template>
              {{ t("login.sso_saml") }}
            </n-button>
          </n-space>
        </template>
      </n-form>

      <!-- Step 2: TOTP -->
      <n-form v-else @submit.prevent="submitMfa">
        <p style="margin: 0 0 12px 0; opacity: 0.8">{{ t("login.mfa_prompt") }}</p>
        <n-form-item :label="t('login.mfa_code')" :show-feedback="false">
          <n-input
            v-model:value="code"
            placeholder="123456"
            maxlength="11"
            :disabled="loading"
            :input-props="{ autocomplete: 'one-time-code' }"
            data-testid="login-mfa-code"
            @keyup.enter="submitMfa"
          />
        </n-form-item>
        <p class="mfa-hint" style="margin: 4px 0 16px">{{ t("login.mfa_recovery_hint") }}</p>
        <n-space justify="space-between">
          <n-button type="error" ghost @click="cancelSecondStep">
            <template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}
          </n-button>
          <n-button type="primary" :loading="loading" data-testid="login-mfa-submit" @click="submitMfa">
            <template #icon><n-icon><CheckIcon /></n-icon></template>{{ t("login.mfa_submit") }}
          </n-button>
        </n-space>
      </n-form>
    </n-card>
  </div>
</template>

<style scoped>
.login-shell {
  height: 100vh;
  height: var(--app-height, 100dvh);
  display: flex;
  align-items: center;
  justify-content: center;
  background:
    radial-gradient(ellipse at top left, rgba(64, 128, 255, 0.08), transparent 60%),
    radial-gradient(ellipse at bottom right, rgba(0, 255, 192, 0.06), transparent 60%);
}
.login-brand {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.login-brand-name {
  display: flex;
  align-items: center;
  gap: 10px;
}
.login-lang {
  flex: 0 0 auto;
  opacity: 0.8;
  font-weight: 400;
}
.mfa-hint {
  margin: -8px 0 12px 0;
  font-size: 12px;
  opacity: 0.7;
}
.login-logo {
  width: 26px;
  height: 26px;
  display: block;
  flex: 0 0 auto;
}
/* 登入卡片要浮在背景上：淺色背景下只靠細框線，邊緣幾乎看不出來（使用者 2026-10-16） */
/* 要比 App.vue 的全站「html[data-theme=light] .n-card」更具體，否則淺色模式下被它蓋掉 */
html[data-theme="light"] .login-card,
html:not([data-theme]) .login-card {
  box-shadow: 0 10px 30px rgba(15, 23, 42, 0.12), 0 2px 8px rgba(15, 23, 42, 0.08);
}
html[data-theme="dark"] .login-card {
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.45), 0 2px 8px rgba(0, 0, 0, 0.30);
}
</style>
