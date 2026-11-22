<template>
  <n-drawer :show="show" :width="drawerWidth" placement="right" @update:show="(v: boolean) => emit('update:show', v)">
    <n-drawer-content :title="t('scan_load.title', { name: agent?.name ?? '' })" closable>
      <n-spin v-if="loading && !data" :size="20" />
      <div v-else-if="!ev" class="sl-muted" data-testid="scan-load-empty">{{ t("scan_load.no_data") }}</div>
      <template v-else>
        <!-- 摘要：一輪耗時／週期、背景待辦 -->
        <div class="sl-summary" data-testid="scan-load-summary">
          <n-tag :type="levelType(ev.level)" :bordered="false" size="small">{{ t(`scan_load.level_${ev.level}`) }}</n-tag>
          <span>{{ t("scan_load.cycle_line", { d: fmtSec(ev.duration_s), i: fmtSec(ev.interval_s), pct: pct(ev.ratio) }) }}</span>
        </div>
        <n-progress type="line" :percentage="Math.min(100, pct(ev.ratio))" :status="progressStatus(ev.level)"
                    :show-indicator="false" style="margin: 6px 0 4px" />
        <div class="sl-muted">
          {{ t("scan_load.backlog_line", { n: ev.heavy_backlog }) }}
          <template v-if="data?.last_cycle?.at"> · {{ t("scan_load.last_at", { at: fmtDateTime(String(data.last_cycle.at)) }) }}</template>
        </div>

        <!-- 最近幾輪的耗時（虛線是週期） -->
        <div v-if="spark" class="sl-block">
          <div class="sl-h">{{ t("scan_load.trend", { n: data!.history.length }) }}</div>
          <svg :viewBox="`0 0 ${spark.w} ${spark.h}`" class="sl-spark" preserveAspectRatio="none" data-testid="scan-load-trend">
            <line :x1="0" :x2="spark.w" :y1="spark.iy" :y2="spark.iy" class="sl-spark__int" />
            <polyline :points="spark.points" class="sl-spark__line" />
          </svg>
        </div>

        <!-- 建議：要能直接照做 -->
        <div class="sl-block">
          <div class="sl-h">{{ t("scan_load.suggestions") }}</div>
          <div v-if="!ev.suggestions.length" class="sl-muted">{{ t("scan_load.no_suggestions") }}</div>
          <ul v-else class="sl-sugg" data-testid="scan-load-suggestions">
            <li v-for="(s, i) in ev.suggestions" :key="i">{{ suggestionText(s) }}</li>
          </ul>
          <n-button size="small" style="margin-top: 8px" @click="emit('create')">
            <template #icon><n-icon><PlusIcon /></n-icon></template>{{ t("scan_load.add_agent") }}
          </n-button>
        </div>

        <!-- 逐子網路：耗時、每個位址多久、是否被截斷；可以直接移到別的代理 -->
        <div class="sl-block">
          <div class="sl-h">{{ t("scan_load.subnets") }}</div>
          <n-data-table :columns="cols" :data="ev.subnets" size="small" :bordered="false" :scroll-x="640"
                        :row-key="(r: ScanLoadSubnet) => r.cidr" data-testid="scan-load-subnets" />
        </div>
      </template>
    </n-drawer-content>
  </n-drawer>
</template>

