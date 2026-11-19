/**
 * 戰情室的圖示（使用者 10-22：「現在戰情室全是文字，有些地方可否加些 icon 美化」）。
 * 只用在區塊標題、異常類別、卡片抬頭這類「一眼辨認」的位置，不加在數字旁邊（數字本身就是重點）。
 * 圖示一律來自 @/icons（Iconoir），顏色跟著文字（stroke=currentColor）。
 */
import type { Component } from "vue";
import {
  AnomalyIcon, ChangeImpactIcon, CloneIcon, DhcpServerIcon, DnsIcon, EyeIcon, FirewallIcon,
  IdentifyIcon, IntegrationsIcon, IPChangesIcon, LockIcon, MacIcon, NatIcon, OfflineIcon, PendingIcon,
  ScanAgentsIcon, SubnetsIcon, UnauthorizedIcon, VirtualizationIcon, WarnIcon,
} from "@/icons";

export const SECTION_ICONS = {
  priority: WarnIcon, openAnomalies: AnomalyIcon, timeline: PendingIcon, evidence: FirewallIcon,
  dns: DnsIcon, dhcp: DhcpServerIcon, cert: LockIcon, certExpiry: PendingIcon,
  coverage: EyeIcon, change: ChangeImpactIcon, sources: IntegrationsIcon,
  subnet: SubnetsIcon, pool: DhcpServerIcon, cluster: VirtualizationIcon, agent: ScanAgentsIcon,
} satisfies Record<string, Component>;

const CATEGORY_ICONS: Record<string, Component> = {
  ip_conflicts: CloneIcon, duplicate_ip_records: CloneIcon, arp_flux: NatIcon, l2_subnet_bleed: SubnetsIcon,
  mac_drifts: MacIcon, mac_flapping: NatIcon, ghost_ips: OfflineIcon, unauthorized_ips: UnauthorizedIcon,
  rogue_dhcp: DhcpServerIcon, external_exposure: FirewallIcon, dangling_dns: DnsIcon,
  suspicious_changes: IPChangesIcon, fw_rule_rot: FirewallIcon, identity_changes: IdentifyIcon, k8s_conflicts: SubnetsIcon,
};

export function categoryIcon(category: string): Component {
  return CATEGORY_ICONS[category] ?? AnomalyIcon;
}
