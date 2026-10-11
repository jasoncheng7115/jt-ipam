<script setup lang="ts">
/**
 * Palo Alto（PAN-OS）整合 —— 與 OPNsense / pfSense / FortiGate 各自獨立設定。
 * 唯讀拉取（只讀，不會更動防火牆任何設定）。
 *
 * 「測試連線」回逐端點診斷：PAN-OS 的 REST URI 帶版本段（`/restapi/v11.1/…`），
 * 對不上就整批 404，所以診斷會把偵測到的版本一起顯示出來。
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
  listPaloAlto, createPaloAlto, updatePaloAlto, deletePaloAlto,
  testPaloAlto, syncPaloAlto,
  type PaloAltoFirewall, type PaloAltoDiagnosis,
} from "@/api/paloalto";
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

const COLS = ["name", "api_url", "enabled", "vsys", "sync_flags", "last_sync_at", "last_error", "actions"];
const { visibleKeys: vis, setVisible: setVis, reset: resetVis, order, setOrder, orderColumns } =
  useColumnPrefs("paloalto", COLS, COLS);
const picker = computed(() => [
  { key: "name", label: t("cols.name") },
  { key: "api_url", label: "API URL" },
  { key: "enabled", label: t("cols.status") },
  { key: "vsys", label: "vsys" },
  { key: "sync_flags", label: t("cols.sync_items") },
  { key: "last_sync_at", label: t("cols.last_sync") },
  { key: "last_error", label: t("cols.last_error") },
  { key: "actions", label: t("cols.actions") },
]);

const rows = ref<PaloAltoFirewall[]>([]);
const loading = ref(false);
const show = ref(false);
const editing = ref<PaloAltoFirewall | null>(null);

const diag = ref<PaloAltoDiagnosis | null>(null);
const diagFor = ref<string>("");
const diagOpen = ref(false);

function blankForm() {
  return {
    name: "", api_url: "", api_key: "", vsysText: "", api_version: "",
    enabled: true, verify_tls: true,
    sync_dhcp: false, sync_arp: true,
    sync_policies: false, sync_nat: false, sync_addresses: false, sync_vpn: true,
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
  try { rows.value = (await listPaloAlto()).items; }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}

function openCreate() {
  editing.value = null;
  form.value = blankForm();
  show.value = true;
}

function openEdit(r: PaloAltoFirewall) {
  editing.value = r;
  form.value = {
    name: r.name, api_url: r.api_url, api_key: "",
    vsysText: (r.vsys_list ?? []).join(", "),
    api_version: r.api_version ?? "",
    enabled: r.enabled, verify_tls: r.verify_tls,
    sync_dhcp: r.sync_dhcp, sync_arp: r.sync_arp,
    sync_policies: r.sync_policies, sync_nat: r.sync_nat,
    sync_addresses: r.sync_addresses,
    sync_vpn: r.sync_vpn ?? true,
    sync_interval_seconds: r.sync_interval_seconds,
    description: r.description ?? "",
    scope_subnet_ids: r.scope_subnet_ids ?? [],
  };
  show.value = true;
}

async function submit() {
  const vsys = form.value.vsysText.split(",").map((s) => s.trim()).filter(Boolean);
  const ver = form.value.api_version.trim();
   
  const payload: any = {
    name: form.value.name, api_url: form.value.api_url,
    vsys_list: vsys.length ? vsys : null,
    enabled: form.value.enabled, verify_tls: form.value.verify_tls,
    sync_dhcp: form.value.sync_dhcp, sync_arp: form.value.sync_arp,
    sync_policies: form.value.sync_policies, sync_nat: form.value.sync_nat,
    sync_addresses: form.value.sync_addresses,
    sync_vpn: form.value.sync_vpn,
    sync_interval_seconds: form.value.sync_interval_seconds,
    description: form.value.description || undefined,
    scope_subnet_ids: form.value.scope_subnet_ids,
  };
  if (ver) payload.api_version = ver;
  try {
    if (editing.value) {
      if (form.value.api_key) payload.api_key = form.value.api_key;
      // 清空版本欄＝改回自動偵測。PATCH 的 null 是「不修改」，所以要用旗標講清楚。
      if (!ver) payload.clear_api_version = true;
      await updatePaloAlto(editing.value.id, payload);
    } else {
      payload.api_key = form.value.api_key;
      await createPaloAlto(payload);
    }
    show.value = false;
    msg.success(t("common.ok"));
    await refresh();
  } catch (e) { msg.error(apiErrMsg(e)); }
}

async function test(r: PaloAltoFirewall) {
  try {
    diag.value = await testPaloAlto(r.id);
    diagFor.value = r.name;
    diagOpen.value = true;
  } catch (e) { msg.error(apiErrMsg(e)); }
}

async function sync(id: string) {
  try {
    const r = await syncPaloAlto(id);
    trackTask(r.task_id, { onDone: () => void refresh() });
  } catch (e) { msg.error(apiErrMsg(e)); }
}

async function del(id: string) {
  try { await deletePaloAlto(id); await refresh(); }
  catch (e) { msg.error(apiErrMsg(e)); }
}

function iconAction(icon: unknown, label: string, onClick: () => void, type?: string) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type: type as never,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon as never)) }),
    default: () => label,
  });
}

const SYNC_TAGS: [keyof PaloAltoFirewall, string][] = [
  ["sync_dhcp", "DHCP"], ["sync_arp", "ARP"],
  ["sync_policies", "policy"], ["sync_nat", "NAT"], ["sync_addresses", "addr"], ["sync_vpn", "VPN"],
];

const allCols = computed<DataTableColumns<PaloAltoFirewall>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 150, ellipsis: { tooltip: true } },
  { title: "API URL", key: "api_url", minWidth: 180, ellipsis: { tooltip: true } },
  {
    title: t("common.status"), key: "enabled", width: 100,
    render: (r) => h(NTag, { type: r.enabled ? "success" : "default", size: "small" },
      () => r.enabled ? t("common.enabled") : t("common.disabled")),
  },
  {
    title: "vsys", key: "vsys", width: 140, ellipsis: { tooltip: true },
    render: (r) => (r.vsys_list && r.vsys_list.length)
      ? r.vsys_list.join(", ") : t("paloalto.vsys_auto"),
  },
  {
    title: t("common.sync"), key: "sync_flags", minWidth: 170,
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
      iconAction(TestIcon, t("paloalto.diagnose"), () => test(r)),
      iconAction(SyncIcon, t("common.pull"), () => sync(r.id), "primary"),
      h(NPopconfirm, { onPositiveClick: () => del(r.id) }, {
        trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]));
const cols = computed<DataTableColumns<PaloAltoFirewall>>(() =>
   
  orderColumns(allCols.value.filter((c: any) => vis.value.includes(c.key))),
);

onMounted(() => { void refresh(); void loadSubnetOptions(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><FirewallIcon /></n-icon>
        <span>{{ t("paloalto.title") }}</span>
        <n-tag size="small" type="warning" :bordered="false">Beta</n-tag>
      </n-space>
    </template>

    <n-alert type="info" :bordered="false" style="margin-bottom: 12px">
      {{ t("paloalto.intro") }}
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
             :title="editing ? t('common.edit') : `${t('common.create')} — ${t('paloalto.title')}`"
             style="width: 580px">
      <n-form>
        <n-form-item :label="t('common.name')"><n-input v-model:value="form.name" /></n-form-item>
        <n-form-item label="API URL">
          <n-input v-model:value="form.api_url" placeholder="https://192.0.2.1" />
        </n-form-item>
        <n-form-item :label="editing ? t('paloalto.key_keep') : t('paloalto.key')">
          <n-input v-model:value="form.api_key" type="password" show-password-on="click" />
        </n-form-item>
        <div style="margin: -8px 0 12px">
          <span style="font-size: 11px; opacity: .7">{{ t("paloalto.key_hint") }}</span>
        </div>
        <n-form-item label="vsys">
          <n-input v-model:value="form.vsysText" :placeholder="t('paloalto.vsys_ph')" />
        </n-form-item>
        <n-form-item :label="t('paloalto.api_version')">
          <div style="width: 100%">
            <n-input v-model:value="form.api_version" placeholder="v11.1" />
            <div style="font-size: 11px; opacity: .7; margin-top: 4px">
              {{ t("paloalto.api_version_hint") }}
            </div>
          </div>
        </n-form-item>
        <n-form-item :label="t('firewall_admin.verify_tls')">
          <n-switch v-model:value="form.verify_tls" />
        </n-form-item>
        <n-form-item :label="t('paloalto.pull_what')">
          <n-space :size="16" :wrap="true">
            <n-checkbox v-model:checked="form.sync_dhcp">{{ t("pfsense_admin.dhcp_leases") }}</n-checkbox>
            <n-checkbox v-model:checked="form.sync_arp">ARP</n-checkbox>
            <n-checkbox v-model:checked="form.sync_policies">{{ t("paloalto.policies") }}</n-checkbox>
            <n-checkbox v-model:checked="form.sync_nat">NAT</n-checkbox>
            <n-checkbox v-model:checked="form.sync_addresses">{{ t("paloalto.addresses") }}</n-checkbox>
            <n-checkbox v-model:checked="form.sync_vpn">{{ t("paloalto.vpn_s2s") }}</n-checkbox>
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
             :title="`${t('paloalto.diagnose')} — ${diagFor}`" style="width: 560px">
      <template v-if="diag">
        <n-space vertical :size="10">
          <div><strong>{{ t("paloalto.api_version") }}：</strong>{{ diag.api_version || "—" }}</div>
          <div><strong>vsys：</strong>{{ diag.vsys.join(", ") || "—" }}</div>
          <n-alert :type="diag.ok_count === diag.checks.length ? 'success' : 'warning'" :bordered="false">
            {{ t("paloalto.diag_summary", { ok: diag.ok_count, total: diag.checks.length }) }}
          </n-alert>
          <div v-for="c in diag.checks" :key="c.endpoint" class="diag-row">
            <n-tag :type="c.ok ? 'success' : 'error'" size="small" :bordered="false">
              {{ c.ok ? "OK" : "ERR" }}
            </n-tag>
            <code>{{ c.endpoint }}</code>
            <span v-if="c.ok" class="diag-note">{{ t("paloalto.diag_rows", { n: c.rows ?? 0 }) }}</span>
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
