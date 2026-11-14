/**
 * 「調查」檔案的每一段要顯示成哪幾行字（畫面與四種匯出共用）。
 *
 * 為什麼抽出來：匯出第一版是照記憶另寫一份（欄位名猜錯，摘要整排「—」、DNS 每行 undefined），
 * 畫面跟匯出必須是同一份資料的同一種讀法。2026-10-05 檔案加了十幾段（OCS、RustDesk、Zabbix、
 * 虛擬化、DHCP、交換器埠、防火牆證據、探測、異常…），更不能各寫一份。
 *
 * 純函式：不碰 i18n 實例，翻譯與時間格式由呼叫端傳進來（vitest 直接測）。
 * 段落沒有資料就不回傳（畫面不放空標題）。
 */
import { fmtClock } from "./datetime";
import { virtTech } from "./virt";

export type Translate = (key: string, params?: Record<string, unknown>) => string;

export interface SectionHelpers {
  t: Translate;
  te: (key: string) => boolean;
  /** 時間格式（呼叫端傳 fmtDateTime：本地時區） */
  fmt: (iso: string | null | undefined) => string;
}

export interface InvSection {
  key: string;
  title: string;
  lines: string[];
  /** 清單類的段落：幾筆（標題顯示「（n）」）；一段由好幾種資料組成的不給（行數不等於筆數） */
  count?: number;
}

type D = Record<string, any>;

const SEP = " · ";

/** 防火牆／整合廠牌的顯示名稱（IP 詳細資料「各來源最後出現」與調查共用） */
export const VENDOR_LABEL: Record<string, string> = {
  opnsense: "OPNsense", pfsense: "pfSense", fortigate: "FortiGate", paloalto: "Palo Alto", checkpoint: "Check Point",
  mikrotik: "MikroTik", librenms: "LibreNMS", windows_dhcp: "Windows DHCP", kea_dhcp: "Kea DHCP",
  technitium: "Technitium DHCP",
  isc_dhcp: "ISC DHCP", isoinsight: "ISOinsight", proxmox: "Proxmox", vmware: "VMware", wazuh: "Wazuh", rustdesk: "RustDesk",
  ocs: "OCS", zabbix: "Zabbix", checkmk: "Checkmk",
  // SNMP 收集（0217）：`arp:snmp`、`iface:snmp`；`arp:snmp_live`（設備標成 reachable 的項目）見 evidenceVendorLabel
  snmp: "SNMP",
};

export function vendorLabel(v: string | null | undefined): string {
  if (!v) return "";
  return VENDOR_LABEL[v] ?? v;
}

/**
 * 證據來源鍵裡的「廠牌」部分：產品名照 VENDOR_LABEL；`snmp_live` 不是產品名，要翻譯
 * （「SNMP，reachable」：IP-MIB 裡設備標成 reachable 的 ARP 項目，會過期、可以宣稱上線）。
 */
export function evidenceVendorLabel(v: string | null | undefined, t: Translate): string {
  if (v === "snmp_live") return t("snmp.vendor_live");
  return vendorLabel(v);
}

/** `arp:opnsense` → 「ARP 表（OPNsense）」 */
export function fwSeenLabel(key: string, t: Translate): string {
  if (key === "device") return t("system_settings.src_device");
  const [kind, vendor] = key.split(":", 2);
  if (!vendor) return key;
  return `${t(`system_settings.src_kind_${kind}`)}（${evidenceVendorLabel(vendor, t)}）`;
}

function join(parts: (string | null | undefined | false)[]): string {
  return parts.filter((p) => p !== null && p !== undefined && p !== false && p !== "").join(SEP);
}

function label(h: SectionHelpers, key: string, fallback: string): string {
  return h.te(key) ? h.t(key) : fallback;
}

function kindLabel(h: SectionHelpers, kind: string | null | undefined): string {
  if (!kind) return "";
  return label(h, `identify.type.${kind}`, kind);
}

function mb(v: unknown): string {
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return "";
  return n >= 1024 ? `${Math.round((n / 1024) * 10) / 10} GB` : `${n} MB`;
}

