<script setup lang="ts">
/**
 * Subnet 視覺方塊圖 (phpIPAM 招牌)。
 *
 * 顯示子網路內每個 host 的 1×1 cell；顏色代表狀態。
 * 大網段 (/16+) 採聚合：每 cell = 256 個 host，顯示已用比率。
 *
 * Props:
 *   cidr        — 子網路 CIDR(例 "192.168.1.0/24")
 *   addresses   — 此網段的 IP 物件清單 (subnet_id 已篩過)
 */
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import { NEmpty } from "naive-ui";
import type { IPAddress } from "@/types";
import { classifyAddressLiveness } from "@/composables/useLivenessSettings";

const { t } = useI18n();

// 自製 tooltip — 原生 title 屬性會有 500-1500ms 延遲，這個 0 延遲
const tip = ref<{ x: number; y: number; text: string } | null>(null);

// 依游標 / viewport 算出不會超出螢幕的 (x, y)；tip 估寬度為 text*7 + padding
function _clampTipPos(ev: MouseEvent, text: string): { x: number; y: number } {
  const approxW = Math.min(Math.max(text.length * 7 + 16, 60), 360);
  const approxH = 24;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  // 預設游標右下方；超出右邊就移到游標左方
  let x = ev.clientX + 12;
  let y = ev.clientY + 16;
  if (x + approxW > vw - 8) x = Math.max(8, ev.clientX - 12 - approxW);
  if (y + approxH > vh - 8) y = Math.max(8, ev.clientY - 8 - approxH);
  return { x, y };
}

function showTip(ev: MouseEvent, text: string) {
  const { x, y } = _clampTipPos(ev, text);
  tip.value = { x, y, text };
}
function moveTip(ev: MouseEvent) {
  if (tip.value) {
    const { x, y } = _clampTipPos(ev, tip.value.text);
    tip.value.x = x;
    tip.value.y = y;
  }
}
function hideTip() { tip.value = null; }

interface Props {
  cidr: string;
  addresses: IPAddress[];
}
const props = defineProps<Props>();

const emit = defineEmits<{
  (e: "open-ip", address: IPAddress): void;
  (e: "create-ip", ip: string): void;
}>();

interface ParsedCidr {
  ok: boolean;
  base: number; // network address as bigint-like (we use number with care)
  prefixlen: number;
  hostCount: number;
  isV4: boolean;
}

function parseCidrV4(cidr: string): ParsedCidr {
  const m = /^(\d+)\.(\d+)\.(\d+)\.(\d+)\/(\d+)$/.exec(cidr);
  if (!m) return { ok: false, base: 0, prefixlen: 0, hostCount: 0, isV4: true };
  const [, a, b, c, d, p] = m;
  const base =
    (Number(a) << 24) | (Number(b) << 16) | (Number(c) << 8) | Number(d);
  const prefixlen = Number(p);
  const total = prefixlen >= 32 ? 1 : 2 ** (32 - prefixlen);
  // /31, /32 不扣 network/broadcast；其餘扣 2
  const hostCount = prefixlen >= 31 ? total : Math.max(total - 2, 0);
  // base 已可能含 host bits，正規化為 network
  const mask =
    prefixlen === 0 ? 0 : ~0 << (32 - prefixlen); // signed; mask high bits
  return {
    ok: true,
    base: (base & mask) >>> 0, // unsigned
    prefixlen,
    hostCount,
    isV4: true,
  };
}

function intToIpV4(n: number): string {
  return [
    (n >>> 24) & 0xff,
    (n >>> 16) & 0xff,
    (n >>> 8) & 0xff,
    n & 0xff,
  ].join(".");
}

interface Cell {
  ip: string;
  state: "active" | "reserved" | "offline" | "dhcp" | "used" | "free";
  hostname: string | null;
  // 完整 IPAddress(給 live color 邏輯用)
  addr: IPAddress | null;
}

const parsed = computed<ParsedCidr>(() => parseCidrV4(props.cidr));

const isV6 = computed(() => props.cidr.includes(":"));

// 取得每個 host 的 cell(IPv4 only Phase 1，且最多 4096 cell 直接展開；超過聚合)
const RAW_CELL_LIMIT = 4096;

const directCells = computed<Cell[] | null>(() => {
  const p = parsed.value;
  if (!p.ok || isV6.value) return null;
  if (p.hostCount > RAW_CELL_LIMIT) return null;

  // 建 ip → IPAddress 索引
  const idx: Record<string, IPAddress> = {};
  for (const a of props.addresses) idx[a.ip] = a;

  // 計算 cell 範圍：/31、/32 含 network/broadcast；其餘從 +1 到 -1
  const total = p.prefixlen >= 32 ? 1 : 2 ** (32 - p.prefixlen);
  let start = p.base;
  let end = p.base + total - 1;
  if (p.prefixlen < 31) {
    start = p.base + 1;
    end = p.base + total - 2;
  }

  const out: Cell[] = [];
  for (let i = start; i <= end; i++) {
    const ip = intToIpV4(i);
    const a = idx[ip];
    if (a) {
      const st = (a.state || "used") as Cell["state"];
      out.push({ ip, state: st, hostname: a.hostname, addr: a });
    } else {
      out.push({ ip, state: "free", hostname: null, addr: null });
    }
  }
  return out;
});

