// 集中處理「登入逾時」：任何 API 因 401 且無法 refresh 時，統一彈出提示並導向登入頁。
// 由 App 內(可用 useMessage/useRouter/i18n 的元件)註冊 handler；client.ts 在攔截器呼叫 trigger。

type Handler = () => void;

let handler: Handler | null = null;
let firing = false;

export function setSessionExpiredHandler(h: Handler): void {
  handler = h;
}

/** 觸發登入逾時流程（同一波 401 只跑一次，避免重複彈窗/重複導向）。*/
export function triggerSessionExpired(): void {
  if (firing) return;
  firing = true;
  // 一律先清掉 token
  try {
    localStorage.removeItem("access_token");
    localStorage.removeItem("refresh_token");
  } catch { /* ignore */ }

  if (handler) {
    handler();
  } else if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
    // App 尚未註冊 handler 時的後備：硬導向
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    window.location.assign(`/login?next=${next}&expired=1`);
  }

  // 重新登入後要能再次觸發
  setTimeout(() => { firing = false; }, 5000);
}
