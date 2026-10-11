import { describe, expect, it } from "vitest";
import zh from "../zh-TW.json";
import en from "../en-US.json";
import ja from "../ja-JP.json";

/**
 * 每一類異常都要有標籤與說明文字（兩種語言）。
 *
 * 為什麼要用測試守：說明文字的鍵是**動態組出來的**（`anomaly.explain_${key}`），
 * `check-i18n` 這種靜態掃描看不到 —— 新增一類異常而忘了補文案時，畫面上會直接
 * 印出 `anomaly.explain_mac_flapping` 這種鍵名，而所有檢查都是綠的。
 * 這個專案已經被同一個鍵咬過一次。
 */
const CATEGORIES = [
  "ip_conflicts", "mac_drifts", "ghost_ips", "unauthorized_ips", "rogue_dhcp",
  "external_exposure", "dangling_dns", "duplicate_ip_records", "suspicious_changes",
  "fw_rule_rot", "arp_only_liveness", "stale_device_links", "mac_flapping", "identity_changes",
  "dns_compare_mismatch", "arp_flux", "l2_subnet_bleed",
];

describe("異常偵測的文案", () => {
  for (const key of CATEGORIES) {
    it(`${key} 三種語言都有說明`, () => {
      const z = (zh as any).anomaly?.[`explain_${key}`];
      const e = (en as any).anomaly?.[`explain_${key}`];
      const j = (ja as any).anomaly?.[`explain_${key}`];
      expect(z, `zh-TW 少了 anomaly.explain_${key}`).toBeTruthy();
      expect(e, `en-US 少了 anomaly.explain_${key}`).toBeTruthy();
      expect(j, `ja-JP 少了 anomaly.explain_${key}`).toBeTruthy();
    });
  }
});
