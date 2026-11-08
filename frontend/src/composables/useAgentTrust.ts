// 代理安裝指令要怎麼確認連到的是真的 jt-ipam（2026-11-04）：舊指令一律關掉 TLS 驗證（curl -k、JT_IPAM_INSECURE），
// 能攔截網路的人就能假冒 jt-ipam 拿走代理金鑰、推一支以 root 執行的程式。讀得到這台出示的憑證時預設釘選它的公鑰；
// 是公開 CA 簽的就用一般的憑證驗證；讀不到（例如前面有負載平衡器在終結 TLS）就預設憑證驗證。
// 憑證派送、掃描代理、RustDesk 代理三個頁面共用（AgentTrustPicker.vue 是畫面那一半）。
import { computed, h, ref, type VNodeChild } from "vue";
import { useI18n } from "vue-i18n";
import { NTag, NTooltip } from "naive-ui";
import { getServerIdentity, type ServerIdentity } from "@/api/certificates";

export type TrustMode = "pinned" | "ca" | "insecure";

export function useAgentTrust(opts: { explicitCa?: boolean } = {}) {
  const { t } = useI18n();
  const serverId = ref<ServerIdentity | null>(null);
  const mode = ref<TrustMode>("ca");

  async function load() {
    try {
      serverId.value = await getServerIdentity(window.location.origin);
      mode.value = serverId.value.available ? (serverId.value.ca_trusted ? "ca" : "pinned") : "ca";
    } catch { serverId.value = { available: false, reason: "unreachable" }; }
  }

  // Linux：curl 旗標（下載安裝程式那一次）＋傳給安裝程式的環境變數（寫進代理設定，之後每次連線都用）。
  // explicitCa：重跑時會沿用舊設定的安裝程式，「用系統 CA 驗證」要明講（JT_IPAM_INSECURE= 空值），舊指紋才會被清掉
  const linux = computed<[string, string]>(() => {
    const pin = serverId.value?.spki_pin;
    if (mode.value === "pinned" && pin) return [`-fsSLk --pinnedpubkey '${pin}'`, ` JT_IPAM_PIN='${pin}'`];
    if (mode.value === "insecure") return ["-fsSLk", " JT_IPAM_INSECURE=1"];
    return ["-fsSL", opts.explicitCa ? " JT_IPAM_INSECURE=" : ""];
  });

  // Windows：PowerShell 5.1 的 iwr 在下載安裝程式之前就要知道要信任誰（比對 jt-ipam 憑證的 SHA-256）
  const windows = computed(() => {
    const sha = serverId.value?.cert_sha256;
    if (mode.value === "pinned" && sha) {
      // 比對寫成 C#：PowerShell 5.1 把 script block 當憑證回呼時，會在沒有 runspace 的執行緒上呼叫而丟例外，
      // 連指紋正確的也一律「傳送時發生未預期的錯誤」（2026-11-04 Windows 11 實機測試）。安裝程式與代理沿用同一個型別。
      return `$env:JT_IPAM_CERT_SHA256='${sha}'\n[Net.ServicePointManager]::SecurityProtocol='Tls12'\n`
        + `Add-Type 'using System;using System.Net.Security;using System.Security.Cryptography;using System.Security.Cryptography.X509Certificates;`
        + `public static class JtPin{public static string Sha256="";public static bool Check(object s,X509Certificate c,X509Chain h,SslPolicyErrors e)`
        + `{if(c==null)return false;using(SHA256 a=SHA256.Create()){return string.Equals(BitConverter.ToString(a.ComputeHash(c.GetRawCertData())).Replace("-",""),Sha256,StringComparison.OrdinalIgnoreCase);}}}'\n`
        + `[JtPin]::Sha256=$env:JT_IPAM_CERT_SHA256; [Net.ServicePointManager]::ServerCertificateValidationCallback=`
        + `[Delegate]::CreateDelegate([Net.Security.RemoteCertificateValidationCallback],[JtPin].GetMethod('Check'))\n`;
    }
    if (mode.value === "insecure") {
      return `$env:JT_IPAM_INSECURE='1'\n[Net.ServicePointManager]::ServerCertificateValidationCallback={$true}\n`;
    }
    return `[Net.ServicePointManager]::SecurityProtocol='Tls12'\n`;
  });

  const options = computed(() => [
    { value: "pinned" as const, label: t("certTrust.mode_pinned"), disabled: !serverId.value?.available },
    { value: "ca" as const, label: t("certTrust.mode_ca"), disabled: false },
    { value: "insecure" as const, label: t("certTrust.mode_insecure"), disabled: false },
  ]);

  return { serverId, mode, load, linux, windows, options };
}

export type AgentTrust = ReturnType<typeof useAgentTrust>;

/** 清單的「伺服器驗證」欄：代理回報的 X-Agent-Tls；沒連過顯示 —、連過但沒回報是舊版代理 */
/**
 * onFix：給「未驗證」的代理一個入口（使用者 2026-11-07：「伺服器驗證未驗證，但沒地方啟用」）。
 * 標籤可以點，點了打開那台代理的安裝指令（已預選釘選憑證），拿到主機上重跑一次就好。
 */
export function renderTlsState(seen: boolean, tls: string | null | undefined, testid: string,
  hint: (state: string) => string, t: (k: string) => string, onFix?: () => void): VNodeChild {
  if (!seen) return h("span", { style: "opacity:.5" }, "—");
  const st = tls ?? "legacy";
  const type = st === "pinned" ? "success" : st === "ca" ? "info" : "error";
  const fixable = !!onFix && (st === "insecure" || st === "legacy");
  return h(NTooltip, { style: "max-width:380px" }, {
    trigger: () => h(NTag, {
      size: "small", type, bordered: false, "data-testid": testid,
      ...(fixable ? { style: "cursor:pointer;text-decoration:underline dotted", role: "button", tabindex: 0, "data-fixable": "1",
                      onClick: (e: MouseEvent) => { e.stopPropagation(); onFix!(); },
                      onKeydown: (e: KeyboardEvent) => { if (e.key === "Enter") onFix!(); } } : {}),
    }, () => t(`certTrust.state_${st}`)),
    default: () => (fixable
      ? [h("div", hint(st)), h("div", { style: "margin-top:6px;font-weight:600" }, t("certTrust.click_to_fix"))]
      : hint(st)),
  });
}
