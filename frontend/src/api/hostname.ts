import { apiClient } from "@/api/client";

// feature A：hostname 多來源優先序
export interface HostnameObservation {
  source: string;
  hostname: string;
  observed_at: string;
}

export interface HostnameSources {
  effective: string | null;
  pin: string | null;
  order: string[];
  observations: HostnameObservation[];
}

export interface HostnamePrecedence {
  order: string[];
  disabled: string[];
  sources: string[];
}

// 單一 IP 的各來源 hostname + 目前 pin
export async function getHostnameSources(addressId: string): Promise<HostnameSources> {
  const { data } = await apiClient.get<HostnameSources>(
    `/api/v1/addresses/${addressId}/hostname-sources`,
  );
  return data;
}

// 清掉某 IP 某來源的 hostname 觀測（例如過時的「手動: tp-link-c7」）
export async function clearHostnameSource(addressId: string, source: string): Promise<void> {
  await apiClient.delete(`/api/v1/addresses/${addressId}/hostname-sources/${encodeURIComponent(source)}`);
}

// 全域優先序 (admin)
export async function getHostnamePrecedence(): Promise<HostnamePrecedence> {
  const { data } = await apiClient.get<HostnamePrecedence>(
    "/api/v1/system/hostname-precedence",
  );
  return data;
}

export async function setHostnamePrecedence(
  order: string[], disabled: string[] = [],
): Promise<HostnamePrecedence> {
  const { data } = await apiClient.put<HostnamePrecedence>(
    "/api/v1/system/hostname-precedence",
    { order, disabled },
  );
  return data;
}

// ARP / MAC 來源優先序
export interface ArpPrecedence { order: string[]; disabled: string[]; sources: string[]; }

export async function getArpPrecedence(): Promise<ArpPrecedence> {
  const { data } = await apiClient.get<ArpPrecedence>("/api/v1/system/arp-precedence");
  return data;
}

export async function setArpPrecedence(order: string[], disabled: string[] = []): Promise<ArpPrecedence> {
  const { data } = await apiClient.put<ArpPrecedence>(
    "/api/v1/system/arp-precedence", { order, disabled },
  );
  return data;
}

// 裝置名稱來源優先序（與 ARP 同形狀）
export interface DevNamePrecedence { order: string[]; disabled: string[]; sources: string[]; }

export async function getDevNamePrecedence(): Promise<DevNamePrecedence> {
  const { data } = await apiClient.get<DevNamePrecedence>("/api/v1/system/device-name-precedence");
  return data;
}

export async function setDevNamePrecedence(order: string[], disabled: string[] = []): Promise<DevNamePrecedence> {
  const { data } = await apiClient.put<DevNamePrecedence>(
    "/api/v1/system/device-name-precedence", { order, disabled },
  );
  return data;
}

export async function getModelPrecedence(): Promise<DevNamePrecedence> {
  const { data } = await apiClient.get<DevNamePrecedence>("/api/v1/system/device-model-precedence");
  return data;
}
export async function setModelPrecedence(order: string[], disabled: string[] = []): Promise<DevNamePrecedence> {
  const { data } = await apiClient.put<DevNamePrecedence>(
    "/api/v1/system/device-model-precedence", { order, disabled },
  );
  return data;
}
