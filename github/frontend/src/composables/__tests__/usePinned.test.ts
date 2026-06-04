import { describe, it, expect, beforeEach } from "vitest";
import { usePinned } from "@/composables/usePinned";

// usePinned 以 module-level cache 依 namespace 共用，故每個測試用獨立 namespace 以隔離。
describe("usePinned", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("toggle 會同步寫入 localStorage（不靠 watch，卸除後仍保存）", () => {
    const ns = "test-sync";
    const { toggle, isPinned } = usePinned(ns);
    toggle("a");
    // 同步：toggle 後立刻就能在 localStorage 讀到，不需等 tick
    expect(JSON.parse(localStorage.getItem(`jtipam.pinned.${ns}`)!)).toEqual(["a"]);
    expect(isPinned("a")).toBe(true);

    toggle("a"); // 再 toggle → 取消釘選
    expect(JSON.parse(localStorage.getItem(`jtipam.pinned.${ns}`)!)).toEqual([]);
    expect(isPinned("a")).toBe(false);
  });

  it("初始化時讀回既有 localStorage 內容", () => {
    const ns = "test-load";
    localStorage.setItem(`jtipam.pinned.${ns}`, JSON.stringify(["x", "y"]));
    const { isPinned } = usePinned(ns);
    expect(isPinned("x")).toBe(true);
    expect(isPinned("y")).toBe(true);
    expect(isPinned("z")).toBe(false);
  });

  it("sortPinnedFirst 把釘選項排到最前面且穩定", () => {
    const ns = "test-sort";
    const { toggle, sortPinnedFirst } = usePinned(ns);
    toggle("2");
    const rows = [{ id: "1" }, { id: "2" }, { id: "3" }, { id: "4" }];
    const sorted = sortPinnedFirst(rows).map((r) => r.id);
    expect(sorted[0]).toBe("2"); // 釘選的在最前
    // 其餘維持原相對順序（穩定）
    expect(sorted.slice(1)).toEqual(["1", "3", "4"]);
  });

  it("壞掉的 localStorage 內容不會炸，退回空清單", () => {
    const ns = "test-bad";
    localStorage.setItem(`jtipam.pinned.${ns}`, "{not json");
    const { isPinned } = usePinned(ns);
    expect(isPinned("anything")).toBe(false);
  });
});
