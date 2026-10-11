<script setup lang="ts">
/**
 * FortiGate 整合 —— 與 OPNsense / pfSense 各自獨立設定。
 * 走 FortiOS REST API 唯讀拉取（只打 GET，不會更動 FortiGate 任何設定）。
 * 「測試連線」回逐端點診斷，方便對齊不同 FortiOS 版本的欄位差異。
 */
import { computed, h, onMounted, ref } from "vue";
import { fmtDateTime } from "@/utils/datetime";
import { useI18n } from "vue-i18n";
import { trackTask } from "@/composables/useTaskTracker";
import ScopeOverlapWarning from "@/components/ScopeOverlapWarning.vue";
import {
  NCard, NDataTable, NSpace, NButton, NTag, NIcon, NTooltip, NAlert, NModal, NForm,
  NFormItem, NInput, NInputNumber, NSwitch, NSelect, NCheckbox, NPopconfirm,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { listSubnets } from "@/api/subnets";
import {
  listFortiGate, createFortiGate, updateFortiGate, deleteFortiGate,
  testFortiGate, syncFortiGate,
  type FortiGateFirewall, type FortiGateDiagnosis,
} from "@/api/fortigate";
import {
  FirewallIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SyncIcon, TestIcon,
  SaveIcon, CancelIcon,
} from "@/icons";
import { autoSort } from "@/composables/useTableSort";
import ColumnPicker from "@/components/ColumnPicker.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { apiErrMsg } from "@/api/client";

const { t } = useI18n();
const msg = useMessage();

const COLS = ["name", "api_url", "enabled", "vdoms", "sync_flags", "last_sync_at", "last_error", "actions"];
const { visibleKeys: vis, setVisible: setVis, reset: resetVis, order, setOrder, orderColumns } =
  useColumnPrefs("fortigate", COLS, COLS);
const picker = computed(() => [
  { key: "name", label: t("cols.name") },
  { key: "api_url", label: "API URL" },
  { key: "enabled", label: t("cols.status") },
  { key: "vdoms", label: "VDOM" },
  { key: "sync_flags", label: t("cols.sync_items") },
  { key: "last_sync_at", label: t("cols.last_sync") },
  { key: "last_error", label: t("cols.last_error") },
  { key: "actions", label: t("cols.actions") },
]);

const rows = ref<FortiGateFirewall[]>([]);
const loading = ref(false);
const show = ref(false);
const editing = ref<FortiGateFirewall | null>(null);

// 連線診斷結果（測試連線後顯示）
const diag = ref<FortiGateDiagnosis | null>(null);
const diagFor = ref<string>("");
const diagOpen = ref(false);

function blankForm() {
  return {
    name: "", api_url: "", api_token: "", vdomsText: "",
    enabled: true, verify_tls: true,
    sync_dhcp: false, sync_dhcp_ranges: false, sync_arp: true, sync_vpn: false,
    sync_policies: false, sync_nat: false, sync_addresses: false,
    sync_interval_seconds: 300, description: "",
    scope_subnet_ids: [] as string[],
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
  try { rows.value = (await listFortiGate()).items; }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}

function openCreate() {
  editing.value = null;
  form.value = blankForm();
  show.value = true;
}

function openEdit(r: FortiGateFirewall) {
  editing.value = r;
  form.value = {
    name: r.name, api_url: r.api_url, api_token: "",
    vdomsText: (r.vdoms ?? []).join(", "),
    enabled: r.enabled, verify_tls: r.verify_tls,
    sync_dhcp: r.sync_dhcp, sync_dhcp_ranges: r.sync_dhcp_ranges, sync_arp: r.sync_arp,
    sync_vpn: r.sync_vpn, sync_policies: r.sync_policies, sync_nat: r.sync_nat,
    sync_addresses: r.sync_addresses,
    sync_interval_seconds: r.sync_interval_seconds,
    description: r.description ?? "",
    scope_subnet_ids: r.scope_subnet_ids ?? [],
  };
  show.value = true;
}

async function submit() {
  const vdoms = form.value.vdomsText.split(",").map((s) => s.trim()).filter(Boolean);
  const payload: any = {
    name: form.value.name, api_url: form.value.api_url,
    vdoms: vdoms.length ? vdoms : null,
    enabled: form.value.enabled, verify_tls: form.value.verify_tls,
    sync_dhcp: form.value.sync_dhcp, sync_dhcp_ranges: form.value.sync_dhcp_ranges,
    sync_arp: form.value.sync_arp, sync_vpn: form.value.sync_vpn,
    sync_policies: form.value.sync_policies, sync_nat: form.value.sync_nat,
    sync_addresses: form.value.sync_addresses,
    sync_interval_seconds: form.value.sync_interval_seconds,
    description: form.value.description || undefined,
    scope_subnet_ids: form.value.scope_subnet_ids,
  };
  try {
    if (editing.value) {
      if (form.value.api_token) payload.api_token = form.value.api_token;
      await updateFortiGate(editing.value.id, payload);
    } else {
      payload.api_token = form.value.api_token;
      await createFortiGate(payload);
    }
    show.value = false;
    msg.success(t("common.ok"));
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

async function test(r: FortiGateFirewall) {
  try {
    diag.value = await testFortiGate(r.id);
    diagFor.value = r.name;
    diagOpen.value = true;
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

async function sync(id: string) {
  try {
    const r = await syncFortiGate(id);
    trackTask(r.task_id, { onDone: () => void refresh() });
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

async function del(id: string) {
  try { await deleteFortiGate(id); await refresh(); }
  catch { msg.error(t("errors.server")); }
}

function iconAction(icon: any, label: string, onClick: () => void, type?: any) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}

const SYNC_TAGS: [keyof FortiGateFirewall, string][] = [
  ["sync_dhcp", "DHCP"], ["sync_dhcp_ranges", "range"], ["sync_arp", "ARP"],
  ["sync_vpn", "VPN"], ["sync_policies", "policy"], ["sync_nat", "NAT"],
  ["sync_addresses", "addr"],
];

const allCols = computed<DataTableColumns<FortiGateFirewall>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 150, ellipsis: { tooltip: true } },
  { title: "API URL", key: "api_url", minWidth: 180, ellipsis: { tooltip: true } },
  {
    title: t("common.status"), key: "enabled", width: 100,
    render: (r) => h(NTag, { type: r.enabled ? "success" : "default", size: "small" },
      () => r.enabled ? t("common.enabled") : t("common.disabled")),
  },
  {
    title: "VDOM", key: "vdoms", width: 140, ellipsis: { tooltip: true },
    render: (r) => (r.vdoms && r.vdoms.length) ? r.vdoms.join(", ") : t("fortigate.vdom_auto"),
  },
  {
    title: t("common.sync"), key: "sync_flags", minWidth: 190,
    render: (r) => h(NSpace, { size: 3, wrap: true }, () =>
      SYNC_TAGS.filter(([k]) => r[k]).map(([, label]) =>
        h(NTag, { size: "tiny", type: "info", bordered: false }, () => label))),
  },
  { title: t("cols.last_sync"), key: "last_sync_at", width: 165, render: (r) => fmtDateTime(r.last_sync_at) },
  {
    title: t("cols.last_error"), key: "last_error", minWidth: 150,
    ellipsis: { tooltip: true }, render: (r) => r.last_error ?? "—",
  },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 176,
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openEdit(r)),
      iconAction(TestIcon, t("fortigate.diagnose"), () => test(r)),
      iconAction(SyncIcon, t("common.pull"), () => sync(r.id), "primary"),
      h(NPopconfirm, { onPositiveClick: () => del(r.id) }, {
        trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]));
const cols = computed<DataTableColumns<FortiGateFirewall>>(() =>
  orderColumns(allCols.value.filter((c: any) => vis.value.includes(c.key))),
);

onMounted(() => { void refresh(); void loadSubnetOptions(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><FirewallIcon /></n-icon>
        <span>{{ t("fortigate.title") }}</span>
      </n-space>
    </template>

    <n-alert type="info" :bordered="false" style="margin-bottom: 12px">
      {{ t("fortigate.intro") }}
    </n-alert>

    <n-space style="margin-bottom: 12px">
      <n-button @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
      <n-button type="primary" @click="openCreate">
        <template #icon><n-icon><PlusIcon /></n-icon></template>
        {{ t("common.create") }}
      </n-button>
      <ColumnPicker :all="picker" :visible="vis" @update:visible="setVis" @reset="resetVis"
                    :order="order" @update:order="setOrder" />
    </n-space>

    <n-data-table :columns="cols" :data="rows" :loading="loading" :bordered="false" :scroll-x="1200" />

    <!-- 新增 / 編輯 -->
    <n-modal v-model:show="show" preset="card"
             :title="editing ? t('common.edit') : `${t('common.create')} — ${t('fortigate.title')}`"
             style="width: 580px">
      <n-form>
        <n-form-item :label="t('common.name')"><n-input v-model:value="form.name" /></n-form-item>
        <n-form-item label="API URL">
          <n-input v-model:value="form.api_url" placeholder="https://192.0.2.1" />
        </n-form-item>
        <n-form-item :label="editing ? t('fortigate.token_keep') : t('fortigate.token')">
          <n-input v-model:value="form.api_token" type="password" show-password-on="click" />
        </n-form-item>
        <div style="margin: -8px 0 12px">
          <span style="font-size: 11px; opacity: .7">{{ t("fortigate.token_hint") }}</span>
        </div>
        <n-form-item label="VDOM">
          <n-input v-model:value="form.vdomsText" :placeholder="t('fortigate.vdom_ph')" />
        </n-form-item>
        <n-form-item :label="t('firewall_admin.verify_tls')">
          <n-switch v-model:value="form.verify_tls" />
        </n-form-item>
        <n-form-item :label="t('fortigate.pull_what')">
          <n-space :size="16" :wrap="true">
            <n-checkbox v-model:checked="form.sync_dhcp">{{ t("pfsense_admin.dhcp_leases") }}</n-checkbox>
            <n-checkbox v-model:checked="form.sync_dhcp_ranges">{{ t("pfsense_admin.dhcp_ranges") }}</n-checkbox>
            <n-checkbox v-model:checked="form.sync_arp">ARP</n-checkbox>
            <n-checkbox v-model:checked="form.sync_vpn">VPN</n-checkbox>
            <n-checkbox v-model:checked="form.sync_policies">{{ t("fortigate.policies") }}</n-checkbox>
            <n-checkbox v-model:checked="form.sync_nat">NAT</n-checkbox>
            <n-checkbox v-model:checked="form.sync_addresses">{{ t("fortigate.addresses") }}</n-checkbox>
          </n-space>
        </n-form-item>
        <n-form-item :label="t('adguard_admin.sync_interval')">
          <n-input-number v-model:value="form.sync_interval_seconds" :min="30" :max="86400" />
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
        <n-button type="primary" @click="submit">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </n-space>
    </n-modal>

    <!-- 連線診斷 -->
    <n-modal v-model:show="diagOpen" preset="card"
             :title="`${t('fortigate.diagnose')} — ${diagFor}`" style="width: 560px">
      <template v-if="diag">
        <n-space vertical :size="10">
          <div>
            <strong>VDOM：</strong>
            <template v-if="diag.vdom_scoped === false">
              {{ t("fortigate.no_vdom") }}
            </template>
            <template v-else>{{ diag.vdoms.join(", ") || "—" }}</template>
            <span v-if="diag.vdom_mode" class="diag-note"> · {{ diag.vdom_mode }}</span>
          </div>
          <n-alert :type="diag.ok_count === diag.checks.length ? 'success' : 'warning'" :bordered="false">
            {{ t("fortigate.diag_summary", { ok: diag.ok_count, total: diag.checks.length }) }}
          </n-alert>
          <div v-for="c in diag.checks" :key="c.endpoint" class="diag-row">
            <n-tag :type="c.ok ? 'success' : 'error'" size="small" :bordered="false">
              {{ c.ok ? "OK" : "ERR" }}
            </n-tag>
            <code>{{ c.endpoint }}</code>
            <span v-if="c.ok" class="diag-note">
              {{ t("fortigate.diag_rows", { n: c.rows ?? 0 }) }}
              <!-- 帶 VDOM 讀不到、不帶就讀得到：這是「範圍設錯」而不是「端點不存在」，
                   兩者的錯誤訊息長得一樣，不講出來現場分不出來（issue #26） -->
              <template v-if="c.without_vdom"> · {{ t("fortigate.diag_without_vdom") }}</template>
            </span>
            <span v-else class="diag-note">{{ c.error }}</span>
          </div>
        </n-space>
      </template>
    </n-modal>
  </n-card>
</template>

<style scoped>
.diag-row { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.diag-note { opacity: .7; }
</style>
