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
  jaJP,
  dateZhTW,
  dateEnUS,
  dateJaJP,
} from "naive-ui";
import { storeToRefs } from "pinia";
import { useUiStore } from "@/stores/ui";

const ui = useUiStore();
const { effectiveTheme, locale } = storeToRefs(ui);

// 啟動時用後端偏好同步佈景 / 語言（跨裝置一致）
onMounted(() => { void ui.hydrateFromServer(); });

const naiveTheme = computed(() => (effectiveTheme.value === "dark" ? darkTheme : null));
// naive-ui 自己的語系包（日期挑選器、分頁、上傳等內建字串）。少一種語言時，
// 元件內建的字會退回英文，而外圍是日文 —— 畫面上會一半一半。
const naiveLocale = computed(() =>
  locale.value === "zh-TW" ? zhTW : locale.value === "ja-JP" ? jaJP : enUS,
);
const naiveDateLocale = computed(() =>
  locale.value === "zh-TW" ? dateZhTW : locale.value === "ja-JP" ? dateJaJP : dateEnUS,
);

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
// 表頭底色 —— n-data-table 與手刻表頭（AI 巡檢的發現清單）共用同一個值。
// 深色模式下方另有一條 `.n-data-table-th` 的 !important 規則會蓋掉 naive 的 thColor，
// 所以「深色實際看到的顏色」是 TH_DARK 這個半透明值，不是 thColor 的底色。
// 兩處都引用同一個常數，不要各寫各的 —— 否則改了一邊、另一邊就悄悄不一致。
const TH_LIGHT = "#f7f9fc";
const TH_DARK = "rgba(148, 163, 184, 0.10)";
const TH_DARK_BASE = "#1a212c";
// 固定欄（操作欄 fixed: "right"）用 sticky 疊在捲動中的欄位上面：深色的表頭與滑過列是半透明的，
// 固定欄照用就會透出底下的欄位、字疊在一起。這三個是「半透明值疊在卡片底色 #0f1825 上」算出來的不透明色，
// 看起來與旁邊一樣。卡片底色或 TH_DARK 改了要一起重算。
const FIXED_TH_DARK = "#1c2634";        // TH_DARK 疊在卡片上
const FIXED_TD_DARK = "#0f1825";        // 卡片底色
const FIXED_TD_HOVER_DARK = "#17202e";  // 滑過列 rgba(148,163,184,.06) 疊在卡片上

// 頁籤要看得出可以切換（使用者 2026-10-16：「頁籤視覺上不夠明顯，常常被忽略」）：每個頁籤是有內距的按鈕、
// 彼此緊湊、選中的加粗；底色條與滑過效果在下方全域樣式（.n-tabs--bar-type／--line-type）
const _tabsShape = {
  tabFontWeight: "500", tabFontWeightActive: "600",
  tabPaddingSmallBar: "6px 12px", tabPaddingMediumBar: "8px 14px", tabPaddingLargeBar: "10px 16px",
  tabPaddingSmallLine: "6px 12px", tabPaddingMediumLine: "8px 14px", tabPaddingLargeLine: "10px 16px",
  tabGapSmallBar: "2px", tabGapMediumBar: "4px", tabGapLargeBar: "4px",
  tabGapSmallLine: "2px", tabGapMediumLine: "4px", tabGapLargeLine: "4px",
};

/* 單選按鈕組（分層／自動排列這類）的 small 高度要與全站 small 按鈕一致：下面的 CSS 把 small 按鈕統一拉到 34px，
   單選按鈕組沒跟上，同一排會高低不齊（使用者 10-21 指出 IP 變更評估關係圖的工具列） */
const _radioShape = { buttonHeightSmall: "34px" };
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
  DataTable: { thColor: TH_LIGHT, borderColor: "#eef1f6", tdColorHover: "#f7f9fc" },
  Radio: _radioShape,
  Tabs: { ..._tabsShape, tabTextColorActiveLine: PRIMARY, tabTextColorActiveBar: PRIMARY, barColor: PRIMARY,
          tabTextColorLine: "#334155", tabTextColorBar: "#334155" },
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
  DataTable: { thColor: TH_DARK_BASE, borderColor: "rgba(255,255,255,0.07)", tdColorHover: "rgba(255,255,255,0.04)" },
  Radio: _radioShape,
  Tabs: { ..._tabsShape, tabTextColorActiveLine: "#34d399", tabTextColorActiveBar: "#34d399", barColor: "#34d399",
          tabTextColorLine: "#c3cfdf", tabTextColorBar: "#c3cfdf" },
};
const themeOverrides = computed(() =>
  effectiveTheme.value === "dark" ? darkOverrides : lightOverrides,
);

// 同一個值也用 CSS 變數送出去，手刻表頭（AI 巡檢的發現清單）才能跟 n-data-table 一致。
// 複製一份色碼到別的檔案的話，主題一改就會有一個地方沒跟上。
const cssVars = computed(() => ({
  "--table-th-color": effectiveTheme.value === "dark" ? TH_DARK : TH_LIGHT,
  "--table-fixed-th-color": FIXED_TH_DARK,
  "--table-fixed-td-color": FIXED_TD_DARK,
  "--table-fixed-td-hover": FIXED_TD_HOVER_DARK,
}));
</script>

