import { describe, expect, it } from "vitest";
import {
  addrParts, BODY, CANVAS_H, CANVAS_W, GAP, IP_RECT, layout, longTime, NODE_W, rowTop, shortTime, type REdge, type RNode,
} from "../relationsLayout";

const n = (key: string, row: RNode["row"], col: number, slot = 0): RNode =>
  ({ key, type: key.split(":")[0], id: key, label: key, sub: null, row, col, slot });
const e = (from: string, to: string, extra: Partial<REdge> = {}): REdge =>
  ({ from, to, kind: "k", source: "ipam", at: null, line: "solid", state: "ok", reason: null, ...extra });

describe("relations layout", () => {
  it("keeps everything inside the canvas and the IP box spans all three rows", () => {
    expect(CANVAS_W).toBe(1174);
    expect(IP_RECT.x + IP_RECT.w).toBe(CANVAS_W);
    expect(IP_RECT.y).toBe(rowTop(0));
    expect(IP_RECT.y + IP_RECT.h).toBe(rowTop(2) + BODY);
    expect(CANVAS_H).toBeLessThanOrEqual(700);              // 留給圖例的空間
  });

  it("edges into the IP are horizontal at the source's height", () => {
    const { edges, nodes } = layout(
      [n("ip:1", null, 3), n("device:1", "physical", 2), n("subnet:1", "address", 2)],
      [e("device:1", "ip:1"), e("subnet:1", "ip:1")]);
    const dev = nodes.find((x) => x.key === "device:1")!;
    const toIp = edges.find((x) => x.from === "device:1")!;
    const y = dev.rect.y + dev.rect.h / 2;
    expect(toIp.d).toBe(`M ${dev.rect.x + NODE_W} ${y} H ${IP_RECT.x}`);
    expect(toIp.label.y).toBe(y);
  });

  it("stacks two objects in a cell and moves single nodes to the top lane", () => {
    const { nodes, edges } = layout(
      [n("location:a", "physical", 0, 0), n("location:b", "physical", 0, 1), n("rack:1", "physical", 1),
        n("device:1", "physical", 2), n("ip:1", null, 3)],
      [e("location:a", "rack:1"), e("location:b", "device:1", { state: "pending", line: "dashed" })]);
    const by = Object.fromEntries(nodes.map((x) => [x.key, x]));
    expect(by["location:a"].rect.y).toBe(by["rack:1"].rect.y);
    expect(by["location:b"].rect.y).toBeGreaterThan(by["rack:1"].rect.y + by["rack:1"].rect.h);
    expect(by["rack:1"].compact).toBe(true);
    // 跨欄的待確認線走下方走道，不穿過機櫃方塊；最後才畫（在最上層）
    const skip = edges[edges.length - 1];
    expect(skip.state).toBe("pending");
    const laneY = by["location:b"].rect.y + by["location:b"].rect.h / 2;
    expect(skip.d.startsWith(`M ${NODE_W} ${laneY} H `)).toBe(true);
    expect(laneY).toBeGreaterThan(by["rack:1"].rect.y + by["rack:1"].rect.h);
    // 轉折的垂直線貼著終點，不穿過那個間隙裡另一條邊的標籤
    const dev = by["device:1"].rect;
    expect(skip.d).toContain(`H ${dev.x - 6} V`);
    const straight = edges.find((x) => x.from === "location:a")!;
    expect(straight.label.w).toBeLessThan(GAP - 12);
  });

  it("routes a host in the physical row down to its VM", () => {
    const { edges, nodes } = layout(
      [n("device:h", "physical", 2), n("vm:1", "virtual", 1), n("ip:1", null, 3)],
      [e("device:h", "vm:1", { line: "dashed" })]);
    const h = nodes.find((x) => x.key === "device:h")!.rect;
    const v = nodes.find((x) => x.key === "vm:1")!.rect;
    const d = edges[0].d;
    expect(d.startsWith(`M ${h.x + h.w / 2} ${h.y + h.h} V `)).toBe(true);
    expect(d.endsWith(`H ${v.x + v.w / 2} V ${v.y}`)).toBe(true);
    expect(edges[0].label.align).toBe("side");
  });

  it("drops edges whose ends are not drawn (hidden by permissions)", () => {
    const { edges } = layout([n("ip:1", null, 3)], [e("device:x", "ip:1")]);
    expect(edges).toEqual([]);
  });
});

describe("address wrapping", () => {
  it("breaks only after separators and keeps every character", () => {
    expect(addrParts("192.168.123.0/24")).toEqual(["192.", "168.", "123.", "0/", "24"]);
    expect(addrParts("2001:db8::1a2b").join("")).toBe("2001:db8::1a2b");
  });
});

describe("relation times", () => {
  const now = new Date(2026, 9, 23, 18, 30);
  it("same day shows hours, otherwise month-day", () => {
    expect(shortTime(new Date(2026, 9, 23, 9, 5).toISOString(), now)).toBe("09:05");
    expect(shortTime(new Date(2026, 9, 21, 9, 5).toISOString(), now)).toBe("10-21");
    expect(longTime(new Date(2026, 9, 21, 9, 5).toISOString(), now)).toBe("10-21 09:05");
    expect(shortTime(null, now)).toBe("");
  });
});
