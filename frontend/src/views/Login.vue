<script setup lang="ts">
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import {
  NCard,
  NForm,
  NFormItem,
  NInput,
  NButton,
  NIcon,
  NSpace,
  NAlert,
  NDivider,
  useMessage,
} from "naive-ui";
import { storeToRefs } from "pinia";
import { useAuthStore } from "@/stores/auth";
import { LoginIcon } from "@/icons";
import { ShieldCheck, Globe } from "@iconoir/vue";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const { mfaToken } = storeToRefs(auth);
const msg = useMessage();

const username = ref("");
const password = ref("");
const code = ref("");
const loading = ref(false);
const errorMsg = ref<string | null>(null);

function targetAfterLogin(): string {
  const next = route.query.next;
  if (typeof next === "string" && next.startsWith("/")) return next;
  return "/";
}

async function submitLogin() {
  errorMsg.value = null;
  loading.value = true;
  try {
    const res = await auth.login(username.value, password.value);
    if (!res.mfa_required) {
      router.push(targetAfterLogin());
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
    router.push(targetAfterLogin());
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
    <n-card style="width: 380px" :title="t('login.title')">
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
        <n-space justify="end">
          <n-button type="primary" :loading="loading" @click="submitLogin">
            <template #icon><n-icon><LoginIcon /></n-icon></template>
            {{ t("login.submit") }}
          </n-button>
        </n-space>

        <n-divider style="margin: 16px 0 12px 0">{{ t("login.or_sso") }}</n-divider>
        <n-space vertical size="small">
          <n-button block @click="ssoOidc">
            <template #icon><n-icon><Globe /></n-icon></template>
            {{ t("login.sso_oidc") }}
          </n-button>
          <n-button block @click="ssoSaml">
            <template #icon><n-icon><ShieldCheck /></n-icon></template>
            {{ t("login.sso_saml") }}
          </n-button>
        </n-space>
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
</style>
