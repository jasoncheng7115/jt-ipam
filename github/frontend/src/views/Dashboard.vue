<script setup lang="ts">
/**
 * Dashboard / IP 指示計
 *
 * phpIPAM 缺點：dashboard 只是堆數字。
 * jt-ipam：
 *   - 全系統使用率 donut(CSS conic-gradient，無圖表 lib 依賴)
 *   - 上線 / 離線 / 未知 三色指示燈
 *   - Top-N 最滿 subnet(capacity planning)
 *   - section heat：每個 section 的使用熱度條
 *   - 24h audit count
 */
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import {
  NCard,
  NH2,
  NSpace,
  NIcon,
  NProgress,
  NAlert,
  NSpin,
  NTag,
  useMessage,
} from "naive-ui";
import { getOverview, type DashboardOverview } from "@/api/dashboard";
import { listLocations, listRacks } from "@/api/basic";
import { usePinned } from "@/composables/usePinned";
import {
  DashboardIcon, SectionsIcon, SubnetsIcon, AddressesIcon, AuditIcon, LocationsIcon, RacksIcon, DevicesIcon, VirtualizationIcon,
} from "@/icons";
import { Database as CapacityIcon } from "@iconoir/vue";

const { t } = useI18n();
const router = useRouter();
const msg = useMessage();
const data = ref<DashboardOverview | null>(null);
const loading = ref(false);

async function load() {
  loading.value = true;
  try {
    data.value = await getOverview();
  } catch {
    msg.error(t("errors.network"));
  } finally {
    loading.value = false;
  }
}

const usePctColor = (pct: number): string => {
  if (pct >= 90) return "#ef4444";
  if (pct >= 75) return "#f59e0b";
  if (pct >= 50) return "#eab308";
  return "#22c55e";
};

const donutColor = computed(() => usePctColor(data.value?.used_pct ?? 0));

// KPI 卡的視覺差異化：每張卡一個獨立 accent 色 + icon
const kpiTiles = computed(() => [
  { key: "sections",  i18n: "kpi_sections",       value: data.value?.sections ?? 0,        color: "#6366f1", icon: SectionsIcon },   // indigo
  { key: "subnets",   i18n: "kpi_subnets",        value: data.value?.subnets ?? 0,         color: "#0ea5e9", icon: SubnetsIcon },    // sky
  { key: "used",      i18n: "kpi_ips_allocated",  value: data.value?.used ?? 0,            color: "#22c55e", icon: AddressesIcon },  // green
  { key: "capacity",  i18n: "kpi_total_capacity", value: data.value?.total_capacity ?? 0,  color: "#a855f7", icon: CapacityIcon },   // purple
  { key: "audit",     i18n: "kpi_audit_24h",      value: data.value?.audit_24h ?? 0,       color: "#f59e0b", icon: AuditIcon },      // amber
]);

// ── 統計圖表（純 SVG/CSS，無圖表 lib）──
const DEVICE_TYPE_COLOR: Record<string, string> = {
  server: "#8b5cf6", switch: "#0ea5e9", router: "#6366f1", firewall: "#ef4444",
  ap: "#14b8a6", storage: "#f59e0b", ipmi: "#ec4899", other: "#94a3b8",
};
const deviceTypes = computed(() => data.value?.device_types ?? []);
const deviceTypeMax = computed(() => Math.max(1, ...deviceTypes.value.map((d) => d.count)));
function deviceTypeColor(t: string) { return DEVICE_TYPE_COLOR[t] ?? "#94a3b8"; }

const rackUsage = computed(() => data.value?.rack_usage ?? []);

const custResources = computed(() => data.value?.customer_resources ?? []);
const custResMax = computed(() => Math.max(1, ...custResources.value.map((c) => c.subnets + c.devices + c.ips)));

