/**
 * 全域「懸浮水平捲軸」。
 *
 * 當頁面上有水平內容超出、但其原生水平捲軸落在可視範圍「下方」（要捲到頁尾才摸得到）
 * 的表格時，在視窗底部固定顯示一條捲軸，與該表格雙向同步左右捲動。
 * 一次只追蹤「目前在可視範圍內、且需要」的那個捲動容器（取可視面積最大者）。
 *
 * 在 MainLayout setup 內呼叫一次即全站生效；零相依。
 */
import { onBeforeUnmount, onMounted } from "vue";

// Naive DataTable 的水平捲動容器
const SELECTOR = ".n-data-table .n-scrollbar-container";

export function useFloatingHScroll() {
  let bar: HTMLDivElement | null = null;
  let inner: HTMLDivElement | null = null;
  let target: HTMLElement | null = null;
  let raf = 0;
  let timer = 0;
  let mo: MutationObserver | null = null;

  function ensureBar() {
    if (bar) return;
    bar = document.createElement("div");
    bar.className = "jt-float-hscroll";
    Object.assign(bar.style, {
      position: "fixed",
      bottom: "0px",
      height: "14px",
      overflowX: "auto",
      overflowY: "hidden",
      zIndex: "8000",
      display: "none",
      background: "var(--n-color, rgba(127,127,127,0.06))",
      borderTop: "1px solid rgba(127,127,127,0.25)",
      boxShadow: "0 -2px 6px rgba(0,0,0,0.06)",
    });
    inner = document.createElement("div");
    inner.style.height = "1px";
    bar.appendChild(inner);
    document.body.appendChild(bar);
    bar.addEventListener("scroll", () => {
      if (target && Math.abs(target.scrollLeft - bar!.scrollLeft) > 1) {
        target.scrollLeft = bar!.scrollLeft;
      }
    });
  }

  function pickTarget(): HTMLElement | null {
    const cands = Array.from(document.querySelectorAll<HTMLElement>(SELECTOR))
      .filter((e) => e.scrollWidth - e.clientWidth > 2);
    let best: HTMLElement | null = null;
    let bestArea = 0;
    for (const e of cands) {
      const r = e.getBoundingClientRect();
      const visArea = Math.min(r.bottom, window.innerHeight) - Math.max(r.top, 0);
      // 在可視範圍內，且底部（原生捲軸所在）超出可視範圍下緣 → 需要懸浮捲軸
      if (visArea > 0 && r.bottom > window.innerHeight - 16 && visArea > bestArea) {
        best = e;
        bestArea = visArea;
      }
    }
    return best;
  }

  function update() {
    raf = 0;
    ensureBar();
    target = pickTarget();
    if (!target || !bar || !inner) {
      if (bar) bar.style.display = "none";
      return;
    }
    const r = target.getBoundingClientRect();
    bar.style.left = `${r.left}px`;
    bar.style.width = `${r.width}px`;
    inner.style.width = `${target.scrollWidth}px`;
    if (Math.abs(bar.scrollLeft - target.scrollLeft) > 1) bar.scrollLeft = target.scrollLeft;
    bar.style.display = "block";
  }

  function schedule() {
    if (!raf) raf = requestAnimationFrame(update);
  }

  function onScrollCapture(e: Event) {
    // 表格自身水平捲動 → 同步懸浮捲軸；其它捲動（頁面）→ 重新定位
    if (target && e.target === target && bar) {
      bar.scrollLeft = target.scrollLeft;
    } else {
      schedule();
    }
  }

  onMounted(() => {
    ensureBar();
    schedule();
    document.addEventListener("scroll", onScrollCapture, true);
    window.addEventListener("resize", schedule);
    mo = new MutationObserver(() => schedule());
    mo.observe(document.body, { childList: true, subtree: true });
    timer = window.setInterval(schedule, 1200); // 內容/欄寬變動的保險
  });

  onBeforeUnmount(() => {
    document.removeEventListener("scroll", onScrollCapture, true);
    window.removeEventListener("resize", schedule);
    mo?.disconnect();
    if (timer) clearInterval(timer);
    if (raf) cancelAnimationFrame(raf);
    bar?.remove();
    bar = null;
  });
}
