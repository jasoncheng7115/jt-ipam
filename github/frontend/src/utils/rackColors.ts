/** 機櫃 device type → 顏色（RackDiagram U 位塊 + 共用圖例共用同一套）。 */
export const RACK_DEVICE_TYPES = [
  "router", "switch", "firewall", "server", "storage", "ap", "ipmi",
] as const;

export function rackTypeColor(type: string): string {
  switch (type) {
    case "router":
      return "rgba(99, 102, 241, 0.85)"; // indigo
    case "switch":
      return "rgba(34, 197, 94, 0.85)";  // green
    case "firewall":
      return "rgba(239, 68, 68, 0.85)";  // red
    case "ap":
      return "rgba(59, 130, 246, 0.85)"; // blue
    case "server":
      return "rgba(107, 114, 128, 0.85)"; // grey
    case "storage":
      return "rgba(245, 158, 11, 0.85)"; // amber
    case "ipmi":
      return "rgba(236, 72, 153, 0.6)";  // pink
    default:
      return "rgba(107, 114, 128, 0.6)";
  }
}
