import { computed, ref } from "vue";
import { defineStore } from "pinia";
import type { UserMe, TokenResponse } from "@/types";
import { apiClient } from "@/api/client";

const STORAGE_KEY_ACCESS = "access_token";
const STORAGE_KEY_REFRESH = "refresh_token";

export const useAuthStore = defineStore("auth", () => {
  const accessToken = ref<string | null>(localStorage.getItem(STORAGE_KEY_ACCESS));
  const refreshToken = ref<string | null>(localStorage.getItem(STORAGE_KEY_REFRESH));
  const me = ref<UserMe | null>(null);
  const mfaToken = ref<string | null>(null);

  const isAuthenticated = computed(() => !!accessToken.value);

  function persistTokens(tokens: TokenResponse) {
    if (tokens.access_token) {
      accessToken.value = tokens.access_token;
      localStorage.setItem(STORAGE_KEY_ACCESS, tokens.access_token);
    }
    if (tokens.refresh_token) {
      refreshToken.value = tokens.refresh_token;
      localStorage.setItem(STORAGE_KEY_REFRESH, tokens.refresh_token);
    }
  }

  function clearTokens() {
    accessToken.value = null;
    refreshToken.value = null;
    me.value = null;
    mfaToken.value = null;
    localStorage.removeItem(STORAGE_KEY_ACCESS);
    localStorage.removeItem(STORAGE_KEY_REFRESH);
  }

  async function login(username: string, password: string, realm = "local"): Promise<TokenResponse> {
    const { data } = await apiClient.post<TokenResponse>("/api/v1/auth/login", {
      username,
      password,
      realm,
    });
    if (data.mfa_required && data.mfa_token) {
      mfaToken.value = data.mfa_token;
    } else {
      persistTokens(data);
      await fetchMe();
    }
    return data;
  }

  async function verifyMfa(code: string): Promise<TokenResponse> {
    if (!mfaToken.value) throw new Error("No MFA challenge in progress");
    const { data } = await apiClient.post<TokenResponse>("/api/v1/auth/mfa/verify", {
      mfa_token: mfaToken.value,
      code,
    });
    persistTokens(data);
    mfaToken.value = null;
    await fetchMe();
    return data;
  }

  async function fetchMe() {
    const { data } = await apiClient.get<UserMe>("/api/v1/auth/me");
    me.value = data;
  }

  // OIDC / SAML callback：後端把 token 放在 URL fragment 帶回前端，由 Login.vue 解析後呼叫此函式落地
  async function loginFromSso(access: string, refresh: string): Promise<void> {
    persistTokens({
      access_token: access,
      refresh_token: refresh,
      token_type: "bearer",
      expires_in: null,
      mfa_required: false,
      mfa_token: null,
    });
    await fetchMe();
  }

  async function logout() {
    try {
      await apiClient.post("/api/v1/auth/logout");
    } catch {
      // ignore
    }
    clearTokens();
  }

  return {
    accessToken,
    refreshToken,
    me,
    mfaToken,
    isAuthenticated,
    login,
    verifyMfa,
    fetchMe,
    loginFromSso,
    logout,
    clearTokens,
  };
});
