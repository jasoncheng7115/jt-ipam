/** 網域管理（客戶許願清單 2026-10-28）：註冊商、到期日（RDAP）、NS 與 DNS 託管、綁定的服務。 */
import { apiClient } from "@/api/client";

export interface Domain {
  id: string; name: string;
  registrar: string | null; registrar_effective: string | null; registrar_url: string | null;
  registrar_account: string | null;
  dns_provider: string | null; dns_provider_effective: string | null; dns_provider_inferred: boolean;
  services: string | null; description: string | null;
  expires_at: string | null; expires: string | null; expires_source: "rdap" | "manual" | null;
  days_left: number | null; auto_renew: boolean | null;
  nameservers: string[]; status: string[]; registered_at: string | null;
  rdap_checked_at: string | null; rdap_error: string | null;
}
export interface DomainDetail extends Domain {
  zones: { id: string; name: string; server: string; type: string }[];
  certificates: { id: string; name: string; domains: string[] }[];
}
export interface DomainFields {
  registrar?: string | null; registrar_url?: string | null; registrar_account?: string | null;
  dns_provider?: string | null; services?: string | null; description?: string | null;
  expires_at?: string | null; auto_renew?: boolean | null;
}

/** 探測結果（不存檔）：新增網域時填表用 */
export interface DomainProbe {
  name: string; rdap_error: string | null;
  registrar: string | null; registrar_url: string | null; registered_at: string | null; expires_at: string | null;
  status: string[]; nameservers: string[]; dns_provider: string | null;
  mx: string[]; mail_provider: string | null; spf: boolean; dmarc: boolean; website: boolean;
}

export const Domains = {
  probe: (name: string) =>
    apiClient.get<DomainProbe>("/api/v1/domains/probe", { params: { name } }).then((r) => r.data),
  async list(): Promise<Domain[]> {
    const { data } = await apiClient.get<{ items: Domain[] }>("/api/v1/domains", { params: { page_size: 500 } });
    return data.items;
  },
  get: (id: string) => apiClient.get<DomainDetail>(`/api/v1/domains/${id}`).then((r) => r.data),
  create: (body: DomainFields & { name: string; lookup?: boolean }) =>
    apiClient.post<Domain>("/api/v1/domains", body).then((r) => r.data),
  bulk: (names: string[]) =>
    apiClient.post<{ created: { id: string; name: string }[]; skipped: { value: string; reason: string }[] }>(
      "/api/v1/domains/bulk", { names }).then((r) => r.data),
  update: (id: string, body: DomainFields) => apiClient.patch<Domain>(`/api/v1/domains/${id}`, body).then((r) => r.data),
  remove: (id: string) => apiClient.delete(`/api/v1/domains/${id}`),
  refresh: (id: string) => apiClient.post<Domain>(`/api/v1/domains/${id}/rdap`).then((r) => r.data),
};
