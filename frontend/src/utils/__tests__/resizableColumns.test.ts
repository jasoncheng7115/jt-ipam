import { describe, expect, it } from "vitest";
import { h, nextTick, ref } from "vue";
import { mount } from "@vue/test-utils";
import { NDataTable } from "naive-ui";
import {
  FILL_KEY, installResizableColumns, usesFixedLayout, withProportionalWidths, withResizable,
} from "@/utils/resizableColumns";

// 使用者要求：所有有欄位標頭的表格都能拖拉改欄寬 —— 做在元件層，逐頁補一定會漏
installResizableColumns(NDataTable);

describe("withResizable", () => {
  it("資料欄預設可拖拉，明寫 false 的尊重", () => {
    const out = withResizable<Record<string, unknown>>([
      { key: "a", title: "A" }, { key: "b", title: "B", resizable: false },
    ]);
    expect(out[0].resizable).toBe(true);
    expect(out[1].resizable).toBe(false);
  });

  it("勾選欄與展開欄不動；群組欄往下套到子欄", () => {
    const out = withResizable<Record<string, unknown>>([
      { type: "selection" }, { type: "expand" },
      { title: "G", key: "g", children: [{ key: "c", title: "C" }] },
    ]);
    expect(out[0].resizable).toBeUndefined();
    expect(out[1].resizable).toBeUndefined();
    expect(out[2].resizable).toBeUndefined();
    expect((out[2].children as Record<string, unknown>[])[0].resizable).toBe(true);
  });

  it("寬度相關的設定一概不動（拖拉之前的排版要跟原本一模一樣）", () => {
    // 元件拿 width 當標頭的最小寬度；補 minWidth 會蓋掉它，沒有固定排版的表格在手機上會被擠成直排
    const out = withResizable<Record<string, unknown>>([
      { key: "a" }, { key: "ip", width: 160 }, { key: "m", minWidth: 140 }, { key: "p", width: "20%" },
    ]);
    expect(out.map((c) => [c.width, c.minWidth, c.maxWidth])).toEqual([
      [undefined, undefined, undefined], [160, undefined, undefined],
      [undefined, 140, undefined], ["20%", undefined, undefined],
    ]);
  });

  it("欄位的函式（render／sorter）原樣保留", () => {
    const render = () => "x";
    const sorter = () => 0;
    const [c] = withResizable<Record<string, unknown>>([{ key: "a", render, sorter }]);
    expect(c.render).toBe(render);
    expect(c.sorter).toBe(sorter);
  });
});

describe("NDataTable 套用後", () => {
  it("每個資料欄的標頭都有拖拉把手", () => {
    const w = mount(NDataTable, {
      props: {
        columns: [{ type: "selection" }, { key: "a", title: "A" }, { key: "b", title: "B" },
                  { key: "c", title: "C", resizable: false }],
        data: [{ a: 1, b: 2, c: 3, key: 1 }],
        rowKey: (r: { key: number }) => r.key,
      },
    });
    expect(w.findAll("[data-data-table-resizable]").length).toBe(2);
  });

  it("父層改欄位（響應式）時跟著更新", async () => {
    const cols = ref([{ key: "a", title: "A" }]);
    const w = mount({ render: () => h(NDataTable, { columns: cols.value, data: [] }) });
    expect(w.findAll("[data-data-table-resizable]").length).toBe(1);
    cols.value = [...cols.value, { key: "b", title: "B" }];
    await nextTick();
    expect(w.findAll("[data-data-table-resizable]").length).toBe(2);
  });
});

