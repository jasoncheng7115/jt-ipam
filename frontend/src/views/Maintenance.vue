<script setup lang="ts">
/**
 * 維護排程清單（2026-11-01）：進行中、即將開始、已結束。進行中的可以直接延長或提前結束。
 * 最常用的「臨時停一台」在 IP、裝置、子網路頁上的「維護」按鈕；這裡是排定一次、每週例行與整體檢視。
 */
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import {
  NButton, NCard, NDataTable, NDropdown, NIcon, NPopconfirm, NSpace, NTabPane, NTabs, NTag, NTooltip, useMessage,
  type DataTableColumns,
} from "naive-ui";
import { DeleteIcon, EditIcon, EndNowIcon, ExtendIcon, MaintenanceIcon, PlusIcon, RefreshIcon } from "@/icons";
import { apiErrMsg } from "@/api/client";
import { Maintenance, type Window } from "@/api/maintenance";
import MaintenanceEditModal from "@/components/MaintenanceEditModal.vue";
import { useTablePagination } from "@/composables/useTablePagination";
import { remaining, scheduleText, statusLine } from "@/utils/maintenance";
import { autoSort } from "@/composables/useTableSort";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import { withExportValue } from "@/utils/tableExport";

const { t } = useI18n();
const msg = useMessage();
const router = useRouter();
const pg = useTablePagination();

const rows = ref<Window[]>([]);
const loading = ref(false);
const tab = ref<"active" | "upcoming" | "past" | "all">("active");
async function load() {
  loading.value = true;
  try { rows.value = await Maintenance.list("all"); }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}
const shown = computed(() => (tab.value === "all" ? rows.value : rows.value.filter((r) => r.status === tab.value)));
const count = (s: string) => rows.value.filter((r) => r.status === s).length;

async function act(fn: () => Promise<unknown>) {
  try { await fn(); await load(); } catch (e) { msg.error(apiErrMsg(e)); }
}
const ico = (c: typeof EditIcon) => () => h(NIcon, null, () => h(c));
const STATUS_TYPE: Record<string, "warning" | "info" | "default"> = { active: "warning", upcoming: "info", past: "default" };

const STATUS_ORDER: Record<string, number> = { active: 0, upcoming: 1, past: 2 };
const whenOf = (r: Window) => r.current_start ?? r.next_start ?? r.starts_at ?? "";
const scopeText = (r: Window) => r.scopes.map((s) => `${t(`maint.scope_${s.scope_type}`)} ${s.label ?? ""}`.trim()).join("、");
/** 清單裡標題下那一行只寫剩多久或幾點開始（完整時間在「時間」欄，不重複） */
function shortStatus(r: Window): string {
  if (r.status === "active") return t("maint.left", { left: remaining(r.current_end, t) });
  if (r.status === "upcoming") return statusLine(r, t);
  return "";
}

const cols = computed<DataTableColumns<Window>>(() => autoSort([
  withExportValue({ title: t("maint.status"), key: "status", width: 80,
    sorter: (a: Window, b: Window) => STATUS_ORDER[a.status] - STATUS_ORDER[b.status],
    render: (r: Window) => h(NTag, { size: "small", bordered: false, type: STATUS_TYPE[r.status] }, () => t(`maint.status_${r.status}`)) },
    (r: Window) => t(`maint.status_${r.status}`)),
  withExportValue({ title: t("maint.title"), key: "title", minWidth: 160,
    render: (r: Window) => h("div", [
      h("a", { href: "#", class: "lnk", onClick: (e: MouseEvent) => { e.preventDefault(); void router.push({ name: "maintenance-detail", params: { id: r.id } }); } }, r.title),
      shortStatus(r) ? h("div", { class: "sub" }, shortStatus(r)) : null,
    ]) }, (r: Window) => r.title),
  withExportValue({ title: t("maint.when"), key: "schedule", minWidth: 180,
    sorter: (a: Window, b: Window) => String(whenOf(a)).localeCompare(String(whenOf(b))),
    render: (r: Window) => scheduleText(r, t) }, (r: Window) => scheduleText(r, t)),
  withExportValue({ title: t("maint.scopes"), key: "scopes", minWidth: 170,
    sorter: (a: Window, b: Window) => scopeText(a).localeCompare(scopeText(b)),
    // 標籤不可超出欄寬（長的 IP＋主機名稱會蓋到隔壁欄）：截斷加「…」，滑鼠移上去看全文
    render: (r: Window) => h(NSpace, { size: 4, itemStyle: "max-width: 100%; min-width: 0" }, () => [
      ...r.scopes.slice(0, 3).map((s) => {
        const text = `${t(`maint.scope_${s.scope_type}`)} ${s.label ?? ""}`.trim();
        return h(NTag, { size: "small", bordered: false, class: "scope-tag", title: text },
                 () => h("span", { class: "scope-text" }, text));
      }),
      r.scopes.length > 3 ? h("span", { class: "sub" }, t("maint.more", { n: r.scopes.length - 3 })) : null,
    ]) }, scopeText),
  withExportValue({ title: t("maint.behavior"), key: "behavior", width: 105,
    render: (r: Window) => t(`maint.behavior_${r.behavior}`) }, (r: Window) => t(`maint.behavior_${r.behavior}`)),
  { title: t("maint.suppressed"), key: "suppressed", width: 85 },
  { title: t("maint.ticket"), key: "ticket", width: 100, ellipsis: { tooltip: true }, render: (r: Window) => r.ticket || "—" },
  { title: t("common.actions"), key: "_", width: 185, className: "col-actions", fixed: "right" as const,
    render: (r: Window) => {
      if (r.source !== "manual") {
        return h(NTooltip, null, { trigger: () => h(NTag, { size: "small", bordered: false }, () => t(`maint.source_${r.source}`)),
                                   default: () => t("maint.readonly_source") });
      }
      return h(NSpace, { size: 4, wrapItem: false }, () => [
        r.status === "active" ? h(NDropdown, {
          trigger: "click", options: [
            ...[1, 2, 4].map((n) => ({ key: String(n * 60), label: t("maint.extend_n", { n }), icon: ico(ExtendIcon) })),
            { type: "divider", key: "d" },
            { key: "end", label: t("maint.end_now"), icon: ico(EndNowIcon) }],
          onSelect: (k: string) => act(() => (k === "end" ? Maintenance.end(r.id) : Maintenance.extend(r.id, Number(k)))),
        }, () => h(NButton, { size: "small", type: "warning", ghost: true },
                { icon: () => h(NIcon, null, () => h(ExtendIcon)), default: () => t("maint.adjust") })) : null,
        h(NButton, { size: "small", type: "primary", title: t("common.edit"), onClick: () => openEdit(r) },
          { icon: () => h(NIcon, null, () => h(EditIcon)) }),
        h(NPopconfirm, { onPositiveClick: () => act(() => Maintenance.remove(r.id)) }, {
          trigger: () => h(NButton, { size: "small", type: "error", ghost: true, title: t("common.delete") },
                           { icon: () => h(NIcon, null, () => h(DeleteIcon)) }),
          default: () => t("maint.delete_confirm", { title: r.title }),
        }),
      ]);
    } },
]) as DataTableColumns<Window>);

