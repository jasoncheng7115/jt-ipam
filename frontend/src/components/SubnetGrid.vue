<script setup lang="ts">
/**
 * Subnet 視覺方塊圖（phpIPAM 招牌）。
 *
 * 顯示子網內每個 host 的 1×1 cell；顏色代表狀態。
 * 大網段（/16+）採聚合：每 cell = 256 個 host，顯示已用比率。
 *
 * Props:
 *   cidr        — 子網 CIDR（例 "192.168.1.0/24"）
 *   addresses   — 此網段的 IP 物件清單（subnet_id 已篩過）
 */
import { computed } from "vue";
import { NEmpty } from "naive-ui";
import type { IPAddress } from "@/types";

interface Props {
  cidr: string;
  addresses: IPAddress[];
}
const props = defineProps<Props>();

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
}

const parsed = computed<ParsedCidr>(() => parseCidrV4(props.cidr));

const isV6 = computed(() => props.cidr.includes(":"));

// 取得每個 host 的 cell（IPv4 only Phase 1，且最多 4096 cell 直接展開；超過聚合）
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
      out.push({ ip, state: st, hostname: a.hostname });
    } else {
      out.push({ ip, state: "free", hostname: null });
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

  // 把網段切成 256 個一組（/24-class blocks）；每個 cell = 256 host
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

  const out: AggCell[] = [];
  for (let i = 0; i < blocks; i++) {
    const startInt = p.base + i * 256;
    const endInt = Math.min(startInt + 255, p.base + total - 1);
    const used = idx.get(i) ?? 0;
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

function cellColor(state: Cell["state"]): string {
  switch (state) {
    case "active":
      return "var(--jt-cell-active, #22c55e)";
    case "reserved":
      return "var(--jt-cell-reserved, #3b82f6)";
    case "offline":
      return "var(--jt-cell-offline, #ef4444)";
    case "dhcp":
      return "var(--jt-cell-dhcp, #f59e0b)";
    case "used":
      return "var(--jt-cell-used, #6b7280)";
    case "free":
    default:
      return "var(--jt-cell-free, rgba(127,127,127,0.16))";
  }
}

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
      description="IPv6 / 非標準 CIDR 視覺方塊圖將於 Phase 2 補上"
    />
    <div v-else-if="directCells" class="grid grid-direct">
      <span
        v-for="c in directCells"
        :key="c.ip"
        class="cell"
        :title="`${c.ip}${c.hostname ? ' · ' + c.hostname : ''} · ${c.state}`"
        :style="{ background: cellColor(c.state) }"
      ></span>
    </div>
    <div v-else-if="aggregated" class="grid grid-agg">
      <div
        v-for="(c, i) in aggregated"
        :key="i"
        class="agg-cell"
        :title="`${c.range} · ${c.used}/${c.total} (${c.pct}%)`"
        :style="{ background: aggColor(c.pct) }"
      >
        <span class="agg-pct">{{ c.pct }}%</span>
      </div>
    </div>
    <div class="legend">
      <span class="legend-item"><i :style="{ background: 'var(--jt-cell-active, #22c55e)' }"></i>active</span>
      <span class="legend-item"><i :style="{ background: 'var(--jt-cell-reserved, #3b82f6)' }"></i>reserved</span>
      <span class="legend-item"><i :style="{ background: 'var(--jt-cell-dhcp, #f59e0b)' }"></i>dhcp</span>
      <span class="legend-item"><i :style="{ background: 'var(--jt-cell-offline, #ef4444)' }"></i>offline</span>
      <span class="legend-item"><i :style="{ background: 'var(--jt-cell-free, rgba(127,127,127,0.16))', border: '1px solid rgba(127,127,127,0.4)' }"></i>free</span>
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
  transition: transform 0.08s ease;
}
.cell:hover {
  transform: scale(1.4);
  z-index: 1;
}
.grid-agg {
  grid-template-columns: repeat(auto-fill, 56px);
}
.agg-cell {
  width: 56px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 3px;
  color: white;
  font-size: 11px;
  font-weight: 600;
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
