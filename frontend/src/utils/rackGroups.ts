/**
 * 機櫃圖的裝置群組括線（客戶 2026-11-05：兩台 N2048P 堆疊要標得出來）。
 *
 * 畫面（RackDiagram.vue）與圖形匯出（rackGraphicsExport.ts）共用這份計算；對外嵌入的 SVG
 * （backend services/rack_svg.py）照同一套規則另寫一份。改規則三處要一起改。
 */
export interface RackGroupInfo {
  id: string;
  name: string;
  kind: string;
  role: string | null;
  order: number | null;
  /** 群組總共幾台 */
  total: number;
  /** 不在這個機櫃的成員，依機櫃彙總 */
  elsewhere: { rack_id: string; rack_name: string | null; count: number }[];
  /** 沒放進任何機櫃的成員台數 */
  unracked: number;
}

interface GroupDevice {
  device_id: string;
  name: string;
  type: string;
  u_position: number;
  u_size: number;
  rack_face?: "front" | "rear" | null;
  group?: RackGroupInfo | null;
}

export interface GroupBracket extends RackGroupInfo {
  /** 這一面的成員占到的 U 範圍（含） */
  from: number;
  to: number;
  /** 這個機櫃裡（兩面都算）有幾台 */
  here: number;
  /** 範圍重疊的群組分到不同軌道，0 起算 */
  lane: number;
  /** 這一面的成員，畫面由上往下（U 高的先） */
  members: GroupDevice[];
  color: string;
}

/** 跟裝置類型色、另一面提示的藍色都分得開的一組顏色 */
const PALETTE = ["#0f9d8a", "#d9822b", "#c2185b", "#7b5cd6", "#2e7d32", "#b8860b", "#00838f", "#ad1457"];

export function groupColor(id: string): string {
  let h = 0;
  for (const ch of id) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

export function groupBrackets(devices: GroupDevice[], face: "front" | "rear"): GroupBracket[] {
  const byGroup = new Map<string, { info: RackGroupInfo; here: number; members: GroupDevice[] }>();
  for (const d of devices) {
    if (!d.group || !(d.u_position > 0)) continue;
    const e = byGroup.get(d.group.id) ?? { info: d.group, here: 0, members: [] };
    e.here += 1;
    if ((d.rack_face ?? "front") === face) e.members.push(d);
    byGroup.set(d.group.id, e);
  }
  const out: GroupBracket[] = [];
  for (const { info, here, members } of byGroup.values()) {
    if (!members.length) continue;
    members.sort((a, b) => b.u_position - a.u_position || a.name.localeCompare(b.name));
    const from = Math.min(...members.map((m) => m.u_position));
    const to = Math.max(...members.map((m) => m.u_position + Math.max(1, m.u_size) - 1));
    out.push({ ...info, from, to, here, lane: 0, members, color: groupColor(info.id) });
  }
  // 軌道：依範圍由上往下排，跟已經放的任何一條重疊就往外挪一條
  out.sort((a, b) => b.to - a.to || a.name.localeCompare(b.name));
  const placed: GroupBracket[] = [];
  for (const b of out) {
    let lane = 0;
    while (placed.some((p) => p.lane === lane && p.from <= b.to && b.from <= p.to)) lane++;
    b.lane = lane;
    placed.push(b);
  }
  return out;
}
