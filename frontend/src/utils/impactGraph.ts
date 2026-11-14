/**
 * IP 變更評估關係圖的群組化（使用者 2026-10-08：45 個節點、每條線都有字，全部擠在一起看不了）。
 *
 * 一個 IP 常被二、三十筆 DNS 紀錄或反向代理引用，每筆都是只連到根目標的葉節點：
 * 畫成一圈只會讓名稱和線上的字疊成一團，也看不出「其實就是 24 筆 DNS」。
 * 掛在同一個鄰居下、同一種類型的葉節點有 GROUP_MIN 個以上，就收成一個群組節點 ——
 * 不再依影響或關係細分（第二輪回饋：同類分成好幾小群反而看不出是什麼），群組顏色取成員裡最嚴重的影響。
 * 路徑上的物件（有兩條以上的邊，例如 VM → 主機、服務 → VM）一律照畫，不收。自己連到自己的邊不畫。
 */
import type { RelationGraph, RelationNode } from "@/api/changeImpact";

export const GROUP_MIN = 3;

/** 會讓停機傳下去的關係（其餘 references／observed_on 只是引用）—— 與後端 NON_PROPAGATING 相反 */
export const PROPAGATING = new Set(["hosted_on", "requires_network", "requires_storage", "requires_power",
                                    "requires_service"]);
/** 影響的嚴重程度（數字小＝嚴重），與後端 _IMPACT_RANK 相同 */
export const IMPACT_RANK: Record<string, number> = {
  modeled_disruption: 0, potential_disruption: 1, redundancy_unverified: 2, change_required: 3, unknown: 4,
  reference_only: 5,
};
const rank = (impact: string | null | undefined) => IMPACT_RANK[impact ?? ""] ?? 9;

export interface GraphNode {
  id: string; label: string; type: string; impact: string | null; category: string | null;
  root: boolean; group: boolean; count: number;
  /** 有阻擋項目（群組：任一成員有） */
  blocker: boolean;
}
export interface GraphEdge { id: string; from: string; to: string; relation: string; strength: string; propagates: boolean }
export interface GraphGroup { id: string; category: string | null; type: string; impact: string | null;
  /** 成員都同一種關係時是那種關係，不一樣時是 "mixed" */
  relation: string; strength: string; neighbor: string; members: RelationNode[] }
export interface GroupedGraph { nodes: GraphNode[]; edges: GraphEdge[]; groups: Record<string, GraphGroup> }

function asNode(n: RelationNode): GraphNode {
  return { id: n.id, label: n.label, type: n.type, impact: n.impact, category: n.category ?? null, root: n.root,
           group: false, count: 1, blocker: !!n.blocker };
}

export function groupRelationGraph(
  g: RelationGraph, opts: { expanded?: Set<string>; expandAll?: boolean; min?: number } = {},
): GroupedGraph {
  const min = opts.min ?? GROUP_MIN;
  const rawEdges = g.edges.filter((e) => e.from !== e.to);
  const degree = new Map<string, number>();
  for (const e of rawEdges) {
    degree.set(e.from, (degree.get(e.from) ?? 0) + 1);
    degree.set(e.to, (degree.get(e.to) ?? 0) + 1);
  }
  const byId = new Map(g.nodes.map((n) => [n.id, n]));
  // 葉節點：不是根、剛好一條邊
  const candidates = new Map<string, GraphGroup & { edges: (typeof rawEdges)[number][] }>();
  for (const e of rawEdges) {
    for (const [leaf, other] of [[e.from, e.to], [e.to, e.from]] as const) {
      const n = byId.get(leaf);
      if (!n || n.root || degree.get(leaf) !== 1) continue;
      // 兩個葉節點互連（孤立的一對）：兩邊都收掉就沒有東西可以連，照畫
      if (degree.get(other) === 1 && !byId.get(other)?.root) continue;
      const key = ["grp", other, n.type].join("|");
      const grp = candidates.get(key) ?? { id: key, category: n.category ?? null, type: n.type, impact: n.impact,
                                            relation: e.relation, strength: e.strength, neighbor: other, members: [],
                                            edges: [] };
      grp.members.push(n);
      grp.edges.push(e);
      if (rank(n.impact) < rank(grp.impact)) grp.impact = n.impact;
      if (grp.relation !== e.relation) grp.relation = "mixed";
      if (e.strength !== "inferred") grp.strength = e.strength;
      candidates.set(key, grp);
    }
  }
  const groups: Record<string, GraphGroup> = {};
  const memberOf = new Map<string, string>();
  const groupEdges = new Map<string, (typeof rawEdges)[number][]>();
  for (const { edges, ...grp } of candidates.values()) {
    if (grp.members.length < min || opts.expandAll || opts.expanded?.has(grp.id)) continue;
    grp.members.sort((a, b) => rank(a.impact) - rank(b.impact) || a.label.localeCompare(b.label));
    groups[grp.id] = grp;
    groupEdges.set(grp.id, edges);
    for (const m of grp.members) memberOf.set(m.id, grp.id);
  }

  const nodes: GraphNode[] = [];
  for (const n of g.nodes) if (!memberOf.has(n.id)) nodes.push(asNode(n));
  for (const grp of Object.values(groups)) {
    nodes.push({ id: grp.id, label: "", type: grp.type, impact: grp.impact, category: grp.category, root: false,
                 group: true, count: grp.members.length, blocker: grp.members.some((m) => m.blocker) });
  }
  const edges: GraphEdge[] = [];
  rawEdges.forEach((e, i) => {
    if (memberOf.has(e.from) || memberOf.has(e.to)) return;
    edges.push({ id: `e${i}`, from: e.from, to: e.to, relation: e.relation, strength: e.strength,
                 propagates: PROPAGATING.has(e.relation) });
  });
  for (const grp of Object.values(groups)) {
    // 原本的方向：葉節點 → 鄰居（例如「DNS 紀錄 → 引用 → 這個 IP」），反過來的也保留
    const mine = groupEdges.get(grp.id) ?? [];
    const out = mine.length ? memberOf.get(mine[0].from) === grp.id : true;
    edges.push({ id: `g:${grp.id}`, from: out ? grp.id : grp.neighbor, to: out ? grp.neighbor : grp.id,
                 relation: grp.relation, strength: grp.strength,
                 propagates: mine.some((e) => PROPAGATING.has(e.relation)) });
  }
  return { nodes, edges, groups };
}

