<template>
  <!-- IP 變更評估的關係圖（規格 §3.4）：根目標、受影響的物件與它們之間保存下來的關係。
       資料由後端依讀者過濾、最多畫 limit 個節點；手機改成清單（複雜的圖在小螢幕看不清楚）。
       使用者 2026-10-08 幾輪回饋：擠在一起看不了、線上的字疊成一團、看不出節點是什麼、點了不知道明細在下面、
       要能切換排列方式並有縮圖（參考另一個內部專案的 JSON 圖）→
       同類收成群組（utils/impactGraph）、節點畫類型圖示（utils/impactGraphIcons）、四種排列可切換、
       線用粗細分「對方停機這裡也斷／只是設定裡寫著位址」、明細在圖右側的面板、右下角縮圖可拖曳 -->
  <div class="irg" data-testid="cip-graph">
    <n-spin :show="loading">
      <n-alert v-if="error" type="error" :bordered="false">{{ error }}</n-alert>
      <div v-else-if="!loading && !data?.nodes.length" class="irg-empty">{{ t("change_impact.graph_empty") }}</div>
      <template v-else-if="data">
        <!-- 一列就好（使用者 2026-10-08：左上資訊文字太多）：影響顏色＋評估目標；線條、圖示、操作說明收進「圖例」 -->
        <div ref="barEl" class="irg-bar">
          <div class="irg-legends">
            <span class="irg-legend"><span class="irg-dot" :style="{ background: rootColor() }" />{{ t("change_impact.graph_root") }}</span>
            <span v-for="k in LEGEND" :key="k" class="irg-legend">
              <span class="irg-dot" :style="{ background: COLOR[k] }" />{{ t(`change_impact.impact.${k}`) }}
            </span>
            <span v-if="hasBlocker" class="irg-legend" data-testid="cip-graph-legend-blocker">
              <span class="irg-dot irg-dot--blocker" />{{ t("change_impact.graph_blocker") }}
            </span>
            <span v-if="data.truncated" class="irg-muted">{{ t("change_impact.graph_truncated", { n: data.limit, total: data.total_nodes }) }}</span>
          </div>
          <!-- 按鈕是一組、不拆開折行（放不下時整組換到下一行） -->
          <div v-if="!narrow" class="irg-tools">
            <n-radio-group v-model:value="layoutName" size="small" data-testid="cip-graph-layout">
              <n-radio-button v-for="l in LAYOUTS" :key="l" :value="l" :data-testid="`cip-graph-layout-${l}`"
                              :title="t(`change_impact.graph_layout_${l}_hint`)">
                <span class="irg-btn"><n-icon :size="14"><component :is="LAYOUT_ICON[l]" /></n-icon>{{ t(`change_impact.graph_layout_${l}`) }}</span>
              </n-radio-button>
            </n-radio-group>
            <n-popover trigger="click" placement="bottom-end" :width="420">
              <template #trigger>
                <n-button size="small" data-testid="cip-graph-legend-btn">
                  <template #icon><n-icon><InfoIcon /></n-icon></template>{{ t("change_impact.graph_legend") }}
                </n-button>
              </template>
              <div class="irg-legend-pop" data-testid="cip-graph-legend">
                <div class="irg-pop-h">{{ t("change_impact.graph_legend_lines") }}</div>
                <div class="irg-legend"><span class="irg-line irg-line--outage" />{{ t("change_impact.graph_line_outage") }}</div>
                <div class="irg-legend"><span class="irg-line irg-line--ref" />{{ t("change_impact.graph_line_reference") }}</div>
                <div class="irg-legend"><span class="irg-line irg-line--inferred" />{{ t("change_impact.graph_line_inferred") }}</div>
                <div class="irg-legend irg-arrow-note">→ {{ t("change_impact.graph_arrow") }}</div>
                <div class="irg-pop-h">{{ t("change_impact.graph_legend_types") }}</div>
                <div class="irg-pop-types">
                  <span v-for="ty in presentTypes" :key="ty" class="irg-legend">
                    <span class="irg-ico"><n-icon :size="12"><component :is="iconFor(ty)" /></n-icon></span>{{ typeLabel(ty) }}
                  </span>
                </div>
                <div class="irg-pop-h">{{ t("change_impact.graph_legend_usage") }}</div>
                <div class="irg-muted">{{ t("change_impact.graph_hint") }}</div>
              </div>
            </n-popover>
            <n-button v-if="hasGroups" size="small" data-testid="cip-graph-expand-all" @click="toggleAll">
              <template #icon><n-icon><ExpandIcon /></n-icon></template>
              {{ expandAll ? t("change_impact.graph_collapse_all") : t("change_impact.graph_expand_all") }}
            </n-button>
            <n-button size="small" data-testid="cip-graph-fit" @click="fit">
              <template #icon><n-icon><FitIcon /></n-icon></template>{{ t("change_impact.graph_fit") }}
            </n-button>
            <n-dropdown trigger="hover" :options="exportOptions" :disabled="exporting" @select="onExport">
              <n-button size="small" :loading="exporting" :disabled="exporting" data-testid="cip-graph-export">
                <template #icon><n-icon><DownloadIcon /></n-icon></template>{{ t("change_impact.graph_export") }}
              </n-button>
            </n-dropdown>
          </div>
        </div>
        <ul v-if="narrow" class="irg-list" data-testid="cip-graph-list">
          <li v-for="(e, i) in data.edges.filter((x) => x.from !== x.to)" :key="i">
            <span class="irg-dot" :style="{ background: colorOf(e.from) }" />
            <strong>{{ nameOf(e.from) }}</strong>
            <span class="irg-muted"> {{ relText(e.relation, nodeOf(e.from)?.type, e.from) }} → </span>
            <strong>{{ nameOf(e.to) }}</strong>
          </li>
        </ul>
        <div v-else ref="wrapEl" class="irg-wrap" :style="fill.height.value ? { height: `${fill.height.value}px` } : undefined">
          <div class="irg-stage">
            <div ref="box" class="irg-canvas" />
            <!-- 縮圖：整張圖的縮影＋目前看到的範圍，點或拖曳就移過去（IP 拓樸圖共用同一個元件） -->
            <GraphMinimap ref="miniRef" selector="[!zone]" testid="cip-graph-minimap"
                          :color-of="(n) => String(n.data('color') || '#94a3b8')"
                          :round-of="(n) => (n.hasClass('card') || n.data('group') ? 1.5 : null)" />
          </div>
          <!-- 明細改成圖旁邊的一欄（使用者 2026-10-08：蓋住圖面、名稱被截斷、文字都一樣樣式很難看）：
               完整名稱、為什麼列出來、依關係分組（可點過去）、證據來源與時間、開啟原始物件 -->
          <aside v-if="pickedGroup || picked" class="irg-side" data-testid="cip-graph-picked">
            <div class="irg-side-head">
              <span class="irg-ico irg-ico--lg" :style="{ background: pickedGroup ? impactColor(pickedGroup.impact) : nodeColor(picked!) }">
                <n-icon :size="15"><component :is="iconFor((pickedGroup ?? picked)!.type)" /></n-icon>
              </span>
              <div class="irg-side-title">{{ pickedGroup ? groupName(pickedGroup) : nameOf(picked!.id) }}</div>
              <n-button quaternary circle size="small" :aria-label="t('common.close')" data-testid="cip-graph-close"
                        @click="clearPick">
                <template #icon><n-icon><CancelIcon /></n-icon></template>
              </n-button>
            </div>
            <template v-if="pickedGroup">
              <div class="irg-tags">
                <n-tag size="small" :bordered="false">{{ typeLabel(pickedGroup.type, pickedGroup.category) }}</n-tag>
                <n-tag size="small" :bordered="false" :color="impactTagColor(pickedGroup.impact)">
                  {{ t(`change_impact.impact.${pickedGroup.impact}`) }}</n-tag>
                <n-tag v-if="pickedGroup.members.some((m) => m.blocker)" size="small" type="error" :bordered="false">
                  {{ t("change_impact.disposition.blocker") }}</n-tag>
              </div>
              <div v-if="pickedGroup.relation !== 'mixed'" class="irg-sec-cap">
                {{ relText(pickedGroup.relation, pickedGroup.type) }} → {{ nameOf(pickedGroup.neighbor) }}</div>
              <n-button size="small" style="margin: 8px 0" data-testid="cip-graph-expand-group" @click="expandGroup(pickedGroup.id)">
                <template #icon><n-icon><ExpandIcon /></n-icon></template>{{ t("change_impact.graph_expand_this") }}
              </n-button>
              <ul class="irg-items" data-testid="cip-graph-members">
                <li v-for="m in pickedGroup.members" :key="m.id" class="irg-item">
                  <span class="irg-dot" :style="{ background: impactColor(m.impact) }" />
                  <span class="irg-item-name">{{ m.label }}</span>
                </li>
              </ul>
            </template>
            <template v-else-if="picked">
              <div class="irg-tags">
                <n-tag size="small" :bordered="false">{{ typeLabel(picked.type, picked.category) }}</n-tag>
                <n-tag v-if="picked.root" size="small" :bordered="false" :color="{ color: rootColor() + '22', textColor: rootColor() }">
                  {{ t("change_impact.graph_root") }}</n-tag>
                <n-tag v-else-if="picked.impact" size="small" :bordered="false" :color="impactTagColor(picked.impact)">
                  {{ t(`change_impact.impact.${picked.impact}`) }}</n-tag>
                <n-tag v-if="picked.blocker" size="small" type="error" :bordered="false" data-testid="cip-graph-picked-blocker">
                  {{ t("change_impact.disposition.blocker") }}</n-tag>
              </div>
              <section v-if="pickedReasons.length" class="irg-sec">
                <div class="irg-sec-h">{{ picked.root ? t("change_impact.graph_about_target") : t("change_impact.graph_why") }}</div>
                <ul class="irg-reasons"><li v-for="(r, i) in pickedReasons" :key="i">{{ r }}</li></ul>
              </section>
              <section v-for="grp in pickedRels" :key="grp.key" class="irg-sec" data-testid="cip-graph-rel-group">
                <div class="irg-sec-h">{{ grp.label }}<span class="irg-count">{{ grp.items.length }}</span></div>
                <div class="irg-sec-cap">{{ grp.dir === "in" ? t("change_impact.graph_rel_in_cap", { name: nameOf(picked.id) })
                  : t("change_impact.graph_rel_out_cap", { name: nameOf(picked.id) }) }}</div>
                <ul class="irg-items">
                  <li v-for="it in grp.items" :key="it.id" class="irg-item irg-item--link" :title="it.name"
                      @click="pick(it.id)">
                    <span class="irg-ico irg-ico--sm" :style="{ background: it.color }">
                      <n-icon :size="10"><component :is="iconFor(it.type)" /></n-icon>
                    </span>
                    <span class="irg-item-name">{{ it.name }}</span>
                  </li>
                </ul>
              </section>
              <section v-if="picked.evidence?.length" class="irg-sec">
                <div class="irg-sec-h">{{ t("change_impact.graph_evidence") }}</div>
                <ul class="irg-ev">
                  <li v-for="(e, i) in picked.evidence" :key="i">
                    <div class="irg-ev-label">{{ e.label }}</div>
                    <div class="irg-muted">{{ sourceLabel(e.source_type) }}<template v-if="e.observed_at"> · {{ fmtDateTime(e.observed_at) }}</template></div>
                  </li>
                </ul>
              </section>
              <n-button v-if="pickedLink" size="small" type="primary" secondary data-testid="cip-graph-open" @click="openPicked">
                <template #icon><n-icon><OpenNewWindowIcon /></n-icon></template>{{ t("change_impact.graph_open") }}
              </n-button>
            </template>
          </aside>
        </div>
      </template>
    </n-spin>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import GraphMinimap from "@/components/GraphMinimap.vue";
