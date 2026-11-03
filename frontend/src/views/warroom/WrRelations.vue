<script setup lang="ts">
/**
 * 09 IP 物件關係：一筆 IP 為焦點，三列畫出實體歸屬、虛擬化對應、位址歸屬（規格 SPEC.md 第 6 節）。
 * - 左窄欄：地點、機櫃、子網路、區段數；右欄：關聯待補（只算適用物件）、焦點來源與最後確認時間
 * - 一個物件一個節點；焦點 IP 是右邊的高方塊，每列最後一個物件以一條有名稱的邊接到它
 * - 線：實線＝來源直接回報或登記，虛線＝推導（名稱或位址比對），紫色虛線＝來源不一致待確認；圖例用文字標明
 * - 每條邊旁邊寫關係種類、來源與時間（電視不靠 hover）；IP、MAC、CIDR 不加省略號，放不下就換行
 * 版面與畫線的計算在 relationsLayout.ts（有單元測試）。
 */
import { computed, type Component } from "vue";
import { useI18n } from "vue-i18n";
import type { PageData } from "@/api/warroom";
import {
  AddressesIcon, DevicesIcon, InfoIcon, LocationsIcon, MacIcon, RacksIcon, SectionsIcon, SubnetsIcon,
  VirtualizationIcon, ElapsedIcon, AuditIcon, IntegrationsIcon, LinkIcon, WarnIcon,
} from "@/icons";
import { pad2 } from "@/utils/warroom";
import WrIco from "./WrIco.vue";
import WrName from "./WrName.vue";
import { usePageTransition } from "./paged";
import {
  CANVAS_H, CANVAS_W, IP_RECT, ROW_KINDS, ROW_TITLE, addrParts, layout, longTime, rowTop, shortTime,
  type REdge, type RNode, type RowKind,
} from "./relationsLayout";

const props = defineProps<{ data: PageData }>();
const pageTrans = usePageTransition();
const { t, te } = useI18n();

interface Row { kind: RowKind; state: string; more: number }
interface Src { source: string; at: string | null }
interface Focus {
  state: string; ips?: number;
  ip?: { key: string; id: string; ip: string; hostname: string | null; mac: string | null; subnet: string | null };
  rows?: Row[]; nodes?: RNode[]; edges?: REdge[]; conflicts?: string[];
  registered?: Src; sources?: Src[]; last_confirmed?: string | null;
}
interface Counts { state: string; locations?: number; racks?: number; subnets?: number; sections?: number }
interface Pending { state: string; vm_state?: string; [k: string]: number | string | null | undefined }

const f = computed(() => (props.data.focus as Focus) ?? { state: "empty" });
const counts = computed(() => (props.data.counts as Counts) ?? { state: "empty" });
const pending = computed(() => (props.data.pending as Pending) ?? { state: "empty" });
const now = computed(() => new Date(props.data.data_at));
const graph = computed(() => (f.value.state === "ok" ? layout(f.value.nodes ?? [], f.value.edges ?? []) : null));
const ipNode = computed(() => graph.value?.nodes.find((n) => !n.row) ?? null);
const rowsByKind = computed(() => Object.fromEntries((f.value.rows ?? []).map((r) => [r.kind, r])) as Record<RowKind, Row>);