/** 後端算好的矛盾清單 → 照語系的句子（`investigate.conflict_<code>`） */
export function conflictLines(d: D | null | undefined, h: SectionHelpers): string[] {
  return ((d?.conflicts ?? []) as D[]).map((c) => {
    const key = `investigate.conflict_${c.code}`;
    return h.te(key) ? h.t(key, c.params ?? {}) : String(c.code);
  });
}

/** 類型的依據（device_identity.resolve_kind 的 `來源:值`）→ 看得懂的一句 */
export function kindReasonText(reason: string | null | undefined, h: SectionHelpers): string {
  if (!reason) return h.t("investigate.kind_reason_scan");
  const [src, ...rest] = reason.split(":");
  const v = rest.join(":");
  const srcKey = `investigate.reason_src_${src}`;
  const name = h.te(srcKey) ? h.t(srcKey) : vendorLabel(src);
  return h.t("investigate.kind_reason", { src: name, value: v || "—" });
}

export function monitorLines(d: D | null | undefined, h: SectionHelpers,
                             wazuhStatus: (s: any) => string, lnmsStatus: (s: any) => string): string[] {
  const m = d?.monitoring ?? {};
  const out: string[] = [];
  if (m.wazuh) {
    out.push(`Wazuh ${m.wazuh.name ?? m.wazuh.agent_id}（${wazuhStatus(m.wazuh.status)}）`
      + (m.wazuh.sca_score != null ? ` · SCA ${m.wazuh.sca_score}` : ""));
  }
  if (m.librenms) {
    out.push(join([
      `LibreNMS ${m.librenms.hostname ?? m.librenms.sysname ?? ""}（${lnmsStatus(m.librenms.status)}）`,
      m.librenms.hardware,
      m.librenms.matched_by === "primary_ip" ? h.t("investigate.matched_by_ip") : null,
    ]));
  }
  for (const z of (m.zabbix ?? []) as D[]) {
    out.push(join([
      `Zabbix ${z.name ?? z.host}（${label(h, `zabbix.${z.status}`, z.status ?? "—")}）`,
      z.available === "up" ? h.t("topology.status_up") : z.available === "down" ? h.t("topology.status_down") : null,
      z.maintenance ? h.t("zabbix.maintenance") : null,
    ]));
  }
  for (const c of (m.checkmk ?? []) as D[]) {
    out.push(join([
      `Checkmk ${c.host}（${label(h, `checkmk.state_${c.state}`, c.state ?? "—")}）`,
      c.downtime ? h.t("checkmk.downtime") : null,
    ]));
  }
  return out;
}

function identityLines(d: D, h: SectionHelpers): string[] {
  const x = d.identity;
  if (!x) return [];
  const out: string[] = [];
  if (x.device_kind || x.device_model) {
    out.push(`${h.t("cols.device_kind")}：${join([kindLabel(h, x.device_kind) || "—", x.device_model])}`);
  }
  if (x.device_kind) out.push(kindReasonText(x.kind_reason, h));
  if (x.kind_by_facts) out.push(h.t("investigate.kind_by_facts", { kind: kindLabel(h, x.kind_by_facts) }));
  if (x.os_guess || x.os_family) {
    out.push(`${h.t("cols.os")}：${x.os_guess ?? x.os_family}`
      + (x.os_guess && x.os_family ? `（${x.os_family}）` : ""));
  }
  if (x.agent_os) {
    const [src, fam] = String(x.agent_os).split(":", 2);
    out.push(h.t("investigate.agent_os", { src: vendorLabel(src), os: fam ?? "" }));
  }
  if (x.nic_vendor) out.push(`${h.t("investigate.nic_vendor")}：${x.nic_vendor}`);
  if (x.mac_random) out.push(h.t("investigate.mac_random"));
  if (x.virtual_guest) {
    // 單獨一個「容器」看不出是哪來的事實：標出是虛擬化平台說的
    out.push(`${h.t("investigate.sec_virt")}：${x.virtual_guest === "ct" ? h.t("addresses.virt_ct_tag") : h.t("addresses.virt_vm_tag")}`);
  }
  if (x.identified_at) out.push(`${h.t("investigate.identified_at")}：${h.fmt(x.identified_at)}`);
  return out;
}

