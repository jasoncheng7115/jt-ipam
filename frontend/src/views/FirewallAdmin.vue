<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { fmtDateTime } from "@/utils/datetime";
import { autoSort } from "@/composables/useTableSort";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NIcon, NButton, NModal, NForm, NFormItem,
  NInput, NInputNumber, NSwitch, NCheckbox, NTabs, NTabPane, NSelect, NPopconfirm, NTag, NAlert,
  NTooltip, NEmpty,
  useMessage, type DataTableColumns,
} from "naive-ui";
import {
  FirewallIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SyncIcon, TestIcon, SaveIcon, CancelIcon,
  WarnIcon, VrfsIcon, ListIcon,
} from "@/icons";
import {
  listFirewalls, createFirewall, updateFirewall, deleteFirewall, testFirewall, syncFirewall,
  listAliasMappings, createAliasMapping, deleteAliasMapping, syncOneMapping,
  listFirewallRules, type OPNsenseRule,
  type OPNsenseFirewall, type OPNsenseAliasMapping,
} from "@/api/integrations";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
const { t } = useI18n();

const fwPrefs = useColumnPrefs("opnsense_fws",
  ["name", "api_url", "enabled", "verify_tls", "sync_dhcp", "sync_arp", "sync_openvpn", "sync_rules", "sync_nat", "last_sync_at", "actions"],
  ["name", "api_url", "enabled", "verify_tls", "sync_dhcp", "sync_arp", "sync_openvpn", "sync_rules", "sync_nat", "last_sync_at", "actions"]);
const fwPicker = [
  { key: "name", label: t("cols.name") }, { key: "api_url", label: "API URL" },
  { key: "enabled", label: t("cols.status") }, { key: "verify_tls", label: "Verify TLS" },
  { key: "sync_dhcp", label: "DHCP" }, { key: "sync_arp", label: "ARP" },
  { key: "sync_openvpn", label: "OpenVPN" }, { key: "sync_rules", label: "Rules" },
  { key: "sync_nat", label: "NAT" }, { key: "last_sync_at", label: t("cols.last_sync") },
  { key: "actions", label: t("cols.actions") },
];
const mapPrefs = useColumnPrefs("opnsense_maps",
  ["firewall_id", "object_type", "object_id", "alias_name", "mode", "last_synced_at", "actions"],
  ["firewall_id", "object_type", "object_id", "alias_name", "mode", "last_synced_at", "actions"]);
const mapPicker = [
  { key: "firewall_id", label: "Firewall" }, { key: "object_type", label: t("cols.object_type") },
  { key: "object_id", label: t("cols.object_id") }, { key: "alias_name", label: t("cols.alias_name") },
  { key: "mode", label: t("cols.mode") }, { key: "last_synced_at", label: t("cols.last_sync") },
  { key: "actions", label: t("cols.actions") },
];
const rulePrefs = useColumnPrefs("opnsense_rules",
  ["enabled", "sequence", "action", "interface", "direction", "protocol", "source_net", "destination_net", "description"],
  ["enabled", "sequence", "action", "interface", "direction", "protocol", "source_net", "destination_net", "description"]);
const rulePicker = [
  { key: "enabled", label: t("cols.enabled") }, { key: "sequence", label: t("cols.order") },
  { key: "action", label: t("cols.action") }, { key: "interface", label: t("cols.iface") },
  { key: "direction", label: t("cols.direction") }, { key: "protocol", label: "Proto" },
  { key: "source_net", label: "Source" }, { key: "destination_net", label: "Destination" },
  { key: "description", label: t("cols.description") },
];

const msg = useMessage();
const tab = ref<"firewalls" | "mappings" | "rules">("firewalls");
const fws = ref<OPNsenseFirewall[]>([]);

