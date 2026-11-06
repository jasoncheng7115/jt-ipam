/**
 * IP 清單共用欄位（子網路詳細頁與 IP 位址頁共用）：算法只有一份，兩頁的「最後出現」「失聯」「DHCP」一致。
 */
import { describe, expect, it } from "vitest";
import { dhcpInfoFor, isProbed, lastSeenText, renderDeviceLink, staleDays, staleDaysLabel, toDhcpRanges } from "../ipColumns";

const t = (k: string, p?: Record<string, unknown>) => (p ? `${k}(${JSON.stringify(p)})` : k);
const day = 86400000;

describe("ipColumns", () => {
  it("last seen takes the newest of scanner, LibreNMS and ARP", () => {
    const iso = (ms: number) => new Date(ms).toISOString();
    const now = Date.now();
    const r: any = { last_seen_scanner: iso(now - 3 * day), last_seen_librenms: null, last_seen_arp: iso(now - day) };
    expect(lastSeenText(r)).not.toBe("—");
    expect(lastSeenText({} as any)).toBe("—");
    // 失聯天數不算 ARP（只看掃描代理與 LibreNMS）
    expect(staleDays(r)).toBe(3);
  });

  it("stale label says not probed, never seen, or n days", () => {
    expect(staleDaysLabel(t, { exclude_from_ping: true } as any)).toBe("stale.not_probed");
    expect(staleDaysLabel(t, {} as any)).toBe("stale.never");
    expect(isProbed({ subnet_scan_enabled: false } as any)).toBe(false);
    const r: any = { last_seen_librenms: new Date(Date.now() - 2 * day).toISOString() };
    expect(staleDaysLabel(t, r)).toBe('stale.n_days({"n":2})');
  });

  it("finds the DHCP range of an IPv4 address and labels manual pools", () => {
    const ranges = toDhcpRanges([
      { id: "1", source_type: "windows_dhcp", source_id: "x", source_name: "dc01", subnet_cidr: null,
        start_ip: "192.0.2.100", end_ip: "192.0.2.150", family: 4, source: "windows" },
      { id: "2", source_type: "manual", source_id: "y", source_name: "LAN", subnet_cidr: null,
        start_ip: "192.0.2.200", end_ip: "192.0.2.210", family: 4, source: "manual" },
      { id: "3", source_type: "kea_dhcp", source_id: "z", subnet_cidr: null,
        start_ip: "2001:db8::10", end_ip: "2001:db8::20", family: 6, source: "kea" },
    ], "手動");
    expect(ranges).toHaveLength(2);
    expect(dhcpInfoFor(ranges, "192.0.2.120/32")?.server).toBe("dc01");
    expect(dhcpInfoFor(ranges, "192.0.2.205")?.source).toBe("手動");
    expect(dhcpInfoFor(ranges, "192.0.2.180")).toBeNull();
    expect(dhcpInfoFor(ranges, null)).toBeNull();
  });
});

describe("ipToBigInt／rangeContaining（手動新增落在保留／DHCP 範圍的警告，GitHub issue #56）", () => {
  it("IPv4 與 IPv6 都比得出大小", async () => {
    const { ipToBigInt } = await import("../ipColumns");
    expect(ipToBigInt("198.51.100.227")).toBe(3325256931n);
    expect(ipToBigInt("2001:db8::1")).toBe((0x20010db8n << 96n) + 1n);
    expect(ipToBigInt("::")).toBe(0n);
    expect(ipToBigInt("198.51.100.1/24")).toBe(ipToBigInt("198.51.100.1"));
    expect(ipToBigInt("nope")).toBeNull();
    expect(ipToBigInt("1::2::3")).toBeNull();
  });
  it("找出包含它的範圍；不同位址族不算", async () => {
    const { rangeContaining } = await import("../ipColumns");
    const ranges = [{ start_ip: "198.51.100.224", end_ip: "198.51.100.230", name: "metallb" },
                    { start_ip: "2001:db8::10", end_ip: "2001:db8::ff", name: "v6" }];
    expect(rangeContaining("198.51.100.227", ranges)?.name).toBe("metallb");
    expect(rangeContaining("198.51.100.231", ranges)).toBeNull();
    expect(rangeContaining("2001:db8::20", ranges)?.name).toBe("v6");
  });

  it("裝置欄：共用給裝置群組的 IP 在裝置名稱旁邊標「群組共用」，沒有群組就只有連結", () => {
    const plain: any = renderDeviceLink({ device_id: "d1", device_name: "sw-1" } as any, () => {}, t);
    expect(plain.type).toBe("a");
    const shared: any = renderDeviceLink(
      { device_id: "d1", device_name: "sw-1", device_group_name: "N2048P" } as any, () => {}, t);
    expect(shared.type).toBe("span");
    const tag = shared.children[1];
    expect(tag.children.default()).toBe("device_groups.shared_tag");
    expect(tag.props.title).toContain("N2048P");
    expect(renderDeviceLink({ device_id: null } as any, () => {}, t)).toBe("—");
  });
});