const ROW_ICON = { physical: RacksIcon, virtual: VirtualizationIcon, address: SubnetsIcon } as const;
const TYPE_ICON: Record<string, Component> = {
  location: LocationsIcon, rack: RacksIcon, device: DevicesIcon, host: DevicesIcon, vm: VirtualizationIcon,
  nic: MacIcon, section: SectionsIcon, subnet: SubnetsIcon, ip: AddressesIcon,
};
const BRAND: Record<string, string> = {
  proxmox: "Proxmox VE", vmware: "VMware", "hyper-v": "Hyper-V", hyperv: "Hyper-V", openstack: "OpenStack",
  kvm: "KVM", xenserver: "XenServer", librenms: "LibreNMS", wazuh: "Wazuh", zabbix: "Zabbix", ocs: "OCS Inventory",
  opnsense: "OPNsense", pfsense: "pfSense", phpipam: "phpIPAM", isoinsight: "ISOinsight",
};
function srcName(s: string): string {
  if (BRAND[s]) return BRAND[s];
  const k = `warroom.rl_s_${s}`;
  return te(k) ? t(k) : t("warroom.dtype.other");
}
/** 邊上的來源名稱要短（間隙只有約 70px）：產品名稱用短的寫法 */
const SHORT_BRAND: Record<string, string> = { proxmox: "Proxmox", "hyper-v": "Hyper-V", hyperv: "Hyper-V" };
function edgeSrc(s: string): string {
  return SHORT_BRAND[s] ?? srcName(s);
}
function typeLabel(n: RNode): string {
  if (n.type === "vm") return `${t(n.sub === "ct" ? "warroom.rl_t_ct" : "warroom.rl_t_vm")} · ${edgeSrc(n.platform ?? "other")}`;
  if (n.type === "host") return t("warroom.rl_t_host");
  if (n.type === "device") {
    const k = `warroom.dtype.${n.dtype}`;
    const dt = te(k) ? t(k) : t("warroom.rl_t_device");
    return n.host ? t("warroom.rl_t_host") : dt;
  }
  return t(`warroom.rl_t_${n.type}`);
}
/** 方塊第三行：VM 不再重複 VM／CT、裝置不重複類型；網卡是 MAC（不省略） */
function subText(n: RNode): string | null {
  if (n.type === "vm" || (n.type === "device" && !n.host)) return null;
  return n.sub;
}
const MONO = new Set(["subnet", "nic"]);
function rowEmpty(r: Row | undefined, kind: RowKind): string {
  const st = r?.state ?? "none";
  if (st === "forbidden") return t("warroom.rl_row_forbidden");
  if (st === "not_configured") return t("warroom.rl_row_not_configured");
  if (st === "hidden") return t("warroom.rl_row_hidden");
  return t(`warroom.rl_none_${kind}`);
}
const COUNT_KEYS = ["locations", "racks", "subnets", "sections"] as const;
const PENDING = [
  { key: "ip_unlinked", tone: "lilac" }, { key: "vm_ip_unmapped", tone: "lilac" },
  { key: "vm_no_report", tone: "ink" }, { key: "device_no_place", tone: "lilac" }, { key: "conflicts", tone: "lilac" },
] as const;
function pendingNa(key: string): string {
  if (pending.value.state === "forbidden") return t("warroom.no_access_short");
  if (key.startsWith("vm_") && pending.value.vm_state === "not_configured") return t("warroom.rl_na_not_configured");
  return t("warroom.no_access_short");
}
const SRC_ROWS = 2;
</script>