import { useI18n } from "vue-i18n";
import { NAlert, NButton, NDropdown, NIcon, NPopover, NRadioButton, NRadioGroup, NSpin, NTag } from "naive-ui";
import { useRouter } from "vue-router";
import cytoscape from "cytoscape";
import coseBilkent from "cytoscape-cose-bilkent";
import {
  CancelIcon, DownloadIcon, ExpandIcon, FitIcon, GraphForceIcon, GraphRadialIcon, GraphRingsIcon, GraphTreeIcon,
  InfoIcon, OpenNewWindowIcon,
} from "@/icons";
import { useAuthStore } from "@/stores/auth";
import { fmtDateTime } from "@/utils/datetime";
import { reasonText } from "@/utils/changeImpact";
import { useExportBusy } from "@/composables/useExportBusy";
import { useFillHeight } from "@/composables/usePageFill";
import { graphToSvg } from "@/utils/graphSvg";
import { download } from "@/utils/tableExport";
import { apiErrMsg } from "@/api/client";
import { getRunRelations, type RelationGraph, type RelationNode } from "@/api/changeImpact";
import {
  IMPACT_RANK, groupRelationGraph, mergeParallelEdges, ringLayout, subjectLink, treePositions, type GraphGroup,
} from "@/utils/impactGraph";
import { iconDataUri, iconFor } from "@/utils/impactGraphIcons";

