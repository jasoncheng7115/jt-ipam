import { ref } from "vue";
import { apiClient } from "@/api/client";

export interface ProbeDef {
  key: string;
  label_en: string;
  label_zh: string;
  klass: "light" | "heavy";
  intrusive: boolean;
  default_on: boolean;
  default_interval_seconds: number;
  min_interval_seconds: number;
  needs: string;
}
export interface OsFamily { key: string; label_en: string; label_zh: string; }

interface ProbeCatalog { probes: ProbeDef[]; os_families: OsFamily[]; }

// 全 SPA 共用一份（探測目錄不會在 session 中變）
const catalog = ref<ProbeCatalog>({ probes: [], os_families: [] });
let loaded = false;
let inflight: Promise<void> | null = null;

async function load(): Promise<void> {
  if (loaded) return;
  if (inflight) return inflight;
  inflight = apiClient
    .get<ProbeCatalog>("/api/v1/scan-agents/probes")
    .then(({ data }) => { catalog.value = data; loaded = true; })
    .catch(() => { /* 未登入 / 失敗：留空，呼叫端自行容錯 */ })
    .finally(() => { inflight = null; });
  return inflight;
}

/** 取得探測目錄（含雙語 label / 間隔 / 侵入性）與 OS 家族；自動載入一次。 */
export function useScanProbes() {
  void load();
  return { catalog };
}

/** 依目前語系挑 label（zh-TW → 中、其餘 → 英）。 */
export function probeLabel(p: ProbeDef, locale: string): string {
  return locale.startsWith("zh") ? p.label_zh : p.label_en;
}
export function osFamilyLabel(fams: OsFamily[], key: string | null | undefined, locale: string): string {
  const f = fams.find((x) => x.key === (key || "unknown"));
  if (!f) return key || "";
  return locale.startsWith("zh") ? f.label_zh : f.label_en;
}
