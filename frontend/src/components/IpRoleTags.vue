<script setup lang="ts">
/**
 * IP 角色標記（清單視覺化）：閘道 / DHCP 伺服器 / 在 DHCP 範圍內。
 * 用緊湊 icon（+tooltip）呈現，避免寬文字標籤把 IP 欄擠成一條線。
 * 旗標由後端讀取端推導（is_gateway / is_dhcp_server / dhcp_server_auto / in_dhcp_range / in_dhcp_lease）。
 * hideRange=true 時不畫「DHCP 範圍」點（給本身已有 DHCP 欄位的表格，如子網路詳情）。
 */
import { NIcon, NTooltip } from "naive-ui";
import { useI18n } from "vue-i18n";
import { GatewayIcon, DhcpServerIcon } from "@/icons";

const props = withDefaults(defineProps<{ row: any; hideRange?: boolean }>(), { hideRange: false });
const { t } = useI18n();
const r = () => props.row || {};
const isDhcpServer = () => r().is_dhcp_server || r().dhcp_server_auto;
const inRange = () => !props.hideRange && (r().in_dhcp_range || r().in_dhcp_lease);
</script>

<template>
  <span v-if="r().is_gateway || isDhcpServer() || inRange()" class="ip-roles">
    <n-tooltip v-if="r().is_gateway" :delay="150">
      <template #trigger><n-icon :size="15" color="#2080f0" class="r-ic"><GatewayIcon /></n-icon></template>
      {{ t("addresses.role_gateway") }} — {{ t("addresses.role_gateway_hint") }}
    </n-tooltip>
    <n-tooltip v-if="isDhcpServer()" :delay="150">
      <template #trigger><n-icon :size="15" color="#f0a020" class="r-ic"><DhcpServerIcon /></n-icon></template>
      {{ t("addresses.role_dhcp_server") }} — {{ r().is_dhcp_server ? t("addresses.role_dhcp_server_manual") : t("addresses.role_dhcp_server_auto") }}
    </n-tooltip>
    <n-tooltip v-if="inRange()" :delay="150">
      <template #trigger><span class="r-dot" /></template>
      {{ t("addresses.role_dhcp_range") }} — {{ t("addresses.role_dhcp_range_hint") }}
    </n-tooltip>
  </span>
</template>

<style scoped>
.ip-roles { display: inline-flex; align-items: center; gap: 3px; margin-left: 6px; vertical-align: middle; flex: none; }
.r-ic { display: inline-flex; }
.r-dot { width: 7px; height: 7px; border-radius: 50%; background: #909399; display: inline-block; }
</style>