// 同一個外掛重複註冊會丟錯（拓樸頁也註冊過）
try { cytoscape.use(coseBilkent as any); } catch { /* 已註冊 */ }

const props = defineProps<{ runId: string }>();
const { t, te } = useI18n();
const data = ref<RelationGraph | null>(null);
const loading = ref(false);
const error = ref("");
const box = ref<HTMLElement | null>(null);
const picked = ref<RelationNode | null>(null);
const pickedGroup = ref<GraphGroup | null>(null);
const router = useRouter();
// 往下佔滿視窗（使用者 2026-10-08）：工具列到頁面底部剛好等於可視高度
const barEl = ref<HTMLElement | null>(null);
const wrapEl = ref<HTMLElement | null>(null);
const fill = useFillHeight(wrapEl, barEl);
const expanded = ref(new Set<string>());
const expandAll = ref(false);
const hasGroups = ref(false);
const winW = ref(window.innerWidth);
const narrow = computed(() => winW.value < 720);
let cy: cytoscape.Core | null = null;

// 排列方式：分層（依影響，越嚴重越靠中心）、自動排列（力導向）、放射（以評估目標為中心）、樹狀（由左往右的卡片）。
// 預設是分層（使用者 2026-10-08，看過各種排列之後指定）；使用者選過就記在這台瀏覽器、照他的。
// 鍵名的版本號＝預設改了之後，之前記下的選擇重設一次（v2→v3）
const LAYOUTS = ["rings", "force", "radial", "tree"] as const;
type LayoutName = (typeof LAYOUTS)[number];
const LAYOUT_ICON: Record<LayoutName, unknown> = {
  force: GraphForceIcon, radial: GraphRadialIcon, tree: GraphTreeIcon, rings: GraphRingsIcon,
};
const LAYOUT_KEY = "jtipam.cip.graphLayout.v3";
function savedLayout(): LayoutName | null {
  try {
    const v = localStorage.getItem(LAYOUT_KEY);
    return (LAYOUTS as readonly string[]).includes(v ?? "") ? (v as LayoutName) : null;
  } catch { return null; }
}
const userLayout = ref<LayoutName | null>(savedLayout());
const layoutName = computed<LayoutName>({
  get: () => userLayout.value ?? "rings",
  set: (v) => {
    userLayout.value = v;
    try { localStorage.setItem(LAYOUT_KEY, v); } catch { /* 私密視窗等：不記也照樣能用 */ }
    redraw();
  },
});

const COLOR: Record<string, string> = {
  modeled_disruption: "#d03050", potential_disruption: "#f0a020", redundancy_unverified: "#e8a33b",
  change_required: "#2080f0", unknown: "#909399", reference_only: "#18a058",
};
const LEGEND = ["modeled_disruption", "redundancy_unverified", "potential_disruption", "change_required", "reference_only"];
const TYPE_KEY: Record<string, string> = { virtual_machine: "vm", ip_address: "ip" };
// 線上的字：邊很少才直接顯示；其餘只在滑過或點選外圍節點時顯示那幾條（滑過中心不顯示，否則整圈的字一起跳出來）
const EDGE_LABELS_MAX = 6;