function agentLines(d: D, h: SectionHelpers): string[] {
  const a = d.agents ?? {};
  const out: string[] = [];
  const o = a.ocs;
  if (o) {
    out.push(join([
      `OCS${o.os ? `：${o.os}` : ""}`,
      o.tag ? `${h.t("investigate.ocs_tag")} ${o.tag}` : null,
      o.last_inventory ? `${h.t("investigate.ocs_last_inventory")} ${h.fmt(o.last_inventory)}` : null,
    ]));
    if (o.system || o.serial) {
      out.push(join([`${h.t("investigate.ocs_hardware")}：${o.system ?? "—"}`,
                     o.serial ? `${h.t("investigate.serial")} ${o.serial}` : null]));
    }
    if (o.cpu) out.push(`CPU：${o.cpu}`);
    if (o.memory_mb) out.push(`${h.t("investigate.memory")}：${mb(o.memory_mb)}`);
    for (const k of (o.disks ?? []) as D[]) {
      out.push(`${h.t("investigate.disk")}：${join([k.model, mb(k.size_mb)])}`);
    }
    for (const n of (o.notes ?? []) as D[]) {
      out.push(`${h.t("investigate.ocs_note")}：${join([n.date, n.user, n.comment])}`);
    }
  }
  for (const p of (a.rustdesk ?? []) as D[]) {
    out.push(join([
      `RustDesk ${p.id}（${p.online ? h.t("rustdesk.online") : h.t("rustdesk.offline")}）`,
      p.last_online_at ? `${h.t("rustdesk.last_online")} ${h.fmt(p.last_online_at)}` : null,
      p.os, p.hostname, p.username,
      `${h.t("rustdesk.match")}：${label(h, `rustdesk.match_${p.match_status}`, p.match_status ?? "—")}`,
      p.key_problem ? h.t("rustdesk.key_problem") : null,
    ]));
  }
  return out;
}

function probeLines(d: D, h: SectionHelpers): string[] {
  const p = d.probe;
  if (!p) return [];
  const out = [join([
    h.fmt(p.at),
    p.device_type ? `${h.t("cols.device_kind")}：${kindLabel(h, p.device_type)}` : null,
    p.os ? `${h.t("cols.os")}：${p.os}` : null,
    join([p.vendor, p.model]) || null,
  ])];
  for (const s of (p.services ?? []) as string[]) out.push(s);
  if (p.services_total) out.push(h.t("investigate.more_rows", { n: p.services_total - (p.services?.length ?? 0) }));
  if (p.names?.length) out.push(`${h.t("investigate.probe_names")}：${p.names.join(", ")}`);
  if (p.evidence?.length) out.push(`${h.t("investigate.probe_evidence")}：${p.evidence.join(", ")}`);
  return out;
}

function virtLines(d: D, h: SectionHelpers): string[] {
  const v = d.virtualization;
  if (!v) return [];
  const tag = v.kind === "ct" ? h.t("addresses.virt_ct_tag") : h.t("addresses.virt_vm_tag");
  const tech = virtTech(v.platform, v.kind);
  return [join([
    `${v.vm}（${tech ? `${tag} · ${tech}` : tag}）`,
    v.node ? `${h.t("investigate.virt_node")} ${v.node}` : null,
    v.status ? label(h, `virt.vm_status_${v.status}`, v.status) : null,
    v.cluster ? `${h.t("investigate.virt_cluster")} ${v.cluster}` : null,
    v.vmid != null ? `VMID ${v.vmid}` : null,
  ])];
}

