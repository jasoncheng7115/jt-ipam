// vitest global setup
import { vi } from "vitest";

// jsdom 沒 matchMedia；naive-ui theme detection 會用到
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

// jsdom 沒 crypto.randomUUID(部分 Node 版本)
if (!globalThis.crypto || typeof globalThis.crypto.randomUUID !== "function") {
  Object.defineProperty(globalThis, "crypto", {
    value: {
      ...(globalThis.crypto || {}),
      randomUUID: () => "00000000-0000-4000-8000-000000000000",
      getRandomValues: (arr: Uint8Array) => {
        for (let i = 0; i < arr.length; i++) arr[i] = i;
        return arr;
      },
    },
    writable: true,
  });
}
