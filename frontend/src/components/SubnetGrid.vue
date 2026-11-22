<script setup lang="ts">
/**
 * Subnet 視覺方塊圖 (phpIPAM 招牌)。
 *
 * 顯示子網路內每個 host 的 1×1 cell；顏色代表狀態。
 * 大網段 (/16+) 採聚合：每 cell = 256 個 host，顯示已用比率。
 *
 * Props:
 *   cidr        — 子網路 CIDR(例 "192.168.123.0/24")
 *   addresses   — 此網段的 IP 物件清單 (subnet_id 已篩過)
 */
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { NEmpty, NTooltip } from "naive-ui";
import type { IPAddress } from "@/types";
import type { UnmanagedAddress } from "@/api/addresses";
import { unmanagedSources } from "@/utils/unmanaged";
import { fmtRelative } from "@/utils/datetime";
import { RANGE_COLORS, type IPRange, type IPRangePurpose } from "@/api/ipRanges";
import { classifyAddressLiveness, onlineGraceMinutes } from "@/composables/useLivenessSettings";

const { t } = useI18n();

// 圖例 tooltip 用的存活門檻（與 classifyLiveness 一致：上線=grace、近期出現=grace~grace*4）
const graceMin = computed(() => onlineGraceMinutes.value || 30);
const staleMaxMin = computed(() => graceMin.value * 4);

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
  /** 子網路內的位址範圍（集區，issue #40）：範圍內的格子底下畫一條該用途的色線 */
  ranges?: IPRange[];
  /** 超大規模：`addresses` 只載入了一部分時，後端彙總的每個 /24 已用數（指示計改用它）與真正的總數 */
  blocks?: { start: string; used: number }[] | null;
  totalAddresses?: number;
  /** 沒有 IP 記錄、但最近看得到在用的位址：格子畫成「未納管」（不是閒置） */
  unmanaged?: UnmanagedAddress[];
  /** 格子邊長（px；卡片標題列的拉桿調整，使用者 2026-10-11）。預設 14 */
  cellSize?: number;
}
const props = defineProps<Props>();
const cellPx = computed(() => Math.min(40, Math.max(8, Math.round(props.cellSize ?? 14))));
/** 位址範圍的底線：畫在背景裡的硬切色帶（底線、1px 分隔線，上面是格子原本的顏色）。
 *  以前用內陰影：內陰影的內緣會跟著格子的圓角彎，色塊在底線上方多出圓角，看起來像有兩個下緣
 *  （使用者 2026-10-13）。粗細跟著格子大小；顏色依主題（App.vue 的 --jt-range-*，淺色主題換深色才看得清楚），
 *  分隔線讓底線壓在綠色、紅色的格子上也分得出來（使用者 2026-10-11：DHCP 的藍在淺色主題不明顯） */
function rangeLayer(purpose: IPRangePurpose, size = cellPx.value): string {
  const w = Math.max(3, Math.round(size * 0.22));
  return `linear-gradient(to top, var(--jt-range-${purpose}, ${RANGE_COLORS[purpose]}) 0 ${w}px, `
    + `var(--jt-range-sep, transparent) ${w}px ${w + 1}px, transparent ${w + 1}px)`;
}
/** 格子的行內樣式：原本的顏色＋（在範圍內的話）底線那一層。顏色是漸層（自動收錄的半格紫）時疊成兩層。 */
function cellInlineStyle(c: Cell): Record<string, string> {
  const base = cellStyle(c).background;
  const r = rangeOf(c.ip);
  if (!r) return { background: base };
  const isGradient = base.includes("gradient(");
  return { backgroundImage: isGradient ? `${rangeLayer(r.purpose)}, ${base}` : rangeLayer(r.purpose),
           backgroundColor: isGradient ? "transparent" : base };
}

/** 圖例跟著格子大小放大（使用者 2026-10-13）：比預設（14px）小時維持原本的 10px 色塊、12px 字 */
const legendVars = computed(() => {
  const k = Math.max(1, cellPx.value / 14);
  return { "--lg-sw": `${Math.round(10 * k)}px`, "--lg-font": `${Math.round(12 * (1 + (k - 1) * 0.5))}px`,
           "--lg-gap": `${Math.round(12 * Math.sqrt(k))}px` };
});
const legendSw = computed(() => Math.round(10 * Math.max(1, cellPx.value / 14)));

/** 點圖例只看那一類（使用者 2026-10-13）：其他類型的圖例與格子變很淡，再點一次（或 Esc）恢復。
 *  鍵盤也能點；選中的那一個用 aria-pressed＋明顯的外框標出來，免得使用者忘了自己點過、以為格子壞了。 */
