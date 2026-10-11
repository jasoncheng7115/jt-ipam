<script setup lang="ts">
/**
 * Technitium DNS Server 的 DHCP（使用者 2026-10-08：下一個要支援 Technitium，它有 DNS 與 DHCP）。
 * jt-ipam 唯讀拉範圍、保留、選項與租約；DNS 那一半在「DNS 伺服器」頁（類型選 Technitium）。
 * 版面照 Wazuh 頁：頁籤（伺服器／範圍）、操作欄編輯／測試／立即同步／刪除。
 */
import { computed, h, onMounted, ref, watch } from "vue";
import { fmtDateTime } from "@/utils/datetime";
import { useI18n } from "vue-i18n";
import { trackTask } from "@/composables/useTaskTracker";
import ScopeOverlapWarning from "@/components/ScopeOverlapWarning.vue";
import {
  NCard, NDataTable, NSpace, NButton, NTag, NIcon, NTooltip, NAlert, NTabs, NTabPane,
  NModal, NForm, NFormItem, NInput, NInputNumber, NSwitch, NSelect, NCheckbox, NPopconfirm,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { listSubnets } from "@/api/subnets";
import {
  listTechnitium, createTechnitium, updateTechnitium, deleteTechnitium, testTechnitium, syncTechnitium,
  listTechnitiumScopes, type TechnitiumDhcpServer, type TechnitiumDhcpWrite, type TechnitiumScope,
} from "@/api/technitium";
import {
  TechnitiumIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SyncIcon, TestIcon, SaveIcon, CancelIcon, ListIcon,
} from "@/icons";
import { autoSort } from "@/composables/useTableSort";
import ColumnPicker from "@/components/ColumnPicker.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { apiErrMsg } from "@/api/client";

const { t } = useI18n();
const msg = useMessage();

const tab = ref<"servers" | "scopes">("servers");

const COLS = ["name", "api_url", "enabled", "sync_flags", "summary", "last_sync_at", "last_error", "actions"];
const prefs = useColumnPrefs("technitium_dhcp", COLS, COLS);
const picker = computed(() => [
  { key: "name", label: t("cols.name") },
  { key: "api_url", label: t("technitium.api_url") },
  { key: "enabled", label: t("cols.status") },
  { key: "sync_flags", label: t("cols.sync_items") },
  { key: "summary", label: t("technitium.last_seen") },
  { key: "last_sync_at", label: t("cols.last_sync") },
  { key: "last_error", label: t("cols.last_error") },
  { key: "actions", label: t("cols.actions") },
]);

const rows = ref<TechnitiumDhcpServer[]>([]);
const loading = ref(false);
const show = ref(false);
const editing = ref<TechnitiumDhcpServer | null>(null);

function blankForm() {
  return {
    name: "", api_url: "", token: "", verify_tls: true, enabled: true, sync_scopes: true, sync_leases: true,
    sync_interval_seconds: 300, description: "", scope_subnet_ids: [] as string[],
  };
}
const form = ref(blankForm());

const subnetOptions = ref<{ label: string; value: string }[]>([]);
async function loadSubnetOptions() {
  try {
    const r = await listSubnets({ page: 1, pageSize: 500 });
    subnetOptions.value = r.items.map((s) => ({
      label: s.description ? `${s.cidr} — ${s.description}` : s.cidr, value: s.id }));
  } catch { /* silent */ }
}

async function refresh() {
  loading.value = true;
  try {
    rows.value = (await listTechnitium()).items;
    if (!scopeServer.value && rows.value.length) scopeServer.value = rows.value[0].id;
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}

function openCreate() {
  editing.value = null;
  form.value = blankForm();
  show.value = true;
}

function openEdit(r: TechnitiumDhcpServer) {
  editing.value = r;
  form.value = {
    name: r.name, api_url: r.api_url, token: "", verify_tls: r.verify_tls, enabled: r.enabled,
    sync_scopes: r.sync_scopes, sync_leases: r.sync_leases, sync_interval_seconds: r.sync_interval_seconds,
    description: r.description ?? "", scope_subnet_ids: r.scope_subnet_ids ?? [],
  };
  show.value = true;
}

async function submit() {
  const f = form.value;
  if (!editing.value && !f.token.trim()) { msg.warning(t("technitium.token_required")); return; }
  const payload: TechnitiumDhcpWrite = {
    name: f.name.trim(), api_url: f.api_url.trim(), verify_tls: f.verify_tls, enabled: f.enabled,
    sync_scopes: f.sync_scopes, sync_leases: f.sync_leases, sync_interval_seconds: f.sync_interval_seconds,
    description: f.description || undefined, scope_subnet_ids: f.scope_subnet_ids,
  };
  if (f.token.trim()) payload.token = f.token.trim();
  try {
    if (editing.value) await updateTechnitium(editing.value.id, payload);
    else await createTechnitium(payload);
    show.value = false;
    msg.success(t("common.ok"));
    await refresh();
  } catch (e) { msg.error(apiErrMsg(e)); }
}

async function test(id: string) {
  try {
    const r = await testTechnitium(id);
    msg.success(t("technitium.test_ok", { version: r.version || "?", user: r.user || "?", scopes: r.scopes,
                                          enabled: r.enabled_scopes, leases: r.leases }), { duration: 6000 });
    // 只需要「檢視」權限；給到修改權限就提醒（jt-ipam 不寫回 Technitium）
    if (r.can_modify) msg.info(t("technitium.can_modify"), { duration: 8000 });
  } catch (e) {
    msg.error(apiErrMsg(e), { duration: 10_000, closable: true });
  }
}

async function sync(id: string) {
  try {
    const r = await syncTechnitium(id);
    trackTask(r.task_id, { onDone: () => void refresh() });
  } catch (e) { msg.error(apiErrMsg(e)); }
}

async function del(id: string) {
  try { await deleteTechnitium(id); if (scopeServer.value === id) scopeServer.value = null; await refresh(); }
  catch (e) { msg.error(apiErrMsg(e)); }
}

function iconAction(icon: any, label: string, onClick: () => void, type?: any, testid?: string) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type, "aria-label": label, "data-testid": testid,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}

function summaryText(r: TechnitiumDhcpServer): string {
  const s = r.last_summary;
  if (!s) return "—";
  const parts = [];
  if (s.version) parts.push(`v${s.version}`);
  if (s.scopes != null) parts.push(t("technitium.n_scopes", { n: s.scopes }));
  if (s.reservations != null) parts.push(t("technitium.n_reservations", { n: s.reservations }));
  if (s.lease_rows != null) parts.push(t("technitium.n_leases", { n: s.lease_rows }));
  return parts.join(" · ") || "—";
}

const allCols = computed<DataTableColumns<TechnitiumDhcpServer>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 150, ellipsis: { tooltip: true } },
  { title: t("technitium.api_url"), key: "api_url", minWidth: 220, ellipsis: { tooltip: true } },
  {
    title: t("common.status"), key: "enabled", width: 100,
    render: (r) => h(NTag, { type: r.enabled ? "success" : "default", size: "small" },
      () => r.enabled ? t("common.enabled") : t("common.disabled")),
  },
  {
    title: t("common.sync"), key: "sync_flags", width: 170,
    render: (r) => h(NSpace, { size: 4 }, () => [
      r.sync_scopes ? h(NTag, { size: "tiny", type: "info", bordered: false }, () => t("technitium.scopes")) : null,
      r.sync_leases ? h(NTag, { size: "tiny", type: "info", bordered: false }, () => t("technitium.leases")) : null,
    ].filter(Boolean)),
  },
  { title: t("technitium.last_seen"), key: "summary", minWidth: 220, ellipsis: { tooltip: true },
    render: (r) => summaryText(r) },
  { title: t("cols.last_sync"), key: "last_sync_at", width: 170, render: (r) => fmtDateTime(r.last_sync_at) },
  {
    title: t("cols.last_error"), key: "last_error", minWidth: 160,
    ellipsis: { tooltip: true }, render: (r) => r.last_error ?? "—",
  },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 176, fixed: "right",
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openEdit(r), undefined, "tdns-edit"),
      iconAction(TestIcon, t("common.test"), () => test(r.id), undefined, "tdns-test"),
      iconAction(SyncIcon, t("common.pull"), () => sync(r.id), "primary", "tdns-sync"),
      h(NPopconfirm, { onPositiveClick: () => del(r.id) }, {
        trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]));
