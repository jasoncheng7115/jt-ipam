<script setup lang="ts">
import { computed, onMounted } from "vue";
import SessionGuard from "@/components/SessionGuard.vue";
import {
  NConfigProvider,
  NMessageProvider,
  NDialogProvider,
  NNotificationProvider,
  NLoadingBarProvider,
  darkTheme,
  zhTW,
  enUS,
  dateZhTW,
  dateEnUS,
} from "naive-ui";
import { storeToRefs } from "pinia";
import { useUiStore } from "@/stores/ui";

const ui = useUiStore();
const { effectiveTheme, locale } = storeToRefs(ui);

// 啟動時用後端偏好同步佈景 / 語言（跨裝置一致）
onMounted(() => { void ui.hydrateFromServer(); });

const naiveTheme = computed(() => (effectiveTheme.value === "dark" ? darkTheme : null));
const naiveLocale = computed(() => (locale.value === "zh-TW" ? zhTW : enUS));
const naiveDateLocale = computed(() => (locale.value === "zh-TW" ? dateZhTW : dateEnUS));

// 共用：品牌綠 + 較大圓角，給整站一致的調性
const PRIMARY = "#18a058";
const PRIMARY_HOVER = "#36ad6a";
const PRIMARY_PRESSED = "#0c7a43";
const _common = {
  borderRadius: "10px",
  borderRadiusSmall: "7px",
  primaryColor: PRIMARY,
  primaryColorHover: PRIMARY_HOVER,
  primaryColorPressed: PRIMARY_PRESSED,
  primaryColorSuppl: PRIMARY_HOVER,
};
const _menuActive = {
  itemColorActive:          "rgba(24, 160, 88, 0.14)",
  itemColorActiveHover:     "rgba(24, 160, 88, 0.20)",
  itemColorActiveCollapsed: "rgba(24, 160, 88, 0.14)",
  itemTextColorActive:      PRIMARY_HOVER,
  itemTextColorActiveHover: PRIMARY_HOVER,
  itemIconColorActive:      PRIMARY_HOVER,
  itemIconColorActiveHover: PRIMARY_HOVER,
  itemTextColorChildActive: PRIMARY_HOVER,
  itemIconColorChildActive: PRIMARY_HOVER,
};

// 淺色：白卡 + 柔和冷灰底，三層對比；狀態色更鮮明
const lightOverrides = {
  common: {
    ..._common,
    bodyColor:    "#eef1f8",
    cardColor:    "#ffffff",
    modalColor:   "#ffffff",
    popoverColor: "#ffffff",
    tableColor:   "#ffffff",
    dividerColor: "#e6e9f0",
    borderColor:  "#e2e6ee",
    textColor1: "#0f172a",
    textColor2: "#334155",
    textColor3: "#64748b",
    infoColor:        "#2563eb",
    infoColorHover:   "#3b82f6",
    successColor:     PRIMARY,
    successColorHover: PRIMARY_HOVER,
    warningColor:     "#f59e0b",
    warningColorHover: "#fbbf24",
    errorColor:       "#ef4444",
    errorColorHover:  "#f87171",
  },
  LayoutSider:  { color: "#ffffff", borderColor: "#e6e9f0" },
  LayoutHeader: { color: "#ffffff", borderColor: "#e6e9f0" },
  Card: { color: "#ffffff", borderColor: "#e8ebf2" },
  Menu: _menuActive,
  DataTable: { thColor: "#f7f9fc", borderColor: "#eef1f6", tdColorHover: "#f7f9fc" },
  Tabs: { tabTextColorActiveLine: PRIMARY, barColor: PRIMARY },
};