const focusKind = ref<string | null>(null);
watch(() => props.cidr, () => { focusKind.value = null; });
function toggleFocus(k: string) { focusKind.value = focusKind.value === k ? null : k; }
function legendAttrs(k: string): Record<string, unknown> {
  if (!directCells.value) return {};
  const on = focusKind.value === k;
  return {
    "data-legend": k, role: "button", tabindex: 0, "aria-pressed": String(on),
    class: { "legend-clickable": true, "legend-active": on, "legend-dim": !!focusKind.value && !on },
    onClick: () => toggleFocus(k),
    onKeydown: (e: KeyboardEvent) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleFocus(k); }
      else if (e.key === "Escape") focusKind.value = null;
    },
  };
}
/** 格子屬不屬於圖例的某一類（分類規則與 legendCounts 相同） */
function cellMatches(c: Cell, k: string): boolean {
  if (k.startsWith("range:")) return rangeOf(c.ip)?.purpose === k.slice(6);
  if (k === "unmanaged") return c.state === "unmanaged";
  if (k === "free") return c.state !== "unmanaged" && (c.state === "free" || !c.addr);
  if (k === "auto") return isAutoAdded(c.addr);
  if (!c.addr || c.state === "unmanaged" || c.state === "free") return false;
  const st = c.state || "used";
  if (st === "reserved") return k === "reserved";
  if (st === "dhcp") return k === "stale";
  return classifyAddressLiveness(c.addr) === k;
}

// 未納管格子的說明：誰看到的、多久以前、MAC（廠商）
function umTip(u: UnmanagedAddress): string {
  const parts = [unmanagedSources(t, u.sources), fmtRelative(u.last_seen_at)];
  if (u.mac) parts.push(u.vendor ? `${u.mac} (${u.vendor})` : u.mac);
  return " · " + parts.filter(Boolean).join(" · ");
}
function umOld(u: UnmanagedAddress): boolean {
  return !!u.last_seen_at && Date.now() - new Date(u.last_seen_at).getTime() > 24 * 3600 * 1000;
}

/** 範圍換成整數區間（位址圖只畫 IPv4） */
const rangeSpans = computed(() => (props.ranges ?? []).flatMap((r) =>
  // 自動範圍被 DHCP 排除區間切成好幾段時照段畫：排除的位址不是 DHCP 範圍
  (r.segments?.length ? r.segments : [[r.start_ip, r.end_ip]]).flatMap(([s, e]) => {
    const a = ipToInt(s), b = ipToInt(e);
    return a == null || b == null ? [] : [{ a, b, r }];
  })));
function ipToInt(ip: string): number | null {
  const m = /^(\d+)\.(\d+)\.(\d+)\.(\d+)$/.exec(String(ip).split("/")[0].trim());
  if (!m) return null;
  return ((+m[1] << 24) >>> 0) + (+m[2] << 16) + (+m[3] << 8) + +m[4];
}
function rangeOf(ip: string): IPRange | null {
  const n = ipToInt(ip);
  if (n == null) return null;
  return rangeSpans.value.find((x) => n >= x.a && n <= x.b)?.r ?? null;
}
function rangeTip(ip: string): string {
  const r = rangeOf(ip);
  return r ? ` · ${t(`ranges.purpose_${r.purpose}`)}${r.name ? `（${r.name}）` : ""}` : "";
}
/** 圖例只列這個子網路真的有的用途 */
const rangePurposes = computed(() =>
  [...new Set((props.ranges ?? []).map((r) => r.purpose))] as IPRangePurpose[]);