function nodeOf(id: string) { return data.value?.nodes.find((n) => n.id === id); }
const BLOCKER_COLOR = "#d03050";
function impactColor(impact: string | null | undefined) { return impact ? COLOR[impact] ?? "#909399" : "#5c6370"; }
/** 評估目標用中性色（規劃人員：目標是綠色、明細又寫「僅有引用」，會被誤認為已判定沒有影響） */
function rootColor() { return isDark() ? "#94a3b8" : "#475569"; }
function nodeColor(n: { root?: boolean; impact: string | null }) { return n.root ? rootColor() : impactColor(n.impact); }
function colorOf(id: string) { const n = nodeOf(id); return n ? nodeColor(n) : impactColor(null); }
function impactTagColor(impact: string | null) {
  const c = impactColor(impact);
  return { color: `${c}22`, textColor: c };
}
/** 同名的物件分得出來（規劃人員：兩個同名的主機、兩個同一個新位址）：監控／盤點類前面加來源，
 *  新位址那兩筆標出是 IPAM 紀錄還是使用跡象 */
const ROLE_PRODUCT: Record<string, string> = {
  librenms_device: "LibreNMS", zabbix_host: "Zabbix", wazuh_agent: "Wazuh", ocs_computer: "OCS",
  rustdesk_peer: "RustDesk",
};
function roleOf(n: RelationNode): string | null {
  if (ROLE_PRODUCT[n.type]) return ROLE_PRODUCT[n.type];
  if (!n.root && te(`change_impact.graph_role.${n.type}`)) return t(`change_impact.graph_role.${n.type}`);
  return null;
}
function nameOf(id: string): string {
  const n = nodeOf(id);
  if (!n) return groupById(id) ? groupName(groupById(id)!) : id;
  const role = roleOf(n);
  return role ? t("change_impact.graph_named", { role, name: n.label }) : n.label;
}
/** 關係的白話說法：通用的「設定裡寫著這個位址」依引用方的類型講具體（規劃人員：「DNS 紀錄指向」「監控目標」…） */
function relText(r: string, fromType?: string | null, fromId?: string) {
  // 同一種物件可能因為不同規則出現（IP 位址：新位址已登記、或說明裡提到舊位址）：先看引用方的規則
  // （2026-10-25 實測：說明提到舊位址的 IP 被寫成「新位址已登記在 IPAM」）
  if (fromId && (r === "references" || r === "observed_on")) {
    for (const rule of nodeOf(fromId)?.rules ?? []) {
      const k = `change_impact.graph_rule.${rule.rule_id.replace(/\./g, "_")}`;
      if (te(k)) return t(k);
    }
  }
  if (fromType && (r === "references" || r === "observed_on") && te(`change_impact.graph_ref.${fromType}`)) {
    return t(`change_impact.graph_ref.${fromType}`);
  }
  if (te(`change_impact.graph_rel.${r}`)) return t(`change_impact.graph_rel.${r}`);
  return te(`change_impact.svc.rel.${r}`) ? t(`change_impact.svc.rel.${r}`) : r;
}
function sourceLabel(src: string) {
  return te(`change_impact.graph_source.${src}`) ? t(`change_impact.graph_source.${src}`) : src;
}
function typeLabel(type: string, category?: string | null): string {
  if (te(`change_impact.stype.${type}`)) return t(`change_impact.stype.${type}`);
  const k = `change_impact.svc.type.${TYPE_KEY[type] ?? type}`;
  if (te(k)) return t(k);
  if (category && te(`change_impact.category.${category}`)) return t(`change_impact.category.${category}`);
  return type;
}
function groupName(g: { category: string | null; type: string; members?: unknown[]; count?: number }): string {
  return t("change_impact.graph_group", { name: typeLabel(g.type, g.category), n: g.count ?? g.members?.length ?? 0 });
}
// 圖例的類型：同一個名稱只列一次（IP 位址的兩種類型以前列了兩次）
const hasBlocker = computed(() => !!data.value?.nodes.some((n) => n.blocker));
const presentTypes = computed(() => {
  const seen = new Map<string, string>();
  for (const n of data.value?.nodes ?? []) if (!seen.has(typeLabel(n.type))) seen.set(typeLabel(n.type), n.type);
  return [...seen.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([, ty]) => ty);
});

// ── 明細面板 ──
let lastGraph: ReturnType<typeof groupRelationGraph> | null = null;
function groupById(id: string) { return lastGraph?.groups[id] ?? null; }
interface RelItem { id: string; name: string; type: string; color: string }
interface RelGroup { key: string; dir: "in" | "out"; relation: string; fromType: string; label: string; items: RelItem[] }
const pickedRels = computed<RelGroup[]>(() => {
  const n = picked.value;
  if (!n || !lastGraph) return [];
  const groups = new Map<string, RelGroup>();
  for (const e of lastGraph.edges) {
    if (e.from !== n.id && e.to !== n.id) continue;
    const dir = e.to === n.id ? "in" : "out";
    const other = dir === "in" ? e.from : e.to;
    const otherNode = lastGraph.nodes.find((x) => x.id === other);
    const fromType = (dir === "in" ? otherNode?.type : n.type) ?? "";
    const label = relText(e.relation, fromType, dir === "in" ? other : n.id);
    const key = `${dir}|${label}`;
    const grp = groups.get(key) ?? { key, dir, relation: e.relation, fromType, label, items: [] };
    grp.items.push({ id: other, name: otherNode?.group ? groupName(otherNode) : nameOf(other),
                     type: otherNode?.type ?? "", color: otherNode ? nodeColor(otherNode) : impactColor(null) });
    groups.set(key, grp);
  }
  return [...groups.values()].sort((a, b) => b.items.length - a.items.length);
});
const pickedReasons = computed(() => {
  const out = new Set<string>();
  for (const r of picked.value?.rules ?? []) out.add(reasonText(t, te, r.reason, r.params));
  return [...out];
});
const pickedLink = computed(() => {
  const n = picked.value;
  if (!n) return null;
  const loc = subjectLink(n);
  if (!loc) return null;
  // 沒權限進的頁不要做成按鈕（點了只會被導回首頁）
  try {
    if (router.resolve(loc).meta.admin && !useAuthStore().me?.is_admin) return null;
  } catch { return null; }
  return loc;
});
function openPicked() { if (pickedLink.value) void router.push(pickedLink.value); }

