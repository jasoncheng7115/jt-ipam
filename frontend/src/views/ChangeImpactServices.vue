<template>
  <!-- IP 變更評估 M2：服務與依賴。維護／停機評估據此判斷服務會不會中斷；不是 CMDB。
       共享基礎設施：全域讀取才看得到，只有管理員能改（後端也會擋） -->
  <div class="svcs-page">
    <n-card :bordered="false" content-style="padding: 14px 16px">
      <div class="svcs-head">
        <div class="svcs-head__title">
          <n-icon :size="20"><ServiceIcon /></n-icon>
          <span>{{ t("change_impact.svc.title") }}</span>
          <n-tag size="small" :bordered="false">{{ total }}</n-tag>
        </div>
        <n-space :size="8">
          <n-button v-if="canEdit" type="primary" size="small" data-testid="svc-new" @click="open(null)">
            <template #icon><n-icon><PlusIcon /></n-icon></template>{{ t("change_impact.svc.new") }}
          </n-button>
          <n-button size="small" @click="router.push({ name: 'change_impact' })">
            <template #icon><n-icon><ArrowLeftIcon /></n-icon></template>{{ t("common.back") }}
          </n-button>
        </n-space>
      </div>
      <p class="svcs-intro">{{ t("change_impact.svc.intro") }}</p>
    </n-card>
    <n-alert v-if="error" type="error" :bordered="false">{{ error }}</n-alert>
    <n-card size="small">
      <n-input v-model:value="q" size="small" clearable style="width: 240px; margin-bottom: 10px"
               :placeholder="t('common.search')" />
      <n-data-table :columns="columns" :data="rows" :loading="loading" size="small" :bordered="false"
                    :row-key="(r: ImpactService) => r.id" :row-props="rowProps" remote @update:sorter="onSorter"
                    :pagination="{ page, pageSize, itemCount: total, onChange: (p: number) => { page = p } }"
                    data-testid="svc-list" />
    </n-card>
    <ImpactServiceEditor v-model:show="editorOpen" :service-id="editing" :can-edit="canEdit" @saved="fetchRows" />
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { NAlert, NButton, NCard, NDataTable, NIcon, NInput, NSpace, NTag, type DataTableColumns } from "naive-ui";
import { ArrowLeft as ArrowLeftIcon } from "@iconoir/vue";
import { apiErrMsg } from "@/api/client";
import { listServices, type ImpactService } from "@/api/changeImpact";
import ImpactServiceEditor from "@/components/ImpactServiceEditor.vue";
import { PlusIcon, ServiceIcon } from "@/icons";
import { fmtDateTime } from "@/utils/datetime";

const router = useRouter();
const { t } = useI18n();
const rows = ref<ImpactService[]>([]);
const total = ref(0);
const page = ref(1);
const pageSize = 50;
const q = ref("");
const loading = ref(false);
const error = ref("");
const canEdit = ref(false);
const editorOpen = ref(false);
const editing = ref<string | null>(null);

const CRIT_TYPE: Record<string, "error" | "warning" | "default" | "info"> = {
  critical: "error", high: "warning", normal: "default", low: "info",
};
// 後端分頁：點標題排序交給後端（分頁前排），不能只排畫面上那一頁
const sortState = ref<{ key: string; order: "ascend" | "descend" } | null>(null);
const so = (k: string) => (sortState.value?.key === k ? sortState.value.order : false);
function onSorter(st: { columnKey: string | number; order: "ascend" | "descend" | false } | null) {
  sortState.value = st && st.order ? { key: String(st.columnKey), order: st.order } : null;
  if (page.value !== 1) page.value = 1;   // 換頁的 watch 會重抓
  else void fetchRows();
}
const columns = computed<DataTableColumns<ImpactService>>(() => [
  { title: t("change_impact.svc.f_name"), key: "name", minWidth: 200, sorter: true, sortOrder: so("name"),
    render: (r) => h("span", { style: r.status === "retired" ? "opacity: .55" : "" }, r.name) },
  { title: t("change_impact.svc.col_criticality"), key: "criticality", width: 110, sorter: true, sortOrder: so("criticality"),
    render: (r) => h(NTag, { size: "small", type: CRIT_TYPE[r.criticality] ?? "default", bordered: false },
                     { default: () => t(`change_impact.param.criticality.${r.criticality}`) }) },
  { title: t("change_impact.svc.col_status"), key: "status", width: 100, sorter: true, sortOrder: so("status"),
    render: (r) => t(`change_impact.svc.status.${r.status}`) },
  { title: t("change_impact.svc.groups"), key: "group_count", width: 110, sorter: true, sortOrder: so("group_count") },
  { title: t("change_impact.svc.col_updated"), key: "updated_at", width: 170, sorter: true, sortOrder: so("updated_at"),
    render: (r) => (r.updated_at ? fmtDateTime(r.updated_at) : "—") },
]);

function rowProps(r: ImpactService) {
  return { style: "cursor: pointer", onClick: () => open(r.id) };
}
function open(id: string | null) {
  editing.value = id;
  editorOpen.value = true;
}

async function fetchRows() {
  loading.value = true;
  error.value = "";
  try {
    const r = await listServices({ q: q.value.trim(), page: page.value, pageSize, sort: sortState.value?.key,
                                   order: sortState.value?.order === "descend" ? "desc" : "asc" });
    rows.value = r.items;
    total.value = r.total;
    canEdit.value = r.can_edit;
  } catch (e) { error.value = apiErrMsg(e); } finally { loading.value = false; }
}

let timer: ReturnType<typeof setTimeout> | null = null;
watch(q, () => {
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => { page.value = 1; void fetchRows(); }, 300);
});
watch(page, () => void fetchRows());
onMounted(() => void fetchRows());
</script>

<style scoped>
.svcs-page { display: flex; flex-direction: column; gap: 12px; }
.svcs-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.svcs-head__title { display: inline-flex; align-items: center; gap: 8px; font-size: 18px; font-weight: 600; }
.svcs-intro { margin: 8px 0 0; font-size: 13px; opacity: .75; line-height: 1.6; }
</style>