function dhcpLines(d: D, h: SectionHelpers): string[] {
  const x = d.dhcp;
  if (!x) return [];
  const out: string[] = [];
  const flags = join([
    x.reserved ? h.t("addresses.dhcp_reserved_tag") : null,
    x.in_lease ? h.t("investigate.dhcp_has_lease") : null,
    x.in_pool ? h.t("addresses.dhcp_in_range_tag") : null,
    x.excluded ? h.t("addresses.dhcp_excluded_tag") : null,
  ]);
  if (flags) out.push(flags);
  for (const r of (x.reservations ?? []) as D[]) {
    out.push(`${h.t("addresses.dhcp_reserved_tag")}：${join([r.mac, r.hostname, r.description,
      r.source_name ?? vendorLabel(r.source_type)])}`);
  }
  for (const l of (x.leases ?? []) as D[]) {
    out.push(`${h.t("investigate.dhcp_lease")}：${join([
      l.source_name ? `${l.source_name}（${vendorLabel(l.source_type)}）` : vendorLabel(l.source_type),
      l.last_seen ? `${h.t("investigate.last_seen")} ${h.fmt(l.last_seen)}` : null])}`);
  }
  for (const p of (x.pools ?? []) as D[]) {
    out.push(`${h.t("addresses.dhcp_range")}：${p.start} - ${p.end}`
      + (p.source_name || p.source_type ? `（${p.source_name ?? vendorLabel(p.source_type)}）` : ""));
  }
  for (const e of (x.exclusions ?? []) as D[]) {
    out.push(`${h.t("addresses.dhcp_excluded_range")}：${e.start} - ${e.end}`
      + (e.source_name || e.source_type ? `（${e.source_name ?? vendorLabel(e.source_type)}）` : ""));
  }
  return out;
}

function switchLines(d: D, h: SectionHelpers): string[] {
  return ((d.switch_ports ?? []) as D[]).map((s) => join([
    s.switch ?? "—", s.port, s.vlan != null ? `VLAN ${s.vlan}` : null,
    s.last_seen ? `${h.t("investigate.last_seen")} ${h.fmt(s.last_seen)}` : null,
    vendorLabel(s.source),
  ]));
}

function fwEvidenceLines(d: D, h: SectionHelpers): string[] {
  return ((d.fw_evidence ?? []) as D[]).map((e) => join([
    fwSeenLabel(e.key, h.t), h.fmt(e.at),
    e.aging ? h.t("investigate.evidence_aging") : h.t("investigate.evidence_not_aging"),
  ]));
}

const SEEN_LABEL: Record<string, string> = {
  scanner: "addresses.seen_src_scanner", wazuh: "addresses.seen_src_wazuh", ocs: "addresses.seen_src_ocs",
  dns: "addresses.seen_src_dns", rustdesk: "addresses.seen_src_rustdesk",
};

function lastSeenLines(d: D, h: SectionHelpers): string[] {
  return ((d.last_seen?.rows ?? []) as D[]).map((r) => {
    const src = SEEN_LABEL[r.source] ? h.t(SEEN_LABEL[r.source])
      : r.source.includes(":") ? fwSeenLabel(r.source, h.t)
        : r.source === "arp" ? "ARP" : vendorLabel(r.source);
    return join([src, h.fmt(r.at), label(h, `addresses.seen_verdict_${r.verdict}`, r.verdict)]);
  });
}

function fwRefLines(d: D, h: SectionHelpers): string[] {
  const x = d.firewall_refs;
  if (!x) return [];
  const out: string[] = [];
  for (const a of (x.aliases ?? []) as D[]) {
    out.push(`${h.t("investigate.fw_object")}：${join([vendorLabel(a.source_type), a.firewall, a.name, a.descr])}`);
  }
  if (x.aliases_total) out.push(h.t("investigate.more_rows", { n: x.aliases_total - x.aliases.length }));
  for (const r of (x.rules ?? []) as D[]) {
    out.push(`${h.t("investigate.fw_rule")}：${join([
      vendorLabel(r.source_type), r.firewall, r.action, r.interface,
      [r.protocol, r.dst_port].filter(Boolean).join("/"),
      r.src || r.dst ? `${r.src ?? "any"} → ${r.dst ?? "any"}` : null, r.descr])}`);
  }
  if (x.rules_total) out.push(h.t("investigate.more_rows", { n: x.rules_total - x.rules.length }));
  return out;
}

