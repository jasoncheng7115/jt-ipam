<script setup lang="ts">
/**
 * 佈線自動推斷（使用者 2026-11-25：「有明確可以判斷的就自動 不要全都人工設」）。
 *
 * 開關、立即執行、上一輪結果，以及「建議」與「已忽略」兩個清單。證據明確的線系統會自己接，
 * 不會出現在這裡；這裡只有要人決定的：跟人工的線衝突、只靠 MAC 表推出的交換器直連、證據互相矛盾。
 */
import { computed, h, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NAlert, NButton, NDataTable, NDrawer, NDrawerContent, NIcon, NPopconfirm, NSpace, NSpin, NSwitch, NTabPane, NTabs,
  NTag, NTooltip, useMessage, type DataTableColumns,
} from "naive-ui";
import { Physical, type CableInferenceConfig, type CableSuggestion } from "@/api/phase3";
import { apiErrMsg } from "@/api/client";
import SwitchPortLabel from "@/components/SwitchPortLabel.vue";
import { autoSort } from "@/composables/useTableSort";
import { useTablePagination } from "@/composables/useTablePagination";
import { fmtDateTime } from "@/utils/datetime";
import { evidenceText, kindLabel } from "@/utils/cableEvidence";
import { CableInferIcon, CheckIcon, DismissIcon, PlayIcon, RestoreIcon } from "@/icons";

const emit = defineEmits<{ (e: "changed"): void }>();
const { t } = useI18n();
const msg = useMessage();
const show = ref(false);
const loading = ref(false);
const running = ref(false);
const saving = ref(false);
const busyId = ref("");
const cfg = ref<CableInferenceConfig | null>(null);
const open = ref<CableSuggestion[]>([]);
const dismissed = ref<CableSuggestion[]>([]);
const tab = ref<"open" | "dismissed">("open");
const pgOpen = useTablePagination();
const pgDismissed = useTablePagination();

async function load() {
  loading.value = true;
  try {
    const [c, o, d] = await Promise.all([Physical.cableInference(), Physical.cableSuggestions("open"),
                                         Physical.cableSuggestions("dismissed")]);
    cfg.value = c;
    open.value = o.items;
    dismissed.value = d.items;
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}
async function openDrawer() { show.value = true; await load(); }

async function toggle(v: boolean) {
  saving.value = true;
  try { cfg.value = await Physical.setCableInference(v); msg.success(t("common.saved")); }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { saving.value = false; }
}
async function runNow() {
  running.value = true;
  try {
    const st = await Physical.runCableInference();
    msg.success(t("cable_infer.run_done", summaryParams(st)));
    await load();
    emit("changed");
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { running.value = false; }
}
async function act(r: CableSuggestion, fn: (id: string) => Promise<void>, ok: string) {
  busyId.value = r.id;
  try { await fn(r.id); msg.success(ok); await load(); emit("changed"); }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { busyId.value = ""; }
}

function summaryParams(s: NonNullable<CableInferenceConfig["last_summary"]>) {
  return { created: s.created ?? 0, refreshed: s.refreshed ?? 0, retired: s.retired ?? 0,
           conflicts: s.conflicts ?? 0, suggested: s.suggested ?? 0, ports: s.ports_created ?? 0 };
}
const lastRun = computed(() => {
  const c = cfg.value;
  if (!c?.last_run_at || !c.last_summary) return t("cable_infer.never_run");
  return t("cable_infer.last_run", { at: fmtDateTime(c.last_run_at), ...summaryParams(c.last_summary) });
});

const endCell = (val: string | null, devId: string | null) =>
  h(SwitchPortLabel, { value: val, to: devId ? { name: "device-detail", params: { id: devId } } : null });
const reasonTag = (r: CableSuggestion) => {
  if (!r.reason) return "—";
  const type = r.reason === "conflict_manual" || r.reason === "conflicting_evidence" ? "warning" : "default";
  return h(NTag, { size: "small", bordered: false, type }, () => t(`cable_infer.reason_${r.reason}`));
};
function iconBtn(icon: unknown, label: string, type: "success" | "default" | "info", r: CableSuggestion,
                 onClick: () => void, testid: string) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "tiny", quaternary: true, type, "aria-label": label, "data-testid": testid,
                                loading: busyId.value === r.id, disabled: !!busyId.value, onClick },
      { icon: () => h(NIcon, null, () => h(icon as never)) }),
    default: () => label,
  });
}
const baseCols = (): DataTableColumns<CableSuggestion> => [
  { title: t("physical.a_end"), key: "a_end", minWidth: 160, render: (r) => endCell(r.a_end, r.a_device_id) },
  { title: t("physical.b_end"), key: "b_end", minWidth: 160, render: (r) => endCell(r.b_end, r.b_device_id) },
  // 依據要讀得完整（判斷要不要接受全靠它），不截斷、換行
  { title: t("cable_infer.col_basis"), key: "kind", minWidth: 220,
    render: (r) => h("span", null, [h("b", null, kindLabel(t, r.kind)), "：", evidenceText(t, r.evidence)]) },
  { title: t("cable_infer.col_reason"), key: "reason", width: 130, render: reasonTag },
];
const openCols = computed<DataTableColumns<CableSuggestion>>(() => autoSort([
  ...baseCols(),
  { title: t("cable_infer.col_last_seen"), key: "last_seen_at", width: 140, render: (r) => fmtDateTime(r.last_seen_at) },
  { title: t("common.actions"), key: "actions", width: 90, fixed: "right", render: (r) => h(NSpace, { size: 2, wrap: false }, () => [
    // 跟人工的線衝突：埠還被那條線占著，直接接受會被擋；改成「接受並取代」，先確認再移除那條線
    r.reason === "conflict_manual"
      ? h(NPopconfirm, { onPositiveClick: () => act(r, (id) => Physical.acceptCableSuggestion(id, true),
                                                    t("cable_infer.replaced")) }, {
          trigger: () => iconBtn(CheckIcon, t("cable_infer.accept_replace"), "success", r, () => {},
                                 `cable-sug-accept-${r.id}`),
          default: () => t("cable_infer.confirm_replace", { a: r.a_end ?? "?", b: r.b_end ?? "?" }),
        })
      : iconBtn(CheckIcon, t("cable_infer.accept"), "success", r,
                () => act(r, Physical.acceptCableSuggestion, t("cable_infer.accepted")), `cable-sug-accept-${r.id}`),
    iconBtn(DismissIcon, t("cable_infer.dismiss"), "default", r,
            () => act(r, Physical.dismissCableSuggestion, t("cable_infer.dismissed_ok")), `cable-sug-dismiss-${r.id}`),
  ]) },
] as DataTableColumns<CableSuggestion>));
const dismissedCols = computed<DataTableColumns<CableSuggestion>>(() => autoSort([
  ...baseCols(),
  { title: t("cable_infer.col_dismissed_at"), key: "dismissed_at", width: 140, render: (r) => fmtDateTime(r.dismissed_at) },
  { title: t("common.actions"), key: "actions", width: 70, fixed: "right", render: (r) =>
    iconBtn(RestoreIcon, t("cable_infer.restore"), "info", r,
            () => act(r, Physical.restoreCableSuggestion, t("cable_infer.restored")), `cable-sug-restore-${r.id}`) },
] as DataTableColumns<CableSuggestion>));
</script>

