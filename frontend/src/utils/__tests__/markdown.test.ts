import { describe, expect, it } from "vitest";

import { renderInlineMarkdown, renderMarkdown } from "../markdown";

describe("renderMarkdown", () => {
  // 客戶回報：AI 對話裡的工具名稱 `recent_ip_changes` 被渲染成 recent<em>ip</em>changes，
  // 底線之間的字變成斜體。CommonMark 明訂**字中間的底線不算強調**，就是為了 snake_case
  // 這種識別字；星號才允許字中強調。
  it("字中間的底線不是斜體（工具名稱要原樣顯示）", () => {
    const html = renderMarkdown("呼叫 recent_ip_changes 看看");
    expect(html).not.toContain("<em>");
    expect(html).toContain("recent_ip_changes");
  });

  it("多個底線的識別字也一樣", () => {
    const html = renderMarkdown("list_ai_findings 與 detect_external_exposure");
    expect(html).not.toContain("<em>");
    expect(html).toContain("list_ai_findings");
    expect(html).toContain("detect_external_exposure");
  });

  it("真正的底線斜體還是要能用", () => {
    expect(renderMarkdown("這是 _重點_ 沒錯")).toContain("<em>重點</em>");
  });

  it("星號斜體不受影響", () => {
    expect(renderMarkdown("這是 *重點* 沒錯")).toContain("<em>重點</em>");
  });

  it("粗體不受影響", () => {
    expect(renderMarkdown("這是 **重點**")).toContain("<strong>重點</strong>");
  });

  it("行內程式碼裡的底線原樣保留", () => {
    const html = renderMarkdown("用 `recent_ip_changes` 這支");
    expect(html).toContain("<code>recent_ip_changes</code>");
    expect(html).not.toContain("<em>");
  });

  it("仍然會跳脫 HTML（不能因為改規則而開了注入）", () => {
    expect(renderMarkdown("<img src=x onerror=alert(1)>")).not.toContain("<img");
  });
});

describe("renderInlineMarkdown", () => {
  // AI 巡檢的標題／說明／建議也是模型寫的，同樣會夾 markdown。但它們被放進既有的
  // <h3> / <p> 裡，不能再包一層區塊標籤 —— 巢狀 <p> 是無效 HTML，瀏覽器會自己拆開。
  it("把行內 code 渲染出來，不留下反引號", () => {
    const html = renderInlineMarkdown("發現裝置 `dhcp1` 具備 DHCP 伺服器功能");
    expect(html).toContain("<code>dhcp1</code>");
    expect(html).not.toContain("`");
  });

  it("不產生區塊標籤", () => {
    const html = renderInlineMarkdown("一段說明");
    expect(html).not.toContain("<p>");
    expect(html).not.toContain("<ul>");
  });

  it("粗體與斜體照樣支援", () => {
    expect(renderInlineMarkdown("這是 **重點**")).toContain("<strong>重點</strong>");
  });

  it("底線識別字不會被當成斜體", () => {
    // 巡檢文字常出現 dhcp_server、scan_agent 這類欄位名
    expect(renderInlineMarkdown("但 dhcp_server 欄位為 false")).toContain("dhcp_server");
    expect(renderInlineMarkdown("但 dhcp_server 欄位為 false")).not.toContain("<em>");
  });

  it("HTML 一律跳脫", () => {
    expect(renderInlineMarkdown("<img src=x onerror=alert(1)>")).not.toContain("<img");
  });
});

describe("模型寫的 LaTeX 符號（使用者 2026-11-06：路徑寫成「進階 $\\rightarrow$ 電路」）", () => {
  it("常見的箭頭與符號轉成文字，不再原樣顯示", () => {
    expect(renderMarkdown("路徑：**進階** $\\rightarrow$ **電路**")).toBe("<p>路徑：<strong>進階</strong> → <strong>電路</strong></p>");
    expect(renderInlineMarkdown("A $\\to$ B $ \\Rightarrow $ C $\\times$ 2 $\\geq$ 1")).toBe("A → B ⇒ C × 2 ≥ 1");
  });
  it("程式碼裡的、不認得的命令、一般的錢字號都不動", () => {
    expect(renderInlineMarkdown("`$\\rightarrow$`")).toBe("<code>$\\rightarrow$</code>");
    expect(renderInlineMarkdown("$\\frac$")).toBe("$\\frac$");
    expect(renderInlineMarkdown("$5 and $10")).toBe("$5 and $10");
  });
});
