/**
 * 關係圖節點的圖示（使用者 2026-10-08：那些節點代表什麼？是 DNS、虛擬機、防火牆？節點上要用圖示呈現）。
 *
 * cytoscape 的節點只能用 background-image 放圖，所以把 iconoir 的 Vue 元件掛到一個不在畫面上的 div，
 * 取出 SVG 字串轉成 data URI（白色筆畫，畫在影響顏色的底上）。同一個圖示只算一次。
 */
import { createApp, h, type Component } from "vue";
import {
  Activity, Box, Cloud, Computer, Cube, DataTransferBoth, Drawer, Fingerprint, Flash, Globe, IpAddressTag, Key, Laptop,
  Link, Lock, Network, Page, RefreshDouble, Server, Settings, Shield, ShieldCheck, Terminal, Text,
} from "@iconoir/vue";

/** 發現主體的類型 → 圖示（後端 Finding.subject_type；關係圖補上的節點是 device／virtual_machine／service…） */
export const TYPE_ICON: Record<string, Component> = {
  dns_record: Globe, adguard_entry: Globe,
  fw_rule: Shield, fw_object: ShieldCheck, nat_translation: RefreshDouble,
  virtual_machine: Box, device: Server, service: Cloud,
  librenms_device: Activity, zabbix_host: Activity, checkmk_host: Activity, wazuh_agent: Fingerprint, ocs_computer: Computer,
  rustdesk_peer: Laptop,
  ip_address: IpAddressTag, address: IpAddressTag, subnet: Network, ip_range: Network,
  dhcp_reservation: DataTransferBoth, dhcp_pool: DataTransferBoth, dhcp_lease: DataTransferBoth,
  dhcp_sighting: DataTransferBoth, dhcp_scope: DataTransferBoth,
  certificate: Lock, cert_agent: Lock, vpn_tunnel: Key, circuit: Link, cable: Link, rack: Drawer,
  device_power_port: Flash, jump_host: Terminal, integration: Settings, system_setting: Settings,
  ip_request: Page, hostname: Text,
};

export function iconFor(type: string): Component {
  return TYPE_ICON[type] ?? Cube;
}

const svgCache = new Map<Component, string>();
const uriCache = new Map<string, string>();

function svgOf(comp: Component): string {
  const hit = svgCache.get(comp);
  if (hit) return hit;
  const el = document.createElement("div");
  const app = createApp({ render: () => h(comp, { width: 24, height: 24, "stroke-width": 1.8 }) });
  app.mount(el);
  let svg = el.innerHTML;
  app.unmount();
  if (!svg.includes("xmlns=")) svg = svg.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"');
  svgCache.set(comp, svg);
  return svg;
}

/** 指定筆畫顏色的 SVG data URI（cytoscape background-image 用；預設白色，畫在影響顏色的底上） */
export function iconDataUri(type: string, color = "#ffffff"): string {
  const comp = iconFor(type);
  const key = `${type}|${color}`;
  const hit = uriCache.get(key);
  if (hit) return hit;
  const uri = `data:image/svg+xml;utf8,${encodeURIComponent(svgOf(comp).replace(/currentColor/g, color))}`;
  uriCache.set(key, uri);
  return uri;
}