describe("欄寬分配（固定排版＋scroll-x 的表格）", () => {
  it("有一欄 ellipsis（或 maxHeight）就是固定排版；一般表格不是", () => {
    expect(usesFixedLayout({}, [{ key: "a" }, { key: "b", ellipsis: { tooltip: true } }])).toBe(true);
    expect(usesFixedLayout({ maxHeight: 400 }, [{ key: "a" }])).toBe(true);
    expect(usesFixedLayout({}, [{ key: "a" }, { key: "b", width: 160 }])).toBe(false);
    expect(usesFixedLayout({}, [{ title: "G", key: "g", children: [{ key: "c", ellipsis: true }] }])).toBe(true);
  });

  it("沒寫 width 的欄補上 width（minWidth 或 120），多出來的空間才會按比例分給每一欄", () => {
    // 以前多出來的空間全部平均給沒寫 width 的欄 → 名稱欄空蕩蕩、日期欄（150）被擠到換行
    const out = withProportionalWidths([
      { key: "id", width: 90 }, { key: "name", minWidth: 160, ellipsis: true },
      { key: "ip" }, { key: "seen", width: 150 }, { key: "p", width: "20%" },
    ], {});
    expect(out.map((c) => c.width)).toEqual([90, 160, 120, 150, "20%"]);
    expect(out.some((c) => c.key === FILL_KEY)).toBe(false);
  });

  it("拖拉過之後：每一欄定在拖拉當下的寬度，空白欄吸收多出來的空間（放在固定在右側的欄之前）", () => {
    const out = withProportionalWidths([
      { type: "selection" }, { key: "a", width: 100 }, { key: "b" }, { key: "act", width: 120, fixed: "right" },
    ], { a: 230, b: 410, act: 120 });
    expect(out.map((c) => c.key ?? c.type)).toEqual(["selection", "a", "b", FILL_KEY, "act"]);
    expect(out[1].width).toBe(230);
    expect(out[2].width).toBe(410);
    const fill = out[3];
    expect(fill.width).toBeUndefined();          // 沒有寬度的那一欄吸收剩下的空間
    expect(fill.resizable).toBe(false);
  });

  it("NDataTable：固定排版＋scroll-x 的表格套用比例寬度；沒有 scroll-x 的照舊（不會把右邊的欄擠出畫面）", () => {
    const cols = [{ key: "a", title: "A", ellipsis: true }, { key: "b", title: "B", width: 150 }];
    const withX = mount(NDataTable, { props: { columns: cols, data: [], scrollX: 900 } });
    const colEls = withX.findAll("colgroup col");
    expect(colEls.some((c) => (c.attributes("style") || "").includes("width: 120px"))).toBe(true);
    const noX = mount(NDataTable, { props: { columns: cols, data: [] } });
    expect(noX.findAll("colgroup col").some((c) => (c.attributes("style") || "").includes("width: 120px"))).toBe(false);
  });
});

// 客戶 2026-10-10：「用滑鼠就變成一直往左滑看資料、往右滑去按操作」—— 寬表格的操作欄在桌面寬度固定在右側
describe("操作欄固定在右側（stickyActions）", () => {
  const cols = () => [
    { key: "name", title: "N", minWidth: 160 },
    { key: "ip", title: "IP", width: 140 },
    { key: "actions", title: "A", width: 158, className: "col-actions" },
  ];

  it("最後一欄是操作欄、有寬度 → 固定在右側", () => {
    const out = withResizable<Record<string, unknown>>(cols(), { stickyActions: true });
    expect(out[2].fixed).toBe("right");
    expect(out[0].fixed).toBeUndefined();
  });

  it("只認 className 也算（有些頁面的操作欄 key 不叫 actions）", () => {
    const out = withResizable<Record<string, unknown>>(
      [{ key: "n", title: "N" }, { key: "ops", title: "O", width: 120, className: "col-actions" }], { stickyActions: true });
    expect(out[1].fixed).toBe("right");
  });

  it("沒開（手機寬度、表格不會橫向捲動）就不動", () => {
    expect(withResizable<Record<string, unknown>>(cols())[2].fixed).toBeUndefined();
    expect(withResizable<Record<string, unknown>>(cols(), { stickyActions: false })[2].fixed).toBeUndefined();
  });

  it("沒有寬度、不是最後一欄、已經寫了 fixed 的都不動", () => {
    const noWidth = withResizable<Record<string, unknown>>(
      [{ key: "n", title: "N" }, { key: "actions", title: "A" }], { stickyActions: true });
    expect(noWidth[1].fixed).toBeUndefined();
    const notLast = withResizable<Record<string, unknown>>(
      [{ key: "actions", title: "A", width: 100 }, { key: "n", title: "N" }], { stickyActions: true });
    expect(notLast[0].fixed).toBeUndefined();
    const explicit = withResizable<Record<string, unknown>>(
      [{ key: "n", title: "N" }, { key: "actions", title: "A", width: 100, fixed: false }], { stickyActions: true });
    expect(explicit[1].fixed).toBe(false);
  });

  it("掛在元件上：有 scroll-x 而且是桌面寬度才固定", async () => {
    const { setWideViewportForTest } = await import("@/utils/resizableColumns");
    setWideViewportForTest(true);
    const w = mount(NDataTable, { props: { columns: cols(), data: [{ name: "a", ip: "192.0.2.1" }], scrollX: 900 } });
    await nextTick();
    expect(w.findAll("th.n-data-table-th--fixed-right").length).toBe(1);
    setWideViewportForTest(false);
    await nextTick();
    expect(w.findAll("th.n-data-table-th--fixed-right").length).toBe(0);
    const noScroll = mount(NDataTable, { props: { columns: cols(), data: [] } });
    setWideViewportForTest(true);
    await nextTick();
    expect(noScroll.findAll("th.n-data-table-th--fixed-right").length).toBe(0);
  });
});