<template>
  <div class="rl">
    <!-- 左窄欄：地點、機櫃、子網路、區段 -->
    <aside class="rl-left" data-testid="wr-rl-counts">
      <div v-for="k in COUNT_KEYS" :key="k" class="rl-count">
        <div v-if="counts.state === 'ok'" class="wr-big rl-count__v">{{ pad2(counts[k] ?? 0) }}</div>
        <div v-else class="rl-count__na">{{ t("warroom.no_access_short") }}</div>
        <div class="rl-count__l">{{ t(`warroom.rl_c_${k}`) }}</div>
      </div>
      <!-- 頁首「虛擬化物件」的統計口徑 -->
      <div class="rl-def">{{ t("warroom.rl_note_virt") }}</div>
    </aside>

    <!-- 中央：焦點關係圖（換資料頁只換這一區） -->
    <Transition :name="pageTrans" mode="out-in"><div :key="data.page" class="rl-center">
      <div v-if="f.state === 'forbidden'" class="wr-empty">{{ t("warroom.forbidden") }}</div>
      <div v-else-if="f.state !== 'ok' || !graph || !f.ip" class="rl-empty">
        <div class="wr-empty">{{ t("warroom.rl_empty") }}</div>
        <div v-if="f.ips" class="wr-muted">{{ t("warroom.rl_empty_d", { n: f.ips.toLocaleString() }) }}</div>
      </div>
      <div v-else class="rl-canvas" :style="{ width: `${CANVAS_W}px`, height: `${CANVAS_H}px` }" data-testid="wr-rl-graph">
        <!-- 列標題與沒有關係時的說明 -->
        <template v-for="(kind, i) in ROW_KINDS" :key="kind">
          <div class="rl-row__t" :style="{ top: `${rowTop(i) - ROW_TITLE}px` }">
            <WrIco :icon="ROW_ICON[kind]" />{{ t(`warroom.rl_row_${kind}`) }}
            <span v-if="rowsByKind[kind]?.more" class="rl-row__more">{{ t("warroom.rl_row_more", { n: rowsByKind[kind].more }) }}</span>
          </div>
          <div v-if="!graph.nodes.some((n) => n.row === kind)" class="rl-row__none" :style="{ top: `${rowTop(i) + 64}px` }"
               :data-testid="`wr-rl-none-${kind}`">{{ rowEmpty(rowsByKind[kind], kind) }}</div>
        </template>

        <svg class="rl-lines" :width="CANVAS_W" :height="CANVAS_H" :viewBox="`0 0 ${CANVAS_W} ${CANVAS_H}`" aria-hidden="true">
          <path v-for="(e, i) in graph.edges" :key="i" :d="e.d" fill="none"
                :class="e.state === 'pending' ? 'ln-pending' : `ln-${e.line}`" />
        </svg>

        <!-- 邊的標籤：上面是關係種類，下面是來源與時間；待確認的改寫「待確認」 -->
        <div v-for="(e, i) in graph.edges" :key="`l${i}`" class="rl-el" :class="[`rl-el--${e.label.align}`, { pending: e.state === 'pending' }]"
             :style="e.label.align === 'center'
               ? { left: `${e.label.x - e.label.w / 2}px`, top: `${e.label.y}px`, width: `${e.label.w}px` }
               : { left: `${e.label.x}px`, top: `${e.label.y}px` }"
             data-testid="wr-rl-edge">
          <template v-if="e.label.align === 'center'">
            <div class="rl-el__k">{{ t(`warroom.rl_k_${e.kind}`) }}</div>
            <div class="rl-el__s">{{ e.state === "pending" ? t("warroom.rl_pending") : edgeSrc(e.source) }}</div>
            <div v-if="e.state !== 'pending' && e.at" class="rl-el__s">{{ shortTime(e.at, now) }}</div>
          </template>
          <template v-else>
            <span class="rl-el__k">{{ t(`warroom.rl_k_${e.kind}`) }}</span>
            <span class="rl-el__s">{{ e.state === "pending" ? t("warroom.rl_pending") : [edgeSrc(e.source), shortTime(e.at, now)].filter(Boolean).join(" · ") }}</span>
          </template>
        </div>

        <!-- 物件節點 -->
        <div v-for="n in graph.nodes.filter((x) => x.row)" :key="n.key" class="rl-node" :class="{ compact: n.compact }"
             :style="{ left: `${n.rect.x}px`, top: `${n.rect.y}px`, width: `${n.rect.w}px`, height: `${n.rect.h}px` }"
             :data-testid="`wr-rl-node-${n.type}`">
          <div class="rl-node__k"><WrIco :icon="TYPE_ICON[n.type] ?? DevicesIcon" /><span>{{ typeLabel(n) }}</span></div>
          <div v-if="MONO.has(n.type)" class="rl-node__mono"><template v-for="(x, j) in addrParts(n.label)" :key="j">{{ x }}<wbr></template></div>
          <WrName v-else :text="n.label" class="rl-node__n" />
          <div v-if="!n.compact && subText(n)" class="rl-node__s" :class="{ mono: n.type === 'nic' }">{{ subText(n) }}</div>
        </div>

        <!-- 焦點 IP：一個節點，三列的邊都接到它 -->
        <div v-if="ipNode" class="rl-ip" :style="{ left: `${IP_RECT.x}px`, top: `${IP_RECT.y}px`, width: `${IP_RECT.w}px`, height: `${IP_RECT.h}px` }"
             data-testid="wr-rl-ip">
          <div class="rl-node__k"><WrIco :icon="AddressesIcon" />{{ t("warroom.rl_t_ip") }}</div>
          <div class="rl-ip__v" :class="{ long: f.ip.ip.length > 15 }"><template v-for="(x, j) in addrParts(f.ip.ip)" :key="j">{{ x }}<wbr></template></div>
          <WrName v-if="f.ip.hostname" :text="f.ip.hostname" :lines="3" class="rl-ip__h" />
          <div v-if="f.ip.mac" class="rl-ip__m"><WrIco :icon="MacIcon" />{{ f.ip.mac }}</div>
          <div v-if="f.conflicts?.length" class="rl-ip__c" data-testid="wr-rl-conflicts">
            <div class="rl-ip__ct"><WrIco :icon="WarnIcon" />{{ t("warroom.rl_pending") }}</div>
            <div v-for="c in f.conflicts" :key="c" class="rl-ip__cr">{{ t(`warroom.rl_why_${c}`) }}</div>
          </div>
        </div>
      </div>

      <div v-if="graph" class="rl-legend" data-testid="wr-rl-legend">
        <div class="rl-legend__r">
          <span><svg width="56" height="10"><line x1="0" y1="5" x2="56" y2="5" class="ln-solid" /></svg>{{ t("warroom.rl_lg_direct") }}</span>
          <span><svg width="56" height="10"><line x1="0" y1="5" x2="56" y2="5" class="ln-dashed" /></svg>{{ t("warroom.rl_lg_derived") }}</span>
          <span v-if="!data.info_mode"><svg width="56" height="10"><line x1="0" y1="5" x2="56" y2="5" class="ln-pending" /></svg>{{ t("warroom.rl_lg_pending") }}</span>
        </div>
        <div class="rl-legend__r rl-legend__notes">
          <span><WrIco :icon="ElapsedIcon" />{{ t("warroom.rl_lg_time") }}</span>
          <span><WrIco :icon="InfoIcon" />{{ t("warroom.rl_note_ownership") }}</span>
        </div>
      </div>
    </div></Transition>

    <!-- 右欄：關聯待補、焦點來源 -->
    <aside class="rl-side">
      <!-- 資訊展示模式（11-03）：關聯待補是問題清單，不畫 -->
      <div v-if="pending.state !== 'hidden'" class="wr-h2"><WrIco :icon="LinkIcon" />{{ t("warroom.rl_pending_title") }}</div>
      <div v-if="pending.state !== 'hidden'" class="rl-pend" data-testid="wr-rl-pending">
        <div v-for="p in PENDING" :key="p.key" class="rl-pend__i">
          <div v-if="pending.state === 'ok' && typeof pending[p.key] === 'number'" class="wr-big rl-pend__v"
               :class="(pending[p.key] as number) > 0 ? `tone-${p.tone}` : 'tone-muted'">{{ pad2(pending[p.key] as number) }}</div>
          <div v-else class="rl-pend__na">{{ pendingNa(p.key) }}</div>
          <div class="rl-pend__t">
            <div class="rl-pend__l">{{ t(`warroom.rl_p_${p.key}`) }}</div>
            <div class="rl-pend__d">{{ t(`warroom.rl_p_${p.key}_d`) }}</div>
          </div>
        </div>
      </div>

      <template v-if="f.state === 'ok' && f.ip">
        <div class="rl-src__h"><WrIco :icon="IntegrationsIcon" />{{ t("warroom.rl_focus_title") }}</div>
        <div class="rl-src" data-testid="wr-rl-sources">
          <div v-if="f.registered" class="rl-src__r">
            <WrIco :icon="AuditIcon" /><span class="rl-src__l">{{ t("warroom.rl_registered") }} · {{ srcName(f.registered.source) }}</span>
            <span class="rl-src__t">{{ longTime(f.registered.at, now) }}</span>
          </div>
          <div v-for="s in (f.sources ?? []).slice(0, SRC_ROWS)" :key="s.source" class="rl-src__r">
            <WrIco :icon="IntegrationsIcon" /><span class="rl-src__l">{{ srcName(s.source) }}</span>
            <span class="rl-src__t">{{ longTime(s.at, now) }}</span>
          </div>
          <div class="rl-src__r rl-src__last">
            <WrIco :icon="ElapsedIcon" /><span class="rl-src__l">{{ t("warroom.rl_last_confirmed") }}</span>
            <span class="rl-src__t">{{ f.last_confirmed ? longTime(f.last_confirmed, now) : t("warroom.rl_no_seen") }}</span>
          </div>
        </div>
      </template>
    </aside>
  </div>
