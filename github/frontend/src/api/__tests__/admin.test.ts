import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { apiClient } from "@/api/client";
import {
  listAudit, verifyAuditChain, listUsers, createUser, updateUser, deleteUser,
  listGroups, createGroup,
} from "@/api/admin";

describe("admin API", () => {
  let getSpy: any, postSpy: any, patchSpy: any, deleteSpy: any;

  beforeEach(() => {
    getSpy = vi.spyOn(apiClient, "get");
    postSpy = vi.spyOn(apiClient, "post");
    patchSpy = vi.spyOn(apiClient, "patch");
    deleteSpy = vi.spyOn(apiClient, "delete");
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe("audit", () => {
    it("listAudit 帶 filter params", async () => {
      getSpy.mockResolvedValueOnce({ data: { items: [], total: 0, page: 1, page_size: 100 } });
      await listAudit({ object_type: "user", action: "create", limit: 50, offset: 0 });
      expect(getSpy).toHaveBeenCalledWith("/api/v1/audit", {
        params: { object_type: "user", action: "create", limit: 50, offset: 0 },
      });
    });

    it("verifyAuditChain 走 POST 並回傳 { ok, broken_at_id, checked }", async () => {
      postSpy.mockResolvedValueOnce({ data: { ok: true, broken_at_id: null, checked: 42 } });
      const r = await verifyAuditChain();
      expect(postSpy).toHaveBeenCalledWith("/api/v1/audit/verify");
      expect(r).toEqual({ ok: true, broken_at_id: null, checked: 42 });
    });
  });

  describe("users", () => {
    it("listUsers 帶 search + provider filter", async () => {
      getSpy.mockResolvedValueOnce({ data: { items: [], total: 0, page: 1, page_size: 50 } });
      await listUsers("alice", "local", 50, 0);
      expect(getSpy).toHaveBeenCalledWith("/api/v1/users", {
        params: { q: "alice", auth_provider: "local", limit: 50, offset: 0 },
      });
    });

    it("listUsers 不傳 q / provider 時 params 不該有那些 key", async () => {
      getSpy.mockResolvedValueOnce({ data: { items: [], total: 0, page: 1, page_size: 50 } });
      await listUsers("", "", 25, 50);
      expect(getSpy).toHaveBeenCalledWith("/api/v1/users", {
        params: { limit: 25, offset: 50 },
      });
    });

    it("createUser POST 到 /users", async () => {
      postSpy.mockResolvedValueOnce({ data: { id: "u1", username: "x" } });
      await createUser({ username: "x", email: "x@y", password: "verylongpassword", is_admin: false });
      expect(postSpy).toHaveBeenCalledWith(
        "/api/v1/users",
        expect.objectContaining({ username: "x", email: "x@y" }),
      );
    });

    it("updateUser PATCH 到 /users/:id", async () => {
      patchSpy.mockResolvedValueOnce({ data: { id: "u1" } });
      await updateUser("u1", { is_active: false, unlock: true });
      expect(patchSpy).toHaveBeenCalledWith("/api/v1/users/u1", { is_active: false, unlock: true });
    });

    it("deleteUser DELETE 到 /users/:id", async () => {
      deleteSpy.mockResolvedValueOnce({ data: undefined });
      await deleteUser("u1");
      expect(deleteSpy).toHaveBeenCalledWith("/api/v1/users/u1");
    });
  });

  describe("groups", () => {
    it("listGroups 帶 limit + offset", async () => {
      getSpy.mockResolvedValueOnce({ data: { items: [], total: 0, page: 1, page_size: 50 } });
      await listGroups(100, 50);
      expect(getSpy).toHaveBeenCalledWith("/api/v1/groups", {
        params: { limit: 100, offset: 50 },
      });
    });

    it("createGroup 送 name + description", async () => {
      postSpy.mockResolvedValueOnce({ data: { id: "g1", name: "netadmins" } });
      await createGroup("netadmins", "Network admins");
      expect(postSpy).toHaveBeenCalledWith("/api/v1/groups", {
        name: "netadmins",
        description: "Network admins",
      });
    });
  });
});
