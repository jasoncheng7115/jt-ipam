/**
 * 12 AI 情報：播報的文字。模型寫的直接用 text；系統摘要（沒有模型、或模型兩次都不合格）只有 code＋params，
 * 這裡依語系組句子。數字一律照後端給的事實，沒有的（null＝無權限或沒資料）不寫成 0。
 */
import type { IntelEvent, IntelItem } from "@/api/warroom";
import { anomalyCategoryLabel } from "./categories";

type T = (k: string, p?: Record<string, unknown>) => string;
type TE = (k: string) => boolean;

/** ISO 時間 → 當地「時:分」；不是今天的加「月/日」（規格：跨日需日期） */
export function hmOf(iso: string | null | undefined, now = new Date()): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const hm = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  if (d.toDateString() === now.toDateString()) return hm;
  return `${String(d.getMonth() + 1).padStart(2, "0")}/${String(d.getDate()).padStart(2, "0")} ${hm}`;
}

const k = (code: string) => `warroom.intel.t.${code}`;

/** 數量加千分位（128,734）；沒有值的照原樣（不寫成 0） */
const n = (v: unknown) => (typeof v === "number" ? v.toLocaleString() : v);

/** 一則播報要顯示的文字 */
export function itemText(it: IntelItem, t: T, te: TE): string {
  if (it.text) return it.text;
  const p = it.params ?? {};
  const cat = (c: string) => anomalyCategoryLabel(c, t, te);
  const sep = t("warroom.intel.t.sep");
  switch (it.code) {
    case "overview":
      return p.open_anomalies == null
        ? t(k("overview_limited"), { at: hmOf(p.at), ips: n(p.registered_ips ?? 0) })
        : t(k("overview"), { at: hmOf(p.at), ips: n(p.registered_ips ?? 0), open: n(p.open_anomalies) });
    // 資訊展示模式（11-03）：只陳述現況與數量
    case "overview_info":
      return t(k("overview_info"), { at: hmOf(p.at), ips: n(p.registered_ips ?? 0), subnets: n(p.subnets ?? 0) });
    case "capacity_info":
      return t(k("capacity_info"), { label: p.top_label ?? "—", pct: Math.round(p.top_pct ?? 0) });
    case "dns_info":
      return t(k("dns_info"), { n: n(p.groups ?? 0) });
    case "certs_info":
      return t(k("certs_info"), { n: n(p.total ?? 0) });
    case "baseline":
      return t(k("baseline"), { open: p.open });
    case "events_none":
      return t(k("events_none"), { since: hmOf(p.since), open: p.open });
    case "events_changed": {
      const ex = (p.examples ?? []) as { category: string; label: string; ip: string | null }[];
      const list = ex.map((e) => `${e.ip || e.label} ${cat(e.category)}`).join(sep);
      return t(k("events_changed"), { since: hmOf(p.since), new: p.new, resolved: p.resolved })
        + (list ? t(k("events_examples"), { list }) : t(k("end")));
    }
    case "ongoing_anomaly":
      return t(k(p.baseline ? "ongoing_baseline" : "ongoing"), {
        subject: p.ip || p.label, category: cat(p.category), first: hmOf(p.first_seen_at) });
    case "anomalies_6h":
      return t(k("anomalies_6h"), { new: n(p.new_6h), resolved: n(p.resolved_6h), open: n(p.open) });
    case "capacity": {
      const top = p.top_label ? t(k("capacity_top"), { label: p.top_label, pct: Math.round(p.top_pct ?? 0) }) : "";
      const head = p.high_util ? t(k("capacity_high"), { n: p.high_util, warn: p.warn })
        : t(k("capacity_ok"), { warn: p.warn });
      const pools = p.pools_alert ? t(k("capacity_pools"), { n: p.pools_alert }) : "";
      return head + top + pools + t(k("end"));
    }
    case "dns":
      return p.mismatch_groups ? t(k("dns_mismatch"), { n: p.mismatch_groups, group: p.group ?? "—" }) : t(k("dns_ok"));
    case "dhcp":
      return t(k(p.level === "crit" ? "dhcp_crit" : "dhcp"), { pool: p.pool ?? "—", pct: Math.round(p.pct ?? 0) });
    case "certs":
      if (!p.soon_30d) return t(k("certs_none"));
      return t(k((p.days ?? 0) < 0 ? "certs_expired" : "certs"), {
        n: p.soon_30d, name: p.name ?? "—", days: Math.abs(p.days ?? 0) });
    case "topology":
      return t(k("topology"), { devices: n(p.devices), links: n(p.links ?? 0), inferred: n(p.inferred ?? 0) });
    case "virt":
      return t(k(p.gaps ? "virt_gaps" : "virt"), { hosts: n(p.hosts ?? 0), vms: n(p.vms ?? 0), gaps: n(p.gaps ?? 0) });
    case "ai_audit":
      return t(k("ai_audit"), { at: hmOf(p.completed_at), high: p.high ?? "—", open: p.open ?? "—" });
    case "collection":
      return t(k(p.stale ? "collection_stale" : "collection_ok"), {
        agents: p.agents, stale: p.stale, at: hmOf(p.last_success_at) });
    case "integrity": {
      if (!p.total) return t(k("integrity_none"));
      const bad = (p.stale ?? 0) + (p.failed ?? 0) + (p.never ?? 0);
      if (!bad) return t(k("integrity_ok"), { total: p.total });
      const ex = (p.examples ?? []) as { name: string; state: string; last_success_at: string | null }[];
      const list = ex.map((e) => t(k(`integrity_ex_${e.state}`), { name: e.name, at: hmOf(e.last_success_at) })).join(sep);
      return t(k("integrity_bad"), { total: p.total, stale: p.stale, failed: p.failed, never: p.never, list });
    }
    case "integrity_hidden":
      return t(k("integrity_hidden"));
    default:
      return "";
  }
}

/** 插播的重大事件摘要（確定性文字，不等 AI） */
export function eventText(ev: IntelEvent, t: T, te: TE): string {
  return t(k(ev.state === "unknown" ? "event_unknown" : "event"), {
    subject: ev.ip || ev.label, category: anomalyCategoryLabel(ev.category, t, te), at: hmOf(ev.first_seen_at) });
}

/** 段落左邊的小標（本輪摘要、持續關注、來源狀態…） */
export function categoryLabel(category: string, t: T, te: TE): string {
  const key = `warroom.intel.cat.${category}`;
  return te(key) ? t(key) : category;
}
