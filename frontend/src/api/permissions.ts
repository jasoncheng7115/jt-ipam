import { apiClient } from "@/api/client";

export type PermObjectType = "customer" | "section" | "subnet" | "ip" | "device" | "rack" | "location";
export type PermLevel = "read" | "write" | "admin";

export interface PermissionGrant {
  id: string;
  object_type: PermObjectType;
  object_id: string | null;   // null = 全部
  principal_type: "user" | "group";
  principal_id: string;
  level: PermLevel;
}

export interface Role {
  id: string;
  name: string;
  is_builtin: boolean;
  member_count: number;
}

export async function listPermissions(principalType: string, principalId: string): Promise<PermissionGrant[]> {
  const { data } = await apiClient.get<PermissionGrant[]>("/api/v1/system/permissions", {
    params: { principal_type: principalType, principal_id: principalId },
  });
  return data;
}

/** 不帶 principal → 後端回傳所有已指派的權限（總覽用）。 */
export async function listAllPermissions(): Promise<PermissionGrant[]> {
  const { data } = await apiClient.get<PermissionGrant[]>("/api/v1/system/permissions");
  return data;
}

export async function upsertPermission(g: {
  object_type: PermObjectType; object_id: string | null;
  principal_type: "user" | "group"; principal_id: string; level: PermLevel;
}): Promise<PermissionGrant> {
  const { data } = await apiClient.post<PermissionGrant>("/api/v1/system/permissions", g);
  return data;
}

export async function deletePermission(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/system/permissions/${id}`);
}

export interface RolesResponse {
  roles: Role[];
  object_types: PermObjectType[];
  levels: PermLevel[];
}
export async function listRoles(): Promise<RolesResponse> {
  const { data } = await apiClient.get<RolesResponse>("/api/v1/system/roles");
  return data;
}
