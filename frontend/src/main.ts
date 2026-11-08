import { createApp } from "vue";
import { createPinia } from "pinia";
import { NDataTable, NTabs } from "naive-ui";

import App from "@/App.vue";
import { router } from "@/router";
import { i18n } from "@/i18n";
import { reportPageLoad, startPageDiag } from "@/utils/pageDiag";
import { installResizableColumns } from "@/utils/resizableColumns";
import { installColResizeStyle, vColResize } from "@/directives/colResize";
import { installTabArrowStyle, installTabScrollArrows } from "@/utils/tabScrollArrows";
import { setExportLogger } from "@/utils/saveFile";
import { reportExportEvent } from "@/api/exportEvents";
import { installSelectionCellClick } from "@/utils/selectionCellClick";
import { installViewportHeight } from "@/utils/viewportHeight";

// 全站表格欄寬可拖拉（必須在任何畫面建立表格之前）
installResizableColumns(NDataTable);
// 頁籤列放不下時兩端出現左右捲動按鈕（沒有滾輪的滑鼠也到得了最左、最右）
installTabScrollArrows(NTabs);
installTabArrowStyle();
// 表格勾選欄整格都可以點（點到勾選框旁邊不會變成點整列、跳進詳細頁）
installSelectionCellClick();

// 版面高度用實際視窗高度（iPad Safari 剛載入時 100dvh 偏小，下面留一大片空白）
installViewportHeight();

// 頁面載入診斷（「切回分頁就整頁重新載入」要靠這個分辨是誰重載的）
startPageDiag();

// 瀏覽器端產生的檔案（表格、報告、拓樸圖…）存檔時回報一筆匯出稽核
setExportLogger(reportExportEvent);

const app = createApp(App);
app.use(createPinia());
app.use(router);
app.use(i18n);
// 手寫 <table> 的欄寬拖拉（NDataTable 已在上面統一處理）
app.directive("col-resize", vColResize);
installColResizeStyle();
app.mount("#app");
void router.isReady().then(() => reportPageLoad());