</template>

<style scoped>
.rl { display: grid; grid-template-columns: 150px 1174px minmax(0, 1fr); grid-template-rows: minmax(0, 1fr); gap: 40px; height: 100%; }
.rl-left { display: flex; flex-direction: column; border-right: 1px solid var(--wr-line); padding-right: 16px; min-height: 0; }
.rl-count { padding: 10px 0 14px; border-bottom: 1px solid var(--wr-line); }
.rl-count:last-child { border-bottom: 0; }
.rl-count__v { font-size: 68px; }
.rl-count__na { font-size: 24px; color: var(--wr-muted); padding: 8px 0; }
.rl-count__l { font-size: 24px; margin-top: 6px; white-space: nowrap; }
.rl-center { min-width: 0; min-height: 0; display: flex; flex-direction: column; gap: 14px; }
.rl-empty { display: flex; flex-direction: column; gap: 8px; }
.rl-empty .wr-empty { padding-bottom: 8px; }
.rl-canvas { position: relative; flex: none; }
.rl-lines { position: absolute; left: 0; top: 0; overflow: visible; }
.ln-solid { stroke: var(--wr-ink); stroke-width: 2.5; }
.ln-dashed { stroke: var(--wr-ink); stroke-width: 2.5; stroke-dasharray: 9 7; opacity: .85; }
.ln-pending { stroke: var(--wr-lilac); stroke-width: 3; stroke-dasharray: 4 6; }
.rl-row__t { position: absolute; left: 0; height: 40px; display: flex; align-items: center; gap: 4px; font-size: 24px; font-weight: 700;
  white-space: nowrap; }