const cols = computed<DataTableColumns<TechnitiumDhcpServer>>(() =>
  prefs.orderColumns(allCols.value.filter((c: any) => prefs.visibleKeys.value.includes(c.key))),
);

// ── 範圍頁籤：上一次同步看到的範圍與發給用戶端的選項 ──
const scopeServer = ref<string | null>(null);
const scopes = ref<TechnitiumScope[]>([]);
const scopesLoading = ref(false);
const serverOptions = computed(() => rows.value.map((r) => ({ label: r.name, value: r.id })));
async function loadScopes() {
  if (!scopeServer.value) { scopes.value = []; return; }
  scopesLoading.value = true;
  try { scopes.value = await listTechnitiumScopes(scopeServer.value); }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { scopesLoading.value = false; }
}
watch([tab, scopeServer], ([tb]) => { if (tb === "scopes") void loadScopes(); });

function leaseText(sec: number | null): string {
  if (!sec) return "—";
  if (sec % 86400 === 0) return t("technitium.days", { n: sec / 86400 });
  if (sec % 3600 === 0) return t("technitium.hours", { n: sec / 3600 });
  return t("technitium.minutes", { n: Math.round(sec / 60) });
}
const list = (xs: string[]) => (xs.length ? xs.join(", ") : "—");
const scopeCols = computed<DataTableColumns<TechnitiumScope>>(() => autoSort([
  { title: t("cols.name"), key: "name", minWidth: 120, ellipsis: { tooltip: true } },
  {
    title: t("cols.status"), key: "enabled", width: 90,
    render: (r) => h(NTag, { type: r.enabled ? "success" : "default", size: "small", bordered: false },
      () => r.enabled ? t("common.enabled") : t("common.disabled")),
  },
  { title: t("technitium.subnet"), key: "subnet_cidr", width: 150, render: (r) => r.subnet_cidr ?? "—" },
  { title: t("technitium.range"), key: "start_ip", minWidth: 220, render: (r) => `${r.start_ip} – ${r.end_ip}` },
  { title: t("technitium.exclusions"), key: "exclusions", minWidth: 180, ellipsis: { tooltip: true },
    render: (r) => r.exclusions.length ? r.exclusions.map((x) => `${x.start} – ${x.end}`).join(", ") : "—" },
  { title: t("technitium.router"), key: "router", width: 130, render: (r) => r.router ?? "—" },
  { title: "DNS", key: "dns_servers", minWidth: 150, ellipsis: { tooltip: true }, render: (r) => list(r.dns_servers) },
  { title: "NTP", key: "ntp_servers", width: 130, ellipsis: { tooltip: true }, render: (r) => list(r.ntp_servers) },
  { title: "WINS", key: "wins_servers", width: 120, ellipsis: { tooltip: true }, render: (r) => list(r.wins_servers) },
  { title: t("technitium.domain"), key: "domain_name", width: 150, ellipsis: { tooltip: true },
    render: (r) => r.domain_name ?? "—" },
  { title: t("technitium.lease_time"), key: "lease_seconds", width: 110, render: (r) => leaseText(r.lease_seconds) },
  { title: t("technitium.reservations"), key: "reservations", width: 90, align: "right" },
  { title: t("cols.last_sync"), key: "synced_at", width: 170, render: (r) => fmtDateTime(r.synced_at) },
]));