<template>
  <!-- inline-theme-disabled：把主題樣式從 inline style 屬性改寫進 <style> 區塊，縮小 CSP
       style-src 的 inline 面積（補償控制，見 SECURITY.md「Accepted risks」）＋ SSR/效能。 -->
  <n-config-provider :theme="naiveTheme" :theme-overrides="themeOverrides" :inline-theme-disabled="true"
                     :locale="naiveLocale" :date-locale="naiveDateLocale"
                     :style="cssVars">
    <n-loading-bar-provider>
      <!-- 主題色以 CSS 變數往下送，手刻元件才不用自己複製一份色碼 -->
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
/* ── 頁籤看得出可以切換（使用者 2026-10-16）──
   以前只是一排字加一條底線，沒選中的頁籤看起來不像能點。現在：頁籤列有淡底色條、每個頁籤是圓角按鈕，
   滑過有底色、選中的加底色與 3px 底線。只套用預設（bar）與 line 型；segment／card 型本來就像按鈕。 */
/* 淺色：不要灰色底條（使用者 2026-10-16：一整條灰色色塊不好看），改用頁籤列下方細線＋選中淡綠底 */
html { --jt-tabs-strip: transparent; --jt-tabs-hover: rgba(15, 23, 42, 0.04);
       --jt-tabs-active: rgba(24, 160, 88, 0.08); --jt-tabs-line: #e5e9f0; }
html[data-theme="dark"] { --jt-tabs-strip: rgba(148, 197, 255, 0.05); --jt-tabs-hover: rgba(148, 197, 255, 0.09);
       --jt-tabs-active: rgba(52, 211, 153, 0.14); --jt-tabs-line: transparent; }
.n-tabs.n-tabs--bar-type > .n-tabs-nav,
.n-tabs.n-tabs--line-type > .n-tabs-nav {
  background: var(--jt-tabs-strip);
  border-radius: 8px 8px 0 0;
  padding: 4px 6px 0;
  box-shadow: inset 0 -1px 0 var(--jt-tabs-line);
}
.n-tabs.n-tabs--bar-type .n-tabs-tab,
.n-tabs.n-tabs--line-type .n-tabs-tab {
  border-radius: 6px 6px 0 0;
  cursor: pointer;
  transition: background-color .15s ease;
}
.n-tabs.n-tabs--bar-type .n-tabs-tab:not(.n-tabs-tab--active):not(.n-tabs-tab--disabled):hover,
.n-tabs.n-tabs--line-type .n-tabs-tab:not(.n-tabs-tab--active):not(.n-tabs-tab--disabled):hover {
  background: var(--jt-tabs-hover);
}
.n-tabs.n-tabs--bar-type .n-tabs-tab.n-tabs-tab--active,
.n-tabs.n-tabs--line-type .n-tabs-tab.n-tabs-tab--active {
  background: var(--jt-tabs-active);
}
.n-tabs.n-tabs--bar-type .n-tabs-bar,
.n-tabs.n-tabs--line-type .n-tabs-bar {
  height: 3px;
  border-radius: 3px 3px 0 0;
}

/* 標題在左的描述表格：標題不折字（iPad 寬度下「CIDR」「閘道」被擠成一字一行），寬度讓內容欄去換行。
   手機版另外改成上下排（下面 640px 的區塊會蓋過這條） */
.n-descriptions.n-descriptions--left-label-placement .n-descriptions-table-header { white-space: nowrap; }

/* 卡片柔和陰影：淺色模式給層次感（深色靠表面/邊框對比，黑陰影本就不明顯） */
.n-card { box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04), 0 2px 6px rgba(16, 24, 40, 0.05); }

/* tooltip 內若含連結（例如表格 ellipsis tooltip 會把綠色 <a> 一起複製進來），
   淺色主題下 tooltip 是深底，綠字看不清 → 讓 tooltip 內連結改用 tooltip 自身的淺色字 */
.n-tooltip a { color: inherit !important; text-decoration: underline; }
/* 提示文字不可以超出畫面：窄視窗（手機、縮小的瀏覽器）長的提示要換行（使用者回報 RustDesk 按鈕的提示被切掉） */
.n-tooltip { max-width: min(420px, calc(100vw - 80px)); box-sizing: border-box; }

/* 表格「操作」欄：依「該欄實際可用寬度」自動決定顯示完整按鈕或只剩 icon。
   欄位寬度不足以容納完整按鈕時 → 收成只剩 icon（不換行）。
   只要把該欄 column 設 className: "col-actions" 即可套用，免改每顆按鈕。 */
td.col-actions { container-type: inline-size; }
/* IP 欄：位址本身不斷行；後面的角色圖示（自動收錄、閘道、DHCP、固定分配…）放不下就換行，
   不可以溢出蓋到右邊的主機名稱欄（使用者回報綠色鎖頭擋到主機名稱） */