const emit = defineEmits<{
  (e: "open-ip", address: IPAddress): void;
  (e: "create-ip", ip: string): void;
  /** 未納管的格子：先進到這個位址的頁面（沒有記錄，但可以從那裡探測或登錄） */
  (e: "open-unmanaged", ip: string): void;
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
  state: "active" | "reserved" | "offline" | "dhcp" | "used" | "free" | "unmanaged";
  hostname: string | null;
  // 完整 IPAddress(給 live color 邏輯用)
  addr: IPAddress | null;
  /** 沒有 IP 記錄、但看得到在用（掃描代理目擊／ARP） */
  um?: UnmanagedAddress;
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
  const umIdx: Record<string, UnmanagedAddress> = {};
  for (const u of props.unmanaged ?? []) umIdx[u.ip] = u;

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
    } else if (umIdx[ip]) {
      out.push({ ip, state: "unmanaged", hostname: umIdx[ip].hostname, addr: null, um: umIdx[ip] });
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
  // 位址只載入了一部分：用後端彙總的數字，不然 /16 只會剩幾格
  for (const b of props.blocks ?? []) {
    const m = /^(\d+)\.(\d+)\.(\d+)\.(\d+)$/.exec(b.start);
    if (!m) continue;
    const off = ((Number(m[1]) << 24) | (Number(m[2]) << 16) | (Number(m[3]) << 8) | Number(m[4])) - p.base;
    if (off >= 0 && off < total) idx.set(Math.floor(off / 256), b.used);
  }
  for (const a of props.blocks ? [] : props.addresses) {
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

// 由整合／掃描代理自動建進來、沒有人登記過的位址。狀態照舊（可能上線也可能離線），
// 但要能一眼認出「這不是有人登記的」—— 它一旦進了 IPAM 就不會再被「未授權 IP」
// 偵測列出來，等於私接的機器會安靜地變成正式紀錄。清單上是紫色 icon，格子圖是半格紫。
// 來源清單要跟 IpRoleTags 一致：以前少了 librenms_arp，清單有標記、格子圖卻沒有。
const AUTO_SOURCES = ["opnsense", "pfsense", "proxmox", "vmware", "hyperv", "openstack", "scanner", "librenms_arp", "snmp_arp"];
function isAutoAdded(a: IPAddress | null): boolean {
  return !!a && AUTO_SOURCES.includes(String((a as any).discovery_source ?? ""));
}

// 回傳 { color, kind } — kind 用來決定 cell 額外樣式 (free 走空心 dashed)
function cellStyle(cell: Cell): { background: string; kind: "filled" | "free" | "unmanaged" } {
  if (cell.state === "unmanaged") return { background: "transparent", kind: "unmanaged" };
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
  // 自動收錄：對角雙色 —— 左上半紫（自動收錄）、右下半保留狀態色（上線/離線）。
  // 第一版橘框、第二版角標都「太小看不到」；第三版整格紫又看不出在不上線。
  // 各佔半格，兩個資訊都一眼可見；紫是調色盤唯一沒用過的顏色。
  if (isAutoAdded(cell.addr)) {
    return { background: `linear-gradient(135deg, var(--jt-cell-auto, #8b5cf6) 0 50%, ${colorMap[kind]} 50% 100%)`,
             kind: "filled" };
  }
  return { background: colorMap[kind], kind: "filled" };
}

// tooltip 用的狀態標籤：要跟 cell 顏色一致 (active 的紅點代表離線，不是 active)
function cellStatusLabel(cell: Cell): string {
  if (cell.state === "unmanaged") return t("visualisation.unmanaged");
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
// auto 是「疊在顏色上」的另一個維度（自動收錄的位址仍然有自己的上線／離線狀態），
// 所以它跟其它項目相加不會等於總數 —— 圖例文字要講清楚，不然看起來像算錯。
const legendCounts = computed(() => {
  const c = { online: 0, stale: 0, offline: 0, reserved: 0, unknown: 0, free: 0, auto: 0, unmanaged: 0 };
  for (const a of props.addresses) {
    if (isAutoAdded(a)) c.auto++;
    const st = a.state || "used";
    if (st === "reserved") { c.reserved++; continue; }
    if (st === "dhcp") { c.stale++; continue; }
    c[classifyAddressLiveness(a)]++;
  }
  const total = parsed.value.ok && !isV6.value ? parsed.value.hostCount : props.addresses.length;
  c.unmanaged = (props.unmanaged ?? []).length;
  // 未納管的位址沒有 IP 記錄，以前算在閒置裡；現在分開算
  c.free = Math.max(total - (props.totalAddresses ?? props.addresses.length) - c.unmanaged, 0);
  return c;
});
// 狀態統計只能依已載入的那些算（上線與否由前端依多個時間欄位判斷）：講清楚
const partial = computed(() => (props.totalAddresses ?? 0) > props.addresses.length);

function aggColor(pct: number): string {
  // 0..100 → 由淺到深綠
  if (pct === 0) return "var(--jt-cell-free, rgba(127,127,127,0.16))";
  if (pct < 50) return "#22c55e";
  if (pct < 85) return "#f59e0b";
  return "#ef4444";
}
</script>

<template>
  <div class="subnet-grid" :style="{ '--cell': cellPx + 'px' }" data-testid="subnet-grid">
    <n-empty
      v-if="isV6 || !parsed.ok"
      :description="t('visualisation.ipv6_pending')"
    />
    <div v-else-if="directCells" class="grid grid-direct">
      <span
        v-for="c in directCells"
        :key="c.ip"
        class="cell"
        :class="[
          cellStyle(c).kind === 'free' ? 'cell-free' : cellStyle(c).kind === 'unmanaged' ? 'cell-unmanaged' : 'cell-filled',
          isAutoAdded(c.addr) ? 'cell-auto' : '',
          c.um && umOld(c.um) ? 'cell-unmanaged-old' : '',
          focusKind ? (cellMatches(c, focusKind) ? 'cell-focus' : 'cell-dim') : '',
        ]"
        :data-state="c.state"
        :style="cellInlineStyle(c)"
        :data-range="rangeOf(c.ip)?.purpose"
        @mouseenter="(e) => showTip(e, `${c.ip}${c.hostname ? ' · ' + c.hostname : ''} · ${cellStatusLabel(c)}`
          + (isAutoAdded(c.addr) ? ` · ${t('visualisation.auto_added')}` : '') + (c.um ? umTip(c.um) : '') + rangeTip(c.ip))"
        @mousemove="moveTip"
        @mouseleave="hideTip"
        @click="() => {
          const a = addresses.find((x) => x.ip === c.ip);
          if (a) emit('open-ip', a);
          else if (c.state === 'unmanaged') emit('open-unmanaged', c.ip);
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
    <div v-if="partial" class="agg-hint" data-testid="grid-legend-partial">
      {{ t("visualisation.legend_partial", { n: addresses.length.toLocaleString(), total: (totalAddresses ?? 0).toLocaleString() }) }}
    </div>
    <div class="legend" :style="legendVars">
      <n-tooltip><template #trigger><span class="legend-item" v-bind="legendAttrs('online')"><i :style="{ background: 'var(--jt-cell-active, #22c55e)' }"></i>{{ t("visualisation.online") }} ({{ legendCounts.online }})</span></template>{{ t("visualisation.tip_online", { grace: graceMin }) }}<div v-if="directCells" class="legend-tip-hint">{{ t("visualisation.legend_click_hint") }}</div></n-tooltip>
      <n-tooltip><template #trigger><span class="legend-item" v-bind="legendAttrs('stale')"><i :style="{ background: 'var(--jt-cell-dhcp, #f59e0b)' }"></i>{{ t("visualisation.stale") }} ({{ legendCounts.stale }})</span></template>{{ t("visualisation.tip_stale", { grace: graceMin, staleMax: staleMaxMin }) }}<div v-if="directCells" class="legend-tip-hint">{{ t("visualisation.legend_click_hint") }}</div></n-tooltip>
      <n-tooltip><template #trigger><span class="legend-item" v-bind="legendAttrs('offline')"><i :style="{ background: 'var(--jt-cell-offline, #ef4444)' }"></i>{{ t("visualisation.offline") }} ({{ legendCounts.offline }})</span></template>{{ t("visualisation.tip_offline", { staleMax: staleMaxMin }) }}<div v-if="directCells" class="legend-tip-hint">{{ t("visualisation.legend_click_hint") }}</div></n-tooltip>
      <n-tooltip><template #trigger><span class="legend-item" v-bind="legendAttrs('reserved')"><i :style="{ background: 'var(--jt-cell-reserved, #3b82f6)' }"></i>{{ t("visualisation.reserved") }} ({{ legendCounts.reserved }})</span></template>{{ t("visualisation.tip_reserved") }}<div v-if="directCells" class="legend-tip-hint">{{ t("visualisation.legend_click_hint") }}</div></n-tooltip>
      <n-tooltip><template #trigger><span class="legend-item" v-bind="legendAttrs('unknown')"><i :style="{ background: 'var(--jt-cell-unknown, rgba(127,127,127,0.45))' }"></i>{{ t("visualisation.unknown") }} ({{ legendCounts.unknown }})</span></template>{{ t("visualisation.tip_unknown") }}<div v-if="directCells" class="legend-tip-hint">{{ t("visualisation.legend_click_hint") }}</div></n-tooltip>
      <n-tooltip><template #trigger><span class="legend-item" v-bind="legendAttrs('auto')"><i :style="{ background: 'linear-gradient(135deg, var(--jt-cell-auto, #8b5cf6) 0 50%, var(--jt-cell-active, #22c55e) 50% 100%)' }"></i>{{ t("visualisation.auto_added") }} ({{ legendCounts.auto }})</span></template>{{ t("visualisation.tip_auto_added") }}<div v-if="directCells" class="legend-tip-hint">{{ t("visualisation.legend_click_hint") }}</div></n-tooltip>
      <n-tooltip><template #trigger><span class="legend-item" v-bind="legendAttrs('unmanaged')" data-testid="grid-legend-unmanaged"><i class="legend-unmanaged"></i>{{ t("visualisation.unmanaged") }} ({{ legendCounts.unmanaged }})</span></template>{{ t("visualisation.tip_unmanaged") }}<div v-if="directCells" class="legend-tip-hint">{{ t("visualisation.legend_click_hint") }}</div></n-tooltip>
      <n-tooltip><template #trigger><span class="legend-item" v-bind="legendAttrs('free')"><i :style="{ background: 'var(--jt-cell-free, rgba(127,127,127,0.16))', border: '1px solid rgba(127,127,127,0.4)' }"></i>{{ t("visualisation.free") }} ({{ legendCounts.free }})</span></template>{{ t("visualisation.tip_free") }}<div v-if="directCells" class="legend-tip-hint">{{ t("visualisation.legend_click_hint") }}</div></n-tooltip>
      <span v-for="p in rangePurposes" :key="'rg-' + p" class="legend-item" v-bind="legendAttrs('range:' + p)">
        <i :style="{ backgroundImage: rangeLayer(p, legendSw), backgroundColor: 'var(--jt-cell-free, rgba(127,127,127,0.16))' }"></i>{{ t(`ranges.purpose_${p}`) }}
      </span>
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
  grid-template-columns: repeat(auto-fill, var(--cell, 14px));
}
.cell {
  width: var(--cell, 14px);
  height: var(--cell, 14px);
  border-radius: 2px;
  cursor: pointer;
  transition: transform 0.08s ease, outline 0.05s ease, opacity 0.15s ease;
  position: relative;
  outline: 0 solid transparent;
  box-sizing: border-box;
}
.cell.cell-free {
  /* 空格：dashed border，視覺權重明顯弱於 unknown 的實心灰。只蓋掉底色（background-color）：
     範圍的底線畫在 background-image，用 background 簡寫會連底線一起蓋掉 */
  border: 1px dashed rgba(127, 127, 127, 0.55);
  background-color: transparent !important;
}
/* 未納管：IPAM 沒有記錄、但看得到在用 —— 橘色虛線框＋淡橘底，跟閒置的灰色虛線分得出來 */
.cell.cell-unmanaged {
  border: 1.5px dashed #f59e0b;
  background-color: rgba(245, 158, 11, 0.16) !important;
}
/* 點了圖例的某一類：其他類型的格子變很淡（不是藏起來，位置還看得出來）。加灰階：淡化後原本的顏色
   仍是一片淺色塊，被選的類型（尤其是透明底的閒置格）反而不突出（使用者 2026-10-13） */
