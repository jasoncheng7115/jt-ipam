/**
 * 背景作業的結果摘要：作業頁的結果欄與右下角的「背景作業」面板共用（2026-10-10 從 Tasks.vue 抽出）。
 * 各種作業的 summary 形狀都不一樣，這裡把它們翻成人話。
 */
import type { BackgroundTask } from "@/api/tasks";

type T = (key: string, params?: Record<string, unknown>) => string;

// 聚合各種 summary 形狀 → 四個數字
export function aggregateCounts(summary: any): { ins: number; upd: number; err: number; total: number } {
  const out = { ins: 0, upd: 0, err: 0, total: 0 };
  if (!summary || typeof summary !== "object") return out;
  const n = (v: any) => (typeof v === "number" ? v : Number(v ?? 0)) || 0;

  // 1) phpipam.migration: {tables: {x: {inserted, updated, skipped, errored}}}
  if (summary.tables && typeof summary.tables === "object") {
    for (const v of Object.values(summary.tables) as any[]) {
      out.ins += n(v.inserted);
      out.upd += n(v.updated);
      out.err += n(v.errored);
      out.total += n(v.inserted) + n(v.updated) + n(v.skipped) + n(v.errored);
    }
    return out;
  }

  // 2) opnsense.sync: {details: [{task, seen, matched, inserted, updated, removed, error?}]}
  if (Array.isArray(summary.details)) {
    for (const d of summary.details) {
      out.ins += n(d.inserted);
      out.upd += n(d.updated) || n(d.matched);
      out.err += n(d.errored) + (d.error ? 1 : 0);
      out.total += n(d.seen);
    }
    return out;
  }

  // 2b) DNS sync: {pulled_zones, pulled_records, mismatches, dns_only, ipam_only, hostname_obs}
  if ("pulled_records" in summary || "pulled_zones" in summary) {
    out.total = n(summary.pulled_records);
    out.upd = n(summary.hostname_obs);   // 套用到 IPAM 的主機名稱觀測數
    return out;
  }

  // 2c) pfSense 排程心跳：{arp, aliases, rules, nat}（每輪重新同步的項目數）
  if ("arp" in summary && "rules" in summary && "nat" in summary && typeof summary.arp === "number") {
    out.upd = n(summary.arp) + n(summary.aliases) + n(summary.rules) + n(summary.nat);
    out.total = out.upd;
    return out;
  }

  // 3) 通用：top-level 數字，或「一層巢狀分組」
  //    （librenms：{devices:{seen,inserted,updated}, arp:{...}, fdb:{...}, vlans:{seen,upserted,mappings}}）
  const bump = (k: string, v: number) => {
    if (k.endsWith("_inserted") || k === "inserted" || k === "upserted" || k === "new") out.ins += v;
    else if (k.endsWith("_updated") || k === "updated" || k.endsWith("_matched") || k.endsWith("_filled") || k === "mappings") out.upd += v;
    else if (k.endsWith("_errored") || k === "errored" || k.endsWith("_failed") || k === "missing_agents") out.err += v;
    else if (k.endsWith("_seen") || k === "seen" || k.endsWith("_count") || k === "fetched") out.total += v;
  };
  for (const [k, v] of Object.entries(summary)) {
    if (typeof v === "number") bump(k, v);
    else if (v && typeof v === "object" && !Array.isArray(v)) {
      for (const [k2, v2] of Object.entries(v as Record<string, unknown>)) {
        if (typeof v2 === "number") bump(k2, v2);
      }
    }
  }
  // 沒有明確的「總數」欄位命中時（如 OPNsense 心跳 {mappings:9}），用 新增+更新 當總數，
  // 至少反映「有做事」，不要顯示成 0。
  if (out.total === 0) out.total = out.ins + out.upd;
  return out;
}

