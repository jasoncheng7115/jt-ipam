import { describe, expect, it } from "vitest";
import { formatSummary, taskResultText } from "@/utils/taskSummary";

const t = (key: string, p?: Record<string, unknown>) => (p ? `${key}:${JSON.stringify(p)}` : key);

describe("taskSummary", () => {
  it("phpIPAM 搬移的摘要不會丟錯（以前迴圈變數叫 t，蓋掉了翻譯函式）", () => {
    const out = formatSummary("phpipam.migration",
      { tables: { sections: { inserted: 2, updated: 1, errored: 0 }, subnets: { skipped: 5 } } }, t);
    expect(out).toContain("sections");
    expect(out).toContain("common.added_n");
    expect(out).not.toContain("subnets");   // 只 skip 的不列
  });

  it("失敗講錯誤，沒有錯誤訊息也要講清楚是失敗；執行中沒有結論", () => {
    expect(taskResultText({ kind: "dns.sync", status: "failed", summary: null, error: "DNSAdapterError: 401" }, t))
      .toBe("DNSAdapterError: 401");
    expect(taskResultText({ kind: "dns.sync", status: "failed", summary: null, error: null }, t))
      .toBe("tasks.summary.failed_no_detail");
    expect(taskResultText({ kind: "dns.sync", status: "running", summary: null, error: null }, t)).toBe("");
  });

  it("成功講摘要", () => {
    const out = taskResultText({ kind: "dns.sync", status: "succeeded", error: null,
                                 summary: { pulled_zones: 2, pulled_records: 10 } }, t);
    expect(out).toContain("zones 2");
    expect(out).toContain("records 10");
  });
});
