<script setup lang="ts">
import { computed, ref } from "vue";
import { fmtDateTime } from "@/utils/datetime";
import { useI18n } from "vue-i18n";
import { h } from "vue";
import {
  NCard, NSpace, NIcon, NButton, NAlert, NStatistic, NGrid, NGi, NDataTable, NEmpty,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { runAnomalyScan, type AnomalyReport } from "@/api/phase3";
import {
  AnomalyIcon, TestIcon, InfoIcon,
} from "@/icons";

const { t } = useI18n();
const msg = useMessage();
const loading = ref(false);
const report = ref<AnomalyReport | null>(null);
const lastRunAt = ref<string | null>(null);
const anyFindings = computed(() => {
  const r = report.value;
  return !!r && (r.ip_conflicts.length + r.mac_drifts.length + r.ghost_ips.length + r.unauthorized_ips.length) > 0;
});

const CATEGORIES = [
  { key: "ip_conflicts", label: () => t("anomaly.ip_conflicts") },
  { key: "mac_drifts", label: () => t("anomaly.mac_drifts") },
  { key: "ghost_ips", label: () => t("anomaly.ghost_ips") },
  { key: "unauthorized_ips", label: () => t("anomaly.unauthorized") },
] as const;

// 欄位標題在地化（其餘技術欄名原樣）
const COLLBL: Record<string, string> = {
  mac: "MAC", ip: "IP", port: "埠", device_id: "裝置",
  last_seen_at: "最後出現", locations: "出現位置", reason: "原因", subnet: "子網路", state: "狀態",
};
// 把單一值轉成易讀字串（時間截到分、UUID 取前 8 碼）
function pretty(k: string, val: any): string {
  if (val == null || val === "") return "";
  if (k.includes("device_id") || k === "device_id") return String(val).slice(0, 8);
  if (k.includes("last_seen") || k.includes("_at") || k.includes("time")) return String(val).replace("T", " ").slice(0, 16);
  return String(val);
}
function objLine(o: Record<string, any>): string {
  // 物件 → 「埠 X · 裝置 ab12 · 最後 …」這類精簡描述
  return Object.entries(o)
    .filter(([, v]) => v != null && v !== "")
    .map(([k, v]) => `${typeof COLLBL[k] === "string" ? COLLBL[k] : k}：${pretty(k, v)}`)
    .join("　·　");
}
// 單一格子：淡色標籤 + 值
function cell(label: string, val: string) {
  return h("span", { style: "white-space:nowrap;overflow:hidden;text-overflow:ellipsis" }, [
    h("span", { style: "opacity:.55;margin-right:4px" }, label),
    h("span", val || "—"),
  ]);
}
// 出現位置（MAC 漂移）：裝置 / 埠 / 最後出現 三欄對齊；裝置優先顯示友善名，無名才退回短 id
function renderLocation(o: Record<string, any>) {
  const dev = o.device_name || (o.device_id ? String(o.device_id).slice(0, 8) : "—");
  return h("div", {
    style: "display:grid;grid-template-columns:minmax(0,1fr) 110px 132px;gap:14px;font-size:12.5px;align-items:baseline",
  }, [
    cell(COLLBL.device_id, dev),
    cell(COLLBL.port, o.port ?? "—"),
    cell(COLLBL.last_seen_at, pretty("last_seen_at", o.last_seen_at)),
  ]);
}
// 依資料 keys 動態產生欄位，把偵測結果以表格呈現（取代難讀的原始 JSON）
function colsFor(rows: Record<string, any>[]): DataTableColumns<any> {
  const keys: string[] = [];
  for (const r of rows) for (const k of Object.keys(r)) if (!keys.includes(k)) keys.push(k);
  return keys.map((k) => {
    const isArr = rows.some((r) => Array.isArray(r[k]));
    return {
      title: typeof COLLBL[k] === "string" ? (COLLBL[k] as string) : k,
      key: k,
      minWidth: isArr ? 420 : 140,
      ellipsis: isArr ? false : { tooltip: true },
      render: (r: any) => {
        const v = r[k];
        if (v == null || v === "") return "—";
        if (Array.isArray(v)) {
          // 位置物件（含 port/last_seen_at）→ 對齊網格；其餘陣列退回精簡描述
          const loc = v.length > 0 && v[0] && typeof v[0] === "object" && ("port" in v[0] || "last_seen_at" in v[0]);
          return h("div", { style: "display:flex;flex-direction:column;gap:3px" },
            v.map((it) => loc
              ? renderLocation(it)
              : h("div", { style: "font-size:12.5px" },
                  it && typeof it === "object" ? objLine(it) : String(it))));
        }
        if (typeof v === "object") return objLine(v);
        return pretty(k, v);
      },
    };
  });
}

async function run() {
  loading.value = true;
  try {
    report.value = await runAnomalyScan();
    lastRunAt.value = fmtDateTime(new Date());
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.server"));
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><AnomalyIcon /></n-icon>
        <span>{{ t("anomaly.title") }}</span>
      </n-space>
    </template>
    <n-space align="center" style="margin-bottom: 12px" :wrap-item="false">
      <n-button type="primary" :loading="loading" @click="run">
        <template #icon><n-icon><TestIcon /></n-icon></template>
        {{ t("anomaly.run_scan") }}
      </n-button>
      <span v-if="lastRunAt" style="opacity: 0.7; font-size: 13px">
        {{ t("anomaly.last_run") }}: {{ lastRunAt }}
      </span>
    </n-space>

    <n-alert v-if="!report" type="info">
      <template #icon><n-icon><InfoIcon /></n-icon></template>
      {{ t("anomaly.help") }}
    </n-alert>

    <template v-if="report">
      <n-grid :cols="4" x-gap="12" style="margin-bottom: 16px">
        <n-gi>
          <n-statistic :label="t('anomaly.ip_conflicts')" :value="report.ip_conflicts.length" />
        </n-gi>
        <n-gi>
          <n-statistic :label="t('anomaly.mac_drifts')" :value="report.mac_drifts.length" />
        </n-gi>
        <n-gi>
          <n-statistic :label="t('anomaly.ghost_ips')" :value="report.ghost_ips.length" />
        </n-gi>
        <n-gi>
          <n-statistic :label="t('anomaly.unauthorized')" :value="report.unauthorized_ips.length" />
        </n-gi>
      </n-grid>
      <n-empty v-if="!anyFindings" :description="t('anomaly.none_found')" style="margin: 24px 0" />
      <template v-for="c in CATEGORIES" :key="c.key">
        <n-card v-if="(report?.[c.key]?.length ?? 0) > 0" size="small" style="margin-bottom: 12px"
                :title="`${c.label()} (${report?.[c.key]?.length ?? 0})`">
          <n-data-table :columns="colsFor(report?.[c.key] ?? [])" :data="report?.[c.key] ?? []"
                        :bordered="false" size="small" :scroll-x="600" />
        </n-card>
      </template>
    </template>
  </n-card>
</template>
