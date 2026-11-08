import { describe, expect, it } from "vitest";
import { stageLabel, stepLine, stepState, taskPushSteps } from "./certPushSteps";

const dict: Record<string, string> = {
  "certpush.step_probe_before": "推之前先連一次", "certpush.stage_push": "推送到設備",
  "errors.certpush_probe_unreachable": "連不到這個位址",
};
const t = (k: string) => dict[k] ?? k;
const te = (k: string) => k in dict;

describe("憑證推送步驟文字（推送目標頁與背景作業面板共用）", () => {
  it("連不到是 ⚠ 不是 ✓，並帶原因", () => {
    const s = { step: "probe_before", ok: true, warn: true, code: "certpush_probe_unreachable", detail: "DNS resolution failed" };
    expect(stepState(s)).toBe("warn");
    expect(stepLine(s, t, te)).toEqual({ mark: "⚠", label: "推之前先連一次", reason: "連不到這個位址", detail: "DNS resolution failed" });
    expect(stepState({ step: "x", ok: false })).toBe("fail");
  });

  it("目前階段的文字；只有推送作業才有步驟", () => {
    expect(stageLabel("push", t, te)).toBe("推送到設備");
    expect(stageLabel("probe_before", t, te)).toBe("推之前先連一次");
    expect(taskPushSteps("cert.push", { steps: [{ step: "a", ok: true }], stage: "verify" })?.stage).toBe("verify");
    expect(taskPushSteps("opnsense.sync", { steps: [] })).toBeNull();
    expect(taskPushSteps("cert.push", { status: "ok" })).toBeNull();
  });
});
