import axios, { AxiosError } from "axios";
import { triggerSessionExpired } from "@/utils/session";
import { AUTH_COOKIE_HEADERS, getAccessToken, setAccessToken } from "@/api/token";
import { i18n } from "@/i18n";
import { formatValidationErrors } from "@/utils/validationErrors";

// 後端常見英文錯誤訊息 → 在地化（元件多半直接顯示 response.data.detail）
const DETAIL_I18N: Record<string, string> = {
  "Admin required": "errors.admin_required",
  "No visible resources": "errors.no_visible_resources",
  "Not found": "errors.not_found",
  "Authentication required": "errors.session_expired",
  // 全域基礎設施的守門（require_global_read）
  "Global resource requires full visibility": "errors.forbidden",
  "Forbidden": "errors.forbidden",
};

/**
 * 下載類請求（responseType: "blob"）出錯時，錯誤內容也是 Blob —— 不先轉回 JSON，
 * `detail` 讀不到，畫面只剩「伺服器錯誤」，後端講的原因使用者永遠看不到。
 * 只轉 JSON 型態的；真的檔案內容原樣保留。
 */
export async function unwrapBlobError(error: AxiosError): Promise<void> {
  const data: unknown = error.response?.data;
  if (typeof Blob === "undefined" || !(data instanceof Blob) || !/json/i.test(data.type || "")) return;
  try {
    // Blob.text() 瀏覽器都有；測試環境（jsdom）沒有，退回 FileReader
    const text = typeof (data as any).text === "function"
      ? await data.text()
      : await new Promise<string>((resolve, reject) => {
        const r = new FileReader();
        r.onload = () => resolve(String(r.result ?? ""));
        r.onerror = () => reject(r.error);
        r.readAsText(data);
      });
    (error.response as any).data = JSON.parse(text);
  } catch { /* 不是合法 JSON 就維持原樣 */ }
}

function localizeDetail(error: AxiosError): void {
  const data: any = error.response?.data;
  const detail = data?.detail;
  const t = (i18n.global as any).t;
  // 結構化訊息 `{code, params, message}`（後端的 core/ui_error.ui_detail）：
  // 句子在這裡組，後端只講「是哪一種錯、參數是什麼」。這樣英文與日文介面才不會
  // 拿到中文 —— 伺服器產生的文字沒辦法跟著使用者的語言走，實際被回報過。
  // 沒有對應翻譯時退回 message，不會變成空白或 [object Object]。
  if (detail && typeof detail === "object" && typeof detail.code === "string") {
    const key = `errors.${detail.code}`;
    const out = t(key, detail.params ?? {});
    data.detail = out === key ? (detail.message ?? out) : out;
    // 攤平成字串是為了讓既有 202 處 `data.detail` 讀取者不必改；但有少數呼叫端是靠
    // 代碼決定流程（例如 PVE 回 tfa_required 要跳出驗證碼欄位），代碼攤掉就等於那個
    // 分支永遠不會成立 —— 所以另外掛一份在 detail_code 上。
    data.detail_code = detail.code;
    return;
  }
  // 欄位檢查失敗（422）：原因在 errors 清單，翻成「欄位：原因」（以前只顯示「Invalid request」）
  if (error.response?.status === 422 && Array.isArray(data?.errors)) {
    const te = (i18n.global as any).te;
    const out = formatValidationErrors(t, (k: string) => te(k), data.errors);
    if (out) {
      data.detail = out;
      return;
    }
  }
  if (typeof detail === "string") {
    const key = DETAIL_I18N[detail];
    if (key) {
      data.detail = t(key);
      return;
    }
  }
  // 403 沒對到已知字串時給通用權限訊息。原本元件的退路是「連線失敗，請稍後再試」，
  // 對權限不足來說是錯的訊息 —— 使用者會以為系統壞了而不是自己沒權限。
  if (error.response?.status === 403 && error.response.data) {
    (error.response.data as any).detail = t("errors.forbidden");
  }
}

/**
 * 從 API 錯誤取出該顯示給使用者的訊息。
 * 優先用後端 detail（已經過 `localizeDetail` 在地化，403 也已轉成權限訊息），
 * 真的沒有才退回「連線失敗」—— 這樣才不會把權限/驗證錯誤一律講成連線問題。
 */
export function apiErrMsg(e: unknown): string {
  const detail = (e as any)?.response?.data?.detail;
  if (typeof detail === "string" && detail) return detail;
  // 結構化的 detail（例如冷卻期擋下建立時會附上 until / previous_hostname）：
  // 取 message 顯示。少了這段會變成「[object Object]」——那比沒有訊息還糟。
  if (detail && typeof detail === "object" && typeof detail.message === "string") {
    return detail.message;
  }
  return (i18n.global as any).t("errors.network");
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
  // 陣列參數一律重複同一個 key（a=1&a=2）：FastAPI 只認這種。axios 預設的 a[]=1 後端會當成沒帶，
  // 「只問限定網段」就靜靜變成全站（11-25 在 Zabbix / Checkmk / LibreNMS 的未監控位址發現）
  paramsSerializer: { indexes: null },
});

apiClient.interceptors.request.use((config) => {
  config.headers.set("X-Request-ID", generateRequestId());
  const token = getAccessToken();
  if (token) {
    config.headers.set("Authorization", `Bearer ${token}`);
  }
  return config;
});

// 一次同時收到多個 401 時，只觸發一次 refresh；其它等同一個 promise
let refreshingPromise: Promise<string | null> | null = null;

/**
 * 用 HttpOnly Cookie 裡的更新權杖換一把新的存取權杖（Cookie 本身由後端換新）。
 * 同一時間多個 401 只換一次。也給「新分頁還沒有存取權杖」時用（stores/auth.ensureSession）。
 */
export async function tryRefreshToken(): Promise<string | null> {
  if (refreshingPromise) return refreshingPromise;
  refreshingPromise = (async () => {
    try {
      // 用 axios 裸請求避免拉 interceptor 連鎖
      const resp = await axios.post(`${import.meta.env.VITE_API_BASE_URL || ""}/api/v1/auth/refresh`, null,
        { headers: { "X-Request-ID": generateRequestId(), ...AUTH_COOKIE_HEADERS },
          withCredentials: true, timeout: 10_000 });
      const data = resp.data as { access_token?: string };
      if (data?.access_token) {
        setAccessToken(data.access_token);
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
      url.includes("/auth/login") || url.includes("/auth/refresh") || url.includes("/auth/mfa")
      || url.includes("/auth/logout");
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
    await unwrapBlobError(error);
    localizeDetail(error);
    return Promise.reject(error);
  },
);
