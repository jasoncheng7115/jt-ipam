import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { nextTick } from "vue";

describe("表格隔行底色（客戶 2026-11-11：淺色一整片白，資料多時眼睛難對焦）", () => {
  beforeEach(() => {
    localStorage.clear();
    delete document.documentElement.dataset.striped;
    setActivePinia(createPinia());
    vi.resetModules();
  });

  it("預設關閉；打開存在這台瀏覽器的 localStorage，<html> 加標記給 CSS 用", async () => {
    const { useUiStore } = await import("../ui");
    const ui = useUiStore();
    await nextTick();
    expect(ui.stripedRows).toBe(false);
    expect(document.documentElement.dataset.striped).toBeUndefined();
    ui.setStripedRows(true);
    await nextTick();
    expect(localStorage.getItem("table_striped")).toBe("1");
    expect(document.documentElement.dataset.striped).toBe("1");
    ui.setStripedRows(false);
    await nextTick();
    expect(localStorage.getItem("table_striped")).toBe("0");
    expect(document.documentElement.dataset.striped).toBeUndefined();
  });

  it("重新開啟頁面時從 localStorage 讀回來", async () => {
    localStorage.setItem("table_striped", "1");
    const { useUiStore } = await import("../ui");
    const ui = useUiStore();
    await nextTick();
    expect(ui.stripedRows).toBe(true);
    expect(document.documentElement.dataset.striped).toBe("1");
  });
});