// 趨勢折線：把 14 天的 audit / ip_changes 轉成 SVG polyline points
const trend = computed(() => data.value?.activity_trend ?? []);
const trendMax = computed(() => Math.max(1, ...trend.value.flatMap((p) => [p.audit, p.ip_changes])));
const TREND_W = 320, TREND_H = 90, TREND_PAD = 4;
function trendPoints(key: "audit" | "ip_changes"): string {
  const n = trend.value.length;
  if (n < 2) return "";
  const innerW = TREND_W - TREND_PAD * 2, innerH = TREND_H - TREND_PAD * 2;
  return trend.value.map((p, i) => {
    const x = TREND_PAD + (i / (n - 1)) * innerW;
    const y = TREND_PAD + innerH - (p[key] / trendMax.value) * innerH;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
}
const trendTotals = computed(() => ({
  audit: trend.value.reduce((a, p) => a + p.audit, 0),
  ip: trend.value.reduce((a, p) => a + p.ip_changes, 0),
}));
// 座標換算（SVG user units）
function trendX(i: number): number {
  const n = trend.value.length;
  return n < 2 ? TREND_PAD : TREND_PAD + (i / (n - 1)) * (TREND_W - TREND_PAD * 2);
}
function trendY(v: number): number {
  const innerH = TREND_H - TREND_PAD * 2;
  return TREND_PAD + innerH - (v / trendMax.value) * innerH;
}
// hover：游標對應到最近的資料點
const trendHover = ref<number | null>(null);
const trendTip = ref<{ x: number; y: number }>({ x: 0, y: 0 });
function onTrendMove(e: MouseEvent) {
  const n = trend.value.length;
  if (n < 1) return;
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
  const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
  trendHover.value = Math.round(ratio * (n - 1));
  trendTip.value = { x: e.clientX - rect.left, y: e.clientY - rect.top };
}
function onTrendLeave() { trendHover.value = null; }
const trendHoverPt = computed(() => trendHover.value != null ? trend.value[trendHover.value] : null);

// ── 上下關係鏈：機房 → 機櫃 → 裝置 → IP 位址 → 子網路 → 區段 ──
// 每層放本系統該層物件總數；即使是 0 也照列，讓人看出完整層級與關聯。
const hierLayers = computed(() => [
  { key: "locations", label: "nav.locations",     icon: LocationsIcon,      value: data.value?.locations ?? 0, route: "locations", color: "#0ea5e9" },
  { key: "racks",     label: "nav.racks",         icon: RacksIcon,          value: data.value?.racks ?? 0,     route: "racks",     color: "#6366f1" },
  { key: "devices",   label: "nav.devices",       icon: DevicesIcon,        value: data.value?.devices ?? 0,   route: "devices",   color: "#8b5cf6" },
  { key: "vms",       label: "nav.virtualization", icon: VirtualizationIcon, value: data.value?.vms ?? 0,       route: "virt",      color: "#ec4899" },
  { key: "addresses", label: "nav.addresses",     icon: AddressesIcon,      value: data.value?.addresses ?? 0, route: "addresses", color: "#18a058" },
  { key: "subnets",   label: "nav.subnets",       icon: SubnetsIcon,        value: data.value?.subnets ?? 0,   route: "subnets",   color: "#f59e0b" },
  { key: "sections",  label: "nav.sections",      icon: SectionsIcon,       value: data.value?.sections ?? 0,  route: "sections",  color: "#ef4444" },
]);

const statusTotal = computed(() => {
  const s = data.value?.status;
  if (!s) return 0;
  return s.online + s.offline + s.unknown;
});

function go(name: string, params?: Record<string, string>) {
  router.push({ name, params }).catch(() => {});
}

// ── 常用機房 / 常用機櫃（localStorage 釘選）──
const locPin = usePinned("locations");
const rackPin = usePinned("racks");
const allLocations = ref<{ id: string; name: string; rack_count: number; device_count: number }[]>([]);
const allRacks = ref<{ id: string; name: string; location_id: string | null }[]>([]);
const pinnedLocations = computed(() => allLocations.value.filter((l) => locPin.isPinned(l.id)));
const pinnedRacks = computed(() => allRacks.value.filter((r) => rackPin.isPinned(r.id)));
// 釘選機房裡裝置數最大值 → 給橫條圖當基準
const maxLocDevices = computed(() => Math.max(1, ...pinnedLocations.value.map((l) => l.device_count)));
// 機櫃數 / 裝置數 各自取最寬位數，讓兩欄數字跨列上下對齊
const locRackDigits = computed(() => Math.max(1, ...pinnedLocations.value.map((l) => String(l.rack_count ?? 0).length)));
const locDevDigits = computed(() => Math.max(1, ...pinnedLocations.value.map((l) => String(l.device_count ?? 0).length)));
async function loadPins() {
  try {
    const [l, r] = await Promise.all([listLocations(), listRacks()]);
    allLocations.value = l.items.map((x: any) => ({
      id: x.id, name: x.name, rack_count: x.rack_count ?? 0, device_count: x.device_count ?? 0,
    }));
    allRacks.value = r.items.map((x: any) => ({ id: x.id, name: x.name, location_id: x.location_id }));
  } catch { /* silent */ }
}
function locName(id: string | null): string {
  return allLocations.value.find((l) => l.id === id)?.name ?? "—";
}

onMounted(() => { void load(); void loadPins(); });
</script>

<template>
  <n-spin :show="loading">
    <n-space v-if="data" vertical :size="16">
      <n-space align="center" :wrap-item="false" style="margin-bottom: 4px">
        <n-icon :size="24"><DashboardIcon /></n-icon>
        <n-h2 style="margin: 0">{{ t("dashboard.title") }}</n-h2>
      </n-space>
      <!-- KPI 列：每張卡有獨立 accent 色 + icon -->
      <div class="kpi-row">
        <n-card
          v-for="k in kpiTiles"
          :key="k.key"
          size="small"
          class="kpi-card"
          :style="{ '--accent': k.color }"
          :content-style="{ padding: '12px 16px', display: 'flex', alignItems: 'center', gap: '12px' }"
        >
          <div class="kpi-icon">
            <n-icon :size="22" :color="k.color"><component :is="k.icon" /></n-icon>
          </div>
          <div class="kpi-body">
            <div class="kpi-label">{{ t(`dashboard.${k.i18n}`) }}</div>
            <div class="kpi-value">{{ k.value }}</div>
          </div>
        </n-card>
      </div>

      <!-- 關係圖（上下層物件）：機房→機櫃→裝置→虛擬機→IP→子網路→區段，每層放本系統總數。
           每一層都列出（即使 0），讓人看出完整層級與上下關聯。 -->
      <n-card :title="t('dashboard.hierarchy_title')" size="small" class="hier-card">
        <div class="hier-chain">
          <template v-for="(layer, i) in hierLayers" :key="layer.key">
            <span v-if="i > 0" class="hier-arrow">→</span>
            <div class="hier-node" :title="t(layer.label)" @click="go(layer.route)">
              <div class="hier-top">
                <span class="hier-badge" :style="{ background: layer.color + '1f', color: layer.color }">
                  <n-icon :size="15"><component :is="layer.icon" /></n-icon>
                </span>
                <span class="hier-label">{{ t(layer.label) }}</span>
              </div>
              <div class="hier-count">{{ layer.value.toLocaleString() }}</div>
            </div>
          </template>
        </div>
      </n-card>

      <div class="row-2col">
        <!-- Donut 使用率 — SVG stroke-dasharray，currentColor 跟主題色 -->
        <n-card :title="t('dashboard.card_ip_usage')" class="row-card">
          <n-space vertical align="center" justify="center" style="height: 100%">
            <svg class="donut-svg" viewBox="0 0 100 100" width="180" height="180">
              <!-- track -->
              <circle cx="50" cy="50" r="42" fill="none"
                      stroke="currentColor" stroke-width="9" stroke-opacity="0.12" />
              <!-- value -->
              <circle cx="50" cy="50" r="42" fill="none"
                      :stroke="donutColor"
                      stroke-width="9"
                      pathLength="100"
                      :stroke-dasharray="`${data.used_pct} 100`"
                      stroke-linecap="round"
                      transform="rotate(-90 50 50)"
                      style="transition: stroke-dasharray 0.5s ease, stroke 0.3s ease;" />
              <text x="50" y="49" text-anchor="middle" dominant-baseline="middle"
                    font-size="18" font-weight="700" fill="currentColor"
                    font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif">
                {{ data.used_pct }}%
              </text>
              <text x="50" y="64" text-anchor="middle" dominant-baseline="middle"
                    font-size="7" fill="currentColor" opacity="0.6"
                    font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', monospace">
                {{ data.used }} / {{ data.total_capacity }}
              </text>
            </svg>
          </n-space>
        </n-card>

        <!-- 狀態指示燈 -->
        <n-card :title="t('dashboard.card_indicator')" class="row-card">
          <n-space vertical :size="16">
            <div class="indicator-row">
              <span class="dot dot-on"></span>
              <span class="indicator-label">{{ t("dashboard.status_online") }}</span>
              <span class="indicator-value">{{ data.status.online }}</span>
            </div>
            <div class="indicator-row">
              <span class="dot dot-off"></span>
              <span class="indicator-label">{{ t("dashboard.status_offline") }}</span>
              <span class="indicator-value">{{ data.status.offline }}</span>
            </div>
            <div class="indicator-row">
              <span class="dot dot-unknown"></span>
              <span class="indicator-label">{{ t("dashboard.status_unknown") }}</span>
              <span class="indicator-value">{{ data.status.unknown }}</span>
            </div>
            <n-progress
              v-if="statusTotal > 0"
              :percentage="(data.status.online / statusTotal) * 100"
              :show-indicator="false"
              status="success"
              type="line"
            />
            <p style="font-size: 12px; opacity: 0.7; margin: 0">
              {{ t("dashboard.indicator_source") }}
            </p>
          </n-space>
        </n-card>
      </div>

      <!-- 統計圖表 2×2 -->
      <div class="chart-grid">
        <!-- 裝置類型分布 -->
        <n-card :title="t('dashboard.chart_device_types')" size="small">
          <div v-if="!deviceTypes.length" class="chart-empty">{{ t("common.no_data") }}</div>
          <div v-else class="hbars">
            <div v-for="d in deviceTypes" :key="d.type" class="hbar-row" @click="go('devices')">
              <span class="hbar-label">{{ d.type }}</span>
              <div class="hbar-track">
                <div class="hbar-fill" :style="{ width: (d.count / deviceTypeMax * 100) + '%', background: deviceTypeColor(d.type) }"></div>
              </div>
              <span class="hbar-val">{{ d.count }}</span>
            </div>
          </div>
        </n-card>

        <!-- 機櫃 U 使用率 -->
        <n-card :title="t('dashboard.chart_rack_usage')" size="small">
          <div v-if="!rackUsage.length" class="chart-empty">{{ t("common.no_data") }}</div>
          <div v-else class="hbars">
            <div v-for="r in rackUsage" :key="r.rack_id" class="hbar-row" @click="go('racks')">
              <span class="hbar-label">{{ r.name }}</span>
              <div class="hbar-track">
                <div class="hbar-fill" :style="{ width: r.pct + '%', background: usePctColor(r.pct) }"></div>
              </div>
              <span class="hbar-val">{{ r.used_u }}/{{ r.total_u }}U</span>
            </div>
          </div>
        </n-card>

        <!-- 各單位資源占比 -->
        <n-card :title="t('dashboard.chart_customer_res')" size="small">
          <div v-if="!custResources.length" class="chart-empty">{{ t("common.no_data") }}</div>
          <div v-else>
            <div class="chart-legend">
              <span><i style="background:#0ea5e9"></i>{{ t("nav.subnets") }}</span>
              <span><i style="background:#8b5cf6"></i>{{ t("nav.devices") }}</span>
              <span><i style="background:#22c55e"></i>IP</span>
            </div>
            <div class="hbars">
              <div v-for="c in custResources" :key="c.customer_id ?? c.label" class="hbar-row" @click="go('customers')">
                <span class="hbar-label">{{ c.label }}</span>
                <div class="hbar-track stack">
                  <div :style="{ width: (c.subnets / custResMax * 100) + '%', background: '#0ea5e9' }" :title="`${t('nav.subnets')}: ${c.subnets}`"></div>
                  <div :style="{ width: (c.devices / custResMax * 100) + '%', background: '#8b5cf6' }" :title="`${t('nav.devices')}: ${c.devices}`"></div>
                  <div :style="{ width: (c.ips / custResMax * 100) + '%', background: '#22c55e' }" :title="`IP: ${c.ips}`"></div>
                </div>
                <span class="hbar-val">{{ c.subnets + c.devices + c.ips }}</span>
              </div>
            </div>
          </div>
        </n-card>

        <!-- 近 14 日 稽核 / IP 異動 趨勢 -->
        <n-card :title="t('dashboard.chart_activity_trend')" size="small">
          <div class="chart-legend">
            <span><i style="background:#f59e0b"></i>{{ t("dashboard.chart_audit_events") }} · {{ trendTotals.audit }}</span>
            <span><i style="background:#0ea5e9"></i>{{ t("nav.ip_changes") }} · {{ trendTotals.ip }}</span>
          </div>
          <div class="trend-wrap" @mousemove="onTrendMove" @mouseleave="onTrendLeave">
            <svg class="trend-svg" :viewBox="`0 0 ${TREND_W} ${TREND_H}`" preserveAspectRatio="none">
              <polyline :points="trendPoints('audit')" fill="none" stroke="#f59e0b" stroke-width="2" stroke-linejoin="round" />
              <polyline :points="trendPoints('ip_changes')" fill="none" stroke="#0ea5e9" stroke-width="2" stroke-linejoin="round" />
              <template v-if="trendHover != null && trendHoverPt">
                <line :x1="trendX(trendHover)" :x2="trendX(trendHover)" :y1="TREND_PAD" :y2="TREND_H - TREND_PAD"
                      stroke="rgba(127,127,127,0.5)" stroke-width="1" stroke-dasharray="2 2" />
                <circle :cx="trendX(trendHover)" :cy="trendY(trendHoverPt.audit)" r="3" fill="#f59e0b" />
                <circle :cx="trendX(trendHover)" :cy="trendY(trendHoverPt.ip_changes)" r="3" fill="#0ea5e9" />
              </template>
            </svg>
            <div v-if="trendHover != null && trendHoverPt" class="trend-tip"
                 :style="{ left: Math.min(trendTip.x + 10, 240) + 'px', top: '2px' }">
              <div class="tt-day">{{ trendHoverPt.day }}</div>
              <div><i style="background:#f59e0b"></i>{{ t("dashboard.chart_audit_events") }} {{ trendHoverPt.audit }}</div>
              <div><i style="background:#0ea5e9"></i>{{ t("nav.ip_changes") }} {{ trendHoverPt.ip_changes }}</div>
            </div>
          </div>
          <div class="trend-axis"><span>{{ trend[0]?.day?.slice(5) }}</span><span>{{ trend[trend.length - 1]?.day?.slice(5) }}</span></div>
        </n-card>
      </div>

      <!-- Pinned subnets(使用者釘選) -->
      <n-card v-if="data.pinned_subnets?.length" :title="t('dashboard.pinned_subnets')">
        <n-space vertical :size="8">
          <div
            v-for="row in data.pinned_subnets"
            :key="row.subnet_id"
            class="row-line"
            @click="go('subnet-detail', { id: row.subnet_id })"
          >
            <div class="row-cust">
              <n-tag v-if="row.customer_label" size="tiny" type="info" bordered>{{ row.customer_label }}</n-tag>
              <n-tag v-else size="tiny" bordered>—</n-tag>
            </div>
            <div class="row-cidr">{{ row.cidr }}</div>
            <div class="row-bar">
              <n-progress
                type="line"
                :percentage="row.used_pct"
                :status="row.used_pct >= 90 ? 'error' : row.used_pct >= 75 ? 'warning' : 'success'"
              />
            </div>
            <div class="row-num">{{ row.used }} / {{ row.total }}</div>
          </div>
        </n-space>
      </n-card>

      <!-- 常用機房 / 地點 -->
      <n-card v-if="pinnedLocations.length" :title="t('dashboard.pinned_locations')">
        <n-space vertical :size="8">
          <div v-for="l in pinnedLocations" :key="l.id" class="loc-row" @click="go('locations')">
            <n-icon :size="16" style="opacity:.6;flex:0 0 auto"><LocationsIcon /></n-icon>
            <span class="loc-name">{{ l.name }}</span>
            <!-- 中間橫式分布條（仿常用子網路），裝置數相對長度 -->
            <div class="loc-bar">
              <div class="loc-bar__fill" :style="{ width: (l.device_count / maxLocDevices * 100) + '%' }"></div>
            </div>
            <span class="loc-counts">
              <span class="loc-metric"><n-icon :size="13"><RacksIcon /></n-icon><span class="loc-num" :style="{ minWidth: locRackDigits + 'ch' }">{{ l.rack_count }}</span></span>
              <span class="loc-metric"><n-icon :size="13"><DevicesIcon /></n-icon><span class="loc-num" :style="{ minWidth: locDevDigits + 'ch' }">{{ l.device_count }}</span></span>
            </span>
          </div>
        </n-space>
      </n-card>

      <!-- 常用機櫃 -->
      <n-card v-if="pinnedRacks.length" :title="t('dashboard.pinned_racks')">
        <n-space vertical :size="6">
          <div v-for="r in pinnedRacks" :key="r.id" class="row-line" @click="go('racks')">
            <n-icon :size="16" style="opacity:.6"><RacksIcon /></n-icon>
            <span style="margin-left:8px">{{ r.name }}</span>
            <span style="margin-left:auto; opacity:.55; font-size:12px">{{ locName(r.location_id) }}</span>
          </div>
        </n-space>
      </n-card>

      <!-- Top fullest subnets -->
      <n-card :title="t('dashboard.card_top_full')">
        <n-space vertical :size="8">
          <div
            v-for="row in data.top_full_subnets"
            :key="row.subnet_id"
            class="row-line"
            @click="go('subnet-detail', { id: row.subnet_id })"
          >
            <div class="row-cust">
              <n-tag v-if="row.customer_label" size="tiny" type="info" bordered>{{ row.customer_label }}</n-tag>
              <n-tag v-else size="tiny" bordered>—</n-tag>
            </div>
            <div class="row-cidr">{{ row.cidr }}</div>
            <div class="row-bar">
              <n-progress
                type="line"
                :percentage="row.used_pct"
                :status="row.used_pct >= 90 ? 'error' : row.used_pct >= 75 ? 'warning' : 'success'"
              />
            </div>
            <div class="row-num">{{ row.used }} / {{ row.total }}</div>
          </div>
          <n-alert v-if="!data.top_full_subnets.length" type="info" size="small">
            {{ t("dashboard.no_subnet_data") }}
          </n-alert>
        </n-space>
      </n-card>

      <!-- Section heat -->
      <n-card :title="t('dashboard.card_section_heat')">
        <n-space vertical :size="8">
          <div
            v-for="row in data.section_heat"
            :key="row.section_id"
            class="row-line"
          >
            <div class="row-cidr">{{ row.name }}</div>
            <div class="row-bar">
              <n-progress
                type="line"
                :percentage="row.used_pct"
                :status="row.used_pct >= 90 ? 'error' : row.used_pct >= 75 ? 'warning' : 'success'"
              />
            </div>
            <div class="row-num">
              {{ t("dashboard.heat_summary", { subnets: row.subnet_count, used: row.used, total: row.total_hosts }) }}
            </div>
          </div>
        </n-space>
      </n-card>
    </n-space>
  </n-spin>
</template>

<style scoped>
/* widget 標題列：加品牌色 tint 的標籤背景條（KPI 卡無 header，不受影響） */
:deep(.n-card > .n-card-header) {
  /* 中性灰底標題列：沉穩、有高級感，與頁面背景明顯區隔 */
  background: rgba(100, 116, 139, 0.10);
  border-radius: 10px 10px 0 0;
  padding-top: 12px;
  padding-bottom: 12px;
  /* 關鍵：標題列與下方內容之間留白（用 margin，不會被各卡的 content-style 內距覆蓋） */
  margin-bottom: 14px;
}
:deep(.n-card > .n-card-header .n-card-header__main) { font-weight: 600; }

/* ── 關係圖（上下層物件）── */
.hier-card { margin-bottom: 16px; }
/* 統計圖表 */
.chart-grid {
  display: grid; gap: 12px; margin-bottom: 16px;
  grid-template-columns: repeat(2, 1fr);
}
@media (max-width: 880px) { .chart-grid { grid-template-columns: 1fr; } }
.chart-empty { text-align: center; opacity: 0.5; font-size: 13px; padding: 20px 0; }
.hbars { display: flex; flex-direction: column; gap: 7px; }
.hbar-row { display: flex; align-items: center; gap: 8px; cursor: pointer; }
.hbar-row:hover .hbar-track { filter: brightness(1.05); }
.hbar-label { flex: 0 0 96px; max-width: 96px; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hbar-label.mono { font-family: monospace; }
.hbar-track { flex: 1 1 auto; height: 12px; border-radius: 6px; background: rgba(127,127,127,0.14); overflow: hidden; }
.hbar-track.stack { display: flex; }
.hbar-track.stack > div { height: 100%; }
.hbar-fill { height: 100%; border-radius: 6px; transition: width .3s; }
.hbar-val { flex: 0 0 auto; min-width: 40px; text-align: right; font-size: 12px; font-variant-numeric: tabular-nums; opacity: 0.75; }
.chart-legend { display: flex; gap: 14px; font-size: 12px; opacity: 0.7; margin-bottom: 8px; flex-wrap: wrap; }
.chart-legend i { display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin-right: 4px; vertical-align: middle; }
.trend-wrap { position: relative; }
.trend-svg { width: 100%; height: 90px; display: block; }
.trend-tip {
  position: absolute; pointer-events: none; z-index: 2;
  background: var(--n-color, #fff); border: 1px solid var(--n-border-color, rgba(127,127,127,0.25));
  border-radius: 6px; padding: 5px 8px; font-size: 11px; line-height: 1.5;
  box-shadow: 0 2px 8px rgba(0,0,0,0.12); white-space: nowrap;
}
.trend-tip .tt-day { font-weight: 600; margin-bottom: 2px; }
.trend-tip i { display: inline-block; width: 8px; height: 8px; border-radius: 2px; margin-right: 4px; vertical-align: middle; }
.trend-axis { display: flex; justify-content: space-between; font-size: 11px; opacity: 0.5; font-variant-numeric: tabular-nums; }
.hier-chain {
  display: flex;
  align-items: stretch;
  flex-wrap: wrap;
  gap: 6px;
  row-gap: 10px;
}
.hier-node {
  flex: 1 1 0;
  min-width: 88px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px 14px;
  border: 1px solid rgba(127, 127, 127, 0.2);
  border-radius: 10px;
  /* 不用 var(--n-card-color)（scoped 取不到 → 深色模式變白底）；用半透明中性色兩種主題皆相容 */
  background: rgba(127, 127, 127, 0.07);
  cursor: pointer;
  transition: transform .12s, box-shadow .12s;
}
.hier-node:hover { transform: translateY(-2px); box-shadow: 0 4px 14px rgba(0, 0, 0, 0.08); }
.hier-top { display: flex; align-items: center; gap: 7px; min-width: 0; }
.hier-badge {
  width: 26px; height: 26px; border-radius: 7px; flex: 0 0 auto;
  display: inline-flex; align-items: center; justify-content: center;
}
.hier-label {
  font-size: 12px; color: var(--n-text-color-3, #8a8a8a);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.hier-count { font-size: 24px; font-weight: 700; font-variant-numeric: tabular-nums; line-height: 1; }

/* 常用機房：單行 — 名稱 | 中間橫式分布條 | 機櫃/裝置數（仿常用子網路） */
.loc-row {
  cursor: pointer; padding: 4px 8px; border-radius: 6px; transition: background .15s;
  display: flex; align-items: center; gap: 8px;
}
.loc-row:hover { background: rgba(127, 127, 127, 0.08); }
.loc-name { font-weight: 500; flex: 0 0 132px; max-width: 132px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.loc-counts {
  flex: 0 0 auto; justify-content: flex-end;
  display: inline-flex; align-items: center; gap: 12px;
  font-size: 12px; opacity: 0.7; font-variant-numeric: tabular-nums;
}
.loc-metric { display: inline-flex; align-items: center; gap: 3px; }
.loc-num { text-align: right; font-variant-numeric: tabular-nums; }
.loc-bar {
  flex: 1 1 auto; height: 6px; border-radius: 3px;
  background: rgba(127, 127, 127, 0.15); overflow: hidden;
}
.loc-bar__fill {
  height: 100%; border-radius: 3px;
  background: linear-gradient(90deg, #18a058, #36ad6a);
  transition: width .3s;
}
.hier-arrow {
  display: flex; align-items: center;
  color: var(--n-text-color-3, #999); font-size: 16px; user-select: none;
}

.kpi-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
}
/* KPI 卡用 n-card，與下方卡片同一表面樣式；只保留 icon／數值的 accent 色 */
.kpi-card {
  transition: transform 0.12s ease, box-shadow 0.12s ease;
}
.kpi-card:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
}
.kpi-icon {
  width: 44px;
  height: 44px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 10px;
  background: color-mix(in srgb, var(--accent) 14%, transparent);
  flex-shrink: 0;
}
.kpi-body {
  flex: 1;
  min-width: 0;
}
.kpi-label {
  font-size: 12px;
  opacity: 0.75;
  margin-bottom: 4px;
}
.kpi-value {
  font-size: 24px;
  font-weight: 700;
  line-height: 1.1;
  color: var(--accent);
}
.row-2col {
  display: grid;
  grid-template-columns: minmax(280px, 1fr) minmax(360px, 2fr);
  gap: 16px;
  align-items: stretch;
}
.row-2col > .row-card {
  height: 100%;
}
@media (max-width: 800px) {
  .row-2col {
    grid-template-columns: 1fr;
  }
}
.donut {
  width: 200px;
  height: 200px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
}
.donut-hole {
  width: 130px;
  height: 130px;
  background: var(--n-card-color, white);
  border-radius: 50%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}
.donut-pct {
  font-size: 28px;
  font-weight: 700;
}
.donut-sub {
  font-size: 12px;
  opacity: 0.7;
  margin-top: 4px;
}
.indicator-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.dot {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  display: inline-block;
  box-shadow: 0 0 8px currentColor;
}
.dot-on {
  background: #22c55e;
  color: rgba(34, 197, 94, 0.5);
}
.dot-off {
  background: #ef4444;
  color: rgba(239, 68, 68, 0.5);
}
.dot-unknown {
  background: #9ca3af;
  color: rgba(156, 163, 175, 0.4);
}
.indicator-label {
  flex: 1;
  font-size: 14px;
}
.indicator-value {
  font-size: 18px;
  font-weight: 600;
  font-family: monospace;
}
.row-line {
  display: grid;
  grid-template-columns: 140px 160px 1fr 120px;
  gap: 12px;
  align-items: center;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 4px;
  transition: background 0.15s;
}
.row-line:hover {
  background: rgba(127, 127, 127, 0.08);
}
.row-cust {
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.row-cidr {
  font-family: monospace;
  font-size: 13px;
}
.row-num {
  text-align: right;
  font-family: monospace;
  font-size: 12px;
  opacity: 0.85;
}

/* 手機：固定四欄網格(140 160 1fr 120 ≈ 456px)會比窄卡片寬 → 右側 CIDR/數量
   被推出畫面外。改 flex 換行：單位+CIDR 一行、使用率長條獨佔一行、數量靠右。
   對只有 icon+名稱的「常用機房/機櫃」列也安全（無 bar/num，就單純並排）。 */
@media (max-width: 640px) {
  .row-line {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    column-gap: 8px;
    row-gap: 4px;
  }
  .row-line > * { min-width: 0; }
  .row-cust { flex: 0 1 auto; overflow: hidden; }
  .row-cidr { flex: 1 1 auto; }
  .row-bar  { flex: 1 1 100%; }
  .row-num  { flex: 0 0 auto; margin-left: auto; }
}
</style>
