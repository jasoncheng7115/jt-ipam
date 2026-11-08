/**
 * 憑證推送的步驟文字：推送目標頁的明細與右下角「背景作業」面板共用（2026-11-07）。
 * 三種狀態：成功 ✓、沒做到 ⚠（例如沒有確認位址所以沒確認）、失敗 ✗。
 */
export interface CertPushStep { step: string; ok: boolean; warn?: boolean; code?: string; detail?: string }

type T = (key: string, params?: Record<string, unknown>) => string;
type Te = (key: string) => boolean;

export type StepState = "ok" | "warn" | "fail";

export function stepState(s: CertPushStep): StepState {
  return !s.ok ? "fail" : s.warn ? "warn" : "ok";
}

export function stepLine(s: CertPushStep, t: T, te: Te): { mark: string; label: string; reason: string; detail: string } {
  const st = stepState(s);
  return {
    mark: st === "fail" ? "✗" : st === "warn" ? "⚠" : "✓",
    label: te(`certpush.step_${s.step}`) ? t(`certpush.step_${s.step}`) : s.step,
    reason: s.code ? (te(`errors.${s.code}`) ? t(`errors.${s.code}`) : s.code) : "",
    detail: s.detail || "",
  };
}

/** 推送中的「目前：…」；不認得的階段退回步驟名稱 */
export function stageLabel(stage: string | null | undefined, t: T, te: Te): string {
  if (!stage) return "";
  if (te(`certpush.stage_${stage}`)) return t(`certpush.stage_${stage}`);
  return te(`certpush.step_${stage}`) ? t(`certpush.step_${stage}`) : stage;
}

/** 背景作業的 summary 裡有推送步驟（執行中與做完都有） */
export function taskPushSteps(kind: string | undefined, summary: unknown): { steps: CertPushStep[]; stage: string | null } | null {
  if (kind !== "cert.push" || !summary || typeof summary !== "object") return null;
  const s = summary as { steps?: unknown; stage?: unknown };
  if (!Array.isArray(s.steps)) return null;
  return { steps: s.steps as CertPushStep[], stage: typeof s.stage === "string" ? s.stage : null };
}
