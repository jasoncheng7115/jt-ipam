<template>
  <n-card>
    <template #header>
      <CardTitle :icon="VirtualizationIcon" :text="t('hyperv.page_title')">
        <n-tag size="small" type="warning" :bordered="false" style="margin-left:8px">Beta</n-tag>
      </CardTitle>
    </template>

    <!-- 沒有實機驗證過就要說出來，而不是等使用者自己踩到欄位對不上 -->
    <n-alert type="warning" :bordered="false" :show-icon="true" style="margin-bottom:12px">
      {{ t("hyperv.beta_note") }}
    </n-alert>

    <n-space align="center" style="margin: 8px 0">
      <n-input v-model:value="query" clearable style="width:180px" :placeholder="t('common.filter')" />
      <n-button :loading="loading" @click="load">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}
      </n-button>
      <n-button type="primary" data-testid="hyperv-create" @click="openCreate">
        <template #icon><n-icon><PlusIcon /></n-icon></template>{{ t("hyperv.create_title") }}
      </n-button>
      <!-- 整合設定好之後，抓到的虛擬機在另一頁：直接帶過去（客戶 2026-10-21 設好 Hyper-V 找不到 VM 清單） -->
      <n-button type="info" ghost data-testid="hyperv-view-vms" @click="router.push({ name: 'virt_hyperv' })">
        <template #icon><n-icon><VirtualizationIcon /></n-icon></template>{{ t("virt.view_vms") }}
      </n-button>
      <ColumnPicker :all="pickerItems" :visible="visibleKeys"
                    @update:visible="setVisible" @reset="resetCols"
                    :order="colPrefs.order.value" @update:order="colPrefs.setOrder" />
      <ExportButton :columns="visibleCols" :rows="filtered" filename="hyperv" :title="t('hyperv.page_title')" />
    </n-space>

    <n-data-table :columns="visibleCols" :data="filtered" :loading="loading" size="small" :scroll-x="1000"
                  :bordered="false" :row-key="(r: HyperVInstance) => r.id" :pagination="pg" />

    <n-modal v-model:show="show" preset="card" :title="modalTitle" style="width:560px;max-width:94vw">
      <n-form label-placement="top">
        <n-form-item :label="t('common.name')">
          <n-input v-model:value="form.name" data-testid="hyperv-name" />
        </n-form-item>
        <n-form-item :label="t('hyperv.mode')">
          <n-space vertical :size="4" style="width:100%">
            <n-radio-group v-model:value="form.mode" data-testid="hyperv-mode">
              <n-radio value="hosts">{{ t("hyperv.mode_hosts") }}</n-radio>
              <n-radio value="scvmm">{{ t("hyperv.mode_scvmm") }}</n-radio>
            </n-radio-group>
            <span class="hint">{{ form.mode === "scvmm" ? t("hyperv.mode_scvmm_hint") : t("hyperv.mode_hosts_hint") }}</span>
          </n-space>
        </n-form-item>
        <n-form-item :label="form.mode === 'scvmm' ? t('hyperv.host_scvmm') : t('hyperv.host_hosts')">
          <n-input v-model:value="form.host" data-testid="hyperv-host"
                   :placeholder="form.mode === 'scvmm' ? 'vmm01.corp.example' : 'hv01.corp.example'" />
        </n-form-item>
        <n-form-item :label="form.mode === 'scvmm' ? t('hyperv.extra_scvmm') : t('hyperv.extra_hosts')">
          <n-input v-model:value="form.extra_hosts" type="textarea" :rows="2" data-testid="hyperv-extra"
                   :placeholder="t('hyperv.extra_ph')" />
        </n-form-item>
        <!-- 與 Windows DNS/DHCP 的 WinRM 設定同一套：HTTP 5985 預設在前、驗證憑證只在 HTTPS 時有意義 -->
        <n-form-item :label="t('windows_dhcp.winrm')">
          <n-space vertical :size="4" style="width: 100%">
            <n-space align="center" :wrap-item="false">
              <n-radio-group :value="form.use_ssl" data-testid="hyperv-winrm-transport" @update:value="setTransport">
                <n-radio :value="false">{{ t("dns_admin.winrm_http_label") }}</n-radio>
                <n-radio :value="true">HTTPS（5986）</n-radio>
              </n-radio-group>
              <n-input-number v-model:value="form.port" :min="1" :max="65535" style="width: 120px" />
            </n-space>
            <span v-if="form.use_ssl"><n-switch v-model:value="form.verify_tls" size="small" /> {{ t("firewall_admin.verify_tls") }}</span>
            <span class="hint">{{ form.auth_method === "kerberos" ? t("hyperv.winrm_hint_krb") : t("windows_dhcp.winrm_hint") }}</span>
          </n-space>
        </n-form-item>
        <!-- 驗證方式：客戶的網域只開 Kerberos（2026-10-20）。伺服器缺套件時反灰並附安裝方法 -->
        <n-form-item :label="t('hyperv.auth')">
          <n-space vertical :size="4" style="width:100%">
            <n-radio-group v-model:value="form.auth_method" data-testid="hyperv-auth">
              <n-radio value="ntlm">NTLM</n-radio>
              <n-radio value="kerberos" :disabled="!krbOk && form.auth_method !== 'kerberos'">Kerberos</n-radio>
            </n-radio-group>
            <n-alert v-if="!krbOk" type="warning" :show-icon="false" :bordered="false" data-testid="hyperv-krb-missing">
              {{ t("hyperv.krb_missing", { missing: krbMissing.join(", ") }) }}
              <code class="cmd">sudo bash /opt/jt-ipam/scripts/jt-ipam.sh upgrade</code>
            </n-alert>
            <span v-if="form.auth_method === 'kerberos'" class="hint">{{ t("hyperv.krb_hint") }}</span>
          </n-space>
        </n-form-item>
        <n-form-item :label="t('common.username')">
          <n-input v-model:value="form.username" data-testid="hyperv-user"
                   :placeholder="form.auth_method === 'kerberos' ? 'svc-ipam@corp.example' : 'CORP\\svc-ipam'" />
        </n-form-item>
        <template v-if="form.auth_method === 'kerberos'">
          <n-form-item :label="t('hyperv.krb_realm')">
            <n-input v-model:value="form.kerberos_realm" placeholder="CORP.EXAMPLE" data-testid="hyperv-krb-realm" />
          </n-form-item>
          <n-form-item :label="t('hyperv.krb_kdc')">
            <n-input v-model:value="form.kerberos_kdc" type="textarea" :rows="2" data-testid="hyperv-krb-kdc"
                     :placeholder="t('hyperv.krb_kdc_ph')" />
          </n-form-item>
        </template>
        <n-form-item :label="t('common.password')">
          <n-input v-model:value="form.password" type="password" show-password-on="click" data-testid="hyperv-password"
                   :placeholder="editing ? t('virt.secret_keep') : ''" />
        </n-form-item>
        <n-space align="center" :size="24">
          <n-form-item :label="t('common.enabled')"><n-switch v-model:value="form.enabled" /></n-form-item>
          <n-form-item :label="t('virt.interval')">
            <n-input-number v-model:value="form.sync_interval_seconds" :min="60" :max="86400" />
          </n-form-item>
        </n-space>
        <n-form-item :label="t('virt.scope_subnets')">
          <div style="width: 100%">
            <n-select v-model:value="form.scope_subnet_ids" :options="subnetOptions"
                      multiple filterable clearable :placeholder="t('virt.scope_all')" />
            <ScopeOverlapWarning :scope-empty="!form.scope_subnet_ids?.length" />
          </div>
        </n-form-item>
        <n-form-item :label="t('virt_autocreate.label')">
          <div style="width:100%">
            <n-switch v-model:value="form.auto_create_ips" />
            <div style="font-size:11px;opacity:.65;margin-top:4px">{{ t("virt_autocreate.hint") }}</div>
            <n-alert v-if="form.auto_create_ips" type="warning" :show-icon="false" :bordered="false"
                     style="margin-top:6px">
              {{ t("virt_autocreate.risk") }}
            </n-alert>
          </div>
        </n-form-item>
        <n-form-item :label="t('common.description')">
          <n-input v-model:value="form.description" type="textarea" :rows="2" />
        </n-form-item>
        <n-alert type="info" :title="t('hyperv.help_title')" :bordered="false" style="margin-top:4px">
          <ol class="px-help">
            <li>{{ form.mode === "scvmm" ? t("hyperv.help_scvmm_1") : t("hyperv.help_hosts_1") }}</li>
            <li>{{ t("hyperv.help_2") }} <code>Enable-PSRemoting -Force</code></li>
            <li>{{ form.mode === "scvmm" ? t("hyperv.help_scvmm_3") : t("hyperv.help_hosts_3") }}</li>
          </ol>
        </n-alert>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="show = false">
            <template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}
          </n-button>
          <n-button type="primary" :loading="saving" data-testid="hyperv-save" @click="save">
            <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
          </n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 逐台、逐步診斷：沒有實機時這是唯一能看出卡在哪一步的方式 -->
    <n-modal v-model:show="diagShow" preset="card" style="width:680px;max-width:94vw" :title="t('hyperv.diag')">
      <n-spin :show="diagLoading">
        <div v-for="s in diagSteps" :key="s.step" class="diag-row" data-testid="hyperv-diag-step">
          <n-tag :type="s.ok ? 'success' : 'error'" size="small" :bordered="false">{{ s.ok ? "OK" : "FAIL" }}</n-tag>
          <b style="margin:0 8px">{{ s.step }}</b>
          <span style="opacity:.75">{{ stepText(s) }}</span>
        </div>
        <div v-if="!diagLoading && !diagSteps.length" style="opacity:.6">—</div>
      </n-spin>
    </n-modal>
  </n-card>
