<script setup lang="ts">
/**
 * 物件上下關係鏈：把 區段 → 子網路 → 位址 → 裝置 → 機櫃 → 機房 等相關物件
 * 橫向用箭頭串起來，一眼看出上下關係。每個節點可點 → 跳到該物件。
 * currentId 標出「目前所在」的節點（高亮、不可點）。
 */
import { computed, ref, watch, nextTick, onMounted, onBeforeUnmount } from "vue";
import { virtRouteFor } from "@/utils/virt";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import { NIcon, NPopover } from "naive-ui";
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
// vmnode 的名稱依平台而定：PVE 叫「節點」、VMware 叫「ESXi 主機」——
// 一律標成「PVE 節點」的話，VMware 使用者看到的是別家產品的名詞。
function nodeTypeLabel(n: RelationNode): string {
  if (n.type === "vmnode") {
    return n.platform === "vmware" ? t("relations.esxi_host")
      : n.platform === "hyper-v" ? t("relations.hyperv_host")
      : n.platform === "openstack" ? t("relations.openstack_host") : t("relations.pve_node");
  }
  return TYPE_LABEL.value[n.type] ?? n.type;
}
const TYPE_LABEL = computed<Record<string, string>>(() => ({
  section: t("nav.sections"), subnet: t("nav.subnets"), ip: t("nav.addresses"),
  vm: t("relations.vm"), vmnode: t("relations.pve_node"),
  device: t("nav.devices"), rack: t("nav.racks"), location: t("nav.locations"),
}));

/** 裝置有不只一個 IP：點卡片跳出清單（每個 IP、主機名稱、網段），不直接跳頁 */
function hasList(n: RelationNode): boolean {
  return n.type === "ip" && (n.ips?.length ?? 0) > 1 && n.id !== props.currentId;
}
function openIp(id: string) { void router.push({ name: "address-detail", params: { id } }); }

function go(n: RelationNode) {
  if (n.id === props.currentId || hasList(n)) return;
  switch (n.type) {
    case "section":  router.push({ name: "section-detail", params: { id: n.id } }); break;
    case "subnet":   router.push({ name: "subnet-detail", params: { id: n.id } }); break;
    // 虛擬化拆成 PVE / VMware 兩頁後，這裡不能再寫死 —— 依節點自帶的平台決定，
    // 否則 VMware 的 VM 會被送到 PVE 那一頁看到 0 筆（實機回報）。
    case "vm":       router.push({ name: virtRouteFor(n.platform) }); break;
    // 虛擬化主機：對得到實體裝置（id 為 UUID）就跳裝置詳細資料；只有名稱（id 以 host: 開頭）則不動作
    case "vmnode":   if (!n.id.startsWith("host:")) router.push({ name: "device-detail", params: { id: n.id } }); break;
    case "device":   router.push({ name: "device-detail", params: { id: n.id } }); break;
    // 直接進這個 IP 的頁面。以前是拿位址去清單模糊搜尋：10.0.0.21 會連 .214、.217 一起出來（客戶 11-02）。
    // 裝置有好幾個 IP 時不走這裡，改跳出清單讓人挑（hasList）
    case "ip":       router.push({ name: "address-detail", params: { id: n.id } }); break;
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
      <n-popover trigger="click" placement="bottom-start" :disabled="!hasList(n)" :show-arrow="false"
                 style="padding: 4px 0; max-width: 420px">
        <template #trigger>
          <div
            class="rel-node" :class="{ current: n.id === currentId, [n.type]: true }"
            :title="nodeTypeLabel(n)" :data-testid="n.type === 'ip' ? 'rel-ip' : undefined"
            @click="go(n)"
          >
            <div class="rel-type">
              <n-icon :size="13"><component :is="ICONS[n.type]" /></n-icon>
              <span>{{ nodeTypeLabel(n) }}</span>
            </div>
            <div class="rel-label">{{ n.label }}<span v-if="n.more" class="rel-more" data-testid="rel-more">{{ t("relations.more_ips", { n: n.more }) }}</span></div>
            <div v-if="n.sub" class="rel-sub">{{ n.sub }}</div>
          </div>
        </template>
        <div class="rel-ip-list" data-testid="rel-ip-list">
          <div class="rel-ip-head">{{ t("relations.device_ips", { n: n.ips?.length ?? 0 }) }}</div>
          <a v-for="x in n.ips" :key="x.id" href="#" class="rel-ip-row" data-testid="rel-ip-row"
             @click.prevent="openIp(x.id)">
            <span class="rel-ip-addr">{{ x.ip }}</span>
            <span class="rel-ip-host">{{ x.hostname || "—" }}</span>
            <span class="rel-ip-net">{{ x.subnet || "" }}</span>
          </a>
        </div>
      </n-popover>
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
/* 裝置還有其他 IP（關係鏈只走一條，用主要 IP 或作業系統那個）：小字標出來，免得以為只有這一個 */
.rel-more { font-size: 11px; font-weight: 400; opacity: .7; margin-left: 6px; white-space: nowrap; }
.rel-ip-list { display: flex; flex-direction: column; min-width: 300px; }
.rel-ip-head { font-size: 11px; opacity: .6; padding: 4px 12px 6px; }
.rel-ip-row {
  display: grid; grid-template-columns: 120px 1fr auto; gap: 10px; align-items: baseline;
  padding: 6px 12px; color: inherit; text-decoration: none; border-radius: 4px;
}
.rel-ip-row:hover { background: rgba(127, 127, 127, 0.12); }
.rel-ip-addr { font-weight: 600; color: var(--primary-color, #18a058); font-variant-numeric: tabular-nums; }
.rel-ip-host { font-size: 12px; opacity: .8; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rel-ip-net { font-size: 11px; opacity: .6; white-space: nowrap; font-variant-numeric: tabular-nums; }
</style>