// 深色：科幻風——藍黑深色 + 青色(cyan)點綴、霓光綠主色，分層藍石板，細邊框帶藍調
const darkOverrides = {
  common: {
    ..._common,
    bodyColor:    "#070b14",
    cardColor:    "#0f1825",
    modalColor:   "#0f1825",
    popoverColor: "#132030",
    tableColor:   "#0d1622",
    dividerColor: "rgba(120, 180, 255, 0.10)",
    borderColor:  "rgba(120, 180, 255, 0.14)",
    textColor1: "#e8f0fb",
    textColor2: "#aab8cc",
    textColor3: "#7585a0",
    primaryColorHover: "#34d399",
    infoColor:        "#22d3ee",
    infoColorHover:   "#67e8f9",
    successColor:     "#10b981",
    successColorHover: "#34d399",
    warningColor:     "#f59e0b",
    warningColorHover: "#fbbf24",
    errorColor:       "#fb7185",
    errorColorHover:  "#fda4af",
  },
  LayoutSider:  { color: "#0a1019", borderColor: "rgba(120,180,255,0.10)" },
  LayoutHeader: { color: "#0a1019", borderColor: "rgba(120,180,255,0.10)" },
  Card: { color: "#0f1825", borderColor: "rgba(120,180,255,0.12)" },
  Menu: {
    ..._menuActive,
    itemColorActive:          "rgba(52, 211, 153, 0.16)",
    itemColorActiveHover:     "rgba(52, 211, 153, 0.22)",
    itemColorActiveCollapsed: "rgba(52, 211, 153, 0.16)",
    itemTextColorActive:      "#34d399",
    itemTextColorActiveHover: "#34d399",
    itemIconColorActive:      "#34d399",
    itemIconColorActiveHover: "#34d399",
    itemTextColorChildActive: "#34d399",
    itemIconColorChildActive: "#34d399",
  },
  DataTable: { thColor: "#1a212c", borderColor: "rgba(255,255,255,0.07)", tdColorHover: "rgba(255,255,255,0.04)" },
  Tabs: { tabTextColorActiveLine: "#34d399", barColor: "#34d399" },
};
const themeOverrides = computed(() =>
  effectiveTheme.value === "dark" ? darkOverrides : lightOverrides,
);
</script>

<template>
  <n-config-provider :theme="naiveTheme" :theme-overrides="themeOverrides"
                     :locale="naiveLocale" :date-locale="naiveDateLocale">
    <n-loading-bar-provider>
      <n-dialog-provider>
        <n-notification-provider>
          <n-message-provider>
            <SessionGuard />
            <router-view />
          </n-message-provider>
        </n-notification-provider>
      </n-dialog-provider>
    </n-loading-bar-provider>
  </n-config-provider>
</template>

<style>
/* 卡片柔和陰影：淺色模式給層次感（深色靠表面/邊框對比，黑陰影本就不明顯） */
.n-card { box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04), 0 2px 6px rgba(16, 24, 40, 0.05); }

/* 表格「操作」欄：依「該欄實際可用寬度」自動決定顯示完整按鈕或只剩 icon。
   欄位寬度不足以容納完整按鈕時 → 收成只剩 icon（不換行）。
   只要把該欄 column 設 className: "col-actions" 即可套用，免改每顆按鈕。 */
td.col-actions { container-type: inline-size; }
td.col-actions .n-space { flex-wrap: nowrap !important; }
@container (max-width: 230px) {
  td.col-actions .n-button__content { font-size: 0; justify-content: center; }
  td.col-actions .n-button__content .n-icon { font-size: 16px; }
  td.col-actions .n-button__content .n-button__icon { margin: 0 !important; }
  td.col-actions .n-button { padding-left: 9px !important; padding-right: 9px !important; }
}
/* 不支援容器查詢的瀏覽器：窄視窗 fallback */
@media (max-width: 1366px) {
  td.col-actions .n-button__content { font-size: 0; justify-content: center; }
  td.col-actions .n-button__content .n-icon { font-size: 16px; }
  td.col-actions .n-button__content .n-button__icon { margin: 0 !important; }
  td.col-actions .n-button { padding-left: 9px !important; padding-right: 9px !important; }
}

/* 中性半透明捲軸：深色/淺色主題都自然（取代瀏覽器預設的深色捲軸） */
* {
  scrollbar-width: thin;
  scrollbar-color: rgba(128, 128, 128, 0.45) transparent;
}

/* 文字選取色：用半透明品牌綠 tint，淺色/深色主題下文字都看得到
   （原本淺色主題選取色太深會把字蓋掉） */
