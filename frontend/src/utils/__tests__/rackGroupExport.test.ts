/**
 * 機櫃圖匯出（SVG／PNG／draw.io）要跟畫面一樣標出裝置群組（2026-11-05）：同色括線＋「名稱 本櫃/總數」。
 * 括線放在機櫃右側，所以有群組的機櫃要多留寬度，不能蓋到隔壁那一座。
 */
import { describe, expect, it } from "vitest";
import { buildRacksDrawio, buildRacksSvg, type ExportDiagram } from "../rackGraphicsExport";
import { groupColor } from "../rackGroups";

const grp = { id: "g1", name: "N2048P-stack", kind: "stack", role: null, order: null, total: 3,
  elsewhere: [{ rack_id: "r2", rack_name: "B", count: 1 }], unracked: 0 };
const rack = (devices: any[]): ExportDiagram => ({ name: "A", u_height: 12, kind: "rack", render_row_px: 28,
  render_row_px_list: [], render_width_px: 250, devices });
const sw = (id: string, u: number, face = "front") => ({ device_id: id, name: id, type: "switch", u_position: u,
  u_size: 1, rack_face: face, group: grp });

describe("匯出的裝置群組括線", () => {
  it("SVG：同一群組一條括線、一個標籤（兩面的成員都算進本櫃台數）", () => {
    const svg = buildRacksSvg([rack([sw("a", 10), sw("b", 8, "rear")])], 0, "left")!.svg;
    expect(svg.match(/class="rack-group"/g)).toHaveLength(1);
    expect(svg).toContain("N2048P-stack 2/3");
    expect(svg).toContain(groupColor("g1"));
  });

  it("有群組的機櫃多留右側寬度：並排時下一座往右移", () => {
    const plain = buildRacksSvg([rack([]), rack([])], 0, "left")!.W;
    const withGroup = buildRacksSvg([rack([sw("a", 10), sw("b", 8)]), rack([])], 0, "left")!.W;
    expect(withGroup).toBeGreaterThan(plain + 100);
  });

  it("沒有群組就不畫", () => {
    const svg = buildRacksSvg([rack([{ ...sw("a", 10), group: null }])], 0, "left")!.svg;
    expect(svg).not.toContain("rack-group");
  });

  it("draw.io：括線與標籤各一個 cell", () => {
    const x = buildRacksDrawio([rack([sw("a", 10), sw("b", 8)])], 0, "left", "t")!;
    expect(x).toContain('value="N2048P-stack 2/3"');
    expect(x.match(/id="grp\d+"/g)?.length).toBe(1);
  });
});
