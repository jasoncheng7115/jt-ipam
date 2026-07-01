<script setup lang="ts">
/**
 * 物件上下關係鏈：把 區段 → 子網路 → 位址 → 裝置 → 機櫃 → 機房 等相關物件
 * 橫向用箭頭串起來，一眼看出上下關係。每個節點可點 → 跳到該物件。
 * currentId 標出「目前所在」的節點（高亮、不可點）。
 */
import { computed, ref, watch, nextTick, onMounted, onBeforeUnmount } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import { NIcon } from "naive-ui";
import {
  SectionsIcon, SubnetsIcon, AddressesIcon, DevicesIcon, RacksIcon, LocationsIcon, VirtualizationIcon,
} from "@/icons";
import type { RelationNode } from "@/api/relations";

const props = defineProps<{ nodes: RelationNode[]; currentId?: string | null }>();
const { t } = useI18n();
const router = useRouter();

// 量測哪幾個節點是「換行後的第一格」（offsetTop 比前一格大）→ 前導連接符改用 ↳
const chainEl = ref<HTMLElement | null>(null);
const segEls: HTMLElement[] = [];
const newline = ref<boolean[]>([]);
function setSeg(el: any, i: number) { if (el) segEls[i] = el as HTMLElement; }
// 只判斷「哪些格是換行後的第一格」→ 那一格前面改用 ↳ 接續符（取代原本易畫歪的 SVG 折線）
function recompute() {
  const flags: boolean[] = [];
  for (let i = 0; i < segEls.length; i++) {
    flags[i] = i > 0 && !!segEls[i] && !!segEls[i - 1]
      && segEls[i].offsetTop > segEls[i - 1].offsetTop + 4;
  }
  newline.value = flags;
}
let ro: ResizeObserver | null = null;
onMounted(() => {
  void nextTick(recompute);
  if (chainEl.value) { ro = new ResizeObserver(() => recompute()); ro.observe(chainEl.value); }
});
onBeforeUnmount(() => ro?.disconnect());
watch(() => props.nodes, () => { segEls.length = 0; void nextTick(recompute); }, { deep: true });

const ICONS: Record<string, any> = {
  section: SectionsIcon, subnet: SubnetsIcon, ip: AddressesIcon, vm: VirtualizationIcon,
  vmnode: DevicesIcon, device: DevicesIcon, rack: RacksIcon, location: LocationsIcon,
};
const TYPE_LABEL = computed<Record<string, string>>(() => ({
  section: t("nav.sections"), subnet: t("nav.subnets"), ip: t("nav.addresses"),
  vm: t("relations.vm"), vmnode: t("relations.pve_node"),
  device: t("nav.devices"), rack: t("nav.racks"), location: t("nav.locations"),
}));

function go(n: RelationNode) {
  if (n.id === props.currentId) return;
  switch (n.type) {
    case "section":  router.push({ name: "section-detail", params: { id: n.id } }); break;
    case "subnet":   router.push({ name: "subnet-detail", params: { id: n.id } }); break;
    case "vm":       router.push({ name: "virt" }); break;
    // PVE 節點：對得到實體裝置（id 為 UUID）就跳裝置詳情；無實體裝置（id 以 pve: 開頭）則不動作
    case "vmnode":   if (!n.id.startsWith("pve:")) router.push({ name: "device-detail", params: { id: n.id } }); break;
    case "device":   router.push({ name: "device-detail", params: { id: n.id } }); break;
    case "ip":       router.push({ name: "addresses", query: { q: n.label } }); break;
    case "rack":     router.push({ name: "racks" }); break;
    case "location": router.push({ name: "locations" }); break;
  }
}
</script>

<template>
  <div ref="chainEl" class="rel-chain">
    <div
      v-for="(n, i) in nodes" :key="n.type + n.id"
      class="rel-seg" :ref="(el) => setSeg(el, i)"
    >
      <!-- 每格前面都放一個往右箭頭（換行後第一格也有 → 在左邊）；第一格除外 -->
      <span v-if="i > 0" class="rel-arrow">→</span>
      <div
        class="rel-node" :class="{ current: n.id === currentId, [n.type]: true }"
        :title="TYPE_LABEL[n.type]"
        @click="go(n)"
      >
        <div class="rel-type">
          <n-icon :size="13"><component :is="ICONS[n.type]" /></n-icon>
          <span>{{ TYPE_LABEL[n.type] }}</span>
        </div>
        <div class="rel-label">{{ n.label }}</div>
        <div v-if="n.sub" class="rel-sub">{{ n.sub }}</div>
      </div>
      <!-- 下一格要換行時，本格（該列最右）右邊也放一個往右箭頭 -->
      <span v-if="newline[i + 1]" class="rel-arrow">→</span>
    </div>
  </div>
</template>

<style scoped>
.rel-chain {
  display: flex;
  align-items: stretch;
  flex-wrap: wrap;
  gap: 6px;
  row-gap: 10px;
}
.rel-chain { position: relative; }
.rel-seg { position: relative; display: inline-flex; align-items: stretch; gap: 6px; z-index: 1; }
.rel-node {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 96px;
  max-width: 220px;
  padding: 6px 12px;
  border: 1px solid rgba(127, 127, 127, 0.28);
  border-radius: 8px;
  background: rgba(127, 127, 127, 0.05);
  cursor: pointer;
  transition: border-color .15s, background .15s, transform .1s;
}
.rel-node:hover { border-color: var(--primary-color, #18a058); transform: translateY(-1px); }
.rel-node.current {
  cursor: default;
  border-color: var(--primary-color, #18a058);
  background: rgba(24, 160, 88, 0.12);
  box-shadow: 0 0 0 1px var(--primary-color, #18a058) inset;
}
.rel-type {
  display: flex; align-items: center; gap: 4px;
  font-size: 11px; opacity: 0.6; white-space: nowrap;
}
.rel-label {
  font-size: 13px; font-weight: 600;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.rel-sub {
  font-size: 11px; opacity: 0.65;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.rel-arrow {
  display: flex; align-items: center;
  color: var(--n-text-color-3, #999); font-size: 16px; user-select: none;
}
/* 換行後第一格前面的接續符：表示「從上一排接下來」，用品牌色 ↳ */
.rel-arrow.rel-wrap {
  color: var(--primary-color, #18a058); font-size: 18px; font-weight: 700;
  align-self: center;
}
</style>
