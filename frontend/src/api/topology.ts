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
    kind: "cable" | "wireless" | "vpn";
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
  includeWireless?: boolean;
  includeVpn?: boolean;
} = {}): Promise<TopologyData> {
  const { data } = await apiClient.get<TopologyData>("/api/v1/topology", {
    params: {
      location_id: params.locationId,
      include_wireless: params.includeWireless ?? true,
      include_vpn: params.includeVpn ?? true,
    },
  });
  return data;
}