function isDark(): boolean {
  const theme = document.documentElement.getAttribute("data-theme");
  return theme === "dark" || (!theme && window.matchMedia("(prefers-color-scheme: dark)").matches);
}

let selected: string | null = null;
function clearPick() {
  selected = null;
  picked.value = null;
  pickedGroup.value = null;
  cy?.elements().removeClass("faded hl");
  if (cy && layoutName.value !== "tree" && cy.edges().length > EDGE_LABELS_MAX) cy.edges().removeClass("lbl");
}

function draw() {
  cy?.destroy();
  cy = null;
  if (!box.value || !data.value || narrow.value) return;
  const g = groupRelationGraph(data.value, { expanded: expanded.value, expandAll: expandAll.value });
  hasGroups.value = expandAll.value || expanded.value.size > 0 || Object.keys(g.groups).length > 0;
  const mode = layoutName.value;
  const tree = mode === "tree";
  const dark = isDark();
  const fg = dark ? "#e5e7eb" : "#1f2328";
  const bg = dark ? "#111827" : "#ffffff";
  const cardBg = dark ? "#1f2937" : "#ffffff";
  const lineOutage = dark ? "#cbd5e1" : "#4b5563";
  const lineRef = dark ? "#4b5563" : "#c4c8cf";
  // 樹狀：線不會交疊，字一律顯示；其他排列只在邊很少時直接顯示
  const showAll = tree || g.edges.length <= EDGE_LABELS_MAX;
  const rootIds = new Set(g.nodes.filter((n) => n.root).map((n) => n.id));
  cy = cytoscape({
    container: box.value,
    elements: [
      ...g.nodes.map((n) => {
        const color = n.root ? rootColor() : impactColor(n.impact);
        const label = n.group ? groupName(n) : nameOf(n.id);
        return {
          data: { id: n.id, label, color, type: n.type, root: n.root ? 1 : 0, group: n.group ? 1 : 0, impact: n.impact ?? "",
                  blocker: n.blocker ? 1 : 0,
                  rank: n.root ? -1 : IMPACT_RANK[n.impact ?? ""] ?? 9,
                  // 樹狀的卡片是淺底，圖示用影響顏色；其他排列是影響顏色的底配白色圖示（根目標卡片是深底）
                  icon: tree && !n.root ? iconDataUri(n.type, color) : iconDataUri(n.type),
                  size: n.root ? 40 : n.group ? Math.min(34 + Math.sqrt(n.count) * 4, 56) : 28 },
          classes: tree ? "card" : "",
        };
      }),
      ...mergeParallelEdges(g.edges).map((e) => ({ data: { id: e.id, source: e.from, target: e.to,
                                       label: [...new Set(e.relations.map((r) =>
                                         relText(r, g.nodes.find((x) => x.id === e.from)?.type, e.from)))].join("\n"),
                                       dashed: e.strength === "inferred" ? 1 : 0, prop: e.propagates ? 1 : 0,
                                       toRoot: rootIds.has(e.to) ? 1 : 0 },
                               classes: showAll ? "lbl" : "" })),
    ],
    style: [
      // 節點：影響顏色的底＋白色類型圖示；名稱太長截斷（點節點看全名），加一點底色，疊到線時還讀得出來
      { selector: "node", style: {
        "background-color": "data(color)", "background-image": "data(icon)", "background-fit": "none",
        "background-width": "58%", "background-height": "58%", "border-width": 2, "border-color": bg,
        label: "data(label)", color: fg, "font-size": 12, "text-valign": "bottom", "text-margin-y": 5,
        width: "data(size)", height: "data(size)", "text-wrap": "ellipsis", "text-max-width": "170px",
        "text-background-color": bg, "text-background-opacity": 0.85, "text-background-padding": "1px" } },
      { selector: "node[group = 1]", style: { shape: "round-rectangle", "font-weight": "bold", "font-size": 13 } },
      { selector: "node[root = 1]", style: { "border-width": 3, "border-color": fg, "font-weight": "bold",
                                             "font-size": 14 } },
      // 樹狀：卡片（左邊類型圖示、名稱在卡片裡，框線是影響顏色）
      { selector: "node.card", style: {
        shape: "round-rectangle", width: 210, height: 40, "background-color": cardBg, "border-color": "data(color)",
        "border-width": 2, "background-width": "18px", "background-height": "18px", "background-position-x": "10px",
        "background-position-y": "50%", "text-valign": "center", "text-halign": "center", "text-margin-x": 10,
        "text-margin-y": 0, "text-max-width": "160px", "text-background-opacity": 0 } },
      { selector: "node.card[root = 1]", style: { "background-color": dark ? "#e5e7eb" : "#1f2328", color: bg,
                                                  "border-color": dark ? "#e5e7eb" : "#1f2328" } },
      // 線：只是設定裡寫著位址＝淡色細線；對方停機這裡也斷＝深色粗線；推定的＝虛線
      // 阻擋項目：紅色粗框（2026-10-25 實測：跟其他「可能中斷」長得一樣，審核人看圖找不到）
      { selector: "node[blocker = 1]", style: { "border-width": 4, "border-color": BLOCKER_COLOR, "border-style": "double" } },
      { selector: "node.card[blocker = 1]", style: { "border-width": 3, "border-color": BLOCKER_COLOR, "border-style": "solid" } },
      { selector: "edge", style: { width: 1.2, "line-color": lineRef, "target-arrow-color": lineRef,
                                   "target-arrow-shape": "triangle", "arrow-scale": 0.7,
                                   // 樹狀：轉折點靠近上一層，線上的字才落在各自那一段水平線上（不會疊在共用的直線上）
                                   "curve-style": tree ? "taxi" : "straight", "taxi-direction": "horizontal",
                                   "taxi-turn": "72%" } },
      { selector: "edge[prop = 1]", style: { width: 2.4, "line-color": lineOutage, "target-arrow-color": lineOutage } },
      { selector: "edge[dashed = 1]", style: { "line-style": "dashed" } },
      // 邊的文字保持水平（中文跟著線轉成直的很難讀）
      { selector: "edge.lbl", style: { label: "data(label)", "font-size": 11, color: fg, "text-rotation": "none",
                                       "text-background-color": bg, "text-background-opacity": 0.9,
                                       "text-background-padding": "2px", "text-wrap": "wrap", "text-max-width": "130px" } },
      // 連到根目標的線：字放在靠外側那一端（線都集中在根目標，字放中間會在中心附近疊在一起）
      { selector: "edge.lbl[toRoot = 1]", style: { label: "", "source-label": "data(label)",
                                                   "source-text-offset": tree ? 82 : 58, "source-text-rotation": "none" } },
      { selector: ".faded", style: { opacity: 0.18 } },
      { selector: "node.hl", style: { "z-index": 10 } },
      { selector: "edge.hl", style: { "line-color": fg, "target-arrow-color": fg, "z-index": 10 } },
      { selector: ":selected", style: { "overlay-opacity": 0.12 } },
      // 依影響分層的圈：虛線圈＋上方的標題與數量（規劃人員：要看得出分層，不是十字排列）
      { selector: "node.zone", style: {
        shape: "ellipse", "background-opacity": 0, "background-image": "none", "border-width": 1.5,
        "border-style": "dashed", "border-color": "data(color)", "border-opacity": 0.55, events: "no",
        "text-valign": "top", "text-margin-y": -4, "font-size": 13, "font-weight": "bold", color: "data(color)",
        "text-max-width": "400px", "text-wrap": "none", "z-index-compare": "manual", "z-index": 0 } },
      { selector: "node[!zone], edge", style: { "z-index-compare": "manual", "z-index": 5 } },
    ] as any,
    layout: { name: "preset" },
    minZoom: 0.15,
    maxZoom: 2.5,
    wheelSensitivity: 0.3,
  });
  lastGraph = g;
  runLayout(g.nodes.length, mode);
  initialView();
  if (miniRef.value) miniRef.value.attach(cy);

  // 滑過／點選：只留這個節點與相鄰的；外圍節點才顯示它那幾條線上的字
  const focus = (id: string | null) => {
    if (!cy) return;
    cy.elements().removeClass("faded hl");
    if (!showAll) cy.edges().removeClass("lbl");
    if (!id) return;
    const n = cy.getElementById(id);
    const hood = n.closedNeighborhood();
    cy.elements().not(hood).addClass("faded");
    hood.addClass("hl");
    if (!n.data("root")) n.connectedEdges().addClass("lbl");
  };
  selected = null;
  focusFn = focus;
  cy.on("mouseover", "node", (ev) => focus(ev.target.id()));
  cy.on("mouseout", "node", () => focus(selected));
  cy.on("tap", "node", (ev) => pick(ev.target.id() as string));
  cy.on("tap", (ev) => {
    if (ev.target === cy) clearPick();
  });
}