// Rules tab
const rulesFw = ref<string | null>(null);
const rules = ref<OPNsenseRule[]>([]);
import { useTableQuickFilter } from "@/composables/useTableQuickFilter";
const { query: ruleFilterQ, filtered: rulesFiltered } = useTableQuickFilter(rules);
const rulesLoading = ref(false);
async function loadRules() {
  if (!rulesFw.value) { rules.value = []; return; }
  rulesLoading.value = true;
  try {
    const res = await listFirewallRules(rulesFw.value, 1);
    rules.value = res.items;
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.network"));
  } finally {
    rulesLoading.value = false;
  }
}
function iconAction(icon: any, label: string, onClick: () => void, type?: any) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}
const allRuleCols = computed<DataTableColumns<OPNsenseRule>>(() => autoSort([
  {
    title: t("cols.enabled"), key: "enabled", width: 60,
    render: (r) => h(NTag, { type: r.enabled ? "success" : "default", size: "small" },
      () => r.enabled ? "✓" : "—"),
  },
  { title: t("cols.order"), key: "sequence", width: 70, render: (r) => r.sequence ?? "—",
    sorter: (a, b) => (a.sequence ?? 0) - (b.sequence ?? 0) },
  {
    title: t("cols.action"), key: "action", width: 80,
    render: (r) => {
      const c = r.action === "pass" ? "success" : r.action === "block" ? "error" : "warning";
      return h(NTag, { type: c, size: "small" }, () => r.action ?? "—");
    },
  },
  { title: t("cols.iface"), key: "interface", width: 90, render: (r) => r.interface ?? "—" },
  { title: t("cols.direction"), key: "direction", width: 60, render: (r) => r.direction ?? "—" },
  { title: t("cols.proto"), key: "protocol", width: 70, render: (r) => r.protocol ?? "—" },
  { title: t("cols.source"), key: "source_net", minWidth: 140, ellipsis: { tooltip: true },
    render: (r) => `${r.source_net ?? "*"}${r.source_port ? ":" + r.source_port : ""}` },
  { title: t("cols.destination"), key: "destination_net", minWidth: 140, ellipsis: { tooltip: true },
    render: (r) => `${r.destination_net ?? "*"}${r.destination_port ? ":" + r.destination_port : ""}` },
  { title: t("cols.description"), key: "description", minWidth: 200, ellipsis: { tooltip: true }, render: (r) => r.description ?? "—" },
]));
const mappings = ref<OPNsenseAliasMapping[]>([]);
const loading = ref(false);

const showFw = ref(false);
const editingFw = ref<OPNsenseFirewall | null>(null);
const newFw = ref({
  name: "", api_url: "", api_key: "", api_secret: "",
  verify_tls: true,
  sync_dhcp: false, sync_arp: false, sync_openvpn: false,
  sync_rules: false, sync_nat: false,
  sync_interval_seconds: 300,
  description: "",
});

function openFwCreate() {
  editingFw.value = null;
  newFw.value = {
    name: "", api_url: "", api_key: "", api_secret: "", verify_tls: true,
    sync_dhcp: false, sync_arp: false, sync_openvpn: false,
    sync_rules: false, sync_nat: false,
    sync_interval_seconds: 300, description: "",
  };
  showFw.value = true;
}
function openFwEdit(r: OPNsenseFirewall) {
  editingFw.value = r;
  newFw.value = {
    name: r.name, api_url: r.api_url, api_key: "", api_secret: "",
    verify_tls: r.verify_tls,
    sync_dhcp: r.sync_dhcp, sync_arp: r.sync_arp, sync_openvpn: r.sync_openvpn,
    sync_rules: r.sync_rules, sync_nat: r.sync_nat,
    sync_interval_seconds: r.sync_interval_seconds ?? 300,
    description: r.description ?? "",
  };
  showFw.value = true;
}
const showMapCreate = ref(false);
const newMap = ref({
  firewall_id: "", alias_name: "", alias_type: "host",
  selector_type: "section" as "section" | "subnet" | "tag" | "custom_field",
  selector_section_id: "" as string,
  selector_subnet_id: "" as string,
  selector_tag: "",
  selector_field: "",
  selector_value: "",
  direction: "push" as "push" | "pull" | "both",
});

