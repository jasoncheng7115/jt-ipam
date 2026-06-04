import { createI18n } from "vue-i18n";
import zhTW from "@/i18n/zh-TW.json";
import enUS from "@/i18n/en-US.json";

type MessageSchema = typeof zhTW;

const stored = (typeof localStorage !== "undefined" ? localStorage.getItem("locale") : null) as
  | "zh-TW"
  | "en-US"
  | null;
const fallback = (import.meta.env.VITE_DEFAULT_LOCALE as "zh-TW" | "en-US") || "zh-TW";

export const i18n = createI18n<[MessageSchema], "zh-TW" | "en-US">({
  legacy: false,
  locale: stored ?? fallback,
  fallbackLocale: "en-US",
  messages: {
    "zh-TW": zhTW,
    "en-US": enUS,
  },
});