function rogueLines(d: D, h: SectionHelpers): string[] {
  return ((d.dhcp_rogue ?? []) as D[]).map((r) => join([
    r.role === "server"
      ? h.t("investigate.dhcp_rogue_server", { offered: r.offered_ip ?? "—" })
      : h.t("investigate.dhcp_rogue_offered", { server: r.server_ip, offered: r.offered_ip ?? "—" }),
    r.server_mac, r.subnet,
    r.last_seen ? `${h.t("investigate.last_seen")} ${h.fmt(r.last_seen)}` : null,
    r.server_marked ? h.t("investigate.dhcp_server_marked")
      : r.server_integration ? h.t("investigate.dhcp_server_integrated", { src: r.server_integration })
      : h.t("investigate.dhcp_server_unmarked"),
  ]));
}

/** 異常偵測的類別 → 頁面上的類別名稱（anomaly.*，與異常偵測頁同一組鍵） */
const ANOMALY_LABEL: Record<string, string> = {
  ip_conflicts: "anomaly.ip_conflicts", mac_drifts: "anomaly.mac_drifts", mac_drift_reference: "anomaly.mac_drifts",
  ghost_ips: "anomaly.ghost_ips", arp_only_liveness: "anomaly.arp_only", stale_device_links: "anomaly.stale_link",
  mac_flapping: "anomaly.mac_flapping", identity_changes: "anomaly.identity_changes",
  unauthorized_ips: "anomaly.unauthorized", rogue_dhcp: "anomaly.rogue_dhcp", external_exposure: "anomaly.exposure",
  dangling_dns: "anomaly.dangling_dns", duplicate_ip_records: "anomaly.dup_ip", suspicious_changes: "anomaly.changes",
  fw_rule_rot: "anomaly.fw_rot",
};

function anomalyLines(d: D, h: SectionHelpers): string[] {
  return ((d.anomalies ?? []) as D[]).map((a) => {
    const cat = ANOMALY_LABEL[a.category] ? h.t(ANOMALY_LABEL[a.category]) : a.category;
    const kind = a.kind && h.te(`anomaly.exp_${a.kind}`) ? h.t(`anomaly.exp_${a.kind}`) : a.kind;
    const change = a.field && (a.old || a.new) ? `${a.field}：${a.old ?? "—"} → ${a.new ?? "—"}` : null;
    return join([cat, kind, a.mac, (a.macs ?? []).join(", ") || null, a.hostname, change,
                 a.port != null ? `port ${a.port}` : null,
                 h.fmt(a.last_at ?? a.last_seen_at ?? a.detected_at)]);
  });
}

function aiLines(d: D, h: SectionHelpers): string[] {
  return ((d.ai_findings ?? []) as D[]).map((f) => join([
    `[${label(h, `ai_audit.sev_${f.severity}`, f.severity ?? "—")}] ${f.title ?? ""}`, h.fmt(f.at),
  ]));
}

function consoleLines(d: D, h: SectionHelpers): string[] {
  return ((d.console_sessions ?? []) as D[]).map((c) => join([
    h.fmt(c.at), label(h, `investigate.console_${c.kind}`, String(c.kind ?? "").toUpperCase()),
    c.user ?? h.t("investigate.console_unknown_user"),
    c.actor_ip ? h.t("investigate.console_from", { ip: c.actor_ip }) : null,
    c.remote_user ? h.t("investigate.console_login_as", { user: c.remote_user }) : null,
    // 連了多久：後端配對到結束記錄才有（本機 RustDesk 客戶端不經 jt-ipam，沒有）
    typeof c.duration_seconds === "number" ? h.t("investigate.console_duration", { time: fmtClock(c.duration_seconds) }) : null,
  ]));
}

function make(key: string, title: string, lines: string[], count?: number): InvSection | null {
  if (!lines.length) return null;
  return count === undefined ? { key, title, lines } : { key, title, lines, count };
}

