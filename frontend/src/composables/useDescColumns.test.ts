import { describe, expect, it } from "vitest";
import { descColumnsFor } from "./useDescColumns";

describe("描述表格的欄數依寬度（iPad 橫向三欄太擠）", () => {
  it("夠寬三欄，iPad 寬度兩欄，量不到時照原本", () => {
    expect(descColumnsFor(1200)).toBe(3);
    expect(descColumnsFor(960)).toBe(3);
    expect(descColumnsFor(760)).toBe(2);
    expect(descColumnsFor(0)).toBe(3);
    expect(descColumnsFor(1200, 2)).toBe(2);
  });
});