.rl-row__t .wr-ico { color: var(--wr-muted); }
.rl-row__more { margin-left: 14px; font-size: 20px; font-weight: 400; color: var(--wr-lilac); }
.rl-row__none { position: absolute; left: 0; font-size: 24px; color: var(--wr-muted); }
.rl-node { position: absolute; box-sizing: border-box; border: 1px solid var(--wr-deep-line); background: var(--wr-panel);
  padding: 10px 12px; display: flex; flex-direction: column; gap: 4px; overflow: hidden; }
.rl-node.compact { padding: 6px 10px; gap: 2px; }
.rl-node__k { display: flex; align-items: center; font-size: 17px; color: var(--wr-muted); white-space: nowrap; min-width: 0; }
.rl-node__k > span { overflow: hidden; text-overflow: ellipsis; }
.rl-node__n { font-size: 21px; font-weight: 600; font-family: var(--wr-num); }
.compact .rl-node__n { font-size: 19px; }
.rl-node__mono { font-size: 20px; font-weight: 600; font-family: var(--wr-num); font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere; line-height: 1.22; }
.compact .rl-node__mono { font-size: 19px; }
.rl-node__s { font-size: 17px; color: var(--wr-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rl-node__s.mono { font-family: var(--wr-num); white-space: normal; overflow-wrap: anywhere; text-overflow: clip; }
.rl-el { position: absolute; font-size: 15px; line-height: 1.2; color: var(--wr-muted); pointer-events: none; }
.rl-el--center { text-align: center; }
.rl-el--center .rl-el__k { position: absolute; left: 0; right: 0; bottom: calc(100% + 1px); color: var(--wr-ink); font-weight: 600; font-size: 17px; }
.rl-el--center { padding-top: 5px; }
.rl-el--side { transform: translateY(-50%); white-space: nowrap; display: flex; gap: 10px; align-items: baseline; }
.rl-el--side .rl-el__k { color: var(--wr-ink); font-weight: 600; font-size: 17px; }
.rl-el.pending .rl-el__k, .rl-el.pending .rl-el__s { color: var(--wr-lilac); }
.rl-el__s { font-family: var(--wr-num); }
.rl-ip { position: absolute; box-sizing: border-box; border: 2px solid var(--wr-accent); background: var(--wr-panel);
  padding: 22px 18px; display: flex; flex-direction: column; justify-content: center; gap: 12px; }
.rl-ip__v { font-family: var(--wr-num); font-size: 32px; font-weight: 700; color: var(--wr-accent); line-height: 1.12;
  overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
.rl-ip__v.long { font-size: 28px; }
.rl-ip__h { font-size: 24px; font-family: var(--wr-num); }
.rl-ip__m { font-family: var(--wr-num); font-size: 19px; color: var(--wr-muted); overflow-wrap: anywhere; }
.rl-ip__c { margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--wr-line); display: grid; gap: 6px; }
.rl-ip__ct { font-size: 20px; font-weight: 700; color: var(--wr-lilac); }
.rl-ip__cr { font-size: 17px; color: var(--wr-lilac); line-height: 1.3; }
.rl-legend { display: grid; gap: 6px; font-size: 18px; color: var(--wr-muted); }
.rl-legend__r { display: flex; gap: 28px; flex-wrap: wrap; }
.rl-legend__r > span { display: inline-flex; align-items: center; gap: 10px; white-space: nowrap; }
.rl-legend__notes { font-size: 17px; gap: 24px; }
.rl-side { border-left: 1px solid var(--wr-line); padding-left: 32px; display: flex; flex-direction: column; gap: 10px; min-width: 0;
  min-height: 0; overflow: hidden; }
.rl-pend { display: grid; }
.rl-pend__i { display: grid; grid-template-columns: 84px minmax(0, 1fr); gap: 12px; align-items: center; padding: 7px 0;
  border-bottom: 1px solid var(--wr-line); }
.rl-pend__v { font-size: 48px; }
.rl-pend__na { font-size: 20px; color: var(--wr-muted); }
.rl-pend__l { font-size: 22px; font-weight: 700; line-height: 1.2; }
.rl-pend__d { font-size: 16px; color: var(--wr-muted); line-height: 1.3; margin-top: 2px; }
.rl-src__h { font-size: 24px; font-weight: 700; margin-top: 6px; display: flex; align-items: center; }
.rl-src__h .wr-ico { color: var(--wr-muted); }
.rl-src { display: grid; }
.rl-src__r { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 4px 6px; align-items: center; font-size: 20px;
  padding: 6px 0; border-bottom: 1px solid var(--wr-line); }
.rl-src__r .wr-ico { color: var(--wr-outline); }
.rl-src__l { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rl-src__t { font-family: var(--wr-num); color: var(--wr-ink); white-space: nowrap; }
.rl-src__last .rl-src__l { font-weight: 700; }
.rl-def { margin-top: auto; font-size: 16px; color: var(--wr-muted); line-height: 1.35; }
</style>