interface AggCell {
  range: string;
  total: number;
  used: number;
  pct: number;
}

const aggregated = computed<AggCell[] | null>(() => {
  if (directCells.value !== null) return null;
  const p = parsed.value;
  if (!p.ok || isV6.value) return null;

  // 把網段切成 256 個一組 (/24-class blocks)；每個 cell = 256 host
  const total = p.prefixlen >= 32 ? 1 : 2 ** (32 - p.prefixlen);
  const blocks = Math.ceil(total / 256);
  const idx = new Map<number, number>(); // block index → used count
  for (const a of props.addresses) {
    const m = /^(\d+)\.(\d+)\.(\d+)\.(\d+)$/.exec(a.ip);
    if (!m) continue;
    const ipInt =
      (Number(m[1]) << 24) | (Number(m[2]) << 16) | (Number(m[3]) << 8) | Number(m[4]);
    const offset = ipInt - p.base;
    if (offset < 0 || offset >= total) continue;
    const blockIdx = Math.floor(offset / 256);
    idx.set(blockIdx, (idx.get(blockIdx) ?? 0) + 1);
  }

  // 大網段（如 /16）若把所有 /24 區塊都畫出來，會是一整片「0%」的空白方塊，沒意義。
  // 只保留「有已登記位址」的區塊，讓指示計聚焦在實際有在用的範圍。
  const out: AggCell[] = [];
  for (let i = 0; i < blocks; i++) {
    const used = idx.get(i) ?? 0;
    if (used === 0) continue;
    const startInt = p.base + i * 256;
    const endInt = Math.min(startInt + 255, p.base + total - 1);
    const blockTotal = endInt - startInt + 1;
    out.push({
      range: `${intToIpV4(startInt)} – ${intToIpV4(endInt)}`,
      total: blockTotal,
      used,
      pct: blockTotal ? Math.round((used / blockTotal) * 100) : 0,
    });
  }
  return out;
});

// 回傳 { color, kind } — kind 用來決定 cell 額外樣式 (free 走空心 dashed)
function cellStyle(cell: Cell): { background: string; kind: "filled" | "free" } {
  if (cell.state === "free" || !cell.addr) {
    return { background: "transparent", kind: "free" };
  }
  if (cell.state === "reserved")
    return { background: "var(--jt-cell-reserved, #3b82f6)", kind: "filled" };
  if (cell.state === "dhcp")
    return { background: "var(--jt-cell-dhcp, #f59e0b)", kind: "filled" };

  const kind = classifyAddressLiveness(cell.addr);
  const colorMap = {
    online: "var(--jt-cell-active, #22c55e)",
    stale: "var(--jt-cell-dhcp, #f59e0b)",
    offline: "var(--jt-cell-offline, #ef4444)",
    unknown: "var(--jt-cell-unknown, #6b7280)",
  };
  return { background: colorMap[kind], kind: "filled" };
}

// tooltip 用的狀態標籤：要跟 cell 顏色一致 (active 的紅點代表離線，不是 active)
function cellStatusLabel(cell: Cell): string {
  if (cell.state === "free" || !cell.addr) return t("visualisation.free");
  if (cell.state === "reserved") return t("addresses.state_reserved");
  if (cell.state === "dhcp") return t("addresses.state_dhcp");
  const kind = classifyAddressLiveness(cell.addr);
  const map: Record<string, string> = {
    online: t("addresses.effective_online"),
    stale: t("addresses.effective_stale"),
    offline: t("addresses.effective_offline"),
    unknown: t("addresses.effective_unknown"),
  };
  return map[kind] ?? kind;
}

// 圖例筆數：依 cell 顏色分類統計 (dhcp 併入 stale，未登記＝free)
const legendCounts = computed(() => {
  const c = { online: 0, stale: 0, offline: 0, reserved: 0, unknown: 0, free: 0 };
  for (const a of props.addresses) {
    const st = a.state || "used";
    if (st === "reserved") { c.reserved++; continue; }
    if (st === "dhcp") { c.stale++; continue; }
    c[classifyAddressLiveness(a)]++;
  }
  const total = parsed.value.ok && !isV6.value ? parsed.value.hostCount : props.addresses.length;
  c.free = Math.max(total - props.addresses.length, 0);
  return c;
});

function aggColor(pct: number): string {
  // 0..100 → 由淺到深綠
  if (pct === 0) return "var(--jt-cell-free, rgba(127,127,127,0.16))";
  if (pct < 50) return "#22c55e";
  if (pct < 85) return "#f59e0b";
  return "#ef4444";
}
</script>

