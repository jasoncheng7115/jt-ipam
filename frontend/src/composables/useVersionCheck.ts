/**
 * 偵測「已部署新版前端」→ 提示使用者重新整理。
 *
 * 解決長壽 SPA 分頁跑舊 bundle 的老問題：build 時 vite 會輸出 dist/version.json，
 * 這裡每隔幾分鐘（及視窗重新取得焦點時）以 no-store 取回，比對編譯進來的 __APP_VERSION__；
 * 不同就跳一次持久提醒，點「重新整理」即整頁重載拿新 bundle。
 */
import { h, onMounted, onUnmounted } from "vue";
import { useMessage } from "naive-ui";
import { useI18n } from "vue-i18n";
import { RefreshIcon } from "@/icons";

let prompted = false; // 全分頁只提醒一次，避免洗版

export function useVersionCheck() {
  const message = useMessage();
  const { t } = useI18n();
  let timer: number | null = null;

  async function check() {
    if (prompted) return;
    try {
      const res = await fetch(`/version.json?t=${Date.now()}`, { cache: "no-store" });
      if (!res.ok) return;
      const data = (await res.json()) as { version?: string };
      const deployed = data?.version;
      const running = __APP_VERSION__;
      if (deployed && running && deployed !== running) {
        prompted = true;
        message.warning("", {
          duration: 0,
          closable: true,
          render: () =>
            h(
              "div",
              {
                title: t("update.new_version_v", { v: deployed }),
                style:
                  "display:inline-flex;align-items:center;gap:8px;cursor:pointer;line-height:1;"
                  + "border:1px solid var(--primary-color,#18a058);border-radius:10px;"
                  + "padding:8px 16px;background:rgba(24,160,88,.1);"
                  + "box-shadow:0 4px 14px rgba(0,0,0,.15);"
                  + "font-weight:600;color:var(--primary-color,#18a058);white-space:nowrap",
                onClick: () => window.location.reload(),
              },
              [
                h("span", {
                  style: "display:inline-flex;align-items:center;justify-content:center;"
                    + "width:16px;height:16px;flex:0 0 auto",
                }, h(RefreshIcon, { width: 16, height: 16, style: "display:block" })),
                h("span", { style: "line-height:1" }, t("update.banner")),
              ],
            ),
        });
      }
    } catch { /* 離線 / 尚未部署 version.json → 略過 */ }
  }

  function onFocus() { void check(); }

  onMounted(() => {
    void check();
    timer = window.setInterval(check, 180_000); // 每 3 分鐘
    window.addEventListener("focus", onFocus);
  });
  onUnmounted(() => {
    if (timer !== null) window.clearInterval(timer);
    window.removeEventListener("focus", onFocus);
  });
}
