<template>
  <div class="cip-page">
    <n-card :bordered="false" content-style="padding: 14px 16px">
      <div class="cip-head">
        <div class="cip-head__title">
          <n-icon :size="20"><ChangeImpactIcon /></n-icon>
          <span>{{ t("change_impact.title") }}</span>
        </div>
        <n-space v-if="settings.enabled" :size="8">
          <!-- 服務（M2）：維護／停機評估據此判斷業務影響。共享基礎設施，全域讀取才看得到 -->
          <n-button v-if="auth.me?.is_admin || auth.me?.has_global_read" size="small" data-testid="cip-services"
                    @click="router.push({ name: 'change_impact_services' })">
            <template #icon><n-icon><ServiceIcon /></n-icon></template>{{ t("change_impact.svc.title") }}
          </n-button>
          <n-button type="primary" size="small" data-testid="cip-new" @click="wizard = true">
            <template #icon><n-icon><PlusIcon /></n-icon></template>{{ t("change_impact.new_plan") }}
          </n-button>
        </n-space>
      </div>
      <p class="cip-intro">{{ t("change_impact.intro") }}</p>
    </n-card>

    <n-result v-if="loaded && !settings.enabled" status="info" :title="t('change_impact.disabled_title')"
              :description="t('change_impact.disabled_desc')" style="margin-top: 24px" data-testid="cip-disabled" />

    <n-card v-else size="small">
      <div class="cip-filters">
        <n-select v-model:value="scenario" :options="scenarioOptions" clearable size="small" style="width: 160px"
                  :placeholder="t('change_impact.f_scenario')" />
        <n-select v-model:value="lifecycle" :options="lifecycleOptions" clearable size="small" style="width: 150px"
                  :placeholder="t('change_impact.col_lifecycle')" />
        <n-input v-model:value="q" size="small" clearable style="width: 220px" :placeholder="t('common.search')" />
        <n-checkbox v-model:checked="awaiting" data-testid="cip-filter-awaiting">{{ t("change_impact.filter_awaiting") }}</n-checkbox>
      </div>
      <n-data-table :columns="columns" :data="rows" :loading="loading" size="small" :bordered="false"
                    :row-key="(r: ChangePlan) => r.id" :row-props="rowProps" :scroll-x="900"
                    :row-class-name="(r: ChangePlan) => r.lifecycle === 'cancelled' ? 'cip-row-cancelled' : ''"
                    :pagination="{ page, pageSize, itemCount: total, onChange: (p: number) => { page = p } }" remote
                    @update:sorter="onSorter" data-testid="cip-list" />
    </n-card>

    <ChangeImpactWizard v-model:show="wizard" />
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import {
  NButton, NCard, NCheckbox, NDataTable, NIcon, NInput, NResult, NSelect, NSpace, NTag, type DataTableColumns,
} from "naive-ui";
import { ALL_SCENARIOS, listPlans, type ChangePlan } from "@/api/changeImpact";
import ChangeImpactWizard from "@/components/ChangeImpactWizard.vue";
import { useChangeImpact } from "@/composables/useChangeImpact";
import { ChangeImpactIcon, PlusIcon, ServiceIcon } from "@/icons";
import { useAuthStore } from "@/stores/auth";
import { fmtDateTime } from "@/utils/datetime";
import { decisionType, lifecycleType } from "@/utils/changeImpact";

const { t } = useI18n();
const router = useRouter();
const auth = useAuthStore();
const { settings, load } = useChangeImpact();
const loaded = ref(false);
const wizard = ref(false);
const rows = ref<ChangePlan[]>([]);
const total = ref(0);
const page = ref(1);
const pageSize = 50;
const loading = ref(false);
const scenario = ref<string | null>(null);
const awaiting = ref(false);
const lifecycle = ref<string | null>(null);
const q = ref("");

const scenarioOptions = computed(() => ALL_SCENARIOS
  .map((v) => ({ label: t(`change_impact.scenario.${v}`), value: v })));
const lifecycleOptions = computed(() => ["draft", "in_review", "approved", "in_progress", "verified", "closed", "cancelled"]
  .map((v) => ({ label: t(`change_impact.lifecycle.${v}`), value: v })));