</template>

<script setup lang="ts">
/**
 * Hyper-V 整合設定（Beta）：直連 Hyper-V 主機（單機或叢集的每個節點），或經 System Center VMM。
 *
 * 與 Proxmox、VMware 各自獨立設定、獨立同步，但寫進同一組虛擬化資料表 ——
 * 拓樸、AI 對話、MCP 的 list_vms 不必為了新平台改。
 */
import { computed, h, onMounted, ref } from "vue";
import {
  NAlert, NButton, NCard, NDataTable, NForm, NFormItem, NIcon, NInput, NInputNumber,
  NModal, NPopconfirm, NRadio, NRadioGroup, NSelect, NSpace, NSpin, NSwitch, NTag, NTooltip, useMessage,
  type DataTableColumns,
} from "naive-ui";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import CardTitle from "@/components/CardTitle.vue";
import ScopeOverlapWarning from "@/components/ScopeOverlapWarning.vue";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { useTablePagination } from "@/composables/useTablePagination";
import { trackTask } from "@/composables/useTaskTracker";
import { listSubnets } from "@/api/subnets";
import { HyperV, type HyperVAuth, type HyperVDiagStep, type HyperVInstance, type HyperVPayload } from "@/api/hyperv";
import { apiErrMsg } from "@/api/client";
import {
  CancelIcon, DeleteIcon, EditIcon, PlusIcon, RefreshIcon, SaveIcon, SyncIcon, TestIcon, VirtualizationIcon,
} from "@/icons";
import { fmtDateTime } from "@/utils/datetime";
import { autoSort } from "@/composables/useTableSort";
import { cmpNatural } from "@/utils/sort";