.ip-cell { display: inline-flex; align-items: center; flex-wrap: wrap; row-gap: 2px; max-width: 100%; }
.ip-cell-addr { white-space: nowrap; }
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
/* 清單 MAC 欄的 OUI 廠商（#38）：utils/macVendor.ts 產生，render 函式的元素吃不到 scoped style */
.mac-cell { display: flex; flex-direction: column; line-height: 1.3; min-width: 0; }
.mac-cell__vendor {
  font-size: 11px; opacity: 0.72; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%;
}
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

/* 手機：分頁列放不下時換行，「共 N 筆」維持一行（以前被擠成直排、頁碼超出畫面） */
@media (max-width: 640px) {
  .n-pagination { flex-wrap: wrap; row-gap: 6px; justify-content: flex-end; }
  .n-pagination .n-pagination-prefix { white-space: nowrap; }
}

/* 手機側欄打開時，後面的頁面不跟著捲（MainLayout 切換這個 class） */
html.sider-open,
html.sider-open body {
  overflow: hidden;
  overscroll-behavior: none;
}

/* 瀏覽器自己的配色（沒指定顏色的文字、捲軸、原生控制項）跟著 jt-ipam 的主題，不跟著作業系統。
   index.html 的 color-scheme 是 light dark（第一次繪製前用）：作業系統深色、jt-ipam 淺色時，瀏覽器給沒指定顏色的
   文字白色，掛在 body 底下的下拉選單裡的說明就成了白字白底（2026-10-06 使用者回報畫質、請求提權選單最後一塊空白） */
html[data-theme="light"] { color-scheme: light; }
html[data-theme="dark"] { color-scheme: dark; }
/* 子網路指示計的位址範圍底線（SubnetGrid.rangeBar）：淺色主題用深一點的顏色＋白色分隔線，
   亮青色壓在綠色格子上、又在白底上幾乎看不見（使用者 2026-10-11） */
html[data-theme="light"] { --jt-range-dhcp: #0369a1; --jt-range-reserved: #be185d; --jt-range-other: #475569; --jt-range-sep: #ffffff; }
html[data-theme="dark"] { --jt-range-dhcp: #22d3ee; --jt-range-reserved: #f472b6; --jt-range-other: #94a3b8; --jt-range-sep: rgba(0, 0, 0, 0.55); }

/* 淺色：給卡片一點陰影 + 圓角，從一片白裡浮出來 */
html[data-theme="light"] .n-card {
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06),
              0 0 0 1px rgba(15, 23, 42, 0.04);
}
/* 主控台（SSH／RDP／VNC／SFTP／RustDesk／BMC／noVNC）的連線卡片：整頁只有這一張卡浮在背景上，
   一般卡片那層淡陰影看不出邊緣（使用者 2026-10-20）。比照登入卡片；選擇器要比上面那條更具體才不會被蓋掉 */
html[data-theme="light"] .n-card.console-connect-card,
html:not([data-theme]) .n-card.console-connect-card {
  box-shadow: 0 10px 30px rgba(15, 23, 42, 0.12), 0 2px 8px rgba(15, 23, 42, 0.08);
}
html[data-theme="dark"] .n-card.console-connect-card {
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.45), 0 2px 8px rgba(0, 0, 0, 0.30);
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
  background-color: var(--table-th-color) !important;
}
html[data-theme="dark"] .n-data-table-td {
  border-bottom: 1px solid rgba(148, 163, 184, 0.07);
}
html[data-theme="dark"] .n-data-table-tr:hover .n-data-table-td {
  background-color: rgba(148, 163, 184, 0.06) !important;
}
/* 固定欄不可以半透明：會透出底下捲過去的欄位（色碼見 FIXED_*_DARK） */
html[data-theme="dark"] .n-data-table-th.n-data-table-th--fixed-left,
html[data-theme="dark"] .n-data-table-th.n-data-table-th--fixed-right {
  background-color: var(--table-fixed-th-color) !important;
}
html[data-theme="dark"] .n-data-table-td.n-data-table-td--fixed-left,
html[data-theme="dark"] .n-data-table-td.n-data-table-td--fixed-right {
  background-color: var(--table-fixed-td-color);
}
html[data-theme="dark"] .n-data-table-tr:hover .n-data-table-td.n-data-table-td--fixed-left,
html[data-theme="dark"] .n-data-table-tr:hover .n-data-table-td.n-data-table-td--fixed-right {
  background-color: var(--table-fixed-td-hover) !important;
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
  .n-layout-content .n-layout-scroll-container { padding: 10px 10px 88px !important; }  /* 底部留給 AI 助手浮動按鈕（MainLayout） */
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

  /* 頂列工具區（語言 / 佈景 / 通知 / 使用者）窄螢幕改 icon-only：
     隱藏文字標籤、整列不換行，搜尋框自動讓出空間，避免擠成多列把按鈕推出畫面 */
  .topbar .n-space { flex-wrap: nowrap !important; }
  .topbar-ctl__label { display: none !important; }
}
</style>
