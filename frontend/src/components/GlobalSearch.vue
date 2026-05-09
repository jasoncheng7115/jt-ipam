<script setup lang="ts">
/**
 * 全域搜尋框 — 帶自動類型偵測（CIDR / IP / MAC / 自由文字）。
 *
 * 比 phpIPAM 改進的地方：
 *  - 一個 input 接受所有查詢類型，後端自動 dispatch
 *  - debounce + race-safe（後請求覆蓋前請求結果）
 *  - 結果按類別分組，每類限 8 筆，分數排序
 */
import { computed, ref, watch } from "vue";
import { useRouter } from "vue-router";
import {
  NAutoComplete,
  NTag,
  NSpace,
  NText,
  type AutoCompleteOption,
} from "naive-ui";
import { search, type SearchHit } from "@/api/search";

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
    return;
  }
  debounceTimer = window.setTimeout(() => {
    void runSearch(val);
  }, 250);
});

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

interface GroupedOption {
  type: string;
  label: string;
  children: AutoCompleteOption[];
}

const options = computed<GroupedOption[]>(() => {
  // 按類別分組
  const groups: Record<string, SearchHit[]> = {};
  for (const h of hits.value) {
    (groups[h.type] ??= []).push(h);
  }
  const order = ["section", "subnet", "vlan", "ip_address", "device"];
  const labelMap: Record<string, string> = {
    section: "Sections",
    subnet: "Subnets",
    vlan: "VLANs",
    ip_address: "IP Addresses",
    device: "Devices",
  };
  return order
    .filter((t) => groups[t]?.length)
    .map((t) => ({
      type: t,
      label: labelMap[t] || t,
      children: groups[t].map((h) => ({
        label: h.label,
        value: `${h.type}:${h.id}`,
        // 把 sublabel + score 用 disabled prop 不適合；用 render 模式
      })),
    }));
});

function navigateTo(value: string) {
  const [type, id] = value.split(":", 2);
  switch (type) {
    case "subnet":
      router.push({ name: "subnet-detail", params: { id } });
      break;
    case "section":
      router.push({ name: "sections" }); // TODO: section detail page
      break;
    case "ip_address":
      router.push({ name: "addresses" }); // TODO: address detail
      break;
    case "device":
      // TODO: device detail
      break;
    case "vlan":
      // TODO
      break;
  }
  q.value = "";
  hits.value = [];
}

function detectedTagType(d: string): "info" | "success" | "warning" | "default" {
  if (d === "cidr" || d === "ip") return "success";
  if (d === "mac") return "info";
  if (d === "vlan_number") return "warning";
  return "default";
}
</script>

<template>
  <n-space align="center" :wrap="false" style="width: 280px">
    <n-auto-complete
      v-model:value="q"
      :options="options as any"
      :loading="loading"
      placeholder="Search anything: hostname, IP, CIDR, MAC, VLAN…"
      clearable
      :get-show="() => q.trim().length >= 2"
      style="width: 280px"
      @select="(v: string) => navigateTo(v)"
    />
    <n-tag
      v-if="detected && detected !== 'free' && detected !== 'empty'"
      size="small"
      :type="detectedTagType(detected)"
      style="margin-left: 4px"
    >
      {{ detected }}
    </n-tag>
  </n-space>
</template>
