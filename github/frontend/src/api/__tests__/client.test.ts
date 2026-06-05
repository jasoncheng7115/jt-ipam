import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import axios from "axios";
import { apiClient } from "@/api/client";

describe("apiClient interceptors", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("帶入 Authorization header(若 localStorage 有 token)", async () => {
    localStorage.setItem("access_token", "fake-token-abc");
    const req = apiClient.interceptors.request as any;
    const interceptor = req.handlers[0].fulfilled;
    const captured: Record<string, string> = {};
    const headers = { set: (k: string, v: string) => { captured[k] = v; } };
    await interceptor({ headers });
    expect(captured["Authorization"]).toBe("Bearer fake-token-abc");
    expect(captured["X-Request-ID"]).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("沒 token 時不送 Authorization", async () => {
    const req = apiClient.interceptors.request as any;
    const interceptor = req.handlers[0].fulfilled;
    const captured: Record<string, string> = {};
    const headers = { set: (k: string, v: string) => { captured[k] = v; } };
    await interceptor({ headers });
    expect(captured["Authorization"]).toBeUndefined();
    expect(captured["X-Request-ID"]).toBeDefined();
  });

  it("X-Request-ID 是 UUID v4 格式 (與後端 audit chain 標準化相容)", async () => {
    const req = apiClient.interceptors.request as any;
    const interceptor = req.handlers[0].fulfilled;
    const captured: Record<string, string> = {};
    const headers = { set: (k: string, v: string) => { captured[k] = v; } };
    await interceptor({ headers });
    // UUID v4：第 14 位 = '4'，第 19 位 ∈ {8,9,a,b}
    expect(captured["X-Request-ID"]).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
  });

  it("401 且 refresh 失敗 → 清空 token 並導向 login", async () => {
    localStorage.setItem("access_token", "expired");
    localStorage.setItem("refresh_token", "expired-r");
    // refresh 端點失敗 → tryRefreshToken 回傳 null → 視為登入逾時
    vi.spyOn(axios, "post").mockRejectedValue(new Error("refresh failed"));
    // mock window.location.assign（無註冊 session handler 時走硬導向後備）
    const assignMock = vi.fn();
    Object.defineProperty(window, "location", {
      value: { pathname: "/sections", search: "", assign: assignMock },
      writable: true,
      configurable: true,
    });
    const resp = apiClient.interceptors.response as any;
    const errHandler = resp.handlers[0].rejected;
    // 登入逾時 path 回傳「永不 resolve」的 promise（避免元件再彈錯），故不 await；
    // 等 microtask/timer 後驗證副作用即可。
    void errHandler({ response: { status: 401 }, config: { url: "/api/v1/sections" } });
    await new Promise((r) => setTimeout(r, 50));
    expect(localStorage.getItem("access_token")).toBeNull();
    expect(localStorage.getItem("refresh_token")).toBeNull();
    expect(assignMock).toHaveBeenCalledWith(
      expect.stringContaining("/login?next=%2Fsections"),
    );
  });
});