const { t, te } = useI18n();
const router = useRouter();
const msg = useMessage();
const pg = useTablePagination();

const rows = ref<HyperVInstance[]>([]);
const loading = ref(false);
const show = ref(false);
const saving = ref(false);
const editing = ref<HyperVInstance | null>(null);
const diagShow = ref(false);
const diagLoading = ref(false);
const diagSteps = ref<HyperVDiagStep[]>([]);

function blank() {
  return {
    name: "", mode: "hosts" as "hosts" | "scvmm", host: "", extra_hosts: "", port: 5985, use_ssl: false,
    verify_tls: true, username: "", password: "", auth_method: "ntlm" as HyperVAuth, kerberos_realm: "",
    kerberos_kdc: "", enabled: true, sync_interval_seconds: 600,
    scope_subnet_ids: [] as string[], auto_create_ips: false, description: "",
  };
}
const form = ref(blank());
const modalTitle = computed(() => (editing.value ? t("hyperv.edit_title") : t("hyperv.create_title")));

/** WinRM 傳輸切換時連埠號一起換（同 Windows DHCP） */
function setTransport(ssl: boolean) {
  form.value.use_ssl = ssl;
  if (ssl && form.value.port === 5985) form.value.port = 5986;
  if (!ssl && form.value.port === 5986) form.value.port = 5985;
}

