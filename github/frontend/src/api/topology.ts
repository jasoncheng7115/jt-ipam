import { apiClient } from "@/api/client";

export interface CytoscapeNode {
  data: {
    id: string;
    label: string;
    type: string;
    vendor?: string | null;
    model?: string | null;
    rack_id?: string | null;
    location_id?: string | null;
  };
}

export interface CytoscapeEdge {
  data: {
    id: string;
    source: string;
    target: string;
    label?: string;
    kind: "cable" | "wireless" | "vpn" | "l3";
    type?: string;
    color?: string | null;
    status?: string;
    distance_m?: number | null;
    ssid?: string | null;
  };
}

export interface TopologyData {
  nodes: CytoscapeNode[];
  edges: CytoscapeEdge[];
}

export async function getTopology(params: {
  locationId?: string;
  subnetIds?: string[];
  includeWireless?: boolean;
  includeVpn?: boolean;
  includeL3?: boolean;
  onlineOnly?: boolean;
} = {}): Promise<TopologyData> {
  const { data } = await apiClient.get<TopologyData>("/api/v1/topology", {
    params: {
      location_id: params.locationId,
      subnet_id: params.subnetIds && params.subnetIds.length ? params.subnetIds : undefined,
      include_wireless: params.includeWireless ?? true,
      include_vpn: params.includeVpn ?? true,
      include_l3: params.includeL3 ?? true,
      online_only: params.onlineOnly ?? false,
    },
    paramsSerializer: { indexes: null },  // subnet_id 重複 key
  });
  return data;
}
