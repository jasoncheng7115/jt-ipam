import { apiClient } from "@/api/client";
import type { Paginated } from "@/types";

// Kubernetes 整合（唯讀）。路徑帶 /api/v1 前綴（baseURL 為 /）。

export interface K8sCluster {
  id: string;
  name: string;
  api_url: string;
  has_ca_cert: boolean;
  verify_tls: boolean;
  auth_method: "token" | "client_cert";
  has_token: boolean;
  has_client_key: boolean;
  enabled: boolean;
  scope_subnet_ids: string[] | null;
  sync_interval_seconds: number;
  create_ip_ranges: boolean;
  pod_network_routable: boolean;
  missing_permissions: string[] | null;
  server_version: string | null;
  description: string | null;
  last_sync_at: string | null;
  last_error: string | null;
}

export interface K8sWrite {
  name?: string;
  kubeconfig?: string;
  kubeconfig_context?: string;
  api_url?: string;
  ca_cert?: string;
  verify_tls?: boolean;
  auth_method?: "token" | "client_cert";
  token?: string;
  enabled?: boolean;
  scope_subnet_ids?: string[];
  sync_interval_seconds?: number;
  create_ip_ranges?: boolean;
  pod_network_routable?: boolean;
  description?: string;
}

export interface K8sCheck { key: string; ok: boolean | null; count?: number | null; code?: string; error?: string }
export interface K8sHealth { version: string | null; checks: K8sCheck[] }

export interface K8sNode {
  id: string; name: string; ip: string | null; addresses: { type: string; address: string }[] | null;
  roles: string[] | null; ready: boolean | null; unschedulable: boolean; kubelet_version: string | null;
  os_image: string | null; kernel: string | null; runtime: string | null; provider_id: string | null;
  pod_cidrs: string[] | null; ip_address_id: string | null; virtual_machine_id: string | null; vm_name: string | null;
  last_heartbeat: string | null; synced_at: string | null;
}
export interface K8sServiceIP {
  id: string; namespace: string; name: string; kind: "loadbalancer" | "external" | "pod" | "egress" | "apiserver"; ip: string;
  /** Egress IP 目前在哪個節點（OVN-Kubernetes、Antrea） */
  node: string | null;
  ports: { name?: string; port?: number; protocol?: string }[] | null; ip_address_id: string | null; synced_at: string | null;
}
export interface K8sPool {
  id: string; provider: "metallb" | "cilium" | "calico" | "whereabouts" | "antrea" | "multus";
  use: "loadbalancer" | "pod" | "external";
  name: string; namespace: string | null;
  blocks: { start: string; end: string }[]; auto_assign: boolean; disabled: boolean; synced_at: string | null;
}
export interface K8sCidr {
  id: string; kind: "pod" | "service"; cidr: string; source: string | null;
  /** Calico 看得出來的才有值；effective_routable 另外算進叢集的「Pod 網段可直接路由」 */
  routable: boolean | null; encapsulation: string | null; effective_routable: boolean;
  subnets: { id: string; cidr: string }[]; synced_at: string | null;
}
export type K8sDnsState = "match" | "mismatch" | "none" | "wildcard" | "no_ip";
export interface K8sHostname {
  id: string; kind: "ingress" | "gateway" | "httproute" | "grpcroute" | "route"; namespace: string; name: string;
  host: string; ip: string | null; tls: boolean; ip_address_id: string | null; dns_ips: string[];
  dns_state: K8sDnsState; synced_at: string | null;
}
export interface K8sConflict {
  kind: "unregistered" | "in_dhcp" | "pool_dhcp" | "pool_used" | "cidr_overlap" | "dns_mismatch" | "node_unregistered"
    | "cross_ip" | "cross_pool" | "cross_cidr";
  cluster: string | null; cluster_id: string; ip: string; what: string; detail: string;
  ip_address_id?: string | null; count?: number;
}
export interface K8sByIp {
  nodes: { cluster: string | null; cluster_id: string; name: string; roles: string[] | null; ready: boolean | null;
           kubelet_version: string | null; synced_at: string | null }[];
  services: { cluster: string | null; cluster_id: string; namespace: string; name: string; kind: string;
              ports: K8sServiceIP["ports"]; node: string | null; synced_at: string | null }[];
  hostnames: { cluster: string | null; cluster_id: string; kind: K8sHostname["kind"]; namespace: string; name: string;
               host: string; tls: boolean; synced_at: string | null }[];
  pod_networks: { cluster: string | null; cluster_id: string; cidr: string; source: string | null }[];
}

const B = "/api/v1/k8s";
export const K8s = {
  async list(): Promise<K8sCluster[]> {
    return ((await apiClient.get<Paginated<K8sCluster>>(B, { params: { page_size: 500 } })).data).items;
  },
  async create(p: K8sWrite): Promise<K8sCluster> { return (await apiClient.post(B, p)).data; },
  async update(id: string, p: K8sWrite): Promise<K8sCluster> { return (await apiClient.patch(`${B}/${id}`, p)).data; },
  async remove(id: string): Promise<void> { await apiClient.delete(`${B}/${id}`); },
  async test(id: string): Promise<K8sHealth> { return (await apiClient.post(`${B}/${id}/test`)).data; },
  async sync(id: string): Promise<{ task_id: string }> { return (await apiClient.post(`${B}/${id}/sync`)).data; },
  async nodes(id: string): Promise<K8sNode[]> { return (await apiClient.get(`${B}/${id}/nodes`)).data.items; },
  async serviceIps(id: string): Promise<K8sServiceIP[]> { return (await apiClient.get(`${B}/${id}/service-ips`)).data.items; },
  async pools(id: string): Promise<K8sPool[]> { return (await apiClient.get(`${B}/${id}/pools`)).data.items; },
  async cidrs(id: string): Promise<K8sCidr[]> { return (await apiClient.get(`${B}/${id}/cidrs`)).data.items; },
  async hostnames(id: string): Promise<K8sHostname[]> { return (await apiClient.get(`${B}/${id}/hostnames`)).data.items; },
  async conflicts(id: string): Promise<K8sConflict[]> { return (await apiClient.get(`${B}/${id}/conflicts`)).data.items; },
  async byIp(ipId: string): Promise<K8sByIp> { return (await apiClient.get(`${B}/by-ip/${ipId}`)).data; },
  async manifest(): Promise<string> {
    return (await apiClient.get(`${B}/readonly-manifest`, { responseType: "text" })).data as string;
  },
};
