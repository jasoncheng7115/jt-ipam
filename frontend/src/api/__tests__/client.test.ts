import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import axios from "axios";
import { apiClient } from "@/api/client";

describe("apiClient 陣列參數", () => {
  // FastAPI 的 list 參數只認重複同一個 key（a=1&a=2）；axios 預設送 a[]=1&a[]=2，後端會當成沒帶，
  // 「只問限定網段」就靜靜變成全站（11-25 LibreNMS / Zabbix / Checkmk 的未監控位址）
  it("陣列用重複 key 送出，不加 []", () => {
    const uri = apiClient.getUri({ url: "/api/v1/x", params: { subnet_ids: ["a", "b"], q: "z" } });
    expect(uri).toContain("subnet_ids=a&subnet_ids=b");
    expect(uri).not.toContain("%5B%5D");
  });
});

describe("apiClient interceptors", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // 2026-10-09 起存取權杖在這個分頁的 sessionStorage；更新權杖在 HttpOnly Cookie（JS 讀不到）
  it("帶入 Authorization header(若這個分頁有存取權杖)", async () => {
    sessionStorage.setItem("access_token", "fake-token-abc");
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
    sessionStorage.setItem("access_token", "expired");
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
    expect(sessionStorage.getItem("access_token")).toBeNull();
    expect(assignMock).toHaveBeenCalledWith(
      expect.stringContaining("/login?next=%2Fsections"),
    );
  });
});

describe("權杖不放 localStorage", () => {
  it("換發用 Cookie、帶 CSRF 標頭、本文不送更新權杖", async () => {
    const post = vi.spyOn(axios, "post").mockResolvedValue({ data: { access_token: "new-tok" } });
    const { tryRefreshToken } = await import("@/api/client");
    expect(await tryRefreshToken()).toBe("new-tok");
    const [url, body, cfg] = post.mock.calls[0] as [string, unknown, any];
    expect(url).toContain("/api/v1/auth/refresh");
    expect(body).toBeNull();
    expect(cfg.withCredentials).toBe(true);
    expect(cfg.headers["X-Requested-With"]).toBe("jt-ipam");
    expect(sessionStorage.getItem("access_token")).toBe("new-tok");
    expect(localStorage.getItem("access_token")).toBeNull();
    expect(localStorage.getItem("refresh_token")).toBeNull();
    post.mockRestore();
  });
});
