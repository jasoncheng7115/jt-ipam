<script setup lang="ts">
import { computed, ref, h } from "vue";
import { fmtDateTime } from "@/utils/datetime";
import { useI18n } from "vue-i18n";
import {
  NCard, NSpace, NIcon, NButton, NAlert, NStatistic, NGrid, NGi, NDataTable, NEmpty,
  NTabs, NTabPane, useMessage, type DataTableColumns,
} from "naive-ui";
import { runAnomalyScan, type AnomalyReport } from "@/api/phase3";
import { AnomalyIcon, TestIcon, InfoIcon } from "@/icons";
import { useTablePagination } from "@/composables/useTablePagination";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import ColumnPicker from "@/components/ColumnPicker.vue";

const { t } = useI18n();
const msg = useMessage();
const pg = useTablePagination();
const loading = ref(false);
const report = ref<AnomalyReport | null>(null);
const lastRunAt = ref<string | null>(null);
const activeTab = ref("ip_conflicts");

type CatKey = "ip_conflicts" | "mac_drifts" | "ghost_ips" | "unauthorized_ips";
const CATEGORIES: { key: CatKey; label: () => string }[] = [
  { key: "ip_conflicts", label: () => t("anomaly.ip_conflicts") },
  { key: "mac_drifts", label: () => t("anomaly.mac_drifts") },
  { key: "ghost_ips", label: () => t("anomaly.ghost_ips") },
  { key: "unauthorized_ips", label: () => t("anomaly.unauthorized") },
];

const anyFindings = computed(() => {
  const r = report.value;
  return !!r && (r.ip_conflicts.length + r.mac_drifts.length + r.ghost_ips.length + r.unauthorized_ips.length) > 0;
});
function catRows(key: CatKey): Record<string, any>[] {
  return (report.value?.[key] as Record<string, any>[]) ?? [];
}

// 欄位標題（技術欄名在地化；其餘原樣）
const COLLBL: Record<string, string> = {
  mac: "MAC", macs: "MAC", ip: "IP", ips: "對應 IP / 主機名稱", hostname: "主機名稱",
  port: "埠", device_id: "裝置", last_seen_at: "最後出現", locations: "出現位置",
  last_seen_scanner: "最後出現（掃描）", last_seen_librenms: "最後出現（LibreNMS）",
  ip_address_id: "IP 物件 ID", reason: "原因", subnet: "子網路", state: "狀態",
};
// 各類別的欄位（順序）＋預設隱藏（ip_address_id 是內部 UUID，預設不顯示，可在「欄位」勾選）
const CAT_KEYS: Record<CatKey, string[]> = {
  ip_conflicts: ["ip", "macs"],
  mac_drifts: ["mac", "ips", "locations"],
  ghost_ips: ["ip", "hostname", "last_seen_scanner", "last_seen_librenms", "ip_address_id"],
  unauthorized_ips: ["ip"],
};
const CAT_HIDDEN: Partial<Record<CatKey, string[]>> = { ghost_ips: ["ip_address_id"] };

// 每個類別一份欄位顯示偏好
const prefs = {} as Record<CatKey, ReturnType<typeof useColumnPrefs>>;
for (const c of CATEGORIES) {
  const keys = CAT_KEYS[c.key];
  const hidden = CAT_HIDDEN[c.key] ?? [];
  prefs[c.key] = useColumnPrefs(`anomaly_${c.key}`, keys, keys.filter((k) => !hidden.includes(k)));
}
function pickerItems(key: CatKey) {
  return CAT_KEYS[key].map((k) => ({ key: k, label: COLLBL[k] ?? k }));
}

function pretty(k: string, val: any): string {
  if (val == null || val === "") return "";
  if (k.includes("device_id")) return String(val).slice(0, 8);
  if (k.includes("last_seen") || k.includes("_at") || k.includes("time")) return String(val).replace("T", " ").slice(0, 16);
  return String(val);
}
function objLine(o: Record<string, any>): string {
  return Object.entries(o)
    .filter(([, v]) => v != null && v !== "")
    .map(([k, v]) => `${COLLBL[k] ?? k}：${pretty(k, v)}`)
    .join("　·　");
}
function cell(label: string, val: string) {
  return h("span", { style: "white-space:nowrap;overflow:hidden;text-overflow:ellipsis" }, [
    h("span", { style: "opacity:.55;margin-right:4px" }, label),
    h("span", val || "—"),
  ]);
}
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
function renderVal(k: string, v: any) {
  if (v == null || v === "") return "—";
  if (k === "ips" && Array.isArray(v)) {
    if (!v.length) return h("span", { style: "opacity:.5" }, "—");
    return h("div", { style: "display:flex;flex-direction:column;gap:2px;font-size:12.5px" },
      v.map((it: any) => h("div", null, it.hostname ? `${it.ip}（${it.hostname}）` : it.ip)));
  }
  if (Array.isArray(v)) {
    const loc = v.length > 0 && v[0] && typeof v[0] === "object" && ("port" in v[0] || "last_seen_at" in v[0]);
    return h("div", { style: "display:flex;flex-direction:column;gap:3px" },
      v.map((it: any) => loc
        ? renderLocation(it)
        : h("div", { style: "font-size:12.5px" }, it && typeof it === "object" ? objLine(it) : String(it))));
  }
  if (typeof v === "object") return objLine(v);
  return pretty(k, v);
}
// 依該類別的可見欄位（已套欄位偏好）組欄位
function catCols(key: CatKey): DataTableColumns<any> {
  const visible = prefs[key].visibleKeys.value;
  return CAT_KEYS[key].filter((k) => visible.includes(k)).map((k) => {
    const wide = k === "locations" || k === "macs";
    return {
      title: COLLBL[k] ?? k,
      key: k,
      minWidth: wide ? 420 : (k === "ips" ? 220 : 140),
      ellipsis: wide || k === "ips" ? false : { tooltip: true },
      render: (r: any) => renderVal(k, r[k]),
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
        <n-gi><n-statistic :label="t('anomaly.ip_conflicts')" :value="report.ip_conflicts.length" /></n-gi>
        <n-gi><n-statistic :label="t('anomaly.mac_drifts')" :value="report.mac_drifts.length" /></n-gi>
        <n-gi><n-statistic :label="t('anomaly.ghost_ips')" :value="report.ghost_ips.length" /></n-gi>
        <n-gi><n-statistic :label="t('anomaly.unauthorized')" :value="report.unauthorized_ips.length" /></n-gi>
      </n-grid>
      <n-empty v-if="!anyFindings" :description="t('anomaly.none_found')" style="margin: 24px 0" />

      <n-tabs v-else v-model:value="activeTab" type="line" animated>
        <n-tab-pane v-for="c in CATEGORIES" :key="c.key" :name="c.key"
                    :tab="`${c.label()} (${catRows(c.key).length})`">
          <template v-if="catRows(c.key).length">
            <div style="display:flex;justify-content:flex-end;margin-bottom:8px">
              <ColumnPicker :all="pickerItems(c.key)" :visible="prefs[c.key].visibleKeys.value"
                            @update:visible="prefs[c.key].setVisible" @reset="prefs[c.key].reset" />
            </div>
            <n-data-table :columns="catCols(c.key)" :data="catRows(c.key)"
                          :bordered="false" size="small" :scroll-x="600" :pagination="pg" />
          </template>
          <n-empty v-else :description="t('anomaly.none_found')" style="margin: 16px 0" />
        </n-tab-pane>
      </n-tabs>
    </template>
  </n-card>
</template>
