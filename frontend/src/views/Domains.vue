<script setup lang="ts">
/**
 * 網域清單（客戶許願清單 2026-10-28）：註冊商、DNS 託管、到期日（RDAP 優先、查不到用手填的）、NS、綁定的服務。
 * 每個網域大約一天自動查一次 RDAP；管理員可以立刻重新查（逐筆、中間停一下，註冊管理機構有查詢次數限制）。
 */
import { computed, h, onMounted, reactive, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import { storeToRefs } from "pinia";
import {
  NButton, NCard, NDataTable, NIcon, NInput, NModal, NPopconfirm, NSpace, NTag, NTooltip, useMessage,
  type DataTableColumns,
} from "naive-ui";
import { CancelIcon, DeleteIcon, DomainIcon, EditIcon, PlusIcon, RefreshIcon, SaveIcon, SyncIcon } from "@/icons";
import { apiErrMsg } from "@/api/client";
import { Domains, type Domain } from "@/api/domains";
import { useAuthStore } from "@/stores/auth";
import { autoSort } from "@/composables/useTableSort";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { useTableQuickFilter } from "@/composables/useTableQuickFilter";
import { useTablePagination } from "@/composables/useTablePagination";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import DomainEditModal from "@/components/DomainEditModal.vue";
import { withExportValue } from "@/utils/tableExport";
import { fmtDate, fmtRelative } from "@/utils/datetime";
import { expiryTag, rdapErrorText } from "@/utils/domains";

const { t, te } = useI18n();
const msg = useMessage();
const router = useRouter();
const { me } = storeToRefs(useAuthStore());
const isAdmin = computed(() => !!me.value?.is_admin);
const pg = useTablePagination();

const rows = ref<Domain[]>([]);
const loading = ref(false);
async function load() {
  loading.value = true;
  try { rows.value = await Domains.list(); }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}

function link(d: Domain) {
  return h("a", { href: "#", style: "color: var(--primary-color, #18a058); text-decoration: none",
                  onClick: (e: MouseEvent) => { e.preventDefault(); void router.push({ name: "adv-domain-detail", params: { id: d.id } }); } },
           d.name);
}
const busyIds = ref<Set<string>>(new Set());
async function refreshOne(d: Domain) {
  busyIds.value = new Set([...busyIds.value, d.id]);
  try {
    const n = await Domains.refresh(d.id);
    rows.value = rows.value.map((x) => (x.id === n.id ? n : x));
    if (n.rdap_error) msg.warning(`${n.name}：${rdapErrorText(n.rdap_error, t, te)}`);
  } catch (e) { msg.error(apiErrMsg(e)); } finally {
    const s = new Set(busyIds.value); s.delete(d.id); busyIds.value = s;
  }
}
async function remove(d: Domain) {
  try { await Domains.remove(d.id); rows.value = rows.value.filter((x) => x.id !== d.id); }
  catch (e) { msg.error(apiErrMsg(e)); }
}

const cols = computed<DataTableColumns<Domain>>(() => autoSort([
  withExportValue({ title: t("domains.name"), key: "name", minWidth: 180, render: (r: Domain) => link(r) },
                  (r: Domain) => r.name),
  withExportValue({ title: t("domains.registrar"), key: "registrar_effective", minWidth: 150, ellipsis: { tooltip: true },
                    render: (r: Domain) => r.registrar_effective || "—" }, (r: Domain) => r.registrar_effective ?? ""),
  withExportValue({ title: t("domains.dns_provider"), key: "dns_provider_effective", minWidth: 150,
    render: (r: Domain) => (r.dns_provider_effective
      ? h("span", [r.dns_provider_effective, r.dns_provider_inferred
          ? h(NTag, { size: "tiny", bordered: false, style: "margin-left:6px" }, () => t("domains.inferred")) : null])
      : "—") }, (r: Domain) => r.dns_provider_effective ?? ""),
  withExportValue({ title: t("domains.expires"), key: "days_left", width: 210,
    sorter: (a: Domain, b: Domain) => (a.days_left ?? 1e9) - (b.days_left ?? 1e9),
    render: (r: Domain) => {
      if (!r.expires) return "—";
      const tag = expiryTag(r, t);
      return h(NSpace, { size: 6, align: "center", wrapItem: false }, () => [
        h("span", fmtDate(r.expires)),
        tag ? h(NTag, { size: "small", bordered: false, type: tag.type }, () => tag.text) : null,
        r.expires_source === "manual" ? h(NTag, { size: "tiny", bordered: false }, () => t("domains.manual")) : null,
      ]);
    } }, (r: Domain) => r.expires ?? ""),
  withExportValue({ title: t("domains.auto_renew"), key: "auto_renew", width: 100,
    render: (r: Domain) => (r.auto_renew == null ? "—" : r.auto_renew ? t("common.yes") : t("common.no")) },
    (r: Domain) => (r.auto_renew == null ? "" : r.auto_renew ? t("common.yes") : t("common.no"))),
  withExportValue({ title: t("domains.nameservers"), key: "nameservers", minWidth: 200, ellipsis: { tooltip: true },
                    render: (r: Domain) => r.nameservers.join(", ") || "—" }, (r: Domain) => r.nameservers.join(", ")),
  { title: t("domains.services"), key: "services", minWidth: 160, ellipsis: { tooltip: true },
    render: (r: Domain) => r.services || "—" },
  withExportValue({ title: t("domains.rdap_checked"), key: "rdap_checked_at", width: 170,
    render: (r: Domain) => {
      if (!r.rdap_checked_at) return "—";
      if (!r.rdap_error) return fmtRelative(r.rdap_checked_at);
      return h(NTooltip, null, {
        trigger: () => h(NTag, { size: "small", bordered: false, type: r.rdap_error === "rdap_no_service" ? "default" : "warning" },
                         () => rdapErrorText(r.rdap_error, t, te)),
        default: () => fmtRelative(r.rdap_checked_at),
      });
    } }, (r: Domain) => r.rdap_error ?? r.rdap_checked_at ?? ""),
  ...(isAdmin.value ? [{
    title: t("common.actions"), key: "_", width: 130, className: "col-actions",
    render: (r: Domain) => h(NSpace, { size: 4, wrapItem: false }, () => [
      h(NButton, { size: "tiny", title: t("domains.refresh"), loading: busyIds.value.has(r.id), onClick: () => refreshOne(r) },
        { icon: () => h(NIcon, null, () => h(SyncIcon)) }),
      h(NButton, { size: "tiny", type: "success", title: t("common.edit"), onClick: () => openEdit(r) },
        { icon: () => h(NIcon, null, () => h(EditIcon)) }),
      h(NPopconfirm, { onPositiveClick: () => remove(r) }, {
        trigger: () => h(NButton, { size: "tiny", type: "error", ghost: true, title: t("common.delete") },
                         { icon: () => h(NIcon, null, () => h(DeleteIcon)) }),
        default: () => t("domains.delete_confirm", { name: r.name }),
      }),
    ]),
  }] : []),
]) as DataTableColumns<Domain>);

const allKeys = computed(() => cols.value.filter((c: any) => c.key && c.key !== "_").map((c: any) => String(c.key)));
const prefs = useColumnPrefs("domains", allKeys.value, allKeys.value);
const pickerItems = computed(() => cols.value.filter((c: any) => c.key && c.key !== "_")
  .map((c: any) => ({ key: String(c.key), label: typeof c.title === "string" ? c.title : String(c.key) })));
const visibleCols = computed<DataTableColumns<Domain>>(() =>
  prefs.orderColumns(cols.value.filter((c: any) => c.key === "_" || prefs.visibleKeys.value.includes(String(c.key)))));
const { query, filtered } = useTableQuickFilter(rows);

// ── 新增 / 編輯 ──
const edit = reactive({ show: false, domain: null as Domain | null });
function openCreate() { Object.assign(edit, { show: true, domain: null }); }
function openEdit(d: Domain) { Object.assign(edit, { show: true, domain: d }); }
function onSaved(d: Domain) {
  rows.value = rows.value.some((x) => x.id === d.id) ? rows.value.map((x) => (x.id === d.id ? d : x))
    : [...rows.value, d].sort((a, b) => a.name.localeCompare(b.name));
}

// ── 批次新增：建立後逐筆查 RDAP，顯示進度 ──
const bulk = reactive({ show: false, busy: false, text: "", done: 0, total: 0 });
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
async function runLookups(ids: string[]) {
  bulk.total = ids.length; bulk.done = 0;
  for (const id of ids) {
    try {
      const n = await Domains.refresh(id);
      rows.value = rows.value.map((x) => (x.id === n.id ? n : x));
    } catch { /* 單筆失敗不擋後面的 */ }
    bulk.done += 1;
    if (bulk.done < ids.length) await sleep(800);
  }
}
async function saveBulk() {
  const names = bulk.text.split(/[\s,;]+/).map((x) => x.trim()).filter(Boolean);
  if (!names.length) return;
  bulk.busy = true;
  try {
    const r = await Domains.bulk(names);
    await load();
    if (r.skipped.length) {
      msg.warning(t("domains.bulk_skipped", { n: r.skipped.length,
        list: r.skipped.slice(0, 5).map((x) => `${x.value}（${t(`domains.skip_${x.reason}`)}）`).join("、") }),
        { duration: 8000 });
    }
    msg.success(t("domains.bulk_created", { n: r.created.length }));
    await runLookups(r.created.map((x) => x.id));
    bulk.show = false; bulk.text = "";
  } catch (e) { msg.error(apiErrMsg(e)); } finally { bulk.busy = false; bulk.total = 0; }
}
const refreshingAll = ref(false);
async function refreshAll() {
  refreshingAll.value = true;
  try { await runLookups(rows.value.map((x) => x.id)); } finally { refreshingAll.value = false; bulk.total = 0; }
}

onMounted(load);
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><DomainIcon /></n-icon>
        <span>{{ t("advanced.domains") }}</span>
      </n-space>
    </template>
    <div class="hint" style="margin-bottom: 10px">{{ t("domains.page_hint") }}</div>
    <n-space style="margin: 0 0 8px" align="center">
      <n-input v-model:value="query" clearable style="width: 200px" :placeholder="t('common.filter')" />
      <n-button :loading="loading" @click="load">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}
      </n-button>
      <template v-if="isAdmin">
        <n-button type="primary" data-testid="domain-add" @click="openCreate">
          <template #icon><n-icon><PlusIcon /></n-icon></template>{{ t("domains.add") }}
        </n-button>
        <n-button data-testid="domain-bulk" @click="bulk.show = true">
          <template #icon><n-icon><PlusIcon /></n-icon></template>{{ t("domains.bulk_add") }}
        </n-button>
        <n-button :loading="refreshingAll" :disabled="refreshingAll || !rows.length" @click="refreshAll">
          <template #icon><n-icon><SyncIcon /></n-icon></template>
          {{ refreshingAll ? t("domains.refreshing", { done: bulk.done, total: bulk.total }) : t("domains.refresh_all") }}
        </n-button>
      </template>
      <ColumnPicker :all="pickerItems" :visible="prefs.visibleKeys.value" @update:visible="prefs.setVisible"
                    @reset="prefs.reset" :order="prefs.order.value" @update:order="prefs.setOrder" />
      <ExportButton :columns="visibleCols" :rows="filtered" filename="domains" :title="t('advanced.domains')" />
    </n-space>
    <n-data-table :columns="visibleCols" :data="filtered" :loading="loading" :bordered="false" :pagination="pg"
                  :row-key="(r: Domain) => r.id" :scroll-x="1300" data-testid="domain-table" />

    <DomainEditModal v-model:show="edit.show" :domain="edit.domain" @saved="onSaved" />

    <n-modal v-model:show="bulk.show" preset="card" style="width: 520px; max-width: 95vw" :title="t('domains.bulk_add')"
             :mask-closable="!bulk.busy">
      <div class="hint" style="margin-bottom: 8px">{{ t("domains.bulk_hint") }}</div>
      <n-input v-model:value="bulk.text" type="textarea" :autosize="{ minRows: 6, maxRows: 16 }"
               placeholder="example.com&#10;example.com.tw" data-testid="domain-bulk-text" />
      <div v-if="bulk.total" class="hint" style="margin-top: 8px">
        {{ t("domains.refreshing", { done: bulk.done, total: bulk.total }) }}
      </div>
      <template #footer>
        <n-space justify="end">
          <n-button :disabled="bulk.busy" @click="bulk.show = false">
            <template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}
          </n-button>
          <n-button type="primary" :loading="bulk.busy" :disabled="bulk.busy || !bulk.text.trim()"
                    data-testid="domain-bulk-save" @click="saveBulk">
            <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </n-card>
</template>

<style scoped>
.hint { font-size: 12.5px; opacity: .7; }
</style>
