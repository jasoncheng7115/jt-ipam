<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
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
import { LoginIcon } from "@/icons";
import { ShieldCheck, Globe } from "@iconoir/vue";

const { t } = useI18n();
const route = useRoute();
const auth = useAuthStore();
const { mfaToken } = storeToRefs(auth);

// 登入頁語言切換（未登入，不寫回後端偏好）：點開下拉再選，不是一按就切
const ui = useUiStore();
const { locale } = storeToRefs(ui);
const localeMenuOptions = [
  { label: "繁體中文", key: "zh-TW" },
  { label: "English", key: "en-US" },
];
const currentLocaleLabel = computed(
  () => localeMenuOptions.find((o) => o.key === locale.value)?.label ?? "",
);
function pickLocale(k: string | number) {
  ui.setLocale(String(k) as "zh-TW" | "en-US", false);
}

const username = ref("");
const password = ref("");
const code = ref("");
const loading = ref(false);
const errorMsg = ref<string | null>(null);

// 領域（PVE 風）：本機 / LDAP，預設本機
const realm = ref("local");
const realms = ref<{ label: string; value: string }[]>([{ label: "本機", value: "local" }]);
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
  // SSO（OIDC / SAML）callback：後端把 token 放在 URL fragment 帶回（#access_token=…&refresh_token=…）。
  // 先處理它（落地 token → 抓 me → 整頁導向目標頁）；否則登入頁會忽略 token、無限停在登入頁。
  const frag = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const ssoAccess = frag.get("access_token");
  if (ssoAccess) {
    try {
      await auth.loginFromSso(ssoAccess, frag.get("refresh_token") ?? "");
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
      window.location.assign(targetAfterLogin());
      return;
    } catch {
      errorMsg.value = t("login.failed");
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
    }
  }
  try {
    const { data } = await apiClient.get<{
      realms: { label: string; value: string }[];
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

function targetAfterLogin(): string {
  const next = route.query.next;
  if (typeof next === "string" && next.startsWith("/")) return next;
  return "/";
}

async function submitLogin() {
  errorMsg.value = null;
  loading.value = true;
  try {
    const res = await auth.login(username.value, password.value, realm.value);
    if (!res.mfa_required) {
      // 整頁載入(非 SPA 導向)：以新 token 全新啟動，清掉前一個 session 殘留的
      // 模組級快取 / loading 旗標，避免登入後某些功能因舊狀態出錯、要切頁才好。
      window.location.assign(targetAfterLogin());
    }
  } catch (err: unknown) {
    errorMsg.value = t("login.failed");
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
    errorMsg.value = t("login.mfa_failed");
  } finally {
    loading.value = false;
  }
}

function ssoOidc() {
  // backend 處理重導與 cookie
  window.location.assign("/api/v1/auth/oidc/login");
}

function ssoSaml() {
  const next = route.query.next;
  const returnTo = typeof next === "string" && next.startsWith("/") ? next : "/";
  window.location.assign(`/api/v1/auth/saml/login?return_to=${encodeURIComponent(returnTo)}`);
}
</script>

<template>
  <div class="login-shell">
    <n-card style="width: 380px">
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

      <!-- Step 1: 帳密 -->
      <n-form v-if="!mfaToken" @submit.prevent="submitLogin">
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
          <n-select v-model:value="realm" :options="realms" :disabled="loading" />
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
        <n-form-item :label="t('login.mfa_code')">
          <n-input
            v-model:value="code"
            placeholder="123456"
            maxlength="6"
            :disabled="loading"
            @keyup.enter="submitMfa"
          />
        </n-form-item>
        <n-space justify="space-between">
          <n-button @click="auth.clearTokens()">{{ t("common.cancel") }}</n-button>
          <n-button type="primary" :loading="loading" @click="submitMfa">
            {{ t("login.mfa_submit") }}
          </n-button>
        </n-space>
      </n-form>
    </n-card>
  </div>
</template>

<style scoped>
.login-shell {
  height: 100vh;
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
.login-logo {
  width: 26px;
  height: 26px;
  display: block;
  flex: 0 0 auto;
}
</style>
