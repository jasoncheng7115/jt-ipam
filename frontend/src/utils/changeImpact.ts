/**
 * 變更影響預演的顯示輔助：標籤顏色、把後端的代碼（原因碼、缺口、範本待辦）依語系翻成句子。
 * 後端只送代碼與參數（沒有寫死任何語言的句子），翻不到時退回代碼本身，不會整格空白。
 */
import { fmtDateTime } from "@/utils/datetime";
import { evidenceVendorLabel } from "@/utils/investigateSections";

type TagType = "default" | "info" | "success" | "warning" | "error";
type T = (key: string, params?: Record<string, unknown>) => string;
type Te = (key: string) => boolean;

export function lifecycleType(s: string): TagType {
  return ({ draft: "default", in_review: "info", approved: "success", in_progress: "warning",
            verified: "success", closed: "default", cancelled: "default" } as Record<string, TagType>)[s] ?? "default";
}
export function decisionType(s: string | null | undefined): TagType {
  return ({ blocked: "error", needs_review: "warning", no_known_blocker: "success" } as Record<string, TagType>)[s ?? ""]
    ?? "default";
}
export function dispositionType(s: string): TagType {
  return ({ blocker: "error", review: "warning", informational: "default" } as Record<string, TagType>)[s] ?? "default";
}
export function severityType(s: string): TagType {
  return ({ critical: "error", high: "error", medium: "warning", low: "info", info: "default" } as Record<string, TagType>)[s]
    ?? "default";
}

// 參數本身也是代碼的（來源／目的、跨子網路要確認的項目）：一併翻譯
// 服務重要性、節點停機模式（M2）、整合種類、系統設定、線路欄位、IP 範圍用途、未指定埠的服務端點也是代碼
const CODED_PARAMS = new Set(["side", "item", "criticality", "mode", "kind", "setting", "field", "purpose", "endpoint",
                              "option", "circuit_role", "hop_kind"]);
// 時間參數一律以本地時區顯示（後端送 ISO 8601）
const TIME_PARAMS = new Set(["last_sync_at", "last_seen", "until"]);

// 「看到這個位址的來源」：`scanner`、`librenms`…，防火牆的是 `arp:opnsense`、`vpn:fortigate` 這種逐來源鍵
const VENDOR_NAME: Record<string, string> = {
  opnsense: "OPNsense", pfsense: "pfSense", fortigate: "FortiGate", paloalto: "Palo Alto", checkpoint: "Check Point",
  mikrotik: "MikroTik", librenms: "LibreNMS",
};
export function sourceText(t: T, te: Te, token: string): string {
  const [kind, vendor] = token.split(":", 2);
  if (vendor !== undefined) {
    const k = `change_impact.param.source_kind.${kind}`;
    return te(k) ? t("change_impact.param.source_fmt", { kind: t(k), vendor: VENDOR_NAME[vendor] ?? evidenceVendorLabel(vendor, t) }) : token;
  }
  return te(`change_impact.param.source.${token}`) ? t(`change_impact.param.source.${token}`) : token;
}

function params(t: T, te: Te, p: Record<string, unknown> | null | undefined): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(p ?? {})) {
    if (v === null || v === undefined || v === "") { out[k] = "—"; continue; }
    if (TIME_PARAMS.has(k)) { out[k] = fmtDateTime(String(v)); continue; }
    // 單一來源也要翻（2026-10-25：新位址被看到的原因曾印出「scanner」）
    if (k === "source") { out[k] = sourceText(t, te, String(v)); continue; }
    if (k === "sources") {
      out[k] = String(v).split(",").map((x) => x.trim()).filter(Boolean).map((x) => sourceText(t, te, x))
        .join(t("change_impact.param.source_sep"));
      continue;
    }
    const key = `change_impact.param.${k}.${String(v)}`;
    out[k] = CODED_PARAMS.has(k) && te(key) ? t(key) : v;
  }
  return out;
}

/** 發現的原因碼 → 句子（例：OLD_IP_IN_ADDRESS_RECORD → 「DNS 記錄 erp.example.net 指向 198.51.100.10」） */
export function reasonText(t: T, te: Te, code: string, p: Record<string, unknown>): string {
  const key = `change_impact.reason.${code}`;
  return te(key) ? t(key, params(t, te, p)) : code;
}
export function gapText(t: T, te: Te, code: string, p: Record<string, unknown>): string {
  // 同一個原因碼依種類有更精確的句子（例：unknown_semantics 的 non_address_members 要帶物件名稱，
  // 否則兩個不同別名的缺口看起來一模一樣）
  const variant = p?.kind ? `change_impact.gap.${code}__${String(p.kind)}` : "";
  if (variant && te(variant)) return t(variant, params(t, te, p));
  const key = `change_impact.gap.${code}`;
  return te(key) ? t(key, params(t, te, p)) : code;
}
/** 資料不足影響的是哪一項分析：同一類別可能有好幾筆同樣的原因（例：權限不足的 VPN 端點與整合端點）。
 *  跟類別同名的不重複顯示；翻不到就不顯示（不露出代碼） */
export function gapAffected(t: T, te: Te, g: { category: string; affected_analysis: string | null }): string {
  const a = g.affected_analysis;
  if (!a || a === g.category) return "";
  const key = `change_impact.affected.${a}`;
  return te(key) ? t(key) : "";
}
export function taskTitle(t: T, te: Te, task: { template_code: string | null; template_params: Record<string, unknown> | null;
                                                  title: string }): string {
  if (!task.template_code) return task.title;
  const key = `change_impact.task.${task.template_code}`;
  if (te(key)) return t(key, params(t, te, task.template_params));
  // update_refs_<類別>／remove_refs_<類別>：類別多，用一個句型帶類別名稱
  const m = /^(update|remove)_refs_(\w+)$/.exec(task.template_code);
  if (m) {
    const cat = te(`change_impact.category.${m[2]}`) ? t(`change_impact.category.${m[2]}`) : m[2];
    return t(`change_impact.task.${m[1]}_refs`, { ...params(t, te, task.template_params), category: cat });
  }
  return task.template_code;
}