let focusFn: ((id: string | null) => void) | null = null;
/** 選一個節點：圖上點的、或明細面板裡點的（面板裡點的不在畫面內時移過去） */
function pick(id: string) {
  if (!cy || !lastGraph) return;
  selected = id;
  focusFn?.(id);
  pickedGroup.value = lastGraph.groups[id] ?? null;
  picked.value = pickedGroup.value ? null : nodeOf(id) ?? null;
  const n = cy.getElementById(id);
  if (n.nonempty()) {
    const p = n.renderedPosition(), w = cy.width(), h = cy.height();
    if (p.x < 0 || p.y < 0 || p.x > w || p.y > h) cy.center(n);
  }
}

/** 同一圈／同一層的排序：先依影響嚴重度、再依類型與名稱 —— 同類排在一起、每次位置都一樣 */
function orderNodes(a: cytoscape.NodeSingular, b: cytoscape.NodeSingular): number {
  return Number(a.data("rank")) - Number(b.data("rank"))
    || String(a.data("type")).localeCompare(String(b.data("type")))
    || String(a.data("label")).localeCompare(String(b.data("label")));
}

function runLayout(count: number, mode: LayoutName) {
  if (!cy) return;
  const roots = cy.nodes("[root = 1]");
  if (mode === "tree" && roots.nonempty()) {
    const byId = (id: string) => cy!.getElementById(id);
    const pos = treePositions(cy.nodes().map((n) => n.id()), cy.edges().map((e) => ({ from: e.source().id(), to: e.target().id() })),
                              roots[0].id(), (a, b) => orderNodes(byId(a), byId(b)), { colW: 440, rowH: 58 });
    cy.layout({ name: "preset", positions: (n: cytoscape.NodeSingular) => pos.get(n.id()) ?? { x: 0, y: 0 } } as any).run();
    return;
  }
  if (mode === "rings" && roots.nonempty()) {
    // 依影響分層：根目標在中間，每一種影響一圈、越嚴重越內圈；圈與標題另外畫成不能點的背景
    const order = ["modeled_disruption", "potential_disruption", "redundancy_unverified", "change_required",
                   "unknown", "reference_only"];
    const others = cy.nodes("[root = 0]").sort(orderNodes).toArray() as cytoscape.NodeSingular[];
    const levels = order.map((k) => ({ key: k, ids: others.filter((n) => impactOf(n) === k).map((n) => n.id()) }));
    const rest = others.filter((n) => !order.includes(impactOf(n))).map((n) => n.id());
    if (rest.length) levels.push({ key: "unknown_rest", ids: rest });
    const { positions, rings } = ringLayout(levels, roots[0].id());
    cy.layout({ name: "preset", positions: (n: cytoscape.NodeSingular) => positions.get(n.id()) ?? { x: 0, y: 0 } } as any).run();
    cy.add(rings.map((r) => {
      const key = r.key === "unknown_rest" ? "unknown" : r.key;
      return { group: "nodes" as const, classes: "zone", position: { x: 0, y: 0 },
               data: { id: `zone:${r.key}`, zone: 1, size: r.r * 2,
                       label: t("change_impact.graph_ring_title", { name: t(`change_impact.impact.${key}`), n: r.count }),
                       color: impactColor(key) } };
    }));
    return;
  }
  if (mode === "radial" && roots.nonempty() && count <= 40) {
    cy.layout({
      name: "breadthfirst", circle: true, roots, directed: false, animate: false, avoidOverlap: true,
      nodeDimensionsIncludeLabels: true, spacingFactor: count <= 14 ? 1.5 : 1.15, depthSort: orderNodes,
    } as any).run();
    return;
  }
  cy.layout({ name: "cose-bilkent", animate: false, randomize: false, nodeDimensionsIncludeLabels: true,
              nodeRepulsion: 9000, idealEdgeLength: count <= 25 ? 140 : 110, edgeElasticity: 0.3, gravity: 0.2,
              tile: true } as any).run();
}

