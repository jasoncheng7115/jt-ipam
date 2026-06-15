import { apiClient } from "@/api/client";
import type { Paginated } from "@/types";

export interface IPRequest {
  id: string;
  status: "pending" | "approved" | "rejected" | "cancelled" | "fulfilled";
  requester_user_id: string;
  approver_user_id: string | null;
  subnet_id: string;
  requested_ip: string | null;
  hostname: string | null;
  description: string | null;
  purpose: string;
  expires_at: string | null;
  allocated_ip_id: string | null;
  approved_at: string | null;
  rejected_at: string | null;
  rejected_reason: string | null;
  fulfilled_at: string | null;
  cancelled_at: string | null;
  created_at: string;
  updated_at: string;
  can_approve?: boolean;   // 此請求對目前使用者是否可核准（後端依審核政策計算）
}

export interface IPRequestStep {
  name: string;
  user_ids: string[];
  group_ids: string[];
}

export interface IPRequestPolicy {
  approver_mode: "admin" | "designated" | "parallel" | "stages";
  designated_user_ids: string[];
  designated_group_ids: string[];
  allow_self_approve: boolean;
  stages: IPRequestStep[];
}

export async function getRequestPolicy(): Promise<IPRequestPolicy> {
  const { data } = await apiClient.get<IPRequestPolicy>("/api/v1/ip-requests/policy/config");
  return data;
}

export async function setRequestPolicy(p: IPRequestPolicy): Promise<IPRequestPolicy> {
  const { data } = await apiClient.put<IPRequestPolicy>("/api/v1/ip-requests/policy/config", p);
  return data;
}

export interface IPRequestEvent {
  id: string;
  actor_user_id: string | null;
  event_type: string;
  message: string | null;
  created_at: string;
}

export interface IPRequestDetail {
  request: IPRequest;
  events: IPRequestEvent[];
  subnet_cidr?: string | null;
  target_ip?: string | null;       // pending：實際會配發的 IP（申請指定或系統自動）
  target_auto?: boolean;           // target_ip 是否系統自動挑的
  allocated_ip?: string | null;    // 已配發的 IP
  stages?: { index: number; name: string; approved: boolean; is_current: boolean }[];
}

export async function listRequests(
  params: { mine?: boolean; status?: string; page?: number; pageSize?: number } = {},
): Promise<Paginated<IPRequest>> {
  const { data } = await apiClient.get<Paginated<IPRequest>>("/api/v1/ip-requests", {
    params: {
      mine: params.mine ?? false,
      status: params.status,
      page: params.page ?? 1,
      page_size: params.pageSize ?? 50,
    },
  });
  return data;
}

export async function getRequest(id: string): Promise<IPRequestDetail> {
  const { data } = await apiClient.get<IPRequestDetail>(`/api/v1/ip-requests/${id}`);
  return data;
}

export async function createRequest(payload: {
  subnet_id: string;
  purpose: string;
  hostname?: string;
  description?: string;
  requested_ip?: string;
  expires_at?: string;
}): Promise<IPRequest> {
  const { data } = await apiClient.post<IPRequest>("/api/v1/ip-requests", payload);
  return data;
}

export async function approveRequest(id: string, ip?: string): Promise<IPRequest> {
  const { data } = await apiClient.post<IPRequest>(
    `/api/v1/ip-requests/${id}/approve`, ip ? { ip } : {},
  );
  return data;
}

export async function rejectRequest(id: string, reason: string): Promise<IPRequest> {
  const { data } = await apiClient.post<IPRequest>(`/api/v1/ip-requests/${id}/reject`, {
    reason,
  });
  return data;
}

export async function cancelRequest(id: string): Promise<IPRequest> {
  const { data } = await apiClient.post<IPRequest>(`/api/v1/ip-requests/${id}/cancel`);
  return data;
}
