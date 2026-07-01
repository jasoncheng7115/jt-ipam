<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NSpace, NIcon, NSelect, NButton, NTag, NDataTable, NAlert, NInput,
  NTabs, NTabPane, type DataTableColumns,
} from "naive-ui";
import { FirewallIcon, RefreshIcon } from "@/icons";
import {
  listPfSense, getPfSenseRules, listPfSenseAliases, type PfSense, type PfRule,
} from "@/api/pfsense";
import { autoSort } from "@/composables/useTableSort";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import { useTablePagination } from "@/composables/useTablePagination";
import { useTableQuickFilter } from "@/composables/useTableQuickFilter";

const { t } = useI18n();
const pg = useTablePagination();
const insts = ref<PfSense[]>([]);
const fwId = ref<string | null>(null);
const rules = ref<PfRule[]>([]);
const aliases = ref<{ name: string; type: string | null; members: string[]; descr: string | null }[]>([]);
const loading = ref(false);

const fwOptions = computed(() => insts.value.map((i) => ({ label: i.name, value: i.id })));
const { query: ruleQ, filtered: rulesFiltered } = useTableQuickFilter(rules);
const { query: aliasQ, filtered: aliasesFiltered } = useTableQuickFilter(aliases);

const rPrefs = useColumnPrefs("pfsense_view_rules",
  ["disabled", "type", "interface", "protocol", "source", "destination", "destination_port", "descr", "tracker"],
  ["disabled", "type", "interface", "protocol", "source", "destination", "destination_port", "descr"]);
const rPicker = computed(() => [
  { key: "disabled", label: t("cols.enabled") }, { key: "type", label: t("pfsense_admin.r_action") },
  { key: "interface", label: t("pfsense_admin.r_iface") }, { key: "protocol", label: t("pfsense_admin.r_proto") },
  { key: "source", label: t("pfsense_admin.r_source") }, { key: "destination", label: t("pfsense_admin.r_dest") },
  { key: "destination_port", label: t("pfsense_admin.r_port") }, { key: "descr", label: t("common.description") },
  { key: "tracker", label: "tracker" },
]);
const aPrefs = useColumnPrefs("pfsense_view_aliases",
  ["name", "type", "members", "descr"], ["name", "type", "members", "descr"]);
const aPicker = computed(() => [
  { key: "name", label: t("common.name") }, { key: "type", label: t("pfsense_admin.r_action") },
  { key: "members", label: t("firewall_admin.content") }, { key: "descr", label: t("common.description") },
]);

async function loadInstances() {
  insts.value = (await listPfSense(50, 0)).items;
  if (!fwId.value && insts.value.length) fwId.value = insts.value[0].id;
}
async function loadData() {
  if (!fwId.value) { rules.value = []; aliases.value = []; return; }
  loading.value = true;
  try {
    const [ru, al] = await Promise.all([getPfSenseRules(fwId.value), listPfSenseAliases(fwId.value)]);
    rules.value = ru.items;
    aliases.value = al.items;
  } catch { rules.value = []; aliases.value = []; }
  finally { loading.value = false; }
}
watch(fwId, () => void loadData());