const allKeys = computed(() => cols.value.filter((c: any) => c.key && c.key !== "_").map((c: any) => String(c.key)));
const prefs = useColumnPrefs("maintenance", allKeys.value, allKeys.value);
const pickerItems = computed(() => cols.value.filter((c: any) => c.key && c.key !== "_")
  .map((c: any) => ({ key: String(c.key), label: typeof c.title === "string" ? c.title : String(c.key) })));
const visibleCols = computed<DataTableColumns<Window>>(() =>
  prefs.orderColumns(cols.value.filter((c: any) => c.key === "_" || prefs.visibleKeys.value.includes(String(c.key)))));
/** 只算看得到的欄位：藏掉幾欄，表格就不用橫向捲動 */
const scrollX = computed(() => visibleCols.value.reduce((n, c: any) => n + (c.width ?? c.minWidth ?? 100), 0));

const edit = ref<{ show: boolean; window: Window | null }>({ show: false, window: null });
function openEdit(w: Window | null) { edit.value = { show: true, window: w }; }

onMounted(load);
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><MaintenanceIcon /></n-icon><span>{{ t("maint.page_title") }}</span>
      </n-space>
    </template>
    <div class="hint" style="margin-bottom: 10px">{{ t("maint.page_hint") }}</div>
    <n-space style="margin-bottom: 8px" align="center">
      <n-button :loading="loading" @click="load">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}
      </n-button>
      <n-button type="primary" data-testid="maint-add" @click="openEdit(null)">
        <template #icon><n-icon><PlusIcon /></n-icon></template>{{ t("maint.add") }}
      </n-button>
      <ColumnPicker :all="pickerItems" :visible="prefs.visibleKeys.value" @update:visible="prefs.setVisible"
                    @reset="prefs.reset" :order="prefs.order.value" @update:order="prefs.setOrder" />
      <ExportButton :columns="visibleCols" :rows="shown" filename="maintenance" :title="t('maint.page_title')" />
    </n-space>
    <n-tabs v-model:value="tab" type="line" size="small">
      <n-tab-pane name="active" :tab="`${t('maint.status_active')}（${count('active')}）`" />
      <n-tab-pane name="upcoming" :tab="`${t('maint.status_upcoming')}（${count('upcoming')}）`" />
      <n-tab-pane name="past" :tab="`${t('maint.status_past')}（${count('past')}）`" />
      <n-tab-pane name="all" :tab="t('maint.all')" />
    </n-tabs>
    <n-data-table :columns="visibleCols" :data="shown" :loading="loading" :bordered="false" :pagination="pg"
                  :row-key="(r: Window) => r.id" :scroll-x="scrollX" data-testid="maint-table" />
    <MaintenanceEditModal v-model:show="edit.show" :window="edit.window" @saved="load" />
  </n-card>
</template>

<style scoped>
.hint { font-size: 12.5px; opacity: .7; }
:deep(.sub) { font-size: 12px; opacity: .7; }
:deep(.scope-tag) { max-width: 100%; }
:deep(.scope-tag .n-tag__content) { min-width: 0; overflow: hidden; }
:deep(.scope-text) { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
:deep(.lnk) { color: var(--primary-color, #18a058); text-decoration: none; font-weight: 600; }
</style>