<template>
  <n-button data-testid="cable-infer-open" @click="openDrawer">
    <template #icon><n-icon><CableInferIcon /></n-icon></template>{{ t("cable_infer.title") }}
  </n-button>
  <n-drawer v-model:show="show" :width="980" placement="right" style="max-width: 100vw">
    <n-drawer-content closable>
      <template #header>
        <span style="display:inline-flex;align-items:center;gap:8px">
          <n-icon :size="18"><CableInferIcon /></n-icon>{{ t("cable_infer.title") }}
        </span>
      </template>
      <n-spin :show="loading">
        <n-alert type="info" :bordered="false" style="margin-bottom: 12px">{{ t("cable_infer.intro") }}</n-alert>
        <n-space align="center" justify="space-between" style="margin-bottom: 6px" :wrap-item="false">
          <n-space align="center" :size="8" :wrap-item="false">
            <n-switch :value="cfg?.enabled ?? true" :loading="saving" data-testid="cable-infer-enabled"
                      @update:value="toggle" />
            <span>{{ t("cable_infer.enabled") }}</span>
          </n-space>
          <n-button type="primary" :loading="running" :disabled="running" data-testid="cable-infer-run" @click="runNow">
            <template #icon><n-icon><PlayIcon /></n-icon></template>{{ t("cable_infer.run_now") }}
          </n-button>
        </n-space>
        <div class="hint" data-testid="cable-infer-last">{{ lastRun }}</div>
        <n-tabs v-model:value="tab" type="line" style="margin-top: 10px">
          <n-tab-pane name="open" :tab="t('cable_infer.tab_open', { n: open.length })">
            <div class="hint" style="margin-bottom: 6px">{{ t("cable_infer.open_hint") }}</div>
            <n-data-table :columns="openCols" :data="open" size="small" :bordered="false" :scroll-x="900"
                          :pagination="pgOpen" :row-key="(r: CableSuggestion) => r.id" data-testid="cable-sug-open" />
          </n-tab-pane>
          <n-tab-pane name="dismissed" :tab="t('cable_infer.tab_dismissed', { n: dismissed.length })">
            <div class="hint" style="margin-bottom: 6px">{{ t("cable_infer.dismissed_hint") }}</div>
            <n-data-table :columns="dismissedCols" :data="dismissed" size="small" :bordered="false" :scroll-x="880"
                          :pagination="pgDismissed" :row-key="(r: CableSuggestion) => r.id" data-testid="cable-sug-dismissed" />
          </n-tab-pane>
        </n-tabs>
      </n-spin>
    </n-drawer-content>
  </n-drawer>
</template>

<style scoped>
.hint { font-size: 12.5px; opacity: .72; line-height: 1.6; }
</style>