/**
 * 樹狀排列（使用者 2026-10-08：要可以切換成像另一個內部專案的 JSON 圖那樣由左往右的樹）。
 * 從根目標廣度優先找出每個節點的上一層（有多個上一層時取先找到的那個，其餘的邊照畫），
 * 同一層依 `order` 排序；葉節點一列一列往下排，上一層對齊子節點的中間。連不到根的節點接在最下面。
 */
export function treePositions(
  nodeIds: string[], edges: { from: string; to: string }[], rootId: string,
  order: (a: string, b: string) => number, opts: { colW?: number; rowH?: number } = {},
): Map<string, { x: number; y: number }> {
  const colW = opts.colW ?? 280, rowH = opts.rowH ?? 64;
  const adj = new Map<string, string[]>(nodeIds.map((id) => [id, []]));
  for (const e of edges) {
    if (e.from === e.to || !adj.has(e.from) || !adj.has(e.to)) continue;
    adj.get(e.from)!.push(e.to);
    adj.get(e.to)!.push(e.from);
  }
  const children = new Map<string, string[]>();
  const depth = new Map<string, number>();
  const visit = (start: string) => {
    depth.set(start, 0);
    const queue = [start];
    while (queue.length) {
      const cur = queue.shift()!;
      const kids = (adj.get(cur) ?? []).filter((n) => !depth.has(n));
      kids.sort(order);
      for (const k of kids) depth.set(k, depth.get(cur)! + 1);
      children.set(cur, kids);
      queue.push(...kids);
    }
  };
  const tops: string[] = [];
  if (adj.has(rootId)) { visit(rootId); tops.push(rootId); }
  for (const id of [...nodeIds].sort(order)) {
    if (!depth.has(id)) { visit(id); tops.push(id); }
  }
  const pos = new Map<string, { x: number; y: number }>();
  let row = 0;
  const place = (id: string, d: number): number => {
    const kids = children.get(id) ?? [];
    let y: number;
    if (!kids.length) {
      y = row * rowH;
      row += 1;
    } else {
      const ys = kids.map((k) => place(k, d + 1));
      y = (ys[0] + ys[ys.length - 1]) / 2;
    }
    pos.set(id, { x: d * colW, y });
    return y;
  };
  for (const top of tops) { place(top, 0); row += 1; }
  return pos;
}

/**
 * 依影響分層（使用者 2026-10-08、規劃人員建議）：根目標在中心，每一種影響一圈，越嚴重越內圈。
 * 以前用 cytoscape 的 concentric：每圈只有兩三個點時都從正上方開始排，整張圖看起來像十字，
 * 標籤又把圈距撐得很大、縮放後字小到看不見。這裡自己算：
 * - 圈距固定，但圈要夠大讓每個點之間至少隔 `minArc`（標籤寬度）
 * - 每圈的起點錯開，而且不放在正上方（正上方是那一圈的標題）
 */
