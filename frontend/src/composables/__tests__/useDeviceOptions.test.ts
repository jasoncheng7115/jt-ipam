import { beforeEach, describe, expect, it, vi } from "vitest";

const listDevices = vi.fn();
const getDevice = vi.fn();
vi.mock("@/api/basic", () => ({ listDevices: (...a: unknown[]) => listDevices(...a), getDevice: (...a: unknown[]) => getDevice(...a) }));

import { useDeviceOptions } from "../useDeviceOptions";

describe("裝置下拉：搜尋走後端，已選的不在第一批也補得回來（issue #58 連帶）", () => {
  beforeEach(() => { listDevices.mockReset(); getDevice.mockReset(); });

  it("關鍵字送到後端，不在已載入的幾百台裡過濾", async () => {
    listDevices.mockResolvedValue({ items: [{ id: "d9", name: "fw-z", type: "firewall" }], total: 1 });
    const o = useDeviceOptions();
    await o.search("fw-z");
    expect(listDevices).toHaveBeenCalledWith({ page: 1, pageSize: 50, q: "fw-z" });
    expect(o.options.value).toEqual([{ label: "fw-z (firewall)", value: "d9" }]);
  });

  it("編輯時已選的那台不在選項裡：補抓一筆放最前面", async () => {
    listDevices.mockResolvedValue({ items: [{ id: "d1", name: "a", type: null }], total: 900 });
    getDevice.mockResolvedValue({ id: "d700", name: "far-away", type: "server" });
    const o = useDeviceOptions();
    await o.search();
    await o.ensure("d700");
    expect(o.options.value[0]).toEqual({ label: "far-away (server)", value: "d700" });
    await o.ensure("d700");
    expect(getDevice).toHaveBeenCalledTimes(1);
  });
});
