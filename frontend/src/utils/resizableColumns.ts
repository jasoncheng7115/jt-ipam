/**
 * 全站表格的欄寬都能拖拉（2026-09-29 使用者要求：「所有有表格欄位標頭的，都要支援拖拉改變欄位寬度」）。
 *
 * 做在元件這一層、一次套上：七十幾個畫面各自在欄位定義補 `resizable` 一定會漏，之後的新畫面也會漏。
 * `NDataTable` 的 `columns` 在進入元件前先經過 `withResizable`：沒寫 `resizable` 的資料欄預設可拖拉，
 * 真的不要的欄位寫 `resizable: false`。只換掉 `columns` 這個公開屬性的值，不碰元件內部。
 */
import { computed, ref, type SetupContext } from "vue";

type Col = Record<string, unknown>;

// ── 操作欄固定在右側（客戶 2026-10-10：「用滑鼠就變成一直往左滑看資料、往右滑去按操作」）──
// 可以橫向捲動的表格，在桌面寬度把最後一欄「操作」固定在右側，捲到哪裡都按得到。
// 手機寬度不固定：一欄 150px 的操作欄會吃掉三分之一個畫面，手指滑動本來就順。
// 全站共用一個 matchMedia 監聽，不是每張表各掛一個。
const WIDE_QUERY = "(min-width: 768px)";
const wideViewport = ref(typeof window !== "undefined" && typeof window.matchMedia === "function"
  ? window.matchMedia(WIDE_QUERY).matches : true);
if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
  const mq = window.matchMedia(WIDE_QUERY);
  const onChange = () => { wideViewport.value = mq.matches; };
  if (typeof mq.addEventListener === "function") mq.addEventListener("change", onChange);
}

/** 測試用：模擬視窗寬度變化 */
export function setWideViewportForTest(wide: boolean): void { wideViewport.value = wide; }

function isActionsColumn(col: Col): boolean {
  return col.key === "actions" || (typeof col.className === "string" && col.className.includes("col-actions"));
}

/** 最後一欄是操作欄、有數字寬度、沒有自己寫 fixed → 固定在右側（元件要有寬度才算得出固定欄的位置） */
function withStickyActions(cols: Col[]): Col[] {
  const last = cols[cols.length - 1];
  if (!last || typeof last !== "object" || Array.isArray(last.children)) return cols;
  if (!isActionsColumn(last) || last.fixed !== undefined || typeof last.width !== "number") return cols;
  return [...cols.slice(0, -1), { ...last, fixed: "right" }];
}

function mapColumn(col: Col): Col {
  if (!col || typeof col !== "object") return col;
  // 勾選欄、展開欄不是資料欄
  if (col.type === "selection" || col.type === "expand") return col;
  if (Array.isArray(col.children)) {
    return col.children.length ? { ...col, children: withResizable(col.children as Col[]) } : col;
  }
  if (col.resizable !== undefined) return col;
  // 只加 resizable，寬度相關的設定一概不動：元件會拿 width 當標頭的最小寬度，
  // 曾經在這裡補一個 48px 的 minWidth 當拖拉下限，結果蓋掉了那個最小寬度，
  // 沒有固定排版的表格在手機上就把 IP 欄擠成直排（/advanced/connections）。
  return { ...col, resizable: true };
}

export function withResizable<T>(cols: T[] | undefined | null, opts: { stickyActions?: boolean } = {}): T[] {
  if (!Array.isArray(cols)) return cols as unknown as T[];
  const mapped = cols.map((c) => mapColumn(c as unknown as Col));
  return (opts.stickyActions ? withStickyActions(mapped) : mapped) as unknown as T[];
}

// ── 欄寬分配（2026-10-06 使用者：「寬度明明還夠，為何不自動適當分配」「拉一個寬度，其它剛剛拉過的又被改變」）──
//
// 只要有一欄設了 ellipsis（或 maxHeight／virtualScroll），元件就改用 table-layout: fixed，表格寬度固定撐滿 100%。
// 固定排版下，有 width 的欄位寬度不動，多出來的空間**全部平均**分給沒寫 width 的欄 —— 於是名稱、IP 欄寬得空蕩蕩，
// 日期欄（width 150）卻被擠到換行。拖拉也一樣：拉寬一欄，總寬還是 100%，空間就從其他欄挪過來，剛拉好的又變了。
//
// 作法（只對「固定排版而且設了 scroll-x」的表格；那種表格本來就能橫向捲動，不會把右邊的欄位擠掉）：
// 1. 一開始：沒寫 width 的欄補上 width（有 minWidth 就用它，否則 120），多出來的空間由瀏覽器**按比例**分給每一欄。
// 2. 第一次拖拉時：把每一欄定在當下的實際寬度，最右邊（固定在右側的欄之前）補一個空白欄吸收多出來的空間；
//    之後拉哪一欄就只動那一欄，其他欄不會跟著變。總寬超過畫面就橫向捲動。
export const FILL_KEY = "__jt_fill";
const DEFAULT_WIDTH = 120;