// 匯出（使用者 2026-10-08：也要 SVG）：PNG 是兩倍解析度的點陣圖；SVG 是向量圖，可以放大、拿去編輯
const exportOptions = computed(() => [
  { label: t("change_impact.graph_export_png"), key: "png" },
  { label: t("change_impact.graph_export_svg"), key: "svg" },
]);
const { exporting, run: runExport } = useExportBusy();
function onExport(key: string) { void runExport(() => (key === "svg" ? saveSvg() : savePng())); }
function saveSvg() {
  if (!cy) return;
  // 匯出不要帶滑過／點選時的淡化
  const faded = cy.elements(".faded, .hl");
  const cls = faded.map((el) => [el, el.classes()] as const);
  faded.removeClass("faded hl");
  const dark = isDark();
  const svg = graphToSvg(cy, { bg: dark ? "#111827" : "#ffffff", fg: dark ? "#e5e7eb" : "#1f2328" });
  for (const [el, c] of cls) el.classes(c);
  download(`change-impact-graph-${layoutName.value}.svg`, new Blob([svg], { type: "image/svg+xml" }), "image/svg+xml",
           { source: "change-impact-graph" });
}

/** 匯出整張圖（不是只有看得到的那一塊），兩倍解析度，底色跟著主題 */
function savePng() {
  if (!cy) return;
  const blob = cy.png({ output: "blob", full: true, scale: 2, bg: isDark() ? "#111827" : "#ffffff" }) as unknown as Blob;
  download(`change-impact-graph-${layoutName.value}.png`, blob, "image/png", { source: "change-impact-graph" });
}

/** 符合畫面；節點很少時不要放大到一顆球佔半個畫面 */
function fit() {
  if (!cy) return;
  cy.fit(undefined, 36);
  if (cy.zoom() > 1.2) {
    cy.zoom(1.2);
    cy.center();
  }
}
/** 一進來的畫面：字要讀得到（規劃人員：不能只追求全部塞進畫面）。整張放得下就全部放進來；
 *  放不下時維持可讀的大小、以評估目標為中心，其餘用拖曳或右下角縮圖看；「符合畫面」按鈕仍然縮到全部看得到 */
const MIN_READABLE_ZOOM = 0.8;
function initialView() {
  if (!cy) return;
  fit();
  if (cy.zoom() < MIN_READABLE_ZOOM) {
    cy.zoom(MIN_READABLE_ZOOM);
    const root = cy.nodes("[root = 1]");
    if (root.nonempty()) cy.center(root);
    else cy.center();
  }
}
function impactOf(n: cytoscape.NodeSingular) { return String(n.data("impact") ?? ""); }

// ── 縮圖（GraphMinimap）──
const miniRef = ref<InstanceType<typeof GraphMinimap> | null>(null);
function updateView() { miniRef.value?.redraw(); }

function redraw() {
  picked.value = null;
  pickedGroup.value = null;
  void nextTick(draw);
}
function expandGroup(id: string) {
  expanded.value = new Set([...expanded.value, id]);
  redraw();
}
function toggleAll() {
  expandAll.value = !expandAll.value;
  if (!expandAll.value) expanded.value = new Set();
  redraw();
}

