<script setup lang="ts">
import { computed } from "vue";
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

// 淺色：白卡 + 柔和灰底，三層對比
const lightOverrides = {
  common: {
    ..._common,
    bodyColor:    "#f4f6fb",
    cardColor:    "#ffffff",
    modalColor:   "#ffffff",
    popoverColor: "#ffffff",
    tableColor:   "#ffffff",
    dividerColor: "#e6e9f0",
    borderColor:  "#e2e6ee",
    textColor1: "#111827",
    textColor2: "#374151",
    textColor3: "#6b7280",
  },
  LayoutSider:  { color: "#ffffff", borderColor: "#e6e9f0" },
  LayoutHeader: { color: "#ffffff", borderColor: "#e6e9f0" },
  Card: { color: "#ffffff", borderColor: "#e8ebf2" },
  Menu: _menuActive,
  DataTable: { thColor: "#f7f9fc", borderColor: "#eef1f6", tdColorHover: "#f7f9fc" },
  Tabs: { tabTextColorActiveLine: PRIMARY, barColor: PRIMARY },
};

// 深色：分層的深石板色（非純黑），細邊框 + 適度對比，避免「整片黑」
const darkOverrides = {
  common: {
    ..._common,
    bodyColor:    "#0b0f17",
    cardColor:    "#141a23",
    modalColor:   "#141a23",
    popoverColor: "#171e28",
    tableColor:   "#121821",
    dividerColor: "rgba(255, 255, 255, 0.08)",
    borderColor:  "rgba(255, 255, 255, 0.11)",
    textColor1: "#e6edf5",
    textColor2: "#aeb7c4",
    textColor3: "#7d8794",
    primaryColorHover: "#34d399",
  },
  LayoutSider:  { color: "#0e131c", borderColor: "rgba(255,255,255,0.07)" },
  LayoutHeader: { color: "#0e131c", borderColor: "rgba(255,255,255,0.07)" },
  Card: { color: "#141a23", borderColor: "rgba(255,255,255,0.08)" },
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

/* 卡片標頭 extra 區（明細頁的 重新整理 / 欄位 等）按鈕一律 medium 高度，
   避免 popover-trigger 與一般按鈕視覺上一高一矮 */
.n-card-header__extra .n-button:not(.n-button--small-type) {
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
</style>
