<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { fmtDateTime } from "@/utils/datetime";
import { useI18n } from "vue-i18n";
import ScopeOverlapWarning from "@/components/ScopeOverlapWarning.vue";
import {
  NCard, NDataTable, NSpace, NIcon, NButton, NModal, NForm, NFormItem,
  NInput, NInputNumber, NSwitch, NSelect, NTag, NPopconfirm, NAlert, NTooltip,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { listSubnets } from "@/api/subnets";
import {
  FirewallIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SyncIcon, TestIcon, WarnIcon,
} from "@/icons";
import {
  listPfSense, createPfSense, updatePfSense, deletePfSense, testPfSense, syncPfSense,
  type PfSense,
} from "@/api/pfsense";
import { autoSort } from "@/composables/useTableSort";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";

const { t } = useI18n();
const msg = useMessage();
const rows = ref<PfSense[]>([]);
// 停用 TLS 驗證的 pfSense（顯示警告橫幅，比照 OPNsense）
const insecureFws = computed(() => rows.value.filter((f) => !f.verify_tls));
const loading = ref(false);

// 表格欄位偏好（比照 OPNsense）：預設只顯示會撐爆的那幾欄以外的核心欄，其餘可在「欄位」勾選
const PF_ALL = ["name", "api_url", "verify_tls", "last_sync_at", "last_error", "actions"];
const PF_DEFAULT = ["name", "api_url", "verify_tls", "last_sync_at", "actions"];
const pfPrefs = useColumnPrefs("pfsense_fws", PF_ALL, PF_DEFAULT);
// computed 讓欄位選單標籤在切換語言（不重整）時即時重新翻譯
const pfPicker = computed(() => [
  { key: "name", label: t("common.name") }, { key: "api_url", label: "API URL" },
  { key: "verify_tls", label: "TLS" },
  { key: "last_sync_at", label: t("cols.last_sync") }, { key: "last_error", label: t("cols.last_error") },
  { key: "actions", label: t("common.actions") },
]);

const show = ref(false);
const editing = ref<PfSense | null>(null);
const form = ref({
  name: "", api_url: "", api_key: "", verify_tls: true, enabled: true,
  sync_interval_seconds: 300, sync_dhcp: false, sync_arp: true, sync_aliases: false, sync_rules: false, expose_dsv: false,
  scope_subnet_ids: [] as string[], description: "",
});

const subnetOptions = ref<{ label: string; value: string }[]>([]);
async function loadSubnetOptions() {
  try {
    const r = await listSubnets({ page: 1, pageSize: 500 });
    subnetOptions.value = r.items.map((s) => ({
      label: s.description ? `${s.cidr} — ${s.description}` : s.cidr, value: s.id }));
  } catch { /* silent */ }
}

function openCreate() {
  editing.value = null;
  form.value = {
    name: "", api_url: "", api_key: "", verify_tls: true, enabled: true,
    sync_interval_seconds: 300, sync_dhcp: false, sync_arp: true, sync_aliases: false, sync_rules: false, expose_dsv: false,
    scope_subnet_ids: [], description: "",
  };
  show.value = true;
}
function openEdit(r: PfSense) {
  editing.value = r;
  form.value = {
    name: r.name, api_url: r.api_url, api_key: "", verify_tls: r.verify_tls, enabled: r.enabled,
    sync_interval_seconds: r.sync_interval_seconds, sync_dhcp: r.sync_dhcp,
    sync_arp: r.sync_arp, sync_aliases: r.sync_aliases, sync_rules: r.sync_rules, expose_dsv: r.expose_dsv,
    scope_subnet_ids: r.scope_subnet_ids ?? [], description: r.description ?? "",
  };
  show.value = true;
}

async function refresh() {
  loading.value = true;
  try { rows.value = (await listPfSense(50, 0)).items; }
  catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}
async function submit() {
  if (!form.value.name.trim() || !form.value.api_url.trim()) {
    msg.warning(t("pfsense_admin.name_url_required")); return;
  }
  if (!editing.value && !form.value.api_key.trim()) {
    msg.warning(t("pfsense_admin.key_required")); return;
  }
  try {
    const base: any = {
      name: form.value.name.trim(), api_url: form.value.api_url.trim(),
      verify_tls: form.value.verify_tls, enabled: form.value.enabled,
      sync_interval_seconds: form.value.sync_interval_seconds,
      sync_dhcp: form.value.sync_dhcp, sync_arp: form.value.sync_arp,
      sync_aliases: form.value.sync_aliases, sync_rules: form.value.sync_rules, expose_dsv: form.value.expose_dsv,
      scope_subnet_ids: form.value.scope_subnet_ids,
      description: form.value.description.trim() || null,
    };
    if (form.value.api_key.trim()) base.api_key = form.value.api_key.trim();
    if (editing.value) await updatePfSense(editing.value.id, base);
    else await createPfSense(base);
    show.value = false;
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function test(r: PfSense) {
  try {
    const res = await testPfSense(r.id);
    const v = res.version || {};
    const ver = v.version || v.release || v.kernel || JSON.stringify(v).slice(0, 60);
    msg.success(`${t("common.ok")}${ver ? ` — ${ver}` : ""}`);
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function sync(r: PfSense) {
  try {
    const res = await syncPfSense(r.id);
    const c = res.counts || {};
    const parts = Object.entries(c).map(([k, n]) => `${k}: ${n}`);
    msg.success(parts.length ? parts.join("、") : t("common.ok"));
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function del(r: PfSense) {
  try { await deletePfSense(r.id); await refresh(); } catch { msg.error(t("errors.server")); }
}

function iconAction(icon: any, label: string, onClick: () => void, type?: any) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}

const allCols = computed<DataTableColumns<PfSense>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 150, ellipsis: { tooltip: true } },
  { title: "API URL", key: "api_url", minWidth: 190, ellipsis: { tooltip: true } },
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
    title: t("cols.last_sync"), key: "last_sync_at", width: 168,
    render: (r) => h("span", { style: "white-space:nowrap" }, fmtDateTime(r.last_sync_at)),
  },
  { title: t("cols.last_error"), key: "last_error", minWidth: 150, ellipsis: { tooltip: true }, render: (r) => r.last_error ?? "—" },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 168,
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openEdit(r)),
      iconAction(TestIcon, t("common.test"), () => test(r)),
      iconAction(SyncIcon, t("common.pull"), () => sync(r), "primary"),
      h(NPopconfirm, { onPositiveClick: () => del(r) },
        { trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
          default: () => t("common.confirm_delete") }),
    ]),
  },
]));
const cols = computed<DataTableColumns<PfSense>>(() =>
  allCols.value.filter((c: any) => pfPrefs.visibleKeys.value.includes(c.key)));

