import { computed, ref, watch } from "vue";
import { defineStore } from "pinia";
import { i18n } from "@/i18n";
import { getAccessToken } from "@/api/token";

type Theme = "light" | "dark" | "auto";
type Locale = "zh-TW" | "en-US" | "ja-JP";

function detectSystemDark(): boolean {
  if (typeof window === "undefined") return false;
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export const useUiStore = defineStore("ui", () => {
  const storedTheme = (localStorage.getItem("theme") as Theme | null) ?? "auto";
  const storedLocale =
    (localStorage.getItem("locale") as Locale | null) ??
    (import.meta.env.VITE_DEFAULT_LOCALE as Locale | undefined) ??
    "zh-TW";

  const storedPageSize = Number(localStorage.getItem("page_size")) || 50;
  // 表格隔行底色（客戶 2026-11-11：淺色一整片白，資料多時眼睛難對焦）。每個人、每台瀏覽器自己決定，預設關
  const storedStriped = localStorage.getItem("table_striped") === "1";

  const theme = ref<Theme>(storedTheme);
  const locale = ref<Locale>(storedLocale);
  const pageSize = ref<number>(storedPageSize);
  const stripedRows = ref<boolean>(storedStriped);
  const systemDark = ref<boolean>(detectSystemDark());

  if (typeof window !== "undefined") {
    window
      .matchMedia("(prefers-color-scheme: dark)")
      .addEventListener("change", (e) => {
        systemDark.value = e.matches;
      });
  }

  const effectiveTheme = computed<"light" | "dark">(() => {
    if (theme.value === "auto") return systemDark.value ? "dark" : "light";
    return theme.value;
  });

  // 把佈景 / 語言寫回後端偏好（跨裝置同步）；未登入就略過。persist=false 用於
  // 從後端套用時，避免回寫造成迴圈。
  async function persistPref(patch: Record<string, unknown>) {
    if (!getAccessToken()) return;
    try {
      const { updatePreferences } = await import("@/api/preferences");
      await updatePreferences(patch as any);
    } catch { /* 失敗不影響本地；下次 hydrate 會以後端為準 */ }
  }

  function setTheme(value: Theme, persist = true) {
    theme.value = value;
    localStorage.setItem("theme", value);
    if (persist) void persistPref({ theme: value });
  }

  function setLocale(value: Locale, persist = true) {
    locale.value = value;
    localStorage.setItem("locale", value);
    // 直接改全域 i18n 的 locale（單例，隨時可用）→ 全站 t() 立即重繪，不必重新整理。
    // 不能用 useI18n()：它只能在 component setup 內呼叫，在 store action（按鈕點擊）裡會 throw。
    // legacy:false 時 global.locale 執行期是 ref，但型別被推成字串 → 用 cast 設 .value。
    (i18n.global.locale as unknown as { value: Locale }).value = value;
    if (persist) void persistPref({ locale: value });
  }

  // 表格每頁筆數偏好（全站共用，跨裝置同步）。size picker 改值會呼叫此函式。
  function setPageSize(value: number, persist = true) {
    if (!value || value < 1) return;
    pageSize.value = value;
    localStorage.setItem("page_size", String(value));
    if (persist) void persistPref({ page_size: value });
  }

  function setStripedRows(value: boolean) {
    stripedRows.value = value;
    localStorage.setItem("table_striped", value ? "1" : "0");
  }

  // 啟動 / 登入後呼叫：用後端偏好覆寫本地（跨裝置同步），不回寫。
  async function hydrateFromServer() {
    if (!getAccessToken()) return;
    try {
      const { getPreferences } = await import("@/api/preferences");
      const p = await getPreferences();
      if (p?.theme) setTheme(p.theme as Theme, false);
      if (p?.locale) setLocale(p.locale as Locale, false);
      if (p?.page_size) setPageSize(p.page_size, false);
    } catch { /* ignore */ }
  }

  // 同步 <html lang> 與 dark/light class(CSS variables hook)
  watch(
    [locale, effectiveTheme],
    ([loc, mode]) => {
      if (typeof document === "undefined") return;
      document.documentElement.setAttribute("lang", loc);
      document.documentElement.dataset.theme = mode;
    },
    { immediate: true },
  );

  // <html data-striped="1">：全站表格的偶數列套用表格本身的隔行色（App.vue）
  watch(stripedRows, (on) => {
    if (typeof document === "undefined") return;
    if (on) document.documentElement.dataset.striped = "1";
    else delete document.documentElement.dataset.striped;
  }, { immediate: true });

  return { theme, locale, pageSize, stripedRows, effectiveTheme, setTheme, setLocale, setPageSize, setStripedRows,
           hydrateFromServer };
});
