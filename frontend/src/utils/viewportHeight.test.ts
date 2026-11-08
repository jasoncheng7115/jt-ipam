import { afterEach, describe, expect, it, vi } from "vitest";
import { APP_HEIGHT_VAR, installViewportHeight } from "./viewportHeight";

const css = () => document.documentElement.style.getPropertyValue(APP_HEIGHT_VAR);

describe("版面高度跟著實際視窗高度（iPad Safari 剛載入時 dvh 偏小）", () => {
  let off: () => void = () => {};
  afterEach(() => { off(); vi.useRealTimers(); document.documentElement.style.removeProperty(APP_HEIGHT_VAR); });

  it("載入時寫入 innerHeight，之後視窗變了、回到分頁、載入後補量都會更新", () => {
    vi.useFakeTimers();
    Object.defineProperty(window, "innerHeight", { configurable: true, value: 627 });
    off = installViewportHeight(window);
    expect(css()).toBe("627px");

    Object.defineProperty(window, "innerHeight", { configurable: true, value: 695 });
    vi.advanceTimersByTime(1000);               // Safari 載入後才調整工具列：補量
    expect(css()).toBe("695px");

    Object.defineProperty(window, "innerHeight", { configurable: true, value: 1000 });
    window.dispatchEvent(new Event("resize"));
    expect(css()).toBe("1000px");

    Object.defineProperty(window, "innerHeight", { configurable: true, value: 700 });
    document.dispatchEvent(new Event("visibilitychange"));
    expect(css()).toBe("700px");
  });

  it("量不到（0）時不寫，留給 CSS 的 100dvh", () => {
    Object.defineProperty(window, "innerHeight", { configurable: true, value: 0 });
    off = installViewportHeight(window);
    expect(css()).toBe("");
  });
});
