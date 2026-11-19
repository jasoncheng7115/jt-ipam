/** 異常類別 → 異常偵測頁現有的翻譯（同一個名字，不另寫一份）；01 總覽與 05 異常共用 */
const CAT_KEYS: Record<string, string> = {
  ip_conflicts: "anomaly.ip_conflicts", arp_flux: "anomaly.arp_flux", l2_subnet_bleed: "anomaly.l2_bleed",
  mac_drifts: "anomaly.mac_drifts", ghost_ips: "anomaly.ghost_ips", unauthorized_ips: "anomaly.unauthorized",
  rogue_dhcp: "anomaly.rogue_dhcp", external_exposure: "anomaly.exposure", dangling_dns: "anomaly.dangling_dns",
  duplicate_ip_records: "anomaly.dup_ip", suspicious_changes: "anomaly.changes", fw_rule_rot: "anomaly.fw_rot",
  mac_flapping: "anomaly.mac_flapping", identity_changes: "anomaly.identity_changes", k8s_conflicts: "anomaly.k8s_conflicts",
};

/** 所有異常類別（順序同後端 anomaly.ALL_CATEGORIES）：戰情室 AI 情報的重大事件類別從這裡挑 */
export const ANOMALY_CATEGORIES = Object.keys(CAT_KEYS);

export function anomalyCategoryLabel(category: string, t: (k: string) => string, te: (k: string) => boolean): string {
  const k = CAT_KEYS[category];
  return k && te(k) ? t(k) : category;
}
