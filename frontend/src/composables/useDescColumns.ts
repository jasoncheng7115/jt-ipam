import { onBeforeUnmount, ref, watch, type Ref } from "vue";

/**
 * 標題在左的描述表格要幾欄：依它自己的實際寬度（2026-11-07 iPad 橫向回報）。
 *
 * 寫死三欄時，iPad 這種寬度（內容區約 750px）每一格只剩一點點：標題被擠成一字一行，
 * 標題不折字之後，換成「10.20.0.0/24」、日期被拆成好幾行。三組「標題＋內容」大約要 960px，
 * 不到就改兩欄。手機版另外由全域樣式改成上下排，這裡不管。
 */
export const DESC_THREE_COLUMNS_MIN = 960;

export function descColumnsFor(width: number, max = 3): number {
  if (width <= 0) return max;
  return width >= DESC_THREE_COLUMNS_MIN ? Math.min(3, max) : Math.min(2, max);
}

export function useDescColumns(el: Ref<HTMLElement | { $el?: HTMLElement } | null>, max = 3) {
  const cols = ref(max);
  let ro: ResizeObserver | null = null;
  const node = () => {
    const v = el.value as HTMLElement | { $el?: HTMLElement } | null;
    return v instanceof HTMLElement ? v : (v?.$el ?? null);
  };
  const update = () => { cols.value = descColumnsFor(node()?.clientWidth ?? 0, max); };
  // 表格常在資料載入後才出現（v-if）：元素換了就重新觀察
  watch(() => node(), (n) => {
    ro?.disconnect();
    if (!n) return;
    update();
    if (typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(update);
      ro.observe(n);
    }
  }, { flush: "post", immediate: true });
  onBeforeUnmount(() => ro?.disconnect());
  return cols;
}