async function fetchRows() {
  loading.value = true;
  try {
    const r = await listPlans({ page: page.value, page_size: pageSize, scenario_type: scenario.value || undefined,
                                lifecycle: lifecycle.value || undefined, q: q.value.trim() || undefined,
                                awaiting_my_review: awaiting.value || undefined, sort: sortState.value?.key,
                                order: sortState.value ? (sortState.value.order === "descend" ? "desc" : "asc") : undefined });
    rows.value = r.items;
    total.value = r.total;
  } finally {
    loading.value = false;
  }
}

let timer: ReturnType<typeof setTimeout> | null = null;
watch([scenario, lifecycle, q, awaiting], () => {
  page.value = 1;
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => void fetchRows(), 250);
});
watch(page, () => void fetchRows());

// 後端分頁：點標題排序交給後端（分頁前排）；最近一次評估是組合欄位，不排序
const sortState = ref<{ key: string; order: "ascend" | "descend" } | null>(null);
const so = (k: string) => (sortState.value?.key === k ? sortState.value.order : false);
function onSorter(st: { columnKey: string | number; order: "ascend" | "descend" | false } | null) {
  sortState.value = st && st.order ? { key: String(st.columnKey), order: st.order } : null;
  if (page.value !== 1) page.value = 1;   // 換頁的 watch 會重抓
  else void fetchRows();
}
const columns = computed<DataTableColumns<ChangePlan>>(() => [
  { title: t("change_impact.col_title"), key: "title", minWidth: 220, sorter: true, sortOrder: so("title"),
    render: (r) => h("div", null, [h("div", { class: "cip-strong" }, r.title),
                                   h("div", { class: "cip-muted" }, r.target_label
                                     + (r.parameters.new_ip ? ` → ${r.parameters.new_ip}` : ""))]) },
  { title: t("change_impact.f_scenario"), key: "scenario_type", width: 120, sorter: true, sortOrder: so("scenario_type"),
    render: (r) => t(`change_impact.scenario.${r.scenario_type}`) },
  { title: t("change_impact.col_lifecycle"), key: "lifecycle", width: 110, sorter: true, sortOrder: so("lifecycle"),
    render: (r) => h(NTag, { size: "small", type: lifecycleType(r.lifecycle) },
                     { default: () => t(`change_impact.lifecycle.${r.lifecycle}`) }) },
  { title: t("change_impact.col_latest_run"), key: "latest_run", minWidth: 220,
    render: (r) => {
      const lr = r.latest_run;
      if (!lr) return h("span", { class: "cip-muted" }, t("change_impact.never_run"));
      if (!["completed", "partial"].includes(lr.job_status)) return t(`change_impact.job.${lr.job_status}`);
      // 你的權限範圍跟分析當時不同時，後端不給數量（避免從數字知道看不到的項目）：點進去才依你的範圍重算
      const c = lr.counts;
      return h("div", { class: "cip-run-cell" }, [
        lr.decision_status ? h(NTag, { size: "small", type: decisionType(lr.decision_status) },
                               { default: () => t(`change_impact.decision.${lr.decision_status}`) }) : null,
        c ? h("span", { class: "cip-muted" }, t("change_impact.counts_short", {
          blockers: c.blockers ?? 0, review: c.review ?? 0, gaps: c.gaps ?? 0 })) : null,
      ]);
    } },
  { title: t("change_impact.col_window"), key: "planned_start", width: 170, sorter: true, sortOrder: so("planned_start"),
    render: (r) => (r.planned_start ? fmtDateTime(r.planned_start) : "—") },
  { title: t("change_impact.col_created"), key: "created_at", width: 170, render: (r) => fmtDateTime(r.created_at),
    sorter: true, sortOrder: so("created_at") },
]);

function rowProps(r: ChangePlan) {
  return { style: "cursor: pointer", onClick: () => void router.push({ name: "change_impact_plan", params: { id: r.id } }) };
}

onMounted(async () => {
  await load(true);
  loaded.value = true;
  if (settings.value.enabled) await fetchRows();
});
</script>

<style scoped>
.cip-page { display: flex; flex-direction: column; gap: 12px; }
.cip-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.cip-head__title { display: flex; align-items: center; gap: 8px; font-size: 17px; font-weight: 600; }
.cip-intro { margin: 8px 0 0; font-size: 13px; opacity: .75; line-height: 1.6; }
.cip-filters { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
/* 已取消的計畫整列變淡（使用者 2026-10-08），一眼跟進行中的分開 */
:deep(tr.cip-row-cancelled td) { opacity: .45; }
</style>

<style>
.cip-strong { font-weight: 600; }
.cip-muted { font-size: 12.5px; opacity: .65; }
.cip-run-cell { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
</style>