<script setup lang="ts">
import { computed, h, ref, watch } from "vue";
import {
  NButton, NDataTable, NDrawer, NDrawerContent, NIcon, NPopconfirm, NProgress, NSelect, NSpin, NTag,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { useI18n } from "vue-i18n";
import { apiErrMsg } from "@/api/client";
import { getScanAgentLoad, type ScanAgent, type ScanAgentLoad, type ScanLoadLevel, type ScanLoadSubnet,
         type ScanLoadSuggestion } from "@/api/phase3";
import { updateSubnet } from "@/api/subnets";
import { MoveIcon, PlusIcon, renderIcon } from "@/icons";
import { fmtDateTime } from "@/utils/datetime";
import { autoSort } from "@/composables/useTableSort";

const props = defineProps<{ show: boolean; agent: ScanAgent | null; agents: ScanAgent[] }>();
const emit = defineEmits<{ (e: "update:show", v: boolean): void; (e: "changed"): void; (e: "create"): void }>();
const { t } = useI18n();
const msg = useMessage();

const data = ref<ScanAgentLoad | null>(null);
const loading = ref(false);
const ev = computed(() => data.value?.evaluation ?? null);
const drawerWidth = computed(() => Math.min(760, window.innerWidth));
const moveTarget = ref<Record<string, string | null>>({});

const pct = (r: number) => Math.round(r * 100);
function fmtSec(s: number): string {
  return s >= 60 ? `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}` : `${Math.round(s * 10) / 10}s`;
}
function levelType(l: ScanLoadLevel) { return l === "overloaded" ? "error" : l === "busy" ? "warning" : "success"; }
function progressStatus(l: ScanLoadLevel) { return l === "overloaded" ? "error" : l === "busy" ? "warning" : "success"; }

/** 探索繞一圈的時間：不到 1 小時／幾小時／兩天以上改用天 */
function spanText(hours: number): string {
  if (hours < 1) return t("scan_load.span_under_hour");
  if (hours < 48) return t("scan_load.span_hours", { n: hours });
  return t("scan_load.span_days", { n: Math.round(hours / 24) });
}

function suggestionText(s: ScanLoadSuggestion): string {
  const p = s.params as Record<string, unknown>;
  if (s.code === "move_subnets") return t("scan_load.sg_move_subnets", { subnets: (p.subnets as string[]).join("、"), target: p.target });
  if (s.code === "discovery_pass") return t("scan_load.sg_discovery_pass", { ...p, span: spanText(Number(p.hours) || 0) } as Record<string, string | number>);
  return t(`scan_load.sg_${s.code}`, p as Record<string, string | number>);
}

/** 趨勢線：最近幾輪的耗時，高度以「週期」與「最長一輪」較大者為滿格 */
const spark = computed(() => {
  const hs = data.value?.history ?? [];
  if (hs.length < 2) return null;
  const w = 600, h = 80;
  const max = Math.max(...hs.map((x) => x.duration_s), ...hs.map((x) => x.interval_s), 1);
  const y = (v: number) => h - (v / max) * (h - 4) - 2;
  const points = hs.map((x, i) => `${(i / (hs.length - 1)) * w},${y(x.duration_s)}`).join(" ");
  return { w, h, points, iy: y(hs[hs.length - 1].interval_s) };
});

const otherAgents = computed(() => props.agents
  .filter((a) => a.id !== props.agent?.id && a.enabled)
  .map((a) => ({ label: a.name, value: a.id })));

async function move(r: ScanLoadSubnet) {
  const target = moveTarget.value[r.cidr];
  if (!r.subnet_id || !target) return;
  try {
    await updateSubnet(r.subnet_id, { scan_agent_id: target });
    msg.success(t("scan_load.moved", { cidr: r.cidr, agent: otherAgents.value.find((a) => a.value === target)?.label ?? "" }));
    emit("changed");
    await load();
  } catch (e) {
    msg.error(apiErrMsg(e));
  }
}

const cols = computed<DataTableColumns<ScanLoadSubnet>>(() => autoSort<ScanLoadSubnet>([
  { title: t("scan_load.col_cidr"), key: "cidr", width: 150,
    render: (r: ScanLoadSubnet) => {
      if (r.truncated) return h("span", null, [r.cidr, " ", h(NTag, { size: "tiny", type: "warning", bordered: false }, () => t("scan_load.truncated"))]);
      // 兩層掃描：已知位址每輪掃＋背景探索（代理 1.18.0 起）
      if (r.mode === "targets") return h("span", null, [r.cidr, " ", h(NTag, { size: "tiny", type: "success", bordered: false },
        () => ((r.rounds ?? 1) > 1
          ? t("scan_load.known_chunk", { known: r.known ?? 0, chunk: r.chunk ?? 1, rounds: r.rounds ?? 1 })
          : t("scan_load.known_tag", { known: r.known ?? 0 })))]);
      if ((r.rounds ?? 1) > 1) return h("span", null, [r.cidr, " ", h(NTag, { size: "tiny", type: "info", bordered: false },
        () => t("scan_load.chunk", { chunk: r.chunk ?? 1, rounds: r.rounds ?? 1 }))]);
      return r.cidr;
    } },
  { title: t("scan_load.col_hosts"), key: "hosts", width: 110,
    render: (r: ScanLoadSubnet) => (r.total_hosts && r.total_hosts > r.hosts ? `${r.hosts} / ${r.total_hosts}` : String(r.hosts)) },
  { title: t("scan_load.col_alive"), key: "alive", width: 70 },
  { title: t("scan_load.col_duration"), key: "duration_s", width: 80, render: (r: ScanLoadSubnet) => fmtSec(r.duration_s) },
  { title: t("scan_load.col_per_host"), key: "per_host_ms", width: 100,
    render: (r: ScanLoadSubnet) => (r.per_host_ms == null ? "—" : `${r.per_host_ms} ms`) },
  { title: t("scan_load.col_move"), key: "move", minWidth: 200, sorter: false,
    render: (r: ScanLoadSubnet) => (r.subnet_id && otherAgents.value.length
      ? h("div", { style: "display:flex;gap:6px;align-items:center" }, [
          h(NSelect, { size: "small", style: "min-width:120px", options: otherAgents.value,
                       placeholder: t("scan_load.move_to"), value: moveTarget.value[r.cidr] ?? null,
                       "onUpdate:value": (v: string) => { moveTarget.value = { ...moveTarget.value, [r.cidr]: v }; } }),
          h(NPopconfirm, { onPositiveClick: () => move(r) }, {
            trigger: () => h(NButton, { size: "small", disabled: !moveTarget.value[r.cidr] },
                             { icon: renderIcon(MoveIcon, 15), default: () => t("scan_load.move") }),
            default: () => t("scan_load.move_confirm", { cidr: r.cidr }),
          }),
        ])
      : "—") },
]));

async function load() {
  if (!props.agent) return;
  loading.value = true;
  try {
    data.value = await getScanAgentLoad(props.agent.id);
  } catch (e) {
    msg.error(apiErrMsg(e));
  } finally {
    loading.value = false;
  }
}

watch(() => [props.show, props.agent?.id], ([s]) => {
  if (s) { data.value = null; moveTarget.value = {}; void load(); }
}, { immediate: true });
</script>

<style scoped>
.sl-summary { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 14px; }
.sl-muted { font-size: 12.5px; opacity: .65; }
.sl-block { margin-top: 18px; }
.sl-h { font-weight: 600; font-size: 13px; margin-bottom: 6px; }
.sl-spark { width: 100%; height: 80px; display: block; }
.sl-spark__line { fill: none; stroke: #18a058; stroke-width: 2; vector-effect: non-scaling-stroke; }
.sl-spark__int { stroke: #d03050; stroke-width: 1; stroke-dasharray: 4 4; vector-effect: non-scaling-stroke; }
.sl-sugg { margin: 0; padding-left: 18px; font-size: 13px; line-height: 1.7; }
</style>
