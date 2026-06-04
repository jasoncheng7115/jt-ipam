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

export async function approveRequest(id: string): Promise<IPRequest> {
  const { data } = await apiClient.post<IPRequest>(`/api/v1/ip-requests/${id}/approve`);
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
