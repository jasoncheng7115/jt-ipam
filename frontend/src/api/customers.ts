/**
 * Customer / 管理單位 API client。
 */
import { apiClient } from "@/api/client";
import type { Paginated } from "@/types";  // alias to types/index.ts

export interface Customer {
  id: string;
  name: string;
  title: string | null;
  description: string | null;
  contact: string | null;
  email: string | null;
  phone: string | null;
  address: string | null;
  created_at: string;
  updated_at: string;
}

export type CustomerCreate = Partial<Omit<Customer, "id" | "created_at" | "updated_at">> & {
  name: string;
};

export type CustomerUpdate = Partial<Omit<Customer, "id" | "created_at" | "updated_at">>;

export async function listCustomers(
  params: { q?: string; page?: number; pageSize?: number } = {},
): Promise<Paginated<Customer>> {
  const { data } = await apiClient.get<Paginated<Customer>>("/api/v1/customers", {
    params: {
      q: params.q,
      page: params.page ?? 1,
      page_size: params.pageSize ?? 200,
    },
  });
  return data;
}

export async function createCustomer(payload: CustomerCreate): Promise<Customer> {
  const { data } = await apiClient.post<Customer>("/api/v1/customers", payload);
  return data;
}

export async function updateCustomer(id: string, payload: CustomerUpdate): Promise<Customer> {
  const { data } = await apiClient.patch<Customer>(`/api/v1/customers/${id}`, payload);
  return data;
}

export async function deleteCustomer(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/customers/${id}`);
}
