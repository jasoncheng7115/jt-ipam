/**
 * 客戶回報（0.5.208）：在機櫃裡點另一台機器，「連接埠 / 佈線」還是停在第一次點的那台。
 *
 * 機櫃圖點裝置會導到 `/devices/:id`。同一條路由只換參數時，Vue **不會重建**元件 ——
 * 只是把新的 `deviceId` 傳進去。這個面板只在 `onMounted` 抓資料，於是永遠停在第一台。
 * 隔壁的 DevicePowerPortsPanel 與 UptimeBar 都有 watch，就它沒有。
 *
 * 這種 bug 型別檢查與一般單元測試都看不到：畫面渲染成功、只是內容是別人的。
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createI18n } from "vue-i18n";
import zhTW from "@/i18n/zh-TW.json";

const ports = vi.fn();
vi.mock("@/api/phase3", () => ({
  Physical: {
    ports: (id: string) => ports(id),
    createPort: vi.fn(),
    updatePort: vi.fn(),
    deletePort: vi.fn(),
    importPorts: vi.fn(),
    portTrace: vi.fn(async () => ({ hops: [] })),
  },
}));
vi.mock("@/api/basic", () => ({ listDevices: vi.fn(async () => ({ items: [] })) }));
vi.mock("naive-ui", async () => {
  const actual = await vi.importActual<Record<string, unknown>>("naive-ui");
  return { ...actual, useMessage: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
           useDialog: () => ({ warning: vi.fn(), info: vi.fn() }) };
});

import { createPinia, setActivePinia } from "pinia";
import DevicePortsPanel from "../DevicePortsPanel.vue";

function render(deviceId: string) {
  const i18n = createI18n({ legacy: false, locale: "zh-TW", messages: { "zh-TW": zhTW } });
  return mount(DevicePortsPanel, {
    props: { deviceId, deviceName: "sw-a", admin: false },
    global: { plugins: [i18n], stubs: { NDataTable: true, NModal: true } },
  });
}

describe("DevicePortsPanel 換裝置", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    ports.mockReset();
    ports.mockResolvedValue([]);
  });

  it("掛載時抓一次目前這台的連接埠", async () => {
    render("dev-1");
    await Promise.resolve();
    expect(ports).toHaveBeenCalledWith("dev-1");
  });

  it("換一台裝置時要重抓，不能停在上一台", async () => {
    const w = render("dev-1");
    await Promise.resolve();
    ports.mockClear();

    await w.setProps({ deviceId: "dev-2", deviceName: "sw-b", admin: false });
    await Promise.resolve();

    expect(ports).toHaveBeenCalledWith("dev-2");
  });
});