function len(v: unknown): number {
  return Array.isArray(v) ? v.length : 0;
}

/** 同位址其他紀錄之後：設備識別 */
export function headSections(d: D | null | undefined, h: SectionHelpers): InvSection[] {
  if (!d?.found) return [];
  return [make("identity", h.t("investigate.sec_identity"), identityLines(d, h))]
    .filter((s): s is InvSection => s !== null);
}

/** 監控涵蓋之後：電腦上的代理、探測、虛擬化、DHCP、接在哪裡、防火牆的觀測、各來源最後出現 */
export function midSections(d: D | null | undefined, h: SectionHelpers): InvSection[] {
  if (!d?.found) return [];
  return [
    make("agents", h.t("investigate.sec_agents"), agentLines(d, h)),
    make("probe", h.t("investigate.sec_probe"), probeLines(d, h)),
    make("virt", h.t("investigate.sec_virt"), virtLines(d, h)),
    make("dhcp", "DHCP", dhcpLines(d, h)),
    make("switch", h.t("investigate.sec_switch"), switchLines(d, h), len(d.switch_ports)),
    make("fw_evidence", h.t("investigate.sec_fw_evidence"), fwEvidenceLines(d, h), len(d.fw_evidence)),
    make("last_seen", h.t("addresses.seen_title"), lastSeenLines(d, h)),
  ].filter((s): s is InvSection => s !== null);
}

/** 防火牆規則之後：防火牆物件與規則、DHCP 回應、異常偵測、AI 巡檢、主控台連線 */
export function tailSections(d: D | null | undefined, h: SectionHelpers): InvSection[] {
  if (!d?.found) return [];
  return [
    make("fw_refs", h.t("investigate.sec_fw_refs"), fwRefLines(d, h)),
    make("dhcp_rogue", h.t("investigate.sec_dhcp_rogue"), rogueLines(d, h), len(d.dhcp_rogue)),
    make("anomalies", h.t("investigate.sec_anomalies"), anomalyLines(d, h), len(d.anomalies)),
    make("ai_findings", h.t("investigate.sec_ai_findings"), aiLines(d, h), len(d.ai_findings)),
    make("console", h.t("investigate.sec_console"), consoleLines(d, h), len(d.console_sessions)),
  ].filter((s): s is InvSection => s !== null);
}

/** 探測頁「其他整合系統的對應」的一段：欄位標題＋每列一格一個值（cols 與每列等長） */
export interface IntegTable {
  key: string;
  title: string;
  cols: string[];
  rows: string[][];
}

/** 拿掉整欄都沒有值的欄位；沒有列的段落不回傳 */
function table(key: string, title: string, cols: string[], rows: (string | null | undefined)[][]): IntegTable | null {
  if (!rows.length) return null;
  const keep = cols.map((_c, i) => rows.some((r) => r[i] != null && r[i] !== ""));
  return {
    key, title,
    cols: cols.filter((_c, i) => keep[i]),
    rows: rows.map((r) => r.map((v) => (v == null ? "" : String(v))).filter((_v, i) => keep[i])),
  };
}

/** 「項目：內容」→ [項目, 內容]；沒有冒號的整行放在內容、項目用 fallback */
function itemRow(line: string, fallback: string): string[] {
  const i = line.indexOf("：");
  return i > 0 ? [line.slice(0, i), line.slice(i + 1)] : [fallback, line];
}

/**
 * 探測頁的「其他整合系統的對應」（使用者 2026-10-20「探測時也要搭配有整合的其它系統從 IP 找對應，例如虛擬化」）。
 * 2026-10-21 使用者：右邊用「 · 」串成一行的內容參差不齊 → 改成一段一張表，值放在自己的欄位，同頁憑證表的排法。
 * 資料與調查視窗同一份（調查檔案），只是排成表格。
 */
