/**
 * 版面高度用瀏覽器回報的實際視窗高度（2026-11-07 iPad 回報）。
 *
 * iPadOS 的 Safari 剛載入時，`100dvh` 有時比實際看得到的高度少一截：側欄與內容都在同一個位置截斷、
 * 下面一大片空白；切到別的 app 再回來，Safari 重排之後才正常。`window.innerHeight` 是當下真正的高度，
 * 寫進 `--app-height` 給版面用（CSS 沒有這個變數時退回 100dvh）。Safari 載入後還會再調整一次工具列，
 * 所以除了視窗大小、方向改變、回到分頁時重量，載入後也補量幾次。
 */
export const APP_HEIGHT_VAR = "--app-height";

export function measureAppHeight(win: Window = window): void {
  const h = Math.round(win.innerHeight);
  if (h > 0) win.document.documentElement.style.setProperty(APP_HEIGHT_VAR, `${h}px`);
}

export function installViewportHeight(win: Window = window): () => void {
  const measure = () => measureAppHeight(win);
  measure();
  const onVisible = () => { if (win.document.visibilityState === "visible") measure(); };
  win.addEventListener("resize", measure);
  win.addEventListener("orientationchange", measure);
  win.addEventListener("pageshow", measure);
  win.document.addEventListener("visibilitychange", onVisible);
  win.visualViewport?.addEventListener("resize", measure);
  win.requestAnimationFrame?.(measure);
  const timers = [300, 1000].map((ms) => win.setTimeout(measure, ms));
  return () => {
    win.removeEventListener("resize", measure);
    win.removeEventListener("orientationchange", measure);
    win.removeEventListener("pageshow", measure);
    win.document.removeEventListener("visibilitychange", onVisible);
    win.visualViewport?.removeEventListener("resize", measure);
    timers.forEach((t) => win.clearTimeout(t));
  };
}