async function load() {
  loading.value = true;
  error.value = "";
  expanded.value = new Set();
  expandAll.value = false;
  try {
    data.value = await getRunRelations(props.runId);
  } catch (e) { error.value = apiErrMsg(e); } finally { loading.value = false; }
  await nextTick();
  draw();
}

function onResize() { winW.value = window.innerWidth; }
// 明細欄打開／關上時圖的寬度變了：重新量尺寸並重新置中（規劃人員：開啟明細時圖面要縮排或重新置中，
// 不能被切掉一半）；明細欄開著時換點別的節點不重新置中，免得畫面一直跳
const panelOpen = computed(() => !!picked.value || !!pickedGroup.value);
watch(fill.height, () => nextTick(() => {
  if (!cy) return;
  cy.resize();
  initialView();
  updateView();
}));
watch(panelOpen, () => nextTick(() => {
  if (!cy) return;
  cy.resize();
  initialView();
  if (selected) {
    const n = cy.getElementById(selected);
    const p = n.nonempty() ? n.renderedPosition() : null;
    if (p && (p.x < 0 || p.y < 0 || p.x > cy.width() || p.y > cy.height())) cy.center(n);
  }
  updateView();
}));
watch(narrow, () => nextTick(draw));
watch(() => props.runId, () => void load());
onMounted(() => { window.addEventListener("resize", onResize); void load(); });
onBeforeUnmount(() => { window.removeEventListener("resize", onResize); cy?.destroy(); });
</script>

<style scoped>
/* 圖往下佔滿（使用者 2026-10-08：圖下方還有空間）：頁籤捲到頂時，畫布到視窗底；太矮的螢幕至少 560px */
.irg-wrap { display: flex; gap: 12px; height: max(560px, calc(100dvh - 250px)); }
.irg-stage { position: relative; flex: 1; min-width: 0; height: 100%; }
/* box-sizing：寬 100% 再加框線會超出外框，右邊的框線被裁掉（使用者 2026-10-08） */
.irg-canvas { box-sizing: border-box; width: 100%; height: 100%;
  border: 1px solid var(--n-border-color, rgba(128, 128, 128, .25)); border-radius: 10px; }
.irg-bar { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; justify-content: space-between;
  margin-bottom: 8px; font-size: 12px; }
.irg-legends { display: flex; flex-wrap: wrap; gap: 6px 14px; align-items: center; min-width: 0; }
.irg-tools { display: flex; flex-wrap: nowrap; gap: 8px; align-items: center; margin-left: auto; }
.irg-btn { display: inline-flex; align-items: center; gap: 4px; }
.irg-legend { display: inline-flex; align-items: center; gap: 5px; }
.irg-dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; flex: none; }
.irg-ico { display: inline-flex; align-items: center; justify-content: center; width: 18px; height: 18px;
  border-radius: 50%; background: #6b7280; color: #fff; flex: none; }
.irg-ico--sm { width: 16px; height: 16px; }
.irg-ico--lg { width: 26px; height: 26px; }
.irg-line { display: inline-block; width: 22px; height: 0; border-top: 1.5px solid #c4c8cf; flex: none; }
.irg-line--outage { border-top: 3px solid #4b5563; }
.irg-line--inferred { border-top: 1.5px dashed #8a8f98; }
.irg-muted { opacity: .65; }
.irg-empty { opacity: .7; padding: 20px 0; }
.irg-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
.irg-list li { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
/* 圖例（收在按鈕裡） */
.irg-legend-pop { display: flex; flex-direction: column; gap: 6px; font-size: 12.5px; }
.irg-pop-h { font-weight: 600; margin-top: 6px; font-size: 12px; opacity: .75; }
.irg-pop-h:first-child { margin-top: 0; }
.irg-pop-types { display: flex; flex-wrap: wrap; gap: 6px 14px; }
.irg-arrow-note { font-weight: 600; }
.irg-dot--blocker { background: transparent; box-shadow: inset 0 0 0 2.5px #d03050; }
/* 明細欄：在圖旁邊，不蓋住圖；分區、標題、計數，名稱完整換行 */
.irg-side { width: 340px; flex: none; height: 100%; overflow: auto; box-sizing: border-box; padding: 12px 14px 72px;
  border: 1px solid var(--n-border-color, rgba(128, 128, 128, .25)); border-radius: 10px; font-size: 13px; }
.irg-side-head { display: flex; align-items: flex-start; gap: 10px; }
.irg-side-title { flex: 1; min-width: 0; font-weight: 700; font-size: 15px; line-height: 1.35; word-break: break-word; }
.irg-tags { display: flex; flex-wrap: wrap; gap: 6px; margin: 10px 0 4px; }
.irg-sec { margin-top: 14px; padding-top: 10px; border-top: 1px solid rgba(128, 128, 128, .18); }
.irg-sec-h { font-weight: 600; display: flex; align-items: center; gap: 6px; }
.irg-count { font-size: 11px; font-weight: 600; padding: 0 7px; border-radius: 9px; background: rgba(128, 128, 128, .16); }
.irg-sec-cap { font-size: 12px; opacity: .6; margin: 2px 0 6px; }
.irg-items { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 2px; }
.irg-item { display: flex; align-items: center; gap: 8px; min-width: 0; padding: 4px 6px; border-radius: 6px; }
.irg-item--link { cursor: pointer; }
.irg-item--link:hover { background: rgba(128, 128, 128, .12); }
.irg-item-name { min-width: 0; word-break: break-word; }
.irg-reasons { margin: 4px 0 0; padding-left: 18px; line-height: 1.5; }
.irg-ev { margin: 4px 0 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 6px; }
.irg-ev-label { word-break: break-word; }
</style>