::selection { background: rgba(24, 160, 88, 0.30); }
::-moz-selection { background: rgba(24, 160, 88, 0.30); }
::-webkit-scrollbar { width: 11px; height: 11px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb {
  background: rgba(128, 128, 128, 0.45);
  border-radius: 6px;
  border: 2px solid transparent;
  background-clip: content-box;
}
::-webkit-scrollbar-thumb:hover { background: rgba(128, 128, 128, 0.7); background-clip: content-box; }

/* 表格欄位標題一律不換行：短的 CJK 標題（如「子網路」）碰到 sorter 箭頭預留的
   空間時不會被擠成兩行；欄位放得下會自動撐寬（表格都有 scroll-x）。
   只影響標題，資料 cell 仍照各欄 ellipsis 設定截斷。 */
.n-data-table-th__title { white-space: nowrap; }

/* 統一按鈕高度：把 size="small" 的工具列按鈕（重新整理 / 欄位 / 匯出 / 篩選區等）
   一律拉到與 medium 同高 34px，避免一排按鈕高低不齊。tiny（表格內 icon、chat）與
   text 按鈕不動。ColumnPicker / ExportButton 的 trigger 也吃得到。 */
.n-button--small-type:not(.n-button--text):not(.n-button--tiny-type) {
  --n-height: 34px;
  min-height: 34px;
}
.n-card-header__extra .n-button:not(.n-button--tiny-type) {
  --n-height: 34px;
  min-height: 34px;
}

html,
body,
#app {
  height: 100%;
  margin: 0;
  font-family:
    -apple-system, BlinkMacSystemFont, "PingFang TC", "Microsoft JhengHei",
    "Noto Sans TC", "Helvetica Neue", Arial, sans-serif;
}

/* 淺色：給卡片一點陰影 + 圓角，從一片白裡浮出來 */
html[data-theme="light"] .n-card {
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06),
              0 0 0 1px rgba(15, 23, 42, 0.04);
}
html[data-theme="light"] .n-layout-sider {
  box-shadow: 1px 0 0 rgba(15, 23, 42, 0.05);
}
html[data-theme="light"] .n-layout-header {
  box-shadow: 0 1px 0 rgba(15, 23, 42, 0.05);
}

/* ── 全站卡片層次（讓每一頁都有結構，不只儀表板） ── */
/* 卡片標題列：淡灰帶狀底，與儀表板一致；modal 標題也會套到，視覺一致。
   margin-bottom 讓帶狀底與下方內容（工具列 / 表格）留白，不要黏在一起。 */
.n-card > .n-card-header {
  background: rgba(100, 116, 139, 0.10);
  border-radius: 10px 10px 0 0;
  margin-bottom: 14px;
}
/* 深色模式：帶狀底要更亮一點才看得出來（比表頭再亮一階） */
html[data-theme="dark"] .n-card > .n-card-header {
  background: rgba(148, 163, 184, 0.18);
}
/* 深色模式：卡片加細邊框，從深背景浮出來 */
html[data-theme="dark"] .n-card {
  border: 1px solid rgba(148, 163, 184, 0.12);
}
/* 深色模式：表格表頭帶底色 + 列分隔，讓表格不再「奄奄一息」 */
html[data-theme="dark"] .n-data-table-th {
  background-color: rgba(148, 163, 184, 0.10) !important;
}
html[data-theme="dark"] .n-data-table-td {
  border-bottom: 1px solid rgba(148, 163, 184, 0.07);
}
html[data-theme="dark"] .n-data-table-tr:hover .n-data-table-td {
  background-color: rgba(148, 163, 184, 0.06) !important;
}