<template>
  <div class="subnet-grid">
    <n-empty
      v-if="isV6 || !parsed.ok"
      :description="t('visualisation.ipv6_pending')"
    />
    <div v-else-if="directCells" class="grid grid-direct">
      <span
        v-for="c in directCells"
        :key="c.ip"
        class="cell"
        :class="cellStyle(c).kind === 'free' ? 'cell-free' : 'cell-filled'"
        :style="{ background: cellStyle(c).background }"
        @mouseenter="(e) => showTip(e, `${c.ip}${c.hostname ? ' · ' + c.hostname : ''} · ${cellStatusLabel(c)}`)"
        @mousemove="moveTip"
        @mouseleave="hideTip"
        @click="() => {
          const a = addresses.find((x) => x.ip === c.ip);
          if (a) emit('open-ip', a);
          else emit('create-ip', c.ip);
        }"
      ></span>
    </div>
    <template v-else-if="aggregated && aggregated.length">
      <div class="agg-hint">{{ t("visualisation.agg_hint") }}</div>
      <div class="grid grid-agg">
        <div
          v-for="(c, i) in aggregated"
          :key="i"
          class="agg-cell"
          :style="{ background: aggColor(c.pct) }"
          @mouseenter="(e) => showTip(e, `${c.range} · ${c.used}/${c.total} (${c.pct}%)`)"
          @mousemove="moveTip"
          @mouseleave="hideTip"
        >
          <span class="agg-range">{{ c.range.split(' ')[0] }}</span>
          <span class="agg-pct">{{ c.used }} · {{ c.pct }}%</span>
        </div>
      </div>
    </template>
    <n-empty v-else-if="aggregated" :description="t('visualisation.empty')" />
    <!-- floating tooltip — fixed 定位、0 延遲 -->
    <Teleport to="body">
      <div
        v-if="tip"
        class="jt-cell-tip"
        :style="{ left: tip.x + 'px', top: tip.y + 'px' }"
      >{{ tip.text }}</div>
    </Teleport>
    <div class="legend">
      <span class="legend-item"><i :style="{ background: 'var(--jt-cell-active, #22c55e)' }"></i>{{ t("visualisation.online") }} ({{ legendCounts.online }})</span>
      <span class="legend-item"><i :style="{ background: 'var(--jt-cell-dhcp, #f59e0b)' }"></i>{{ t("visualisation.stale") }} ({{ legendCounts.stale }})</span>
      <span class="legend-item"><i :style="{ background: 'var(--jt-cell-offline, #ef4444)' }"></i>{{ t("visualisation.offline") }} ({{ legendCounts.offline }})</span>
      <span class="legend-item"><i :style="{ background: 'var(--jt-cell-reserved, #3b82f6)' }"></i>{{ t("visualisation.reserved") }} ({{ legendCounts.reserved }})</span>
      <span class="legend-item"><i :style="{ background: 'var(--jt-cell-unknown, rgba(127,127,127,0.45))' }"></i>{{ t("visualisation.unknown") }} ({{ legendCounts.unknown }})</span>
      <span class="legend-item"><i :style="{ background: 'var(--jt-cell-free, rgba(127,127,127,0.16))', border: '1px solid rgba(127,127,127,0.4)' }"></i>{{ t("visualisation.free") }} ({{ legendCounts.free }})</span>
    </div>
  </div>
</template>

<style scoped>
.subnet-grid {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.grid {
  display: grid;
  gap: 2px;
}
.grid-direct {
  grid-template-columns: repeat(auto-fill, 14px);
}
.cell {
  width: 14px;
  height: 14px;
  border-radius: 2px;
  cursor: pointer;
  transition: transform 0.08s ease, outline 0.05s ease;
  position: relative;
  outline: 0 solid transparent;
  box-sizing: border-box;
}
.cell.cell-free {
  /* 空格：dashed border，視覺權重明顯弱於 unknown 的實心灰 */
  border: 1px dashed rgba(127, 127, 127, 0.55);
  background: transparent !important;
}
.cell:hover {
  transform: scale(1.7);
  outline: 2px solid #ffffff;
  outline-offset: 1px;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.6), 0 4px 10px rgba(0, 0, 0, 0.5);
  z-index: 2;
}
.agg-hint {
  font-size: 12px;
  opacity: 0.65;
  margin-bottom: -4px;
}
.grid-agg {
  grid-template-columns: repeat(auto-fill, 116px);
}
.agg-cell {
  width: 116px;
  height: 40px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 1px;
  border-radius: 4px;
  color: white;
  cursor: pointer;
}
.agg-cell .agg-range {
  font-size: 11px;
  font-weight: 600;
  font-family: monospace;
}
.agg-cell .agg-pct {
  font-size: 10px;
  opacity: 0.9;
}
.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 12px;
  opacity: 0.85;
}
.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.legend-item i {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 2px;
}
</style>

<style>
/* 不 scoped — Teleport 出去的 div 不在 component 範圍內 */
.jt-cell-tip {
  position: fixed;
  z-index: 9999;
  background: rgba(0, 0, 0, 0.85);
  color: #fff;
  font-size: 12px;
  padding: 4px 10px;
  border-radius: 4px;
  pointer-events: none;
  white-space: nowrap;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", monospace;
}
</style>