const subnetOptions = ref<{ label: string; value: string }[]>([]);
async function loadSubnetOptions() {
  try {
    const r = await listSubnets({ page: 1, pageSize: 500 });
    subnetOptions.value = r.items.map((sn: { id: string; cidr: string; description?: string | null }) => ({
      label: sn.description ? `${sn.cidr} — ${sn.description}` : sn.cidr, value: sn.id }));
  } catch { /* 讀不到就讓下拉是空的，不擋新增 */ }
}

async function load() {
  loading.value = true;
  try { rows.value = await HyperV.list(); }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}

function openCreate() {
  editing.value = null;
  form.value = blank();
  show.value = true;
}

function openEdit(r: HyperVInstance) {
  editing.value = r;
  form.value = {
    name: r.name, mode: r.mode, host: r.host, extra_hosts: r.extra_hosts ?? "", port: r.port,
    use_ssl: r.use_ssl, verify_tls: r.verify_tls, username: r.username, password: "",
    auth_method: r.auth_method ?? "ntlm", kerberos_realm: r.kerberos_realm ?? "", kerberos_kdc: r.kerberos_kdc ?? "",
    enabled: r.enabled,
    sync_interval_seconds: r.sync_interval_seconds, scope_subnet_ids: r.scope_subnet_ids ?? [],
    auto_create_ips: !!r.auto_create_ips, description: r.description ?? "",
  };
  show.value = true;
}

async function save() {
  saving.value = true;
  try {
    const f = form.value;
    const payload: HyperVPayload = {
      name: f.name, mode: f.mode, host: f.host.trim(), extra_hosts: f.extra_hosts.trim() || null,
      port: f.port, use_ssl: f.use_ssl, verify_tls: f.verify_tls, username: f.username,
      auth_method: f.auth_method, kerberos_realm: f.kerberos_realm.trim() || null,
      kerberos_kdc: f.kerberos_kdc.trim() || null,
      enabled: f.enabled, sync_interval_seconds: f.sync_interval_seconds,
      scope_subnet_ids: f.scope_subnet_ids?.length ? f.scope_subnet_ids : null,
      auto_create_ips: f.auto_create_ips, description: f.description || null,
    };
    // 編輯時密碼留空＝不變更（不要把既有密碼洗成空字串）
    if (f.password) payload.password = f.password;
    if (editing.value) await HyperV.update(editing.value.id, payload);
    else await HyperV.create(payload);
    show.value = false;
    await load();
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { saving.value = false; }
}

async function runTest(r: HyperVInstance) {
  diagShow.value = true;
  diagLoading.value = true;
  diagSteps.value = [];
  try { diagSteps.value = (await HyperV.test(r.id)).steps; }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { diagLoading.value = false; }
}

/** 失敗的步驟有代碼就翻成當前語言（附原文），沒有就顯示原文 */
function stepText(s: HyperVDiagStep): string {
  if (!s.ok && s.code && te(`errors.${s.code}`)) {
    return t(`errors.${s.code}`, { ...(s.params ?? {}), reason: s.detail });
  }
  return s.detail;
}

async function runSync(r: HyperVInstance) {
  try {
    const out = await HyperV.sync(r.id);
    trackTask(out.task_id, { onDone: () => void load() });
  } catch (e) { msg.error(apiErrMsg(e)); }
}

async function remove(r: HyperVInstance) {
  try { await HyperV.remove(r.id); await load(); }
  catch (e) { msg.error(apiErrMsg(e)); }
}

const query = ref("");
const filtered = computed(() => {
  const q = query.value.trim().toLowerCase();
  if (!q) return rows.value;
  return rows.value.filter((r) => [r.name, r.host, r.extra_hosts, r.username, r.description]
    .some((v) => String(v ?? "").toLowerCase().includes(q)));
});

function iconAction(icon: any, label: string, onClick: () => void, type?: any, testid?: string) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type, "data-testid": testid,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}

function hostCount(r: HyperVInstance): number {
  return (r.extra_hosts || "").split(/[\s,]+/).filter(Boolean).length;
}

const modeText = (r: HyperVInstance) => (r.mode === "scvmm" ? "SCVMM" : t("hyperv.mode_hosts_short"));
const winrmText = (r: HyperVInstance) =>
  `${r.use_ssl ? "HTTPS" : "HTTP"} ${r.port} · ${r.auth_method === "kerberos" ? "Kerberos" : "NTLM"}`;