onMounted(() => { void refresh(); void loadSubnetOptions(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><TechnitiumIcon /></n-icon>
        <span>{{ t("technitium.title") }}</span>
      </n-space>
    </template>

    <n-tabs v-model:value="tab" type="line">
      <n-tab-pane name="servers">
        <template #tab>
          <span class="tab-label"><n-icon :size="16"><TechnitiumIcon /></n-icon>{{ t("technitium.tab_servers") }}</span>
        </template>
        <n-alert type="info" :bordered="false" style="margin-bottom: 12px">{{ t("technitium.intro") }}</n-alert>
        <n-space style="margin-bottom: 12px">
          <n-button @click="refresh" :loading="loading">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>
            {{ t("common.refresh") }}
          </n-button>
          <n-button type="primary" data-testid="tdns-create" @click="openCreate">
            <template #icon><n-icon><PlusIcon /></n-icon></template>
            {{ t("common.create") }}
          </n-button>
          <ColumnPicker :all="picker" :visible="prefs.visibleKeys.value" @update:visible="prefs.setVisible"
                        @reset="prefs.reset" :order="prefs.order.value" @update:order="prefs.setOrder" />
        </n-space>
        <n-data-table :columns="cols" :data="rows" :loading="loading" :bordered="false" :scroll-x="1300"
                      data-testid="tdns-list" />
      </n-tab-pane>

      <n-tab-pane name="scopes">
        <template #tab>
          <span class="tab-label"><n-icon :size="16"><ListIcon /></n-icon>{{ t("technitium.tab_scopes") }}</span>
        </template>
        <n-space style="margin-bottom: 12px" align="center">
          <n-select v-if="serverOptions.length > 1" v-model:value="scopeServer" :options="serverOptions"
                    style="width: 240px" />
          <n-button @click="loadScopes" :loading="scopesLoading" :disabled="!scopeServer">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>
            {{ t("common.refresh") }}
          </n-button>
        </n-space>
        <div class="form-hint" style="margin-bottom: 8px">{{ t("technitium.scopes_hint") }}</div>
        <n-data-table :columns="scopeCols" :data="scopes" :loading="scopesLoading" :bordered="false"
                      :scroll-x="1700" data-testid="tdns-scopes" />
      </n-tab-pane>
    </n-tabs>

    <n-modal v-model:show="show" preset="card"
             :title="editing ? t('common.edit') : `${t('common.create')} — ${t('technitium.title')}`"
             style="width: min(600px, 100%)">
      <n-alert type="default" :bordered="false" :show-icon="true" style="margin-bottom: 12px">
        {{ t("technitium.setup_hint") }}
      </n-alert>
      <n-form>
        <n-form-item :label="t('common.name')"><n-input v-model:value="form.name" data-testid="tdns-name" /></n-form-item>
        <n-form-item :label="t('technitium.api_url')">
          <div style="width: 100%">
            <n-input v-model:value="form.api_url" placeholder="https://dns.example.test:53443" data-testid="tdns-url" />
            <div class="form-hint">{{ t("technitium.api_url_hint") }}</div>
          </div>
        </n-form-item>
        <n-form-item :label="editing?.has_token ? t('technitium.token_keep') : t('technitium.token')">
          <n-input v-model:value="form.token" type="password" show-password-on="click" data-testid="tdns-token" />
        </n-form-item>
        <n-form-item :label="t('firewall_admin.verify_tls')">
          <n-switch v-model:value="form.verify_tls" />
        </n-form-item>
        <n-form-item :label="t('technitium.pull_what')">
          <n-space :size="20">
            <n-checkbox v-model:checked="form.sync_scopes">{{ t("technitium.scopes") }}</n-checkbox>
            <n-checkbox v-model:checked="form.sync_leases">{{ t("technitium.leases") }}</n-checkbox>
          </n-space>
        </n-form-item>
        <n-form-item :label="t('adguard_admin.sync_interval')">
          <n-input-number v-model:value="form.sync_interval_seconds" :min="60" :max="86400" />
        </n-form-item>
        <n-form-item :label="t('common.enable')">
          <n-switch v-model:value="form.enabled" />
        </n-form-item>
        <n-form-item :label="t('adguard_admin.scope_subnets')">
          <div style="width: 100%">
            <n-select v-model:value="form.scope_subnet_ids" :options="subnetOptions"
                      multiple filterable clearable :placeholder="t('adguard_admin.scope_all')" />
            <ScopeOverlapWarning :scope-empty="!form.scope_subnet_ids?.length" />
          </div>
        </n-form-item>
        <n-form-item :label="t('common.description')">
          <n-input v-model:value="form.description" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="show = false">
          <template #icon><n-icon><CancelIcon /></n-icon></template>
          {{ t("common.cancel") }}
        </n-button>
        <n-button type="primary" data-testid="tdns-save" @click="submit">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </n-space>
    </n-modal>
  </n-card>
</template>

<style scoped>
.form-hint { font-size: 11.5px; opacity: .7; margin-top: 4px; line-height: 1.5; }
.tab-label { display: inline-flex; align-items: center; gap: 6px; }
</style>
