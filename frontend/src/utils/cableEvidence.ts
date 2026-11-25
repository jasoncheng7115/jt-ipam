import type { CableEvidence } from "@/api/phase3";

type T = (key: string, params?: Record<string, unknown>) => string;

const PROTO: Record<string, string> = { lldp: "LLDP", cdp: "CDP", mndp: "MNDP" };
const SOURCE: Record<string, string> = { snmp: "SNMP", librenms: "LibreNMS", mikrotik: "MikroTik" };

/** 自動推斷的依據寫成一句話（佈線清單的來源提示、建議清單共用） */
export function evidenceText(t: T, ev: CableEvidence | null | undefined): string {
  if (!ev?.kind) return "—";
  if (ev.kind in PROTO) {
    return t("cable_infer.ev_neighbor", {
      proto: PROTO[ev.kind], source: SOURCE[ev.source ?? ""] ?? ev.source ?? "",
      remote: ev.remote_sysname ?? "?", port: ev.remote_port ?? "?",
    });
  }
  if (ev.kind === "fdb_single_mac") return t("cable_infer.ev_single_mac", { port: ev.switch_port ?? "?", mac: ev.macs?.[0] ?? "?" });
  if (ev.kind === "fdb_vm_host") return t("cable_infer.ev_vm_host", { port: ev.switch_port ?? "?", n: ev.mac_count ?? 0 });
  if (ev.kind === "fdb_uplink") return t("cable_infer.ev_uplink");
  return ev.kind;
}

export function kindLabel(t: T, kind: string | null | undefined): string {
  if (!kind) return "—";
  if (kind in PROTO) return PROTO[kind];
  const key = `cable_infer.kind_${kind}`;
  const out = t(key);
  return out === key ? kind : out;
}
