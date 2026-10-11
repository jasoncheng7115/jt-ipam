import { computed, ref } from "vue";
import { defineStore } from "pinia";
import type { UserMe, TokenResponse } from "@/types";
import { apiClient, tryRefreshToken } from "@/api/client";
import { AUTH_COOKIE_HEADERS, clearAccessToken, getAccessToken, setAccessToken } from "@/api/token";
import { clearTracked } from "@/composables/useTaskTracker";

/**
 * 登入狀態。
 *
 * 更新權杖在 HttpOnly Cookie（讀不到，也不必讀）；這裡只管存取權杖（api/token）。
 * MFA 兩種第二步：`mfa_required`（已啟用 → 送驗證碼或復原碼）、`mfa_setup_required`
 * （管理員要求、還沒設定 → 先設定 TOTP，完成後會拿到 10 組復原碼）。
 */
export const useAuthStore = defineStore("auth", () => {
  const accessToken = ref<string | null>(getAccessToken());
  const me = ref<UserMe | null>(null);
  const mfaToken = ref<string | null>(null);
  /** 這個 mfa_token 是「先設定」還是「輸入驗證碼」 */
  const mfaSetup = ref(false);
  /** 剛設定好 TOTP 時的復原碼：登入頁顯示一次，使用者確認存好才離開 */
  const pendingRecoveryCodes = ref<string[] | null>(null);

  const isAuthenticated = computed(() => !!accessToken.value);

  function persistTokens(tokens: TokenResponse) {
    if (tokens.access_token) {
      accessToken.value = tokens.access_token;
      setAccessToken(tokens.access_token);
    }
  }

  function clearTokens() {
    accessToken.value = null;
    me.value = null;
    mfaToken.value = null;
    mfaSetup.value = false;
    pendingRecoveryCodes.value = null;
    clearAccessToken();
  }

  function startSecondStep(data: TokenResponse): void {
    mfaToken.value = data.mfa_token;
    mfaSetup.value = !!data.mfa_setup_required;
  }

  async function login(username: string, password: string, realm = "local"): Promise<TokenResponse> {
    const { data } = await apiClient.post<TokenResponse>("/api/v1/auth/login", {
      username,
      password,
      realm,
    }, { withCredentials: true });
    if ((data.mfa_required || data.mfa_setup_required) && data.mfa_token) {
      startSecondStep(data);
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
      code: code.trim(),
    }, { withCredentials: true });
    persistTokens(data);
    mfaToken.value = null;
    await fetchMe();
    return data;
  }

  /** 政策要求 MFA、還沒設定：產生 TOTP 金鑰（還沒生效）。 */
  async function beginMfaSetup(): Promise<{ secret: string; otpauth_uri: string }> {
    if (!mfaToken.value) throw new Error("No MFA setup in progress");
    const { data } = await apiClient.post("/api/v1/auth/mfa/setup/begin", { mfa_token: mfaToken.value });
    return data;
  }

  /** 設定完成：拿到工作階段與 10 組復原碼（放在 pendingRecoveryCodes 給登入頁顯示）。 */
  async function confirmMfaSetup(secret: string, code: string): Promise<TokenResponse> {
    if (!mfaToken.value) throw new Error("No MFA setup in progress");
    const { data } = await apiClient.post<TokenResponse>("/api/v1/auth/mfa/setup/confirm", {
      mfa_token: mfaToken.value, secret, code: code.trim(),
    }, { withCredentials: true });
    persistTokens(data);
    mfaToken.value = null;
    mfaSetup.value = false;
    pendingRecoveryCodes.value = data.recovery_codes ?? null;
    await fetchMe();
    return data;
  }

  async function fetchMe() {
    const { data } = await apiClient.get<UserMe>("/api/v1/auth/me");
    me.value = data;
  }

  /**
   * 這個分頁還沒有存取權杖（新分頁、重新整理後被清掉、SSO 剛回來）→ 用 HttpOnly Cookie 換一把。
   * 換得到就算已登入；換不到（Cookie 沒了、工作階段被撤銷）回 false，由呼叫端導向登入頁。
   */
  async function ensureSession(): Promise<boolean> {
    if (accessToken.value) return true;
    const tok = await tryRefreshToken();
    if (!tok) return false;
    accessToken.value = tok;
    return true;
  }

  async function logout() {
    try {
      // 伺服器端撤銷這個工作階段（存取權杖立即失效）並清掉 Cookie
      await apiClient.post("/api/v1/auth/logout", null, { headers: AUTH_COOKIE_HEADERS, withCredentials: true });
    } catch {
      // ignore
    }
    clearTokens();
    clearTracked();
  }

  return {
    accessToken,
    me,
    mfaToken,
    mfaSetup,
    pendingRecoveryCodes,
    isAuthenticated,
    login,
    verifyMfa,
    beginMfaSetup,
    confirmMfaSetup,
    startSecondStep,
    fetchMe,
    ensureSession,
    logout,
    clearTokens,
  };
});