.cell.cell-dim { opacity: 0.12; filter: grayscale(1); }
/* 只看閒置時，閒置格改成實線框加底色：透明底的虛線框在淡色背景裡看不出來 */
.cell.cell-free.cell-focus {
  border: 1.5px solid var(--jt-cell-free-focus-border, #64748b);
  background-color: var(--jt-cell-free-focus, rgba(100, 116, 139, 0.32)) !important;
}
/* 超過一天沒再看到：淡一點（可能已經不在了） */
.cell.cell-unmanaged-old { opacity: 0.5; }
.legend-unmanaged { border: 1.5px dashed #f59e0b; background: rgba(245, 158, 11, 0.16) !important; box-sizing: border-box; }
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
  gap: var(--lg-gap, 12px);
  font-size: var(--lg-font, 12px);
}
.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  opacity: 0.85;
  padding: 1px 6px;
  border-radius: 4px;
  transition: opacity 0.12s ease;
}
.legend-item i {
  display: inline-block;
  width: var(--lg-sw, 10px);
  height: var(--lg-sw, 10px);
  border-radius: 2px;
  box-sizing: border-box;
}
.legend-clickable { cursor: pointer; user-select: none; }
.legend-clickable:hover { opacity: 1; background: rgba(127, 127, 127, 0.12); }
.legend-clickable:focus-visible { outline: 2px solid var(--primary-color, #18a058); outline-offset: 1px; }
.legend-item.legend-active { opacity: 1; font-weight: 600; box-shadow: inset 0 0 0 1.5px currentColor; }
.legend-item.legend-dim { opacity: 0.35; }
.legend-tip-hint { margin-top: 4px; font-size: 12px; opacity: 0.75; }
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
