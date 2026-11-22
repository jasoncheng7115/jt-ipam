/**
 * 調查報告匯出。四種格式都要能被目標工具正確開啟 —— 中文編碼是最常壞的一環。
 */
import { describe, expect, it } from "vitest";
import { buildReport, type ReportInput } from "../investigateReport";

const input: ReportInput = {
  ip: "192.0.2.10",
  generatedAt: "2026-08-07 10:00:00",
  summary: [["主機名稱", "proxy-01"], ["子網路", "192.0.2.0/24（服務網路區段）"]],
  conflicts: ["各來源回報的主機名稱不一致（3 種）"],
  sections: [
    { title: "DNS（2）", lines: ["A a.example.com", "A b.example.com"] },
    { title: "空的區塊", lines: [] },
  ],
  narrative: "這台是反向代理，多個域名指向同一個位址是正常的。",
  narrativeNote: "以下是推測，不是查核過的結論。",
  labels: {
    title: "192.0.2.10 調查報告", generatedAt: "產生時間", conflicts: "矛盾",
    narrative: "AI 判讀", csvSection: "區塊", csvContent: "內容", htmlLang: "zh-Hant",
  },
};

describe("調查報告", () => {
  it("Markdown 用標記符號，內容不漏", () => {
    const s = buildReport(input, "md");
    expect(s).toContain("# 192.0.2.10 調查報告");
    expect(s).toContain("## DNS（2）");
    expect(s).toContain("proxy-01");
    expect(s).toContain("反向代理");
  });

  it("空的區塊不輸出（不要留下一個孤零零的標題）", () => {
    expect(buildReport(input, "md")).not.toContain("空的區塊");
    expect(buildReport(input, "txt")).not.toContain("空的區塊");
  });

  it("HTML 宣告 UTF-8，並把角括號跳脫", () => {
    const s = buildReport({ ...input, summary: [["說明", "<script>x</script>"]] }, "html");
    expect(s).toContain('<meta charset="utf-8">');
    expect(s).not.toContain("<script>x</script>");
    expect(s).toContain("&lt;script&gt;");
  });

  it("CSV 逗號與引號要跳脫，否則欄位會錯位", () => {
    const s = buildReport({
      ...input,
      summary: [["說明", 'a,b "quoted"']],
    }, "csv");
    expect(s).toContain('"a,b ""quoted"""');
  });

  it("CSV 用 CRLF —— Excel 對只有 LF 的多行欄位處理不一致", () => {
    expect(buildReport(input, "csv")).toContain("\r\n");
  });

  it("純文字版不要出現 Markdown 的井字標題", () => {
    const s = buildReport(input, "txt");
    expect(s).not.toContain("## ");
    expect(s).toContain("【DNS（2）】");
  });
});

describe("報告不可以出現未取到值的痕跡", () => {
  it("沒有 undefined、[object Object]、或整包 JSON", () => {
    // 第一版把欄位取錯（v.hostname 而非 v.address.hostname、r.rtype 而非 r.type、
    // c.summary 根本不存在），於是匯出的摘要整排「—」、DNS 每行 undefined、
    // 異動記錄倒出原始 JSON。這條測試守的是那個症狀本身。
    const s = buildReport({
      ...input,
      sections: [{ title: "DNS", lines: ["A a.example.com", "A b.example.com"] }],
    }, "md");
    expect(s).not.toContain("undefined");
    expect(s).not.toContain("[object Object]");
    expect(s).not.toContain('{"event"');
  });

  it("HTML 版把判讀渲染成 HTML，而不是印出原始標記", () => {
    const s = buildReport({ ...input, narrativeHtml: "<p>這是<strong>重點</strong></p>" }, "html");
    expect(s).toContain("<strong>重點</strong>");
    expect(s).not.toContain("**重點**");
  });

  it("沒有渲染好的 HTML 時仍以純文字呈現，不會變空白", () => {
    const s = buildReport({ ...input, narrativeHtml: undefined }, "html");
    expect(s).toContain("反向代理");
  });
});
