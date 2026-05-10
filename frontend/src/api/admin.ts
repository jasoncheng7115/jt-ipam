/**
 * Admin endpoints：audit / users / groups。
 */
import { apiClient } from "@/api/client";

export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

// ─────────────────── Audit ───────────────────

export interface AuditLog {
  id: number;
  ts: string;
  actor_user_id: string | null;
  actor_ip: string | null;
  actor_user_agent: string | null;
  object_type: string;
  object_id: string | null;
  action: string;
  diff: Record<string, unknown> | null;
  request_id: string | null;
  prev_hash_hex: string;
  this_hash_hex: string;
}

export interface AuditFilter {
  object_type?: string;
  object_id?: string;
  actor_user_id?: string;
  action?: string;
  since?: string;
  until?: string;
  limit?: number;
  offset?: number;
}

export async function listAudit(filter: AuditFilter = {}): Promise<Paginated<AuditLog>> {
  const { data } = await apiClient.get<Paginated<AuditLog>>("/api/v1/audit", {
    params: filter,
  });
  return data;
}

export interface ChainVerifyResult {
  ok: boolean;
  broken_at_id: number | null;
  checked: number;
}

export async function verifyAuditChain(): Promise<ChainVerifyResult> {
  const { data } = await apiClient.post<ChainVerifyResult>("/api/v1/audit/verify");
  return data;
}

// ─────────────────── Users ───────────────────

export interface User {
  id: string;
  username: string;
  email: string;
  display_name: string | null;
  auth_provider: string;
  is_active: boolean;
  is_admin: boolean;
  last_login_at: string | null;
  last_login_ip: string | null;
  failed_login_count: number;
  locked_until: string | null;
  created_at: string;
  updated_at: string;
}

export interface UserCreate {
  username: string;
  email: string;
  display_name?: string;
  password: string;
  is_admin?: boolean;
}

export interface UserUpdate {
  email?: string;
  display_name?: string;
  is_active?: boolean;
  is_admin?: boolean;
  password?: string;
  unlock?: boolean;
}

export async function listUsers(
  q = "", auth_provider = "", limit = 50, offset = 0,
): Promise<Paginated<User>> {
  const { data } = await apiClient.get<Paginated<User>>("/api/v1/users", {
    params: {
      ...(q ? { q } : {}),
      ...(auth_provider ? { auth_provider } : {}),
      limit, offset,
    },
  });
  return data;
}

export async function createUser(payload: UserCreate): Promise<User> {
  const { data } = await apiClient.post<User>("/api/v1/users", payload);
  return data;
}

export async function updateUser(id: string, payload: UserUpdate): Promise<User> {
  const { data } = await apiClient.patch<User>(`/api/v1/users/${id}`, payload);
  return data;
}

export async function deleteUser(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/users/${id}`);
}

// ─────────────────── Groups ───────────────────

export interface Group {
  id: string;
  name: string;
  description: string | null;
  is_builtin: boolean;
  member_count: number;
  created_at: string;
  updated_at: string;
}

export async function listGroups(limit = 50, offset = 0): Promise<Paginated<Group>> {
  const { data } = await apiClient.get<Paginated<Group>>("/api/v1/groups", {
    params: { limit, offset },
  });
  return data;
}

export async function createGroup(name: string, description?: string): Promise<Group> {
  const { data } = await apiClient.post<Group>("/api/v1/groups", { name, description });
  return data;
}

export async function updateGroup(id: string, description: string): Promise<Group> {
  const { data } = await apiClient.patch<Group>(`/api/v1/groups/${id}`, { description });
  return data;
}

export async function deleteGroup(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/groups/${id}`);
}

export async function listGroupMembers(groupId: string): Promise<User[]> {
  const { data } = await apiClient.get<User[]>(`/api/v1/groups/${groupId}/members`);
  return data;
}

export async function addGroupMember(groupId: string, userId: string): Promise<void> {
  await apiClient.post(`/api/v1/groups/${groupId}/members/${userId}`);
}

export async function removeGroupMember(groupId: string, userId: string): Promise<void> {
  await apiClient.delete(`/api/v1/groups/${groupId}/members/${userId}`);
}