// 把 task summary 翻譯成人話
export function formatSummary(kind: string, summary: any, t: T): string {
  if (!summary || typeof summary !== "object") return "—";

  // 通用統計欄位 (migration / sync 都用)
  const lines: string[] = [];
  const num = (v: any) => (typeof v === "number" ? v : Number(v ?? 0));

  // 1) phpipam migration 風格：{tables: {sections: {inserted, updated, skipped, errored}, ...}, error}
  if (summary.tables && typeof summary.tables === "object") {
    if (summary.error) lines.push(t("tasks.summary.error", { msg: summary.error }));
    // 迴圈變數不可叫 t：以前叫 t，蓋掉了翻譯函式，有資料的表一律丟 TypeError（作業頁的結果欄整格壞掉）
    for (const [tname, tb] of Object.entries(summary.tables) as [string, any][]) {
      const ins = num(tb.inserted), upd = num(tb.updated), err = num(tb.errored);
      // 只顯示「有變動或失敗」的；只 skip 的不列
      if (ins + upd + err === 0) continue;
      const parts: string[] = [];
      if (ins) parts.push(t("common.added_n", { n: ins }));
      if (upd) parts.push(t("common.updated_n", { n: upd }));
      if (err) parts.push(t("common.failed_n", { n: err }));
      lines.push(`${tname}：${parts.join("、")}`);
    }
    if (!lines.length) return t("tasks.summary.no_change");
    return lines.join("；");
  }

  // 1a) 代理推上來的回報與資料庫更新：各有自己的形狀，直接講結果
  if (kind === "rustdesk.sync") {
    const p = [t("tasks.summary.rustdesk", { peers: num(summary.peers), online: num(summary.online),
                                            matched: num(summary.matched) })];
    if (num(summary.removed)) p.push(t("tasks.summary.removed_n", { n: num(summary.removed) }));
    return p.join("．");
  }
  if (kind === "checkpoint.sync") {
    const p = [t("tasks.summary.checkpoint", { gateways: num(summary.gateways), rules: num(summary.rules),
                                              objects: num(summary.objects), nat: num(summary.nat) })];
    if (summary.errors && typeof summary.errors === "object") {
      p.push(t("tasks.summary.partial_failed", { what: Object.keys(summary.errors).join(", ") }));
    }
    return p.join("．");
  }
  if (kind === "checkpoint_gaia.sync") {
    const p = [t("tasks.summary.checkpoint_gaia", { subnets: num(summary.dhcp_subnets), pools: num(summary.pools),
                                                   arp: num(summary.arp_rows), leases: num(summary.leases) })];
    if (summary.errors && typeof summary.errors === "object") {
      p.push(t("tasks.summary.partial_failed", { what: Object.keys(summary.errors).join(", ") }));
    }
    return p.join("．");
  }
  if (kind === "technitium_dhcp.sync") {
    return t("tasks.summary.isc_dhcp", { pools: num(summary.pools), reservations: num(summary.reservations),
                                         leases: num(summary.leases) });
  }
  if (kind === "isc_dhcp.sync") {
    return t("tasks.summary.isc_dhcp", { pools: num(summary.pools), reservations: num(summary.reservations),
                                         leases: num(summary.leases) });
  }
  if (kind === "isoinsight.sync") {
    // 結果類型要講出來：部分成功不可以看起來跟完全成功一樣
    return t("tasks.summary.isoinsight", {
      result: t(`isoinsight.result.${summary.result ?? "success"}`), fetched: num(summary.fetched),
      created: num(summary.created), updated: num(summary.updated), unmatched: num(summary.unmatched) });
  }
  if (kind === "oui.refresh") {
    return t("tasks.summary.oui", { parsed: num(summary.parsed), inserted: num(summary.inserted),
                                    updated: num(summary.updated) });
  }
  if (kind === "recog.refresh") {
    if (summary.status === "updated") {
      return t("tasks.summary.recog_updated", { from: summary.previous || "—", to: summary.release || "—",
                                                n: num(summary.fingerprints) });
    }
    if (summary.status === "up_to_date") return t("tasks.summary.recog_up_to_date", { v: summary.release || "—" });
    return "—";
  }
  if (kind === "geoip.refresh") {
    if (summary.error === "not_configured") return t("tasks.summary.geoip_not_configured");
    const res = (summary.results || {}) as Record<string, { ok?: boolean; error?: string }>;
    const p = Object.entries(res).map(([ed, r]) => (r?.ok ? `${ed} ✓` : `${ed} ✗ ${r?.error || ""}`.trim()));
    return p.length ? p.join("；") : "—";
  }

  // 1b) IP 探測：{job_id, agent, ip, device_type?, os?, ports?}
  if (kind === "ip.identify") {
    if (!summary.device_type) return t("tasks.summary.identify_running", { agent: summary.agent ?? "—" });
    return t("tasks.summary.identify_done", {
      type: t(`identify.type.${summary.device_type}`), os: summary.os || "—", ports: num(summary.ports) });
  }

  // 2) OPNsense sync 風格：{firewall, tasks, details: [{task, seen, matched}, ...]}
  if (Array.isArray(summary.details)) {
    for (const d of summary.details) {
      const name = d.task ?? d.alias ?? "?";
      if (d.error) { lines.push(t("tasks.summary.named_error", { name, msg: d.error })); continue; }
      const seen = num(d.seen), matched = num(d.matched);
      // 全 0 的子任務也跳過
      if (seen === 0 && matched === 0) continue;
      lines.push(`${name}：${matched}/${seen}`);
    }
    if (!lines.length) return t("tasks.summary.no_change");
    return lines.join("；");
  }

  // 2b) DNS sync 風格：{pulled_zones, pulled_records, mismatches, dns_only, ipam_only, hostname_obs}
  if ("pulled_records" in summary || "pulled_zones" in summary) {
    const p: string[] = [];
    if (num(summary.pulled_zones)) p.push(`zones ${num(summary.pulled_zones)}`);
    if (num(summary.pulled_records)) p.push(`records ${num(summary.pulled_records)}`);
    if (num(summary.hostname_obs)) p.push(`hostname ${num(summary.hostname_obs)}`);
    if (num(summary.mismatches)) p.push(`mismatch ${num(summary.mismatches)}`);
    if (num(summary.dns_only)) p.push(`DNS-only ${num(summary.dns_only)}`);
    if (num(summary.ipam_only)) p.push(`IPAM-only ${num(summary.ipam_only)}`);
    return p.length ? p.join("；") : t("tasks.summary.no_change");
  }

  // 2c) pfSense 心跳：{arp, aliases, rules, nat}
  if ("arp" in summary && "rules" in summary && "nat" in summary && typeof summary.arp === "number") {
    const p: string[] = [];
    if (num(summary.arp)) p.push(`ARP ${num(summary.arp)}`);
    if (num(summary.rules)) p.push(`rules ${num(summary.rules)}`);
    if (num(summary.aliases)) p.push(`aliases ${num(summary.aliases)}`);
    if (num(summary.nat)) p.push(`NAT ${num(summary.nat)}`);
    return p.length ? p.join("；") : t("tasks.summary.no_change");
  }

  // 2d) Wazuh sync 風格：{fetched, new, updated, matched_ip}
  if ("fetched" in summary && ("new" in summary || "matched_ip" in summary)) {
    const p: string[] = [];
    if (num(summary.new)) p.push(t("common.added_n", { n: num(summary.new) }));
    if (num(summary.updated)) p.push(t("common.updated_n", { n: num(summary.updated) }));
    if (num(summary.fetched)) p.push(`fetched ${num(summary.fetched)}`);
    if (num(summary.matched_ip)) p.push(`matched IP ${num(summary.matched_ip)}`);
    return p.length ? p.join("；") : t("tasks.summary.no_change");
  }

  // 2e) Proxmox sync 風格：{cluster, vms_seen, vms_updated, vms_inserted, nodes_seen, ipam_linked, interfaces_seen}
  if ("vms_seen" in summary || "ipam_linked" in summary) {
    const p: string[] = [];
    if (num(summary.vms_inserted)) p.push(t("common.added_n", { n: num(summary.vms_inserted) }));
    if (num(summary.vms_updated)) p.push(t("common.updated_n", { n: num(summary.vms_updated) }));
    if (num(summary.vms_seen)) p.push(`VM ${num(summary.vms_seen)}`);
    if (num(summary.nodes_seen)) p.push(`nodes ${num(summary.nodes_seen)}`);
    if (num(summary.ipam_linked)) p.push(`IPAM ${num(summary.ipam_linked)}`);
    return p.length ? p.join("；") : t("tasks.summary.no_change");
  }

  // 3) LibreNMS sync 風格：{instance, devices_seen, devices_inserted, ...}
  const k = (key: string, label: string) => {
    if (typeof summary[key] === "number" && summary[key] !== 0) lines.push(`${label} ${summary[key]}`);
  };
  // 巢狀 summary：devices/arp/fdb/vlans 各是 {seen, inserted, updated}
  const kn = (val: unknown, label: string) => {
    if (typeof val === "number" && val !== 0) lines.push(`${label} ${val}`);
  };
  kn(summary.devices?.seen, t("tasks.summary.devices_seen"));
  kn(summary.devices?.inserted, t("tasks.summary.devices_inserted"));
  kn(summary.devices?.updated, t("tasks.summary.devices_updated"));
  kn(summary.arp?.seen, "ARP");
  kn(summary.arp?.inserted, t("tasks.summary.arp_inserted"));
  kn(summary.fdb?.seen, "FDB");
  k("ip_mac_filled", t("tasks.summary.ip_mac_filled"));
  // 依 ARP 自動建立 IP（#48）：建了幾筆，沒建的列出最多的三個原因（管理員才知道為什麼沒長出來）
  kn(summary.arp?.ips_created, t("tasks.summary.arp_ips_created"));
  const skipped = summary.arp?.create_skipped as Record<string, number> | undefined;
  if (skipped && Object.keys(skipped).length) {
    const top = Object.entries(skipped).sort((a, b) => b[1] - a[1]).slice(0, 3)
      .map(([why, n]) => `${t(`librenms_admin.arp_skip.${why}`)} ${n}`);
    lines.push(`${t("tasks.summary.arp_create_skipped")}（${top.join("、")}）`);
  }

  // 4) AdGuard 風格：{clients_result: {clients, ips_seen, ips_matched}, ...}
  if (summary.clients_result) {
    const r = summary.clients_result;
    lines.push(`clients ${num(r.clients)}(IP ${num(r.ips_matched)}/${num(r.ips_seen)})`);
  }
  if (summary.rewrites_result) {
    const r = summary.rewrites_result;
    lines.push(t("tasks.summary.rewrites", { n: num(r.rewrites), matched: num(r.rewrites_matched) }));
  }

  // 5) Wazuh 風格：{instance, agents_seen, agents_inserted, ...}
  k("agents_seen", t("tasks.summary.agents_seen"));
  k("agents_inserted", t("tasks.summary.agents_inserted"));
  k("agents_updated", t("tasks.summary.agents_updated"));
  k("missing_agents", "missing");

  if (!lines.length) return t("tasks.summary.done");
  return lines.join("；");
}

export const TEXT_SUMMARY_KINDS = new Set(["ip.identify", "rustdesk.sync", "isc_dhcp.sync", "isoinsight.sync", "checkpoint.sync", "checkpoint_gaia.sync",
                                    "oui.refresh", "recog.refresh", "geoip.refresh"]);

/** 一筆作業的結論：失敗講錯誤（沒有錯誤訊息也要講清楚是失敗），成功講摘要 */
export function taskResultText(task: Pick<BackgroundTask, "kind" | "status" | "summary" | "error">, t: T): string {
  if (task.status === "failed") return task.error || t("tasks.summary.failed_no_detail");
  if (task.status !== "succeeded") return "";
  return formatSummary(task.kind, task.summary, t);
}
