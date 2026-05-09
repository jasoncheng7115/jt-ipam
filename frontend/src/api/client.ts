import axios, { AxiosError } from "axios";

/**
 * 統一的 API client。
 *
 * OWASP 對應：
 * - A01: 401/403 在攔截器集中處理；不於 component 各自實作
 * - A05: withCredentials 預設 false（Cookie session 模式才打開）；prod 由 nginx 同源
 * - A09: 帶上 X-Request-ID（與後端串接 trace）
 */
function generateRequestId(): string {
  // RFC4122 v4 — 不依賴後端
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

apiClient.interceptors.response.use(
  (resp) => resp,
  (error: AxiosError) => {
    if (error.response?.status === 401) {
      localStorage.removeItem("access_token");
      // TODO: 路由導向 /login
    }
    return Promise.reject(error);
  },
);
