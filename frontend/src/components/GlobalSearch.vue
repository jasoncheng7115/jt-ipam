<script setup lang="ts">
/**
 * 全域搜尋框 — 帶自動類型偵測 (CIDR / IP / MAC / 自由文字)。
 *
 * 比 phpIPAM 改進的地方：
 *  - 一個 input 接受所有查詢類型，後端自動 dispatch
 *  - debounce + race-safe(後請求覆蓋前請求結果)
 *  - 結果按類別分組，每類限 8 筆，分數排序
 */
import { computed, h as vh, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import {
  NAutoComplete,
  NTag,
  NSpace,
  NIcon,
} from "naive-ui";
import { SearchIcon } from "@/icons";
import { search, type SearchHit } from "@/api/search";

const { t: tr } = useI18n();
const router = useRouter();
const q = ref("");
const hits = ref<SearchHit[]>([]);
const detected = ref<string>("");
const loading = ref(false);

let debounceTimer: number | null = null;
let lastIssued = 0;

watch(q, (val) => {
  if (debounceTimer !== null) window.clearTimeout(debounceTimer);
  if (!val || val.trim().length < 2) {
    hits.value = [];
    detected.value = "";
    loading.value = false;
    return;
  }
  // 立刻進 loading 狀態 — 不等 debounce + API，讓使用者馬上看到「搜尋中」
  loading.value = true;
  debounceTimer = window.setTimeout(() => {
    void runSearch(val);
  }, 250);
});

// 重新聚焦時：若框內仍留有上次的字，直接拿它當搜尋條件，而不是顯示「無結果」
function onFocus() {
  const val = q.value.trim();
  if (val.length >= 2 && hits.value.length === 0 && !loading.value) {
    void runSearch(val);
  }
}

async function runSearch(query: string) {
  loading.value = true;
  const myIssue = ++lastIssued;
  try {
    const res = await search(query, 8);
    if (myIssue !== lastIssued) return; // race-safe
    hits.value = res.results;
    detected.value = res.detected;
  } catch {
    // ignore — keep prior results
  } finally {
    if (myIssue === lastIssued) loading.value = false;
  }
}

const options = computed<any[]>(() => {
  // 還沒有結果、但正在搜尋 → 給一個 disabled 的「搜尋中」占位
  if (loading.value && hits.value.length === 0) {
    return [{
      type: "group",
      label: tr("global_search.searching"),
      key: "__loading",
      children: [{
        label: tr("global_search.searching"),
        value: "__loading",
        disabled: true,
      }],
    }];
  }
  if (!loading.value && hits.value.length === 0 && q.value.trim().length >= 2) {
    return [{
      type: "group",
      label: tr("global_search.no_results"),
      key: "__empty",
      children: [{
        label: tr("global_search.no_match"),
        value: "__empty",
        disabled: true,
      }],
    }];
  }
  const groups: Record<string, SearchHit[]> = {};
  for (const x of hits.value) {
    (groups[x.type] ??= []).push(x);
  }
  const order = ["section", "subnet", "vlan", "vm", "ip_address", "device", "vpn", "customer", "rack", "location", "nat", "dns_record", "firewall", "ip_request"];
  const labelMap: Record<string, string> = {
    section: tr("global_search.t_section"),
    subnet: tr("global_search.t_subnet"),
    vlan: "VLAN",
    vm: tr("nav.virtualization"),
    ip_address: tr("global_search.t_ip"),
    device: tr("global_search.t_device"),
    vpn: "VPN",
    customer: tr("nav.customers"),
    rack: tr("nav.racks"),
    location: tr("nav.locations"),
    nat: tr("nav.nat"),
    dns_record: tr("nav.dns"),
    firewall: tr("nav.firewall"),
    ip_request: tr("nav.requests"),
  };
  return order
    .filter((t) => groups[t]?.length)
    .map((t) => ({
      type: "group",
      label: labelMap[t] || t,
      key: t,
      children: groups[t].map((x) => ({
        label: x.label,
        value: `${x.type}:${x.id}`,
        sublabel: x.sublabel,
      })),
    }));
});

function renderOption(option: any) {
  // group title 用預設
  if (option.type === "group") return option.label;
  return vh("div", { style: "display: flex; flex-direction: column; padding: 2px 0;" }, [
    vh("div", { style: "font-weight: 500;" }, option.label),
    option.sublabel
      ? vh("div", { style: "font-size: 11px; opacity: 0.65;" }, option.sublabel)
      : null,
  ]);
}

function navigateTo(value: string) {
  if (value === "__loading" || value === "__empty") return;
  const [type, id] = value.split(":", 2);
  switch (type) {
    case "subnet":
      router.push({ name: "subnet-detail", params: { id } });
      break;
    case "section":
      router.push({ name: "section-detail", params: { id } });
      break;
    case "ip_address":
    case "vm":
      // VM/CT 搜尋結果導到其主 IP 詳情頁（可開 PVE 主控台）
      router.push({ name: "address-detail", params: { id } });
      break;
    case "device":
      router.push({ name: "device-detail", params: { id } });
      break;
    case "vlan":
      router.push({ name: "vlans" });
      break;
    case "vpn":
      router.push({ name: "vpn-tunnels" });
      break;
    case "customer":
      router.push({ name: "customer-detail", params: { id } });
      break;
    case "rack":
      router.push({ name: "racks" });
      break;
    case "location":
      router.push({ name: "locations" });
      break;
    case "nat":
      router.push({ name: "nat" });
      break;
    case "dns_record": {
      // 進「進階 → DNS 記錄」並把該記錄名稱代入搜尋欄（不是 DNS 整合設定頁）
      const hit = hits.value.find((h) => `${h.type}:${h.id}` === value);
      router.push({ name: "adv-dns-records", query: hit?.label ? { q: hit.label } : {} });
      break;
    }
    case "firewall":
      router.push({ name: "firewall" });
      break;
    case "ip_request":
      router.push({ name: "requests" });
      break;
  }
  q.value = "";
  hits.value = [];
}

// 鍵盤 Enter：若 naive 已對被選取的項目觸發 @select（navigateTo 會清空 hits），這裡看到
// hits 已空就不重複動作；否則（沒選到任何項目）就帶去最相關的第一筆結果。
function onEnter() {
  if (hits.value.length) {
    const h = hits.value[0];
    navigateTo(`${h.type}:${h.id}`);
  }
}

function detectedTagType(d: string): "info" | "success" | "warning" | "default" {
  if (d === "cidr" || d === "ip") return "success";
  if (d === "mac") return "info";
  if (d === "vlan_number" || d === "number") return "warning";
  return "default";
}
function detectedLabel(d: string): string {
  return ({
    cidr: "CIDR", ip: "IP", mac: "MAC",
    number: "VLAN / VMID", vlan_number: "VLAN",
  } as Record<string, string>)[d] || d;
}
</script>

<template>
  <n-space class="global-search" align="center" :wrap="false" :size="6">
    <span class="gs-badge"><n-icon :size="16"><SearchIcon /></n-icon></span>
    <n-auto-complete
      v-model:value="q"
      :options="options as any"
      :render-label="renderOption"
      :loading="loading"
      size="small"
      :placeholder="tr('global_search.placeholder')"
      clearable
      :get-show="() => q.trim().length >= 2"
      class="global-search__input"
      @select="(v: string) => navigateTo(v)"
      @keyup.enter="onEnter"
      @focus="onFocus"
    />
    <n-tag
      v-if="detected && detected !== 'free' && detected !== 'empty'"
      size="small"
      :type="detectedTagType(detected)"
      style="margin-left: 4px"
    >
      {{ detectedLabel(detected) }}
    </n-tag>
  </n-space>
</template>

<style scoped>
/* 桌機固定寬度；窄螢幕(手機)改成可縮、最寬不超過容器，避免撐破頂列 */
.gs-badge {
  width: 28px; height: 28px; border-radius: 8px; flex: 0 0 auto;
  display: inline-flex; align-items: center; justify-content: center;
  color: #18a058; background: rgba(24, 160, 88, 0.1);
  pointer-events: none;   /* 純裝飾，不可點，避免被誤認為按鈕 */
}
.global-search { width: 380px; flex-shrink: 0; }
.global-search__input { width: 320px; }
@media (max-width: 640px) {
  .global-search { width: 100%; flex: 1 1 100%; flex-shrink: 1; }
  .global-search__input { width: 100%; min-width: 0; }
}
</style>
