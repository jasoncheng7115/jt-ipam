import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { apiClient } from "@/api/client";
import {
  listFirewalls, createFirewall, syncFirewall,
  listWazuh, listMissingAgents,
  listPlugins,
} from "@/api/integrations";

describe("integrations API", () => {
  let getSpy: any, postSpy: any;

  beforeEach(() => {
    getSpy = vi.spyOn(apiClient, "get");
    postSpy = vi.spyOn(apiClient, "post");
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe("OPNsense firewall", () => {
    it("listFirewalls 走 /firewalls/opnsense", async () => {
      getSpy.mockResolvedValueOnce({ data: { items: [], total: 0, page: 1, page_size: 50 } });
      await listFirewalls(50, 0);
      expect(getSpy).toHaveBeenCalledWith("/api/v1/firewalls/opnsense", {
        params: { limit: 50, offset: 0 },
      });
    });

    it("createFirewall 同時送 api_key + api_secret", async () => {
      postSpy.mockResolvedValueOnce({ data: { id: "fw1" } });
      await createFirewall({
        name: "fw-edge", api_url: "https://opn",
        api_key: "k", api_secret: "s",
      });
      expect(postSpy).toHaveBeenCalledWith(
        "/api/v1/firewalls/opnsense",
        expect.objectContaining({ api_key: "k", api_secret: "s" }),
      );
    });

    it("syncFirewall POST 到正確路徑", async () => {
      postSpy.mockResolvedValueOnce({ data: { ok: true } });
      await syncFirewall("fw1");
      expect(postSpy).toHaveBeenCalledWith("/api/v1/firewalls/opnsense/fw1/sync");
    });
  });

  describe("Wazuh", () => {
    it("listWazuh 走 /wazuh/instances", async () => {
      getSpy.mockResolvedValueOnce({ data: { items: [], total: 0, page: 1, page_size: 50 } });
      await listWazuh();
      expect(getSpy).toHaveBeenCalledWith("/api/v1/wazuh/instances", {
        params: { limit: 50, offset: 0 },
      });
    });

    it("listMissingAgents 走 /wazuh/missing-agents", async () => {
      getSpy.mockResolvedValueOnce({ data: [] });
      await listMissingAgents();
      expect(getSpy).toHaveBeenCalledWith("/api/v1/wazuh/missing-agents");
    });
  });

  describe("plugins", () => {
    it("listPlugins 回 { count, plugins }", async () => {
      getSpy.mockResolvedValueOnce({ data: { count: 0, plugins: [] } });
      const r = await listPlugins();
      expect(r.count).toBe(0);
      expect(r.plugins).toEqual([]);
    });
  });
});
