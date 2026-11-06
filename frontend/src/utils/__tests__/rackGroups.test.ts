import { describe, expect, it } from "vitest";
import { groupBrackets, groupColor, type RackGroupInfo } from "../rackGroups";

const g = (id: string, name: string, total: number, extra: Partial<RackGroupInfo> = {}): RackGroupInfo =>
  ({ id, name, kind: "stack", role: null, order: null, total, elsewhere: [], unracked: 0, ...extra });
const dev = (id: string, u: number, size: number, group: RackGroupInfo | null, face: "front" | "rear" = "front") =>
  ({ device_id: id, name: id, type: "switch", u_position: u, u_size: size, rack_face: face, group });

describe("機櫃圖的裝置群組括線", () => {
  it("同一群組在這一面的成員框成一條括線，從最低的 U 到最高的 U；沒有群組的裝置不畫", () => {
    const grp = g("g1", "N2048P", 2);
    const out = groupBrackets([dev("a", 10, 1, grp), dev("b", 12, 1, grp), dev("c", 20, 2, null)], "front");
    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({ id: "g1", name: "N2048P", from: 10, to: 12, here: 2, total: 2, lane: 0 });
    expect(out[0].members.map((m) => m.device_id)).toEqual(["b", "a"]);    // 畫面由上往下
  });

  it("另一面的成員不畫括線，但算進這個機櫃裡的台數；全部都在另一面就不畫", () => {
    const grp = g("g1", "HA", 3, { elsewhere: [{ rack_id: "r2", rack_name: "B", count: 1 }] });
    const out = groupBrackets([dev("a", 5, 1, grp), dev("b", 6, 1, grp, "rear")], "front");
    expect(out[0]).toMatchObject({ from: 5, to: 5, here: 2, total: 3 });
    expect(groupBrackets([dev("b", 6, 1, grp, "rear")], "front")).toEqual([]);
  });

  it("兩個群組的範圍重疊時分到不同的軌道，不重疊的共用第一條", () => {
    const g1 = g("g1", "A", 2), g2 = g("g2", "B", 2), g3 = g("g3", "C", 2);
    const out = groupBrackets([dev("a1", 1, 1, g1), dev("a2", 5, 1, g1), dev("b1", 3, 1, g2), dev("b2", 4, 1, g2),
                               dev("c1", 10, 1, g3), dev("c2", 11, 1, g3)], "front");
    const lane = Object.fromEntries(out.map((b) => [b.id, b.lane]));
    expect(lane).toEqual({ g1: 0, g2: 1, g3: 0 });
  });

  it("顏色依群組 id 固定，換頁或重畫都同一個顏色", () => {
    expect(groupColor("g1")).toBe(groupColor("g1"));
    expect(groupColor("g1")).toMatch(/^#[0-9a-f]{6}$/);
  });
});
