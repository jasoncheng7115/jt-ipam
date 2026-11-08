import { describe, expect, it } from "vitest";
import { assignOwners, deviceGroupLinks, gridPositions, multiHomed, nodeLabels, packRows, ringPositions, segmentsFor } from "../topologyLayout";

describe("主機歸屬哪一台交換器（接入群組）", () => {
  it("直連的埠優先；兩邊都學到的另一條只算待確認，不當成雙接", () => {
    const owners = assignOwners([
      { id: "e1", source: "h1", target: "sw-006", port: "eth1/0/17", direct: false, port_mac_count: 12 },
      { id: "e2", source: "h1", target: "sw-003", port: "eth1/10", direct: true, port_mac_count: 1 },
      { id: "e3", source: "h2", target: "sw-006", port: "Eth1/5", direct: true, port_mac_count: 1 },
    ], ["sw-006", "sw-003"]);
    expect(owners.get("h1")).toMatchObject({ switch: "sw-003", edge: "e2", port: "eth1/10", direct: true, others: ["e1"] });
    expect(owners.get("h2")).toMatchObject({ switch: "sw-006", others: [] });
  });

  it("都不是直連：埠上 MAC 少的那邊比較靠近；再平手照交換器順序", () => {
    const owners = assignOwners([
      { id: "a", source: "h", target: "s2", port_mac_count: 3 },
      { id: "b", source: "h", target: "s1", port_mac_count: 8 },
    ], ["s1", "s2"]);
    expect(owners.get("h")?.switch).toBe("s2");
    const tie = assignOwners([
      { id: "a", source: "h", target: "s2", port_mac_count: 3 },
      { id: "b", source: "h", target: "s1", port_mac_count: 3 },
    ], ["s1", "s2"]);
    expect(tie.get("h")?.switch).toBe("s1");
  });
});

describe("雙接（不同網卡各接一台交換器）", () => {
  it("兩條線的網卡 MAC 不同才算雙接，交換器照順序", () => {
    const m = multiHomed([
      { id: "a", source: "pve", target: "sw-003", port: "eth1/0/10", mac: "02:00:5e:10:00:01" },
      { id: "b", source: "pve", target: "sw-006", port: "Eth1/4", mac: "02:00:5e:10:00:02" },
      { id: "c", source: "nas", target: "sw-006", port: "Eth1/5", mac: "aa:aa:aa:aa:aa:01" },
    ], ["sw-006", "sw-003"]);
    expect(m.get("pve")).toEqual({ switches: ["sw-006", "sw-003"], edges: ["b", "a"] });
    expect(m.has("nas")).toBe(false);
  });

  it("同一個 MAC 在兩台都學到（上聯造成）或沒有 MAC 的舊資料：不算雙接", () => {
    expect(multiHomed([
      { id: "a", source: "h", target: "s1", mac: "AA:AA:AA:AA:AA:01" },
      { id: "b", source: "h", target: "s2", mac: "aa:aa:aa:aa:aa:01" },
    ], ["s1", "s2"]).size).toBe(0);
    expect(multiHomed([
      { id: "a", source: "h", target: "s1" },
      { id: "b", source: "h", target: "s2" },
    ], ["s1", "s2"]).size).toBe(0);
  });

  it("網卡綁定（後端標 bond）：同一個 MAC 插在兩台也是雙接", () => {
    expect(multiHomed([
      { id: "a", source: "nas-a", target: "s2", port: "Gi1/0/7", mac: "aa:aa:aa:aa:aa:01", bond: true },
      { id: "b", source: "nas-a", target: "s1", port: "Gi1/0/5", mac: "aa:aa:aa:aa:aa:01", bond: true },
    ], ["s1", "s2"]).get("nas-a")).toEqual({ switches: ["s1", "s2"], edges: ["b", "a"] });
  });
});

describe("節點名稱", () => {
  it("同名的補上 IP 才分得出來；不同名的不加", () => {
    const m = nodeLabels([{ id: "1", label: "ap-lobby", ip: "192.0.2.31" }, { id: "2", label: "ap-lobby", ip: "192.0.2.32" },
                          { id: "3", label: "nas3", ip: "192.0.2.5" }]);
    expect(m.get("1")).toBe("ap-lobby\n192.0.2.31");
    expect(m.get("2")).toBe("ap-lobby\n192.0.2.32");
    expect(m.get("3")).toBe("nas3");
  });

  it("同名又沒有 IP：標同名 i/n", () => {
    const m = nodeLabels([{ id: "1", label: "ap-lobby" }, { id: "2", label: "ap-lobby" }], (i, n) => `同名 ${i}/${n}`);
    expect([m.get("1"), m.get("2")]).toEqual(["ap-lobby\n同名 1/2", "ap-lobby\n同名 2/2"]);
  });

  it("長名稱最多兩行，FQDN 在句點後斷，太長才省略", () => {
    const m = nodeLabels([{ id: "1", label: "router-07.example.com" },
                          { id: "2", label: "customer-portal.production.ap-southeast.example.com" }]);
    expect(m.get("1")).toBe("router-07.example.com");
    const two = m.get("2")!.split("\n");
    expect(two.length).toBe(2);
    expect(two[1].endsWith("…")).toBe(true);
  });
});