onMounted(() => { void refresh(); void loadSubnetOptions(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><FirewallIcon /></n-icon>
        <span>{{ t("pfsense_admin.title") }}</span>
      </n-space>
    </template>

    <n-alert v-if="insecureFws.length" type="warning" style="margin-bottom: 12px"
             :title="t('firewall_admin.tls_alert_title')">
      <template #icon><n-icon><WarnIcon /></n-icon></template>
      {{ t('firewall_admin.tls_alert_body', { n: insecureFws.length, names: insecureFws.map(f => f.name).join('、') }) }}
    </n-alert>

    <n-alert type="info" style="margin-bottom: 12px">
      {{ t("pfsense_admin.api_hint") }}
    </n-alert>

    <n-space style="margin-bottom: 12px">
      <n-button @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
      <n-button type="primary" @click="openCreate">
        <template #icon><n-icon><PlusIcon /></n-icon></template>
        {{ t("pfsense_admin.create") }}
      </n-button>
      <ColumnPicker :all="pfPicker" :visible="pfPrefs.visibleKeys.value"
                    @update:visible="pfPrefs.setVisible" @reset="pfPrefs.reset" />
      <ExportButton :columns="cols" :rows="rows" filename="pfsense" :title="t('pfsense_admin.title')" />
    </n-space>
    <n-data-table :columns="cols" :data="rows" :loading="loading" :bordered="false" :scroll-x="980" />

    <n-modal v-model:show="show" preset="card"
             :title="editing ? t('common.edit') : t('pfsense_admin.create')" style="width: 480px">
      <n-form>
        <n-form-item :label="t('common.name')"><n-input v-model:value="form.name" /></n-form-item>
        <n-form-item label="API URL">
          <n-input v-model:value="form.api_url" placeholder="https://192.0.2.1" />
        </n-form-item>
        <n-form-item :label="`API key (X-API-Key)${editing ? ' (' + t('users.password_blank_unchanged') + ')' : ''}`">
          <n-input v-model:value="form.api_key" type="password" show-password-on="click" />
        </n-form-item>
        <n-form-item label="Verify TLS">
          <n-switch v-model:value="form.verify_tls" />
          <template #feedback>
            <span v-if="!form.verify_tls" style="color: #d03050">
              {{ t('firewall_admin.tls_skip_warning') }}
            </span>
          </template>
        </n-form-item>
        <n-form-item :label="t('cols.enabled')"><n-switch v-model:value="form.enabled" /></n-form-item>
        <n-form-item :label="t('pfsense_admin.sync_interval')">
          <n-input-number v-model:value="form.sync_interval_seconds" :min="60" :step="60" style="width: 160px" />
        </n-form-item>
        <n-form-item :label="t('pfsense_admin.syncs')">
          <n-space :size="20" align="center">
            <span><n-switch v-model:value="form.sync_dhcp" size="small" /> DHCP</span>
            <span><n-switch v-model:value="form.sync_arp" size="small" /> ARP</span>
            <span><n-switch v-model:value="form.sync_aliases" size="small" /> {{ t("pfsense_admin.alias") }}</span>
            <span><n-switch v-model:value="form.sync_rules" size="small" /> {{ t("pfsense_admin.rules") }}</span>
          </n-space>
        </n-form-item>
        <n-form-item :label="t('pfsense_admin.expose_dsv')">
          <div style="width:100%">
            <n-switch v-model:value="form.expose_dsv" />
            <div style="font-size:11px;opacity:.65;margin-top:4px">{{ t("pfsense_admin.expose_dsv_hint") }}</div>
          </div>
        </n-form-item>
        <n-form-item :label="t('pfsense_admin.scope_subnets')">
          <div style="width: 100%">
            <n-select v-model:value="form.scope_subnet_ids" :options="subnetOptions"
                      multiple filterable clearable :placeholder="t('pfsense_admin.scope_all')" />
            <ScopeOverlapWarning :scope-empty="!form.scope_subnet_ids?.length" />
          </div>
        </n-form-item>
        <n-form-item :label="t('common.description')">
          <n-input v-model:value="form.description" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="show = false">{{ t("common.cancel") }}</n-button>
        <n-button type="primary" @click="submit">{{ t("common.save") }}</n-button>
      </n-space>
    </n-modal>
  </n-card>
</template>
