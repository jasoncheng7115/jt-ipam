<script setup lang="ts">
/**
 * 站對站 VPN 清單：「進階 → 站對站 VPN」與電路頁的「站對站 VPN」頁籤共用（客戶許願清單：VPN 跟電路當成同一種管）。
 * 名稱連到 VPN 詳細資料（聯絡人、跑在哪條電路上）。
 */
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import { NDataTable, NTag, useMessage, type DataTableColumns } from "naive-ui";
import { Physical } from "@/api/phase3";
import { apiErrMsg } from "@/api/client";
import { autoSort } from "@/composables/useTableSort";
import { useEntityLinks } from "@/composables/useEntityLinks";
import { useTablePagination } from "@/composables/useTablePagination";

const { t } = useI18n();
const msg = useMessage();
const links = useEntityLinks(useRouter());
const pg = useTablePagination();
const vpns = ref<any[]>([]);
const loading = ref(false);

const cols = computed<DataTableColumns<any>>(() => autoSort([
  // 欄寬給下限：沒設時手機寬度下名稱欄分到最少，「hq-branch-xxxx」被擠成一行一兩個字
  { title: t("common.name"), key: "name", minWidth: 160, render: (r) => links.vpn(r.id, r.name) },
  { title: t("cols.type"), key: "type", width: 110 },
  { title: t("common.status"), key: "status", width: 100 },
  {
    title: t("physical.vpn_peer"), key: "peer", minWidth: 260,
    render: (r) => {
      if (r.peered && r.a_device_name && r.b_device_name) {
        const reliable = r.pairing_method === "wireguard_pubkey";
        const badge = h(NTag, {
          size: "tiny", type: reliable ? "success" : "warning", bordered: false,
          style: "margin-left:8px",
        }, () => reliable ? t("physical.pair_reliable") : t("physical.pair_besteffort"));
        return h("span", { class: "vpn-peered" }, [
          `${r.a_device_name} ⇄ ${r.b_device_name}`, badge,
        ]);
      }
      if (r.b_endpoint) return h("span", { style: "opacity:.7" }, `→ ${r.b_endpoint}`);
      return h("span", { style: "opacity:.4" }, "—");
    },
  },
]));

async function refresh() {
  loading.value = true;
  try { vpns.value = await Physical.vpns(); }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}
defineExpose({ refresh });
onMounted(refresh);
</script>

<template>
  <n-data-table :columns="cols" :data="vpns" :loading="loading" :bordered="false" :pagination="pg"
                :scroll-x="630" data-testid="vpn-tunnel-table" />
</template>

<style scoped>
:deep(.vpn-peered) {
  color: #18a058;
  font-weight: 600;
}
</style>