const allRuleCols = computed<DataTableColumns<PfRule>>(() => autoSort([
  { title: t("cols.enabled"), key: "disabled", width: 70,
    render: (r) => h(NTag, { size: "small", type: r.disabled ? "default" : "success" },
      () => r.disabled ? t("common.no") : t("common.yes")) },
  { title: t("pfsense_admin.r_action"), key: "type", width: 80,
    render: (r) => h(NTag, { size: "small", type: r.type === "pass" ? "success" : (r.type === "block" || r.type === "reject") ? "error" : "default" }, () => r.type ?? "—") },
  { title: t("pfsense_admin.r_iface"), key: "interface", width: 100 },
  { title: t("pfsense_admin.r_proto"), key: "protocol", width: 90, render: (r) => r.protocol ?? "any" },
  { title: t("pfsense_admin.r_source"), key: "source", minWidth: 130, ellipsis: { tooltip: true }, render: (r) => String(r.source ?? "any") },
  { title: t("pfsense_admin.r_dest"), key: "destination", minWidth: 130, ellipsis: { tooltip: true }, render: (r) => String(r.destination ?? "any") },
  { title: t("pfsense_admin.r_port"), key: "destination_port", width: 90, render: (r) => r.destination_port ?? "*" },
  { title: t("common.description"), key: "descr", minWidth: 150, ellipsis: { tooltip: true }, render: (r) => r.descr || "—" },
  { title: "tracker", key: "tracker", width: 110, render: (r) => r.tracker ?? "—" },
]));
const ruleCols = computed(() => allRuleCols.value.filter((c: any) => rPrefs.visibleKeys.value.includes(c.key)));
const allAliasCols = computed<DataTableColumns<any>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 160, ellipsis: { tooltip: true } },
  { title: t("pfsense_admin.r_action"), key: "type", width: 100, render: (r) => r.type ?? "—" },
  { title: t("firewall_admin.content"), key: "members", minWidth: 280, ellipsis: { tooltip: true },
    render: (r) => (r.members || []).join(" ") || "—" },
  { title: t("common.description"), key: "descr", minWidth: 150, ellipsis: { tooltip: true }, render: (r) => r.descr || "—" },
]));
const aliasCols = computed(() => allAliasCols.value.filter((c: any) => aPrefs.visibleKeys.value.includes(c.key)));

const tab = ref<"rules" | "aliases">("rules");
onMounted(async () => { await loadInstances(); await loadData(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><FirewallIcon /></n-icon>
        <span>{{ t("nav.pfsense_fw") }}</span>
      </n-space>
    </template>

    <n-alert type="info" style="margin-bottom: 12px">{{ t("pfsense_view.hint") }}</n-alert>

    <n-space align="center" style="margin-bottom: 12px">
      <n-select v-model:value="fwId" :options="fwOptions" style="width: 220px"
                :placeholder="t('pfsense_view.pick_fw')" />
      <n-button @click="loadData" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}
      </n-button>
    </n-space>

    <n-tabs v-model:value="tab" type="line">
      <n-tab-pane name="rules" :tab="`${t('firewall_admin.rules')} (${rules.length})`">
        <n-space align="center" style="margin-bottom: 8px">
          <n-input v-model:value="ruleQ" :placeholder="t('common.filter')" clearable style="width: 180px" />
          <ColumnPicker :all="rPicker" :visible="rPrefs.visibleKeys.value"
                        @update:visible="rPrefs.setVisible" @reset="rPrefs.reset" />
          <ExportButton :columns="ruleCols" :rows="rulesFiltered" filename="pfsense-rules" :title="t('pfsense_admin.rules')" />
        </n-space>
        <n-data-table :columns="ruleCols" :data="rulesFiltered" :loading="loading" :bordered="false"
                      size="small" :scroll-x="930" :pagination="pg" />
      </n-tab-pane>
      <n-tab-pane name="aliases" :tab="`${t('pfsense_admin.alias')} (${aliases.length})`">
        <n-space align="center" style="margin-bottom: 8px">
          <n-input v-model:value="aliasQ" :placeholder="t('common.filter')" clearable style="width: 180px" />
          <ColumnPicker :all="aPicker" :visible="aPrefs.visibleKeys.value"
                        @update:visible="aPrefs.setVisible" @reset="aPrefs.reset" />
          <ExportButton :columns="aliasCols" :rows="aliasesFiltered" filename="pfsense-aliases" :title="t('pfsense_admin.alias')" />
        </n-space>
        <n-data-table :columns="aliasCols" :data="aliasesFiltered" :loading="loading" :bordered="false"
                      size="small" :scroll-x="690" :pagination="pg" />
      </n-tab-pane>
    </n-tabs>
  </n-card>
</template>
