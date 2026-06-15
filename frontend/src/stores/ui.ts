import { computed, ref, watch } from "vue";
import { defineStore } from "pinia";
import { useI18n } from "vue-i18n";

type Theme = "light" | "dark" | "auto";
type Locale = "zh-TW" | "en-US";

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

  const theme = ref<Theme>(storedTheme);
  const locale = ref<Locale>(storedLocale);
  const pageSize = ref<number>(storedPageSize);
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
    if (!localStorage.getItem("access_token")) return;
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
    try {
      const i18n = useI18n();
      i18n.locale.value = value;
    } catch {
      // i18n 尚未注入時略過 (store 初始化時會發生)
    }
    if (persist) void persistPref({ locale: value });
  }

  // 表格每頁筆數偏好（全站共用，跨裝置同步）。size picker 改值會呼叫此函式。
  function setPageSize(value: number, persist = true) {
    if (!value || value < 1) return;
    pageSize.value = value;
    localStorage.setItem("page_size", String(value));
    if (persist) void persistPref({ page_size: value });
  }

  // 啟動 / 登入後呼叫：用後端偏好覆寫本地（跨裝置同步），不回寫。
  async function hydrateFromServer() {
    if (!localStorage.getItem("access_token")) return;
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

  return { theme, locale, pageSize, effectiveTheme, setTheme, setLocale, setPageSize, hydrateFromServer };
});
