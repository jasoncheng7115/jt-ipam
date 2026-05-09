<script setup lang="ts">
/**
 * Dashboard / IP 指示計
 *
 * phpIPAM 缺點：dashboard 只是堆數字。
 * jt-ipam：
 *   - 全系統使用率 donut（CSS conic-gradient，無圖表 lib 依賴）
 *   - 在線/離線/未知 三色指示燈
 *   - Top-N 最滿 subnet（capacity planning）
 *   - section heat：每個 section 的使用熱度條
 *   - 24h audit count
 */
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import {
  NCard,
  NSpace,
  NStatistic,
  NProgress,
  NAlert,
  NSpin,
  useMessage,
} from "naive-ui";
import { getOverview, type DashboardOverview } from "@/api/dashboard";

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

const donutStyle = computed(() => {
  const pct = data.value?.used_pct ?? 0;
  const color = usePctColor(pct);
  return {
    background: `conic-gradient(${color} ${pct * 3.6}deg, rgba(127,127,127,0.15) 0deg)`,
  };
});

const statusTotal = computed(() => {
  const s = data.value?.status;
  if (!s) return 0;
  return s.online + s.offline + s.unknown;
});

function go(name: string, params?: Record<string, string>) {
  router.push({ name, params }).catch(() => {});
}

onMounted(load);
</script>

<template>
  <n-spin :show="loading">
    <n-space v-if="data" vertical :size="16">
      <!-- KPI 列 -->
      <n-space :size="16" wrap>
        <n-card style="min-width: 200px"><n-statistic label="Sections" :value="data.sections" /></n-card>
        <n-card style="min-width: 200px"><n-statistic label="Subnets" :value="data.subnets" /></n-card>
        <n-card style="min-width: 200px"><n-statistic label="IPs allocated" :value="data.used" /></n-card>
        <n-card style="min-width: 200px">
          <n-statistic label="Total capacity" :value="data.total_capacity" />
        </n-card>
        <n-card style="min-width: 200px">
          <n-statistic label="Audit (24h)" :value="data.audit_24h" />
        </n-card>
      </n-space>

      <n-space :size="16" wrap align="stretch">
        <!-- Donut 使用率 -->
        <n-card title="IP usage" style="min-width: 280px">
          <n-space vertical align="center">
            <div class="donut" :style="donutStyle">
              <div class="donut-hole">
                <div class="donut-pct">{{ data.used_pct }}%</div>
                <div class="donut-sub">{{ data.used }} / {{ data.total_capacity }}</div>
              </div>
            </div>
          </n-space>
        </n-card>

        <!-- 狀態指示燈 -->
        <n-card title="IP 指示計（real-time status）" style="min-width: 360px; flex: 1">
          <n-space vertical :size="16">
            <div class="indicator-row">
              <span class="dot dot-on"></span>
              <span class="indicator-label">Online</span>
              <span class="indicator-value">{{ data.status.online }}</span>
            </div>
            <div class="indicator-row">
              <span class="dot dot-off"></span>
              <span class="indicator-label">Offline</span>
              <span class="indicator-value">{{ data.status.offline }}</span>
            </div>
            <div class="indicator-row">
              <span class="dot dot-unknown"></span>
              <span class="indicator-label">Unknown</span>
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
              來源：自家 scanner + LibreNMS（Phase 2）綜合判定
            </p>
          </n-space>
        </n-card>
      </n-space>

      <!-- Top fullest subnets -->
      <n-card title="Top fullest subnets">
        <n-space vertical :size="8">
          <div
            v-for="row in data.top_full_subnets"
            :key="row.subnet_id"
            class="row-line"
            @click="go('subnet-detail', { id: row.subnet_id })"
          >
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
            沒有 subnet 資料；請先建立 Section + Subnet 並配發 IP。
          </n-alert>
        </n-space>
      </n-card>

      <!-- Section heat -->
      <n-card title="Section heat">
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
            <div class="row-num">{{ row.subnet_count }} subnets · {{ row.used }} / {{ row.total_hosts }}</div>
          </div>
        </n-space>
      </n-card>
    </n-space>
  </n-spin>
</template>

<style scoped>
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
  grid-template-columns: 180px 1fr 200px;
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
</style>