describe("VPN 從外側繞的折點", () => {
  it("折點換算成 cytoscape segments 的比例與距離，算回來要落在原座標", () => {
    const s = { x: 0, y: 0 }, t = { x: 100, y: 200 };
    const pts = [{ x: 300, y: 0 }, { x: 300, y: 200 }];
    const { weights, distances } = segmentsFor(s, t, pts);
    const L2 = 100 * 100 + 200 * 200, L = Math.sqrt(L2);
    pts.forEach((p, i) => {
      // 還原：沿線 w 的位置，再往法線方向（右手邊為正）走 d
      const bx = s.x + weights[i] * 100, by = s.y + weights[i] * 200;
      const nx = -200 / L, ny = 100 / L;
      expect(bx + distances[i] * nx).toBeCloseTo(p.x, 6);
      expect(by + distances[i] * ny).toBeCloseTo(p.y, 6);
    });
  });
});

describe("依子網路排版：同網段靠近、不同網段拉開", () => {
  // 10-22 使用者：「不要一開始畫那麼開，同一個子網路的要靠近點，不同子網路的才要拉開」，混合模式的子網路框還互相重疊
  const dist = (a: { x: number; y: number }, b: { x: number; y: number }) => Math.hypot(a.x - b.x, a.y - b.y);

  it("一圈排在子網路節點周圍：彼此不重疊、也不會散得很開", () => {
    for (const n of [1, 3, 8, 20, 60]) {
      const pts = ringPositions(n, 96);
      expect(pts).toHaveLength(n);
      for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) expect(dist(pts[i], pts[j])).toBeGreaterThanOrEqual(90);
      const far = Math.max(...pts.map((p) => dist(p, { x: 0, y: 0 })));
      expect(far).toBeLessThan(140 + Math.sqrt(n) * 70);       // 隨數量成長，但不是線性散開
    }
  });

  it("框內排成格子，以 0,0 為中心", () => {
    const pts = gridPositions(5, 130, 110);
    expect(pts).toHaveLength(5);
    const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
    expect(Math.min(...xs) + Math.max(...xs)).toBeCloseTo(0, 6);
    expect(Math.min(...ys) + Math.max(...ys)).toBeCloseTo(0, 6);
  });

  it("各團依序排開，團與團之間至少留 gap，不會重疊", () => {
    const items = [{ id: "a", w: 400, h: 300 }, { id: "b", w: 200, h: 200 }, { id: "c", w: 600, h: 250 }, { id: "d", w: 300, h: 300 }];
    const pos = packRows(items, 200, 1400);
    const boxes = items.map((it) => ({ ...it, ...pos.get(it.id)! }));
    for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i], b = boxes[j];
      const apart = a.x + a.w + 200 <= b.x + 0.01 || b.x + b.w + 200 <= a.x + 0.01
        || a.y + a.h + 200 <= b.y + 0.01 || b.y + b.h + 200 <= a.y + 0.01;
      expect(apart, `${a.id}-${b.id}`).toBe(true);
    }
    // 超過寬度才換列
    expect(pos.get("a")!.y).toBe(pos.get("b")!.y);
    expect(pos.get("c")!.y).toBeGreaterThan(pos.get("a")!.y);
  });
});

describe("裝置群組（堆疊／HA）在拓樸圖上的連線", () => {
  const n = (id: string, gid: string | null, order: number | null, label = id) =>
    ({ id, label, group_id: gid, group_name: gid ? `G-${gid}` : null, group_kind: "stack", group_order: order });

  it("同一群組的成員依序號串成一條鏈，標群組名稱；只有一台在圖上就不畫", () => {
    const links = deviceGroupLinks([n("b", "g1", 2), n("a", "g1", 1), n("c", "g1", 3), n("x", null, null), n("solo", "g2", 1)]);
    expect(links.map((l) => [l.source, l.target])).toEqual([["a", "b"], ["b", "c"]]);
    expect(links[0]).toMatchObject({ kind: "device_group", group_id: "g1", label: "G-g1" });
    expect(new Set(links.map((l) => l.id)).size).toBe(2);
  });

  it("沒有序號的排在有序號的後面，再依名稱", () => {
    const links = deviceGroupLinks([n("z", "g1", null, "sw-z"), n("y", "g1", null, "sw-a"), n("w", "g1", 1)]);
    expect(links.map((l) => [l.source, l.target])).toEqual([["w", "y"], ["y", "z"]]);
  });
});