/* ── 深色科幻風點綴 ── */
/* 主畫面背景：極淡的藍/青徑向光暈，從深藍黑浮出層次（非整片死黑） */
html[data-theme="dark"] body {
  background:
    radial-gradient(1200px 700px at 12% -8%, rgba(34, 211, 238, 0.06), transparent 60%),
    radial-gradient(1100px 600px at 100% 0%, rgba(52, 211, 153, 0.05), transparent 55%),
    #070b14;
}
html[data-theme="dark"] .n-card { backdrop-filter: saturate(1.05); }
/* 卡片標題列改藍青漸層帶，科幻一點 */
html[data-theme="dark"] .n-card > .n-card-header {
  background: linear-gradient(90deg, rgba(34,211,238,0.10), rgba(52,211,153,0.06));
}
/* 主要按鈕：淡淡霓光 */
html[data-theme="dark"] .n-button.n-button--primary-type:not(.n-button--disabled) {
  box-shadow: 0 0 0 1px rgba(52,211,153,0.25), 0 2px 10px rgba(16,185,129,0.18);
}
/* 側欄選中項：左側青綠光條 */
html[data-theme="dark"] .n-menu .n-menu-item-content--selected::before {
  content: "";
  position: absolute; left: 0; top: 14%; bottom: 14%; width: 3px;
  background: linear-gradient(#22d3ee, #34d399);
  border-radius: 0 3px 3px 0; box-shadow: 0 0 8px rgba(34,211,238,0.6);
}
/* 淺色：卡片標題列改為非常淡的品牌綠帶，比純灰更有生氣 */
html[data-theme="light"] .n-card > .n-card-header {
  background: linear-gradient(90deg, rgba(24,160,88,0.07), rgba(37,99,235,0.04));
}

/* ════════════════════════════════════════════════════════════════
   手機 / 窄視窗排版（≤640px）
   主畫面只剩 ~320px 寬，預設多欄佈局會把文字擠成一字一行。下面把幾個
   全站共用的容器在窄螢幕改成「直向堆疊」，免去逐頁改。
   ════════════════════════════════════════════════════════════════ */
@media (max-width: 640px) {
  /* 內容區與卡片留白縮小，把寶貴的水平空間還給內容 */
  .n-layout-content .n-layout-scroll-container { padding: 10px !important; }
  .n-card > .n-card__content { padding: 12px !important; }
  .n-card > .n-card-header { padding: 12px 12px 10px !important; }

  /* 卡片標題列：標題與「操作按鈕區(header-extra)」改上下堆疊，
     避免長標題(如 192.168.1.0/24)被按鈕擠到一字一行 */
  .n-card > .n-card-header { flex-wrap: wrap; row-gap: 8px; }
  .n-card > .n-card-header > .n-card-header__main { flex: 1 1 100%; min-width: 0; }
  .n-card > .n-card-header > .n-card-header__extra {
    flex: 1 1 100%; margin-left: 0 !important; justify-content: flex-start;
  }
  .n-card > .n-card-header > .n-card-header__extra .n-space { justify-content: flex-start; }

  /* bordered Descriptions：把表格攤平成單欄堆疊
     （原本 :column=3 → 6 格擠在一行，CJK 與 IP 都被折成直書）。
     label 一行、值一行，邊框維持卡片感。 */
  .n-descriptions.n-descriptions--bordered .n-descriptions-table,
  .n-descriptions.n-descriptions--bordered .n-descriptions-table tbody,
  .n-descriptions.n-descriptions--bordered .n-descriptions-table-row,
  .n-descriptions.n-descriptions--bordered .n-descriptions-table-header,
  .n-descriptions.n-descriptions--bordered .n-descriptions-table-content {
    display: block !important;
    width: auto !important;
  }
  .n-descriptions.n-descriptions--bordered .n-descriptions-table-header {
    /* label 列：淡底、小字、不換行折字 */
    white-space: normal;
    font-size: 12px;
    opacity: 0.85;
    padding: 6px 12px !important;
  }
  .n-descriptions.n-descriptions--bordered .n-descriptions-table-content {
    padding: 8px 12px !important;
    word-break: break-word;
  }
  /* 相鄰列之間補一條分隔線（block 化後原本的格線會消失） */
  .n-descriptions.n-descriptions--bordered .n-descriptions-table-row + .n-descriptions-table-row {
    border-top: 1px solid var(--n-merged-td-color, rgba(128,128,128,0.18));
  }

  /* 頂列工具區（語言 / 佈景 / 通知 / 使用者）允許換行，
     否則窄螢幕全擠成一列、選單與登出按鈕被推出畫面外點不到 */
  .topbar .n-space { flex-wrap: wrap !important; }
}
</style>