const cols = computed<DataTableColumns<HyperVInstance>>(() => autoSort<HyperVInstance>([
  { title: t("common.name"), key: "name", minWidth: 150, ellipsis: { tooltip: true } },
  {
    title: t("hyperv.mode"), key: "mode", width: 120,
    sorter: (a: HyperVInstance, b: HyperVInstance) => cmpNatural(modeText(a), modeText(b)),
    render: (r: HyperVInstance) => h(NTag, { size: "small", bordered: false, type: r.mode === "scvmm" ? "info" : "default" },
      { default: () => modeText(r) }),
  },
  {
    title: t("hyperv.col_hosts"), key: "host", minWidth: 200,
    render: (r: HyperVInstance) => h("span", null, [
      r.host,
      hostCount(r)
        ? h(NTag, { size: "tiny", type: "info", bordered: false, style: "margin-left:6px",
                    title: r.extra_hosts ?? "" }, { default: () => `+${hostCount(r)}` })
        : null,
    ]),
  },
  {
    title: "WinRM", key: "port", width: 170,
    render: winrmText,
    sorter: (a: HyperVInstance, b: HyperVInstance) => cmpNatural(winrmText(a), winrmText(b)),
  },
  { title: t("common.username"), key: "username", width: 160, ellipsis: { tooltip: true } },
  {
    title: t("common.enabled"), key: "enabled", width: 80,
    sorter: (a: HyperVInstance, b: HyperVInstance) => Number(a.enabled) - Number(b.enabled),
    render: (r: HyperVInstance) => h(NTag, { size: "small", bordered: false, type: r.enabled ? "success" : "default" },
      { default: () => (r.enabled ? t("common.yes") : t("common.no")) }),
  },
  {
    title: t("cols.last_sync"), key: "last_sync_at", width: 170,
    render: (r: HyperVInstance) => (r.last_sync_at ? fmtDateTime(r.last_sync_at) : "—"),
  },
  {
    title: t("cols.last_error"), key: "last_error", minWidth: 160, ellipsis: { tooltip: true },
    render: (r: HyperVInstance) => (r.last_error ? h("span", { style: "color:#d03050" }, r.last_error) : "—"),
  },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 176,
    render: (r: HyperVInstance) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openEdit(r), "success", "hyperv-edit"),
      iconAction(TestIcon, t("common.test"), () => runTest(r), undefined, "hyperv-test"),
      iconAction(SyncIcon, t("common.pull"), () => runSync(r), "primary", "hyperv-sync"),
      h(NPopconfirm, { onPositiveClick: () => remove(r) },
        { trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error", "hyperv-delete"),
          default: () => t("common.confirm_delete") }),
    ]),
  },
]));

const ALL_KEYS = ["name", "mode", "host", "port", "username", "enabled", "last_sync_at", "last_error"];
const colPrefs = useColumnPrefs("hyperv_admin", ALL_KEYS, ALL_KEYS);
const visibleKeys = computed(() => colPrefs.visibleKeys.value);
function setVisible(keys: string[]) { colPrefs.setVisible(keys); }
function resetCols() { colPrefs.reset(); }
const pickerItems = computed(() => cols.value
  .filter((c: any) => c.key !== "actions")
  .map((c: any) => ({ key: String(c.key), label: String(c.title) })));
// 操作欄永遠留著；其餘依偏好顯示
const visibleCols = computed(() => colPrefs.orderColumns(cols.value.filter(
  (c: any) => c.key === "actions" || visibleKeys.value.includes(String(c.key)))));

// 這台伺服器能不能用 Kerberos（讀不到就當可以：交給後端在連線時講清楚）
const krbOk = ref(true);
const krbMissing = ref<string[]>([]);
async function loadCapabilities() {
  try {
    const c = await HyperV.capabilities();
    krbOk.value = c.kerberos;
    krbMissing.value = c.missing;
  } catch { /* 保持預設 */ }
}

onMounted(() => { void load(); void loadSubnetOptions(); void loadCapabilities(); });
</script>

<style scoped>
.hint { font-size: 11px; opacity: .7; }
.px-help { margin: 0; padding-left: 18px; line-height: 1.9; }
.cmd { display: block; margin-top: 4px; font-size: 12px; user-select: all; }
.diag-row { padding: 6px 0; font-size: 13px; border-bottom: 1px solid var(--n-border-color, #eee); }
</style>