function leafColumns(cols: Col[]): Col[] {
  const out: Col[] = [];
  for (const c of cols) {
    if (!c || typeof c !== "object") continue;
    if (Array.isArray(c.children)) out.push(...leafColumns(c.children as Col[]));
    else out.push(c);
  }
  return out;
}

/** 跟元件自己的判斷一致：virtualScroll／flexHeight／maxHeight／任何一欄 ellipsis／明寫 fixed → 固定排版 */
export function usesFixedLayout(props: Record<string, unknown>, cols: Col[]): boolean {
  if (props.virtualScroll || props.flexHeight || props.maxHeight !== undefined || props.tableLayout === "fixed") return true;
  return leafColumns(cols).some((c) => !!c.ellipsis);
}

export function withProportionalWidths(cols: Col[], frozen: Record<string, number>): Col[] {
  const map = (col: Col): Col => {
    if (!col || typeof col !== "object") return col;
    if (col.type === "selection" || col.type === "expand") return col;
    if (Array.isArray(col.children)) return { ...col, children: (col.children as Col[]).map(map) };
    const key = col.key as string | undefined;
    if (key !== undefined && frozen[key] !== undefined) return { ...col, width: frozen[key] };
    if (col.width === undefined) {
      return { ...col, width: typeof col.minWidth === "number" ? col.minWidth : DEFAULT_WIDTH };
    }
    return col;
  };
  const out = cols.map(map);
  if (!Object.keys(frozen).length) return out;
  let i = out.length;
  while (i > 0 && (out[i - 1] as Col)?.fixed === "right") i--;
  const filler: Col = { key: FILL_KEY, title: "", resizable: false, className: "jt-fill-col", render: () => null };
  return [...out.slice(0, i), filler, ...out.slice(i)];
}

interface SetupComponent {
  setup?: (props: Record<string, unknown>, ctx: SetupContext) => unknown;
  __jtResizable?: boolean;
}

/**
 * 包住元件的 setup，讓它看到的 `props.columns` 是 `withResizable` 之後的版本
 * （可拖拉欄寬；可橫向捲動的表格在桌面寬度把操作欄固定在右側）。
 * 用 computed：父層就地改動欄位（響應式）時跟著重算，跟原本的行為一樣。
 */
export function installResizableColumns(component: unknown): void {
  const comp = component as SetupComponent;
  if (!comp || comp.__jtResizable || typeof comp.setup !== "function") return;
  const original = comp.setup;
  comp.setup = function (this: unknown, props, ctx) {
    // 第一次拖拉時每一欄的實際寬度（之後只動被拉的那一欄）
    const frozen = ref<Record<string, number>>({});
    const proportional = () => props.scrollX !== undefined
      && usesFixedLayout(props, (props.columns as Col[] | undefined) ?? []);
    const mapped = computed(() => {
      const cols = withResizable(props.columns as Col[] | undefined,
        { stickyActions: props.scrollX !== undefined && wideViewport.value });
      return Array.isArray(cols) && proportional() ? withProportionalWidths(cols, frozen.value) : cols;
    });
    function onUnstableColumnResize(...args: unknown[]): void {
      const own = props.onUnstableColumnResize;
      if (typeof own === "function") (own as (...a: unknown[]) => void)(...args);
      if (!proportional() || Object.keys(frozen.value).length) return;
      const getWidth = args[3] as ((key: string) => number | undefined) | undefined;
      if (typeof getWidth !== "function") return;
      const next: Record<string, number> = {};
      for (const c of leafColumns(mapped.value as Col[])) {
        const k = c.key as string | undefined;
        if (k === undefined || k === FILL_KEY || c.type === "selection" || c.type === "expand") continue;
        const w = getWidth(k);
        if (typeof w === "number" && w > 0) next[k] = Math.round(w);
      }
      frozen.value = next;
    }
    const proxied = new Proxy(props, {
      get(target, key, receiver) {
        if (key === "columns") return mapped.value;
        if (key === "onUnstableColumnResize") return onUnstableColumnResize;
        return Reflect.get(target, key, receiver);
      },
    });
    return original.call(this, proxied, ctx);
  };
  comp.__jtResizable = true;
}
