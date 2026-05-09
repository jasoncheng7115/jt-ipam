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

  const theme = ref<Theme>(storedTheme);
  const locale = ref<Locale>(storedLocale);
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

  function setTheme(value: Theme) {
    theme.value = value;
    localStorage.setItem("theme", value);
  }

  function setLocale(value: Locale) {
    locale.value = value;
    localStorage.setItem("locale", value);
    try {
      const i18n = useI18n();
      i18n.locale.value = value;
    } catch {
      // i18n 尚未注入時略過（store 初始化時會發生）
    }
  }

  // 同步 <html lang> 與 dark/light class（CSS variables hook）
  watch(
    [locale, effectiveTheme],
    ([loc, mode]) => {
      if (typeof document === "undefined") return;
      document.documentElement.setAttribute("lang", loc);
      document.documentElement.dataset.theme = mode;
    },
    { immediate: true },
  );

  return { theme, locale, effectiveTheme, setTheme, setLocale };
});