export function ringLayout(
  levels: { key: string; ids: string[] }[], rootId: string | null,
  opts: { r0?: number; gap?: number; minArc?: number } = {},
): { positions: Map<string, { x: number; y: number }>; rings: { key: string; r: number; count: number }[] } {
  const r0 = opts.r0 ?? 150, gap = opts.gap ?? 130, minArc = opts.minArc ?? 170;
  const positions = new Map<string, { x: number; y: number }>();
  const rings: { key: string; r: number; count: number }[] = [];
  if (rootId) positions.set(rootId, { x: 0, y: 0 });
  let prev = 0;
  for (const lv of levels) {
    const n = lv.ids.length;
    if (!n) continue;
    const r = Math.max(rings.length ? prev + gap : r0, (n * minArc) / (2 * Math.PI));
    // 一個點時放正下方；多個點時錯開正上方的標題。每往外一圈再轉一點，相鄰兩圈的點才不會對齊成一條斜線
    let start = -Math.PI / 2 + Math.PI / n + rings.length * 0.6;
    const nearTop = (s0: number) => Array.from({ length: n }, (_, i) => s0 + (2 * Math.PI * i) / n).some((a) => {
      const d = Math.abs(((a + Math.PI / 2) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI));
      return Math.min(d, 2 * Math.PI - d) < 0.2;
    });
    if (nearTop(start)) start += Math.PI / n;
    lv.ids.forEach((id, i) => {
      const a = start + (2 * Math.PI * i) / n;
      positions.set(id, { x: Math.round(r * Math.cos(a)), y: Math.round(r * Math.sin(a)) });
    });
    rings.push({ key: lv.key, r, count: n });
    prev = r;
  }
  return { positions, rings };
}

/** 整合類型 → 設定頁的路由名稱（關係圖明細的「開啟原始物件」） */
export const INTEGRATION_PAGE: Record<string, string> = {
  librenms: "librenms", opnsense: "firewall_admin", pfsense: "pfsense", fortigate: "fortigate", paloalto: "paloalto",
  checkpoint: "checkpoint", mikrotik: "mikrotik", proxmox: "virt_admin", esxi: "esxi_admin", hyperv: "hyperv_admin", openstack: "openstack_admin", zabbix: "zabbix", checkmk: "checkmk",
  wazuh: "wazuh", dns_server: "dns", kea_dhcp: "kea_dhcp", isc_dhcp: "isc_dhcp", technitium: "technitium_dhcp",
  windows_dhcp: "windows_dhcp", ocs: "ocs", adguard: "adguard", isoinsight: "isoinsight", rustdesk: "rustdesk",
  scan_agent: "scan_agents", webhook: "webhooks", snmp: "snmp",
};

/** 節點 → 原始物件的頁面；找不到合適的頁就回 null（不要給一個點了會 404 的連結） */
export function subjectLink(n: { type: string; subject_id?: string | null; kind?: string | null; root?: boolean }):
  { name: string; params?: Record<string, string>; query?: Record<string, string> } | null {
  const id = n.subject_id ?? null;
  switch (n.type) {
    case "device": return id ? { name: "device-detail", params: { id } } : null;
    case "ip_address": return id ? { name: "addresses", query: { open: id } } : null;
    case "subnet": return id ? { name: "subnet-detail", params: { id } } : null;
    case "dns_record": return { name: "dns" };
    case "virtual_machine": return { name: "virt" };
    case "integration": return n.kind && INTEGRATION_PAGE[n.kind] ? { name: INTEGRATION_PAGE[n.kind] } : null;
    case "librenms_device": return { name: "librenms" };
    case "zabbix_host": return { name: "zabbix" };
    case "checkmk_host": return { name: "checkmk" };
    case "wazuh_agent": return { name: "wazuh" };
    case "ocs_computer": return { name: "ocs" };
    case "rustdesk_peer": return { name: "rustdesk" };
    case "nat_translation": return { name: "nat" };
    default: return null;
  }
}

/**
 * 同一對節點之間的多條關係合成一條（2026-10-25 實測：服務「ERP 系統」同時是端點又依賴這個位址，
 * 兩條線疊在一起、兩段說明文字也疊成亂碼）。關係保留順序、去重複；有一條會傳播停機就算傳播；
 * 全部都是推定才畫虛線。
 */
export function mergeParallelEdges(edges: GraphEdge[]): (GraphEdge & { relations: string[] })[] {
  const out = new Map<string, GraphEdge & { relations: string[] }>();
  for (const e of edges) {
    const k = `${e.from}\u0000${e.to}`;
    const cur = out.get(k);
    if (!cur) { out.set(k, { ...e, relations: [e.relation] }); continue; }
    if (!cur.relations.includes(e.relation)) cur.relations.push(e.relation);
    cur.propagates = cur.propagates || e.propagates;
    if (cur.strength === "inferred" && e.strength !== "inferred") cur.strength = e.strength;
  }
  return [...out.values()];
}