export function integrationTables(d: D | null | undefined, h: SectionHelpers,
                                  wazuhStatus: (s: any) => string, lnmsStatus: (s: any) => string): IntegTable[] {
  if (!d?.found) return [];
  const c = (k: string) => h.t(`identify.integ_cols.${k}`);
  const out: (IntegTable | null)[] = [];

  const v = d.virtualization;
  if (v) {
    const tag = v.kind === "ct" ? h.t("addresses.virt_ct_tag") : h.t("addresses.virt_vm_tag");
    const tech = virtTech(v.platform, v.kind);
    out.push(table("virt", h.t("investigate.sec_virt"),
      [c("name"), c("type"), c("node"), c("status"), c("cluster"), "VMID"],
      [[v.vm, tech ? `${tag} · ${tech}` : tag, v.node, v.status ? label(h, `virt.vm_status_${v.status}`, v.status) : null,
        v.cluster, v.vmid != null ? String(v.vmid) : null]]));
  }

  const m = d.monitoring ?? {};
  const mon: (string | null | undefined)[][] = [];
  if (m.wazuh) {
    mon.push(["Wazuh", m.wazuh.name ?? m.wazuh.agent_id, wazuhStatus(m.wazuh.status),
              m.wazuh.sca_score != null ? `SCA ${m.wazuh.sca_score}` : null]);
  }
  if (m.librenms) {
    mon.push(["LibreNMS", m.librenms.hostname ?? m.librenms.sysname, lnmsStatus(m.librenms.status),
              join([m.librenms.hardware, m.librenms.matched_by === "primary_ip" ? h.t("investigate.matched_by_ip") : null]) || null]);
  }
  for (const z of (m.zabbix ?? []) as D[]) {
    mon.push(["Zabbix", z.name ?? z.host, label(h, `zabbix.${z.status}`, z.status ?? "—"),
              join([z.available === "up" ? h.t("topology.status_up") : z.available === "down" ? h.t("topology.status_down") : null,
                    z.maintenance ? h.t("zabbix.maintenance") : null]) || null]);
  }
  for (const c of (m.checkmk ?? []) as D[]) {
    mon.push(["Checkmk", c.host, label(h, `checkmk.state_${c.state}`, c.state ?? "—"),
              join([c.downtime ? h.t("checkmk.downtime") : null, c.folder]) || null]);
  }
  out.push(table("mon", h.t("investigate.sec_mon"), [c("system"), c("name"), c("status"), c("note")], mon));

  const a = d.agents ?? {};
  if (a.ocs) {
    // OCS 的盤點內容是「項目：內容」一行一筆（作業系統、硬體、CPU、記憶體、磁碟…）
    const lines = agentLines({ agents: { ocs: a.ocs } }, h);
    out.push(table("ocs", "OCS Inventory", [c("item"), c("content")], lines.map((l) => itemRow(l, "OCS"))));
  }
  out.push(table("rustdesk", "RustDesk",
    ["ID", c("status"), c("last_online"), c("os"), c("hostname"), c("user"), c("match")],
    ((a.rustdesk ?? []) as D[]).map((p) => [
      String(p.id), p.online ? h.t("rustdesk.online") : h.t("rustdesk.offline"),
      p.last_online_at ? h.fmt(p.last_online_at) : null, p.os, p.hostname, p.username,
      label(h, `rustdesk.match_${p.match_status}`, p.match_status ?? "—")
        + (p.key_problem ? `（${h.t("rustdesk.key_problem")}）` : ""),
    ])));

  out.push(table("dhcp", "DHCP", [c("item"), c("content")], dhcpLines(d, h).map((l) => itemRow(l, c("status")))));

  out.push(table("switch", h.t("investigate.sec_switch"),
    [c("switch"), c("port"), "VLAN", c("last_seen"), c("source")],
    ((d.switch_ports ?? []) as D[]).map((x) => [
      x.switch ?? "—", x.port, x.vlan != null ? String(x.vlan) : null, x.last_seen ? h.fmt(x.last_seen) : null,
      vendorLabel(x.source),
    ])));

  return out.filter((x): x is IntegTable => x !== null);
}
