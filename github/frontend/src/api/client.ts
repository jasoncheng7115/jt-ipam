import axios, { AxiosError } from "axios";
import { triggerSessionExpired } from "@/utils/session";
import { i18n } from "@/i18n";

// 後端常見英文錯誤訊息 → 在地化（元件多半直接顯示 response.data.detail）
const DETAIL_I18N: Record<string, string> = {
  "Admin required": "errors.admin_required",
  "No visible resources": "errors.no_visible_resources",
  "Not found": "errors.not_found",
  "Authentication required": "errors.session_expired",
};

function localizeDetail(error: AxiosError): void {
  const data: any = error.response?.data;
  const detail = data?.detail;
  if (typeof detail !== "string") return;
  const key = DETAIL_I18N[detail];
  if (key) data.detail = (i18n.global as any).t(key);
}

/**
 * 統一的 API client。
 *
 * OWASP 對應：
 * - A01：401/403 集中處理
 * - A05：withCredentials 預設 false(同源由 nginx 反代)
 * - A09：每個 request 帶 X-Request-ID 與後端 trace 串接
 */
function generateRequestId(): string {
  const arr = crypto.getRandomValues(new Uint8Array(16));
  arr[6] = (arr[6] & 0x0f) | 0x40;
  arr[8] = (arr[8] & 0x3f) | 0x80;
  const hex = Array.from(arr, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || "/",
  timeout: 15_000,
  withCredentials: false,
});

apiClient.interceptors.request.use((config) => {
  config.headers.set("X-Request-ID", generateRequestId());
  const token = localStorage.getItem("access_token");
  if (token) {
    config.headers.set("Authorization", `Bearer ${token}`);
  }
  return config;
});

// 一次同時收到多個 401 時，只觸發一次 refresh；其它等同一個 promise
let refreshingPromise: Promise<string | null> | null = null;

async function tryRefreshToken(): Promise<string | null> {
  if (refreshingPromise) return refreshingPromise;
  const refreshToken = localStorage.getItem("refresh_token");
  if (!refreshToken) return null;
  refreshingPromise = (async () => {
    try {
      // 用 axios 裸請求避免拉 interceptor 連鎖
      const resp = await axios.post("/api/v1/auth/refresh",
        { refresh_token: refreshToken },
        { headers: { "X-Request-ID": generateRequestId() }, timeout: 10_000 });
      const data = resp.data as { access_token?: string; refresh_token?: string };
      if (data?.access_token) {
        localStorage.setItem("access_token", data.access_token);
        if (data.refresh_token) {
          localStorage.setItem("refresh_token", data.refresh_token);
        }
        return data.access_token;
      }
      return null;
    } catch {
      return null;
    } finally {
      // 下次 401 又可以觸發新的 refresh(10s 內的併發共用同一個)
      setTimeout(() => { refreshingPromise = null; }, 0);
    }
  })();
  return refreshingPromise;
}

apiClient.interceptors.response.use(
  (resp) => resp,
  async (error: AxiosError) => {
    const config: any = error.config ?? {};
    const url = typeof config.url === "string" ? config.url : "";
    // 登入 / MFA / refresh 端點自己的 401 不算「逾時」(例如密碼錯誤)，照常往上拋
    const isAuthEndpoint =
      url.includes("/auth/login") || url.includes("/auth/refresh") || url.includes("/auth/mfa");
    // 401 嘗試 refresh 一次 (避免 refresh 自己再 refresh 無限迴圈)
    if (error.response?.status === 401 && !config._retried && !isAuthEndpoint) {
      const newToken = await tryRefreshToken();
      if (newToken) {
        config._retried = true;
        config.headers = config.headers ?? {};
        config.headers["Authorization"] = `Bearer ${newToken}`;
        return apiClient.request(config);
      }
      // refresh 失敗 → 登入逾時：統一彈提示 + 導向登入頁
      triggerSessionExpired();
      // 吞掉這個錯誤(回傳永不 resolve 的 promise)，避免發動操作的元件又跳一次通用錯誤訊息；
      // 反正畫面正要被導向登入頁。
      return new Promise(() => {});
    }
    localizeDetail(error);
    return Promise.reject(error);
  },
);