import { listSections } from "@/api/sections";
import { listSubnets } from "@/api/subnets";
const sectionOpts = ref<{ label: string; value: string }[]>([]);
const subnetOpts = ref<{ label: string; value: string }[]>([]);

async function loadAliasSelectorOpts() {
  try {
    const [secs, subs] = await Promise.all([listSections(1, 200), listSubnets({ page: 1, pageSize: 500 })]);
    sectionOpts.value = secs.items.map((s) => ({ label: s.name, value: s.id }));
    subnetOpts.value = subs.items.map((s: any) => ({
      label: `${s.cidr}${s.description ? ' — ' + s.description : ''}`, value: s.id,
    }));
  } catch {}
}

const fwOptions = computed(() => fws.value.map((f) => ({ label: f.name, value: f.id })));
const insecureFws = computed(() => fws.value.filter((f) => !f.verify_tls));

async function refresh() {
  loading.value = true;
  try {
    const [f, m] = await Promise.all([listFirewalls(200, 0), listAliasMappings()]);
    fws.value = f.items;
    mappings.value = m.items;
  } catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}
async function submitFw() {
  try {
    if (editingFw.value) {
      const payload: any = {
        name: newFw.value.name,
        api_url: newFw.value.api_url,
        verify_tls: newFw.value.verify_tls,
        sync_dhcp: newFw.value.sync_dhcp,
        sync_arp: newFw.value.sync_arp,
        sync_openvpn: newFw.value.sync_openvpn,
        sync_rules: newFw.value.sync_rules,
        sync_nat: newFw.value.sync_nat,
        sync_interval_seconds: newFw.value.sync_interval_seconds,
        description: newFw.value.description || undefined,
      };
      // 只在使用者輸入新憑證時才送 — backend 要 key+secret 同時送
      if (newFw.value.api_key && newFw.value.api_secret) {
        payload.api_key = newFw.value.api_key;
        payload.api_secret = newFw.value.api_secret;
      }
      await updateFirewall(editingFw.value.id, payload);
    } else {
      await createFirewall({
        name: newFw.value.name, api_url: newFw.value.api_url,
        api_key: newFw.value.api_key, api_secret: newFw.value.api_secret,
        verify_tls: newFw.value.verify_tls,
        sync_dhcp: newFw.value.sync_dhcp,
        sync_arp: newFw.value.sync_arp,
        sync_openvpn: newFw.value.sync_openvpn,
        sync_rules: newFw.value.sync_rules,
        sync_nat: newFw.value.sync_nat,
        sync_interval_seconds: newFw.value.sync_interval_seconds,
        description: newFw.value.description || undefined,
      } as any);
    }
    showFw.value = false;
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function submitMap() {
  try {
    let sel: Record<string, unknown> = { type: newMap.value.selector_type };
    if (newMap.value.selector_type === "section") {
      sel.section_id = newMap.value.selector_section_id;
    } else if (newMap.value.selector_type === "subnet") {
      sel.subnet_id = newMap.value.selector_subnet_id;
    } else if (newMap.value.selector_type === "tag") {
      sel.tag = newMap.value.selector_tag;
    } else if (newMap.value.selector_type === "custom_field") {
      sel.field = newMap.value.selector_field;
      sel.value = newMap.value.selector_value;
    }
    await createAliasMapping({
      firewall_id: newMap.value.firewall_id,
      alias_name: newMap.value.alias_name,
      alias_type: newMap.value.alias_type,
      selector: sel,
      direction: newMap.value.direction,
    });
    showMapCreate.value = false;
    newMap.value = { firewall_id: "", alias_name: "", alias_type: "host",
      selector_type: "section", selector_section_id: "", selector_subnet_id: "",
      selector_tag: "", selector_field: "", selector_value: "",
      direction: "push" };
    await refresh();
  } catch (e: any) { msg.error(e?.message ?? e?.response?.data?.detail ?? t("errors.server")); }
}
function fmtSyncSummary(r: any): string {
  // 嘗試從 sync 結果撈幾個常見欄位；找不到就 fallback 通用字
  if (!r || typeof r !== "object") return t("common.ok");
  const parts: string[] = [];
  if (typeof r.inserted === "number") parts.push(t("common.added_n", { n: r.inserted }));
  if (typeof r.updated === "number") parts.push(t("common.updated_n", { n: r.updated }));
  if (typeof r.skipped === "number") parts.push(t("common.skipped_n", { n: r.skipped }));
  if (typeof r.errored === "number" && r.errored > 0) parts.push(t("common.failed_n", { n: r.errored }));
  if (Array.isArray(r.results) && r.results.length === 0) return t("common.ok");
  return parts.length ? parts.join("、") : t("common.ok");
}
async function testFw(id: string) {
  try { await testFirewall(id); msg.success(t("common.ok")); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function syncFw(id: string) {
  const row = fws.value.find((r) => r.id === id);
  const targetName = row?.name ?? id.slice(0, 8);
  try {
    await syncFirewall(id);
    // 後端立刻回 task_id，sync 在背景跑
    msg.success(t("tasks.queued_toast", { kind: "OPNsense sync", target: targetName }));
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function delFw(id: string) {
  try { await deleteFirewall(id); await refresh(); } catch { msg.error(t("errors.server")); }
}
async function syncMap(id: string) {
  try { const r = await syncOneMapping(id); msg.success(fmtSyncSummary(r)); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function delMap(id: string) {
  try { await deleteAliasMapping(id); await refresh(); } catch { msg.error(t("errors.server")); }
}

const allFwCols = computed<DataTableColumns<OPNsenseFirewall>>(() => autoSort([
  { title: t("firewall_admin.name"), key: "name", minWidth: 160, ellipsis: { tooltip: true } },
  { title: "API URL", key: "api_url", minWidth: 200, ellipsis: { tooltip: true } },
  {
    title: "TLS", key: "verify_tls", width: 120,
    render: (r) => r.verify_tls
      ? h(NTag, { size: "small", type: "success" }, () => t("firewall_admin.tls_verified"))
      : h(NTooltip, null, {
          trigger: () => h(NSpace, { size: 4, align: "center", "wrap-item": false }, () => [
            h(NIcon, { size: 16, color: "#d03050" }, () => h(WarnIcon)),
            h(NTag, { size: "small", type: "error", bordered: false },
              () => t("firewall_admin.tls_skip")),
          ]),
          default: () => t("firewall_admin.tls_skip_warning"),
        }),
  },
  {
    title: t("cols.last_sync"), key: "last_sync_at", width: 170,
    render: (r) => fmtDateTime(r.last_sync_at),
  },
  { title: t("cols.last_error"), key: "last_error", minWidth: 160, ellipsis: { tooltip: true }, render: (r) => r.last_error ?? "—" },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 176,
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openFwEdit(r)),
      iconAction(TestIcon, t("common.test"), () => testFw(r.id)),
      iconAction(SyncIcon, t("common.pull"), () => syncFw(r.id), "primary"),
      h(NPopconfirm, { onPositiveClick: () => delFw(r.id) },
        { trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
          default: () => t("common.confirm_delete") }),
    ]),
  },
]));
const allMapCols = computed<DataTableColumns<OPNsenseAliasMapping>>(() => autoSort([
  { title: t("firewall_admin.alias_name"), key: "alias_name", minWidth: 160, ellipsis: { tooltip: true } },
  { title: t("firewall_admin.alias_type"), key: "alias_type", width: 110 },
  {
    title: t("cols.fw"), key: "firewall_id", width: 150, ellipsis: { tooltip: true },
    render: (r) => fws.value.find((f) => f.id === r.firewall_id)?.name ?? r.firewall_id.slice(0, 8),
  },
  { title: t("firewall_admin.direction"), key: "direction", width: 110 },
  { title: t("firewall_admin.last_synced_count"), key: "last_synced_count", width: 120,
    render: (r) => r.last_synced_count ?? "—" },
  {
    title: t("cols.selector"), key: "selector", minWidth: 200, ellipsis: { tooltip: true },
    render: (r) => h("code", { style: "font-size: 11px" }, JSON.stringify(r.selector).slice(0, 60)),
  },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 96,
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(SyncIcon, t("common.sync"), () => syncMap(r.id), "primary"),
      h(NPopconfirm, { onPositiveClick: () => delMap(r.id) },
        { trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
          default: () => t("common.confirm_delete") }),
    ]),
  },
]));

const fwCols = computed<DataTableColumns<OPNsenseFirewall>>(() =>
  allFwCols.value.filter((c: any) => fwPrefs.visibleKeys.value.includes(c.key)));
const mapCols = computed<DataTableColumns<OPNsenseAliasMapping>>(() =>
  allMapCols.value.filter((c: any) => mapPrefs.visibleKeys.value.includes(c.key)));
const ruleCols = computed<DataTableColumns<OPNsenseRule>>(() =>
  allRuleCols.value.filter((c: any) => rulePrefs.visibleKeys.value.includes(c.key)));

onMounted(() => { void refresh(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><FirewallIcon /></n-icon>
        <span>{{ t("firewall_admin.title") }}</span>
      </n-space>
    </template>
    <n-tabs v-model:value="tab" type="line">
      <n-tab-pane name="firewalls">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><FirewallIcon /></n-icon>{{ t('firewall_admin.title') }}</span>
        </template>
        <n-alert v-if="insecureFws.length" type="warning" style="margin-bottom: 12px"
                 :title="t('firewall_admin.tls_alert_title')">
          <template #icon><n-icon><WarnIcon /></n-icon></template>
          {{ t('firewall_admin.tls_alert_body', { n: insecureFws.length, names: insecureFws.map(f => f.name).join('、') }) }}
        </n-alert>
        <n-space style="margin-bottom: 12px">
          <n-button @click="refresh" :loading="loading">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>
            {{ t("common.refresh") }}
          </n-button>
          <n-button type="primary" @click="openFwCreate">
            <template #icon><n-icon><PlusIcon /></n-icon></template>
            {{ t("firewall_admin.create_firewall") }}
          </n-button>
          <ColumnPicker :all="fwPicker" :visible="fwPrefs.visibleKeys.value"
                        @update:visible="fwPrefs.setVisible" @reset="fwPrefs.reset" />
          <ExportButton :columns="fwCols" :rows="fws" filename="firewalls" :title="t('firewall_admin.title')" />
        </n-space>
        <n-data-table :columns="fwCols" :data="fws" :loading="loading" :bordered="false" :scroll-x="986" />
      </n-tab-pane>
      <n-tab-pane name="mappings">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><VrfsIcon /></n-icon>{{ t('firewall_admin.alias_mappings') }}</span>
        </template>
        <n-alert type="info" size="small" :show-icon="true" style="margin-bottom: 12px">
          {{ t("firewall_admin.mappings_hint") }}
        </n-alert>
        <n-space style="margin-bottom: 12px">
          <n-button type="primary"
                    @click="loadAliasSelectorOpts(); showMapCreate = true">
            <template #icon><n-icon><PlusIcon /></n-icon></template>
            {{ t("firewall_admin.create_mapping") }}
          </n-button>
          <ColumnPicker :all="mapPicker" :visible="mapPrefs.visibleKeys.value"
                        @update:visible="mapPrefs.setVisible" @reset="mapPrefs.reset" />
          <ExportButton :columns="mapCols" :rows="mappings" filename="firewall-alias-mappings" :title="t('firewall_admin.alias_mappings')" />
        </n-space>
        <n-data-table :columns="mapCols" :data="mappings" :loading="loading" :bordered="false" :scroll-x="946" />
      </n-tab-pane>
      <n-tab-pane name="rules">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><ListIcon /></n-icon>{{ t("firewall_admin.rules") }}</span>
        </template>
        <n-alert type="info" size="small" :show-icon="true" style="margin-bottom: 12px">
          {{ t("firewall_admin.rules_hint") }}
        </n-alert>
        <n-space style="margin-bottom: 12px" align="center">
          <n-select
            v-model:value="rulesFw"
            :options="fwOptions"
            :placeholder="t('firewall_admin.pick_firewall')"
            style="width: 240px"
            @update:value="loadRules"
          />
          <n-button @click="loadRules" :loading="rulesLoading">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>
            {{ t("common.refresh") }}
          </n-button>
          <span v-if="rulesFw && rules.length" style="opacity: 0.7;">
            {{ t("firewall_admin.rules_count", { n: rules.length }) }}
          </span>
          <n-input v-model:value="ruleFilterQ" :placeholder="t('common.filter')" clearable style="width: 160px" />
          <ColumnPicker :all="rulePicker" :visible="rulePrefs.visibleKeys.value"
                        @update:visible="rulePrefs.setVisible" @reset="rulePrefs.reset" />
          <ExportButton :columns="ruleCols" :rows="rules" filename="firewall-rules" :title="t('firewall_admin.rules')" />
        </n-space>
        <n-data-table
          v-if="rulesFw"
          :columns="ruleCols" :data="rulesFiltered" :loading="rulesLoading"
          :bordered="false" size="small" :scroll-x="910"
          :pagination="{ pageSize: 100, showSizePicker: true, pageSizes: [50, 100, 200, 500] }"
        />
        <n-empty v-else :description="t('firewall_admin.pick_firewall_to_view')" />
      </n-tab-pane>
    </n-tabs>

    <n-modal v-model:show="showFw" preset="card"
             :title="editingFw ? t('common.edit') : t('firewall_admin.create_firewall')"
             style="width: 480px">
      <n-form>
        <n-alert type="info" :bordered="false" :show-icon="true" style="margin-bottom: 12px">
          {{ t("firewall_admin.version_hint") }}
        </n-alert>
        <n-form-item :label="t('firewall_admin.name')"><n-input v-model:value="newFw.name" /></n-form-item>
        <n-form-item :label="t('firewall_admin.api_url')">
          <n-input v-model:value="newFw.api_url"
                   :placeholder="t('firewall_admin.url_ph')" />
        </n-form-item>
        <n-form-item :label="`API key${editingFw ? ' (' + t('users.password_blank_unchanged') + ')' : ''}`">
          <n-input v-model:value="newFw.api_key" />
        </n-form-item>
        <n-form-item :label="`API secret${editingFw ? ' (' + t('users.password_blank_unchanged') + ')' : ''}`">
          <n-input v-model:value="newFw.api_secret" type="password" show-password-on="click" />
        </n-form-item>
        <n-form-item :label="t('firewall_admin.verify_tls')">
          <n-switch v-model:value="newFw.verify_tls" />
          <template #feedback>
            <span v-if="!newFw.verify_tls" style="color: #d03050">
              {{ t('firewall_admin.tls_skip_warning') }}
            </span>
            <span v-else style="opacity: 0.7">
              {{ t('firewall_admin.tls_verified_hint') }}
            </span>
          </template>
        </n-form-item>
        <n-form-item :label="t('firewall_admin.sync_interval')">
          <n-input-number v-model:value="newFw.sync_interval_seconds" :min="30" :max="86400" />
          <template #feedback>
            <span style="opacity: 0.7; font-size: 12px;">
              {{ t("firewall_admin.sync_interval_hint") }}
            </span>
          </template>
        </n-form-item>
        <n-form-item :label="t('firewall_admin.sync_sources')">
          <n-space :size="20" wrap>
            <n-checkbox v-model:checked="newFw.sync_dhcp">DHCP leases</n-checkbox>
            <n-checkbox v-model:checked="newFw.sync_arp">ARP table</n-checkbox>
            <n-checkbox v-model:checked="newFw.sync_openvpn">OpenVPN sessions</n-checkbox>
            <n-checkbox v-model:checked="newFw.sync_rules">{{ t("firewall_admin.sync_filter_rules") }}</n-checkbox>
            <n-checkbox v-model:checked="newFw.sync_nat">{{ t("firewall_admin.sync_nat_rules") }}</n-checkbox>
          </n-space>
          <template #feedback>
            <span style="opacity: 0.7; font-size: 12px;">
              {{ t("firewall_admin.sync_sources_hint") }}
            </span>
          </template>
        </n-form-item>
        <n-form-item :label="t('common.description')">
          <n-input v-model:value="newFw.description" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="showFw = false">
          <template #icon><n-icon><CancelIcon /></n-icon></template>
          {{ t("common.cancel") }}
        </n-button>
        <n-button type="primary" @click="submitFw">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </n-space>
    </n-modal>

    <n-modal v-model:show="showMapCreate" preset="card" :title="t('firewall_admin.create_mapping')"
             style="width: 520px">
      <n-form>
        <n-form-item label="Firewall">
          <n-select v-model:value="newMap.firewall_id" :options="fwOptions" />
        </n-form-item>
        <n-form-item :label="t('firewall_admin.alias_name')">
          <n-input v-model:value="newMap.alias_name" placeholder="jt_section_addrs" />
        </n-form-item>
        <n-form-item :label="t('firewall_admin.alias_type')">
          <n-select v-model:value="newMap.alias_type"
                    :options="['host','network','port','url','urltable','geoip','networkgroup','mac','asn'].map(v => ({label: v, value: v}))" />
        </n-form-item>
        <n-form-item :label="t('firewall_admin.selector_type')">
          <n-select v-model:value="newMap.selector_type"
                    :options="[
                      {label: 'Section', value: 'section'},
                      {label: 'Subnet', value: 'subnet'},
                      {label: 'Tag', value: 'tag'},
                      {label: 'Custom field', value: 'custom_field'},
                    ]" />
        </n-form-item>
        <n-form-item v-if="newMap.selector_type === 'section'" label="Section">
          <n-select v-model:value="newMap.selector_section_id" :options="sectionOpts" filterable />
        </n-form-item>
        <n-form-item v-else-if="newMap.selector_type === 'subnet'" label="Subnet">
          <n-select v-model:value="newMap.selector_subnet_id" :options="subnetOpts" filterable />
        </n-form-item>
        <n-form-item v-else-if="newMap.selector_type === 'tag'" label="Tag">
          <n-input v-model:value="newMap.selector_tag" placeholder="wifi-guest" />
        </n-form-item>
        <template v-else>
          <n-form-item label="Custom field name">
            <n-input v-model:value="newMap.selector_field" placeholder="role" />
          </n-form-item>
          <n-form-item label="Value">
            <n-input v-model:value="newMap.selector_value" placeholder="monitoring" />
          </n-form-item>
        </template>
        <n-form-item :label="t('firewall_admin.direction')">
          <n-select v-model:value="newMap.direction"
                    :options="[{label:'push',value:'push'},{label:'pull',value:'pull'},{label:'both',value:'both'}]" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="showMapCreate = false">{{ t("common.cancel") }}</n-button>
        <n-button type="primary" @click="submitMap">{{ t("common.save") }}</n-button>
      </n-space>
    </n-modal>
  </n-card>
</template>
