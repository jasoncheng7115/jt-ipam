<template>
  <n-card>
    <template #header>
      <CardTitle :icon="VirtualizationIcon" :text="t('openstack.page_title')">
      </CardTitle>
    </template>

    <!-- 驗證到什麼程度要說清楚（2026-11-10 CubeCOS 開發團隊已實際搭配 jt-ipam 測試，拿掉 Beta） -->
    <n-alert type="info" :bordered="false" :show-icon="true" style="margin-bottom:12px">
      {{ t("openstack.beta_note") }}
    </n-alert>

    <!-- 照 Wazuh 整合頁：第一個頁籤是連線清單，之後每個連線一個頁籤（子網路對應、CubeCOS 節點） -->
    <n-tabs v-model:value="tab" type="line" data-testid="os-tabs">
      <n-tab-pane name="instances">
        <template #tab>
          <span class="tab-l"><n-icon :size="16"><VirtualizationIcon /></n-icon>{{ `${t("openstack.instances")} (${rows.length})` }}</span>
        </template>
        <n-space align="center" style="margin: 0 0 8px">
          <n-input v-model:value="query" clearable style="width:180px" :placeholder="t('common.filter')" />
          <n-button :loading="loading" @click="load">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}
          </n-button>
          <n-button type="primary" data-testid="os-create" @click="openCreate">
            <template #icon><n-icon><PlusIcon /></n-icon></template>{{ t("openstack.create_title") }}
          </n-button>
          <n-button type="info" ghost data-testid="os-view-vms" @click="router.push({ name: 'virt_openstack' })">
            <template #icon><n-icon><VirtualizationIcon /></n-icon></template>{{ t("virt.view_vms") }}
          </n-button>
          <ColumnPicker :all="pickerItems" :visible="visibleKeys"
                        @update:visible="setVisible" @reset="resetCols"
                        :order="colPrefs.order.value" @update:order="colPrefs.setOrder" />
          <ExportButton :columns="visibleCols" :rows="filtered" filename="openstack" :title="t('openstack.page_title')" />
        </n-space>
        <n-data-table :columns="visibleCols" :data="filtered" :loading="loading" size="small" :scroll-x="1500"
                      :bordered="false" :row-key="(r: OpenStackInstance) => r.id" :pagination="pg"
                      data-testid="os-table" />
      </n-tab-pane>

      <n-tab-pane v-for="r in rows" :key="r.id" :name="`inst:${r.id}`" display-directive="if">
        <template #tab>
          <span class="tab-l" :data-testid="`os-tab-${r.name}`">
            <n-icon :size="16"><ServiceIcon /></n-icon>{{ r.name }}
            <n-tag v-if="r.product === 'cubecos'" size="tiny" type="info" :bordered="false">CubeCOS</n-tag>
          </span>
        </template>
        <OpenStackInstancePanel :instance="r" :subnet-options="subnetOptions"
                                :testing="testingId === r.id" :syncing="isSyncing(r.id)"
                                @test="runTest(r)" @sync="runSync(r)" />
      </n-tab-pane>
    </n-tabs>

    <!-- 新增／編輯 -->
    <n-modal v-model:show="show" preset="card" :title="modalTitle" style="width:620px;max-width:94vw">
      <n-form label-placement="top">
        <n-form-item :label="t('common.name')">
          <n-input v-model:value="form.name" data-testid="os-name" />
        </n-form-item>
        <n-form-item :label="t('openstack.product')">
          <n-space vertical :size="4" style="width:100%">
            <n-radio-group v-model:value="form.product" data-testid="os-product">
              <n-radio value="cubecos">Bigstack CubeCOS</n-radio>
              <n-radio value="openstack">OpenStack</n-radio>
            </n-radio-group>
            <span class="hint">{{ t("openstack.product_hint") }}</span>
          </n-space>
        </n-form-item>
        <n-form-item :label="t('openstack.auth_url')">
          <n-space vertical :size="4" style="width:100%">
            <n-input v-model:value="form.auth_url" data-testid="os-auth-url" placeholder="https://keystone.example.net:5000/v3" />
            <span class="hint">{{ t("openstack.auth_url_hint") }}</span>
            <!-- http 會把帳密與權杖明文送出（OWASP A04）：允許（DevStack 預設就是 http），但要講清楚 -->
            <n-alert v-if="/^http:\/\//i.test(form.auth_url.trim())" type="warning" :show-icon="true" :bordered="false"
                     data-testid="os-http-warning">{{ t("openstack.http_warning") }}</n-alert>
          </n-space>
        </n-form-item>
        <n-form-item :label="t('openstack.auth_method')">
          <n-space vertical :size="4" style="width:100%">
            <n-radio-group v-model:value="form.auth_method" data-testid="os-auth-method">
              <n-radio value="password">{{ t("openstack.auth_password") }}</n-radio>
              <n-radio value="app_credential">{{ t("openstack.auth_app_credential") }}</n-radio>
            </n-radio-group>
            <span class="hint">{{ t("openstack.auth_app_hint") }}</span>
          </n-space>
        </n-form-item>

        <template v-if="form.auth_method === 'password'">
          <div class="grid2">
            <n-form-item :label="t('common.username')">
              <n-input v-model:value="form.username" data-testid="os-username" placeholder="jt-ipam" />
            </n-form-item>
            <n-form-item :label="t('openstack.user_domain')">
              <n-input v-model:value="form.user_domain" data-testid="os-user-domain" placeholder="Default" />
            </n-form-item>
            <n-form-item :label="t('openstack.project')">
              <n-input v-model:value="form.project_name" data-testid="os-project" placeholder="admin" />
            </n-form-item>
            <n-form-item :label="t('openstack.project_domain')">
              <n-input v-model:value="form.project_domain" data-testid="os-project-domain" placeholder="Default" />
            </n-form-item>
          </div>
          <n-form-item :label="t('common.password')">
            <n-input v-model:value="form.secret" type="password" show-password-on="click" data-testid="os-secret"
                     :input-props="{ autocomplete: 'new-password' }"
                     :placeholder="editing ? t('openstack.secret_keep') : ''" />
          </n-form-item>
        </template>
        <template v-else>
          <n-form-item :label="t('openstack.app_credential_id')">
            <n-input v-model:value="form.app_credential_id" data-testid="os-app-cred-id" />
          </n-form-item>
          <n-form-item :label="t('openstack.app_credential_secret')">
            <n-input v-model:value="form.secret" type="password" show-password-on="click" data-testid="os-secret"
                     :input-props="{ autocomplete: 'new-password' }"
                     :placeholder="editing ? t('openstack.secret_keep') : ''" />
          </n-form-item>
        </template>

        <div class="grid2">
          <n-form-item :label="t('openstack.region')">
            <n-input v-model:value="form.region" data-testid="os-region" :placeholder="t('openstack.region_ph')" />
          </n-form-item>
          <n-form-item :label="t('openstack.interface')">
            <n-select v-model:value="form.interface" :options="interfaceOptions" data-testid="os-interface" />
          </n-form-item>
        </div>
        <div class="hint" style="margin: -16px 0 12px">{{ t("openstack.interface_hint") }}</div>

        <n-form-item :label="t('firewall_admin.verify_tls')">
          <n-switch v-model:value="form.verify_tls" data-testid="os-verify-tls" />
        </n-form-item>
        <n-form-item v-if="form.verify_tls">
          <template #label>
            <span class="lbl-row">
              {{ t("openstack.ca_cert") }}
              <n-tag v-if="editing?.has_ca_cert && !form.ca_cert_clear" size="small" type="success" :bordered="false"
                     data-testid="os-ca-set">{{ t("openstack.ca_cert_set") }}</n-tag>
              <n-button v-if="editing?.has_ca_cert && !form.ca_cert_clear" size="tiny" type="error" ghost
                        data-testid="os-ca-clear" @click="form.ca_cert_clear = true; form.ca_cert = ''">
                <template #icon><n-icon><DeleteIcon /></n-icon></template>{{ t("openstack.ca_cert_clear") }}
              </n-button>
              <n-button v-if="form.ca_cert_clear" size="tiny" data-testid="os-ca-undo" @click="form.ca_cert_clear = false">
                <template #icon><n-icon><RestoreIcon /></n-icon></template>{{ t("common.cancel") }}
              </n-button>
            </span>
          </template>
          <n-space vertical :size="4" style="width:100%">
            <n-input v-model:value="form.ca_cert" type="textarea" :rows="3" data-testid="os-ca-cert" class="mono"
                     :placeholder="editing?.has_ca_cert && !form.ca_cert_clear ? t('openstack.ca_cert_keep') : t('openstack.ca_cert_ph')"
                     @update:value="(v: string) => { if (v) form.ca_cert_clear = false; }" />
            <span v-if="form.ca_cert_clear" class="hint warn" data-testid="os-ca-will-clear">{{ t("openstack.ca_cert_will_clear") }}</span>
          </n-space>
        </n-form-item>

        <n-form-item :label="t('openstack.all_projects')">
          <n-space vertical :size="4" style="width:100%">
            <n-switch v-model:value="form.all_projects" data-testid="os-all-projects" />
            <span class="hint">{{ t("openstack.all_projects_hint") }}</span>
          </n-space>
        </n-form-item>

        <n-form-item v-if="form.product === 'cubecos'" :label="t('openstack.cubecos_url')">
          <n-space vertical :size="4" style="width:100%">
            <n-input v-model:value="form.cubecos_url" data-testid="os-cubecos-url"
                     :placeholder="defaultCubecosUrl(form.auth_url)" />
            <span class="hint">{{ t("openstack.cubecos_url_hint") }}</span>
          </n-space>
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
          <n-input v-model:value="form.description" type="textarea" :rows="2" data-testid="os-description" />
        </n-form-item>
        <n-alert type="info" :title="t('openstack.help_title')" :bordered="false" style="margin-top:4px">
          <ol class="px-help">
            <li>{{ t("openstack.help_1") }}</li>
            <li>{{ t("openstack.help_2") }}</li>
            <li>{{ t("openstack.help_3") }}</li>
          </ol>
        </n-alert>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="show = false">
            <template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}
          </n-button>
          <n-button type="primary" :loading="saving" :disabled="saving" data-testid="os-save" @click="save">
            <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
          </n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 測試結果：每個服務連不連得到、看得到哪些欄位 -->
    <n-modal v-model:show="testShow" preset="card" style="width:640px;max-width:94vw"
             :title="testFor ? `${t('openstack.test_title')}：${testFor.name}` : t('openstack.test_title')">
      <n-spin :show="!!testingId">
        <template v-if="testResult">
          <n-alert :type="testResult.ok ? 'success' : 'error'" :bordered="false" style="margin-bottom:12px"
                   data-testid="os-test-summary">
            <div style="font-weight:600">{{ testResult.ok ? t("openstack.test_ok") : t("openstack.test_failed") }}</div>
            <div v-if="testResult.error" data-testid="os-test-error">{{ errorText(testResult.error) }}</div>
          </n-alert>
          <OpenStackCapabilityList :capabilities="testResult.capabilities" :product="testFor?.product ?? 'openstack'" />
        </template>
        <div v-else style="min-height:80px" />
      </n-spin>
      <template #footer>
        <n-space justify="end">
          <n-button @click="testShow = false">
            <template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.close") }}
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </n-card>
</template>

<script setup lang="ts">
/**
 * Bigstack CubeCOS／OpenStack 整合設定（唯讀）。
 *
 * VM 與網卡寫進共用的虛擬化資料表（和 Proxmox、VMware、Hyper-V 同一套），所以拓樸、AI 對話、
 * MCP 的 list_vms 不必為了新平台改。這一頁只管連線設定、測試、同步，以及 Neutron 子網路對應。
 */
import { computed, h, onMounted, ref } from "vue";
import {
  NAlert, NButton, NCard, NDataTable, NForm, NFormItem, NIcon, NInput, NInputNumber, NModal, NPopconfirm,
  NRadio, NRadioGroup, NSelect, NSpace, NSpin, NSwitch, NTabPane, NTabs, NTag, NTooltip, useMessage,
  type DataTableColumns,
} from "naive-ui";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import CardTitle from "@/components/CardTitle.vue";
import ScopeOverlapWarning from "@/components/ScopeOverlapWarning.vue";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import OpenStackCapabilityList from "@/components/OpenStackCapabilityList.vue";
import OpenStackInstancePanel, { type SubnetChoice } from "@/components/OpenStackInstancePanel.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { useTablePagination } from "@/composables/useTablePagination";
import { trackTask, useTaskTracker } from "@/composables/useTaskTracker";
import { listSubnets } from "@/api/subnets";
import {
  OpenStack, blankOpenStackForm, buildOpenStackPayload, defaultCubecosUrl, formFromInstance,
  type OpenStackInstance, type OpenStackTestResult,
} from "@/api/openstack";
import { apiErrMsg } from "@/api/client";
import {
  CancelIcon, DeleteIcon, EditIcon, PlusIcon, RefreshIcon, RestoreIcon, SaveIcon, ServiceIcon, SyncIcon, TestIcon,
  VirtualizationIcon,
} from "@/icons";
import { fmtDateTime } from "@/utils/datetime";
import { withExportValue } from "@/utils/tableExport";

const { t, te } = useI18n();
const router = useRouter();
const msg = useMessage();
const pg = useTablePagination();

const tab = ref<string>("instances");
const rows = ref<OpenStackInstance[]>([]);
const loading = ref(false);
const show = ref(false);
const saving = ref(false);
const editing = ref<OpenStackInstance | null>(null);
const form = ref(blankOpenStackForm());
const modalTitle = computed(() => (editing.value ? t("openstack.edit_title") : t("openstack.create_title")));

const interfaceOptions = [
  { label: "public", value: "public" }, { label: "internal", value: "internal" }, { label: "admin", value: "admin" },
];

// 子網路下拉：範圍選擇與子網路對應共用。這一頁只有管理員進得來，列出的子網路都可以修改。
// 逐頁抓完（每頁 500）：只抓第一頁的話，大站台的子網路會「選不到」（清單只載第一頁的老問題）
const subnetOptions = ref<SubnetChoice[]>([]);
async function loadSubnetOptions() {
  try {
    const out: SubnetChoice[] = [];
    for (let page = 1; page <= 200; page++) {
      const r = await listSubnets({ page, pageSize: 500 });
      for (const sn of r.items as { id: string; cidr: string; description?: string | null }[]) {
        out.push({ label: sn.description ? `${sn.cidr}（${sn.description}）` : sn.cidr, value: sn.id, cidr: sn.cidr });
      }
      if (r.items.length < 500 || out.length >= (r.total ?? 0)) break;
    }
    subnetOptions.value = out;
  } catch { /* 讀不到就讓下拉是空的，不擋設定 */ }
}

async function load() {
  loading.value = true;
  try {
    rows.value = await OpenStack.list();
    // 被刪掉的連線：頁籤回到清單
    if (tab.value.startsWith("inst:") && !rows.value.some((r) => `inst:${r.id}` === tab.value)) tab.value = "instances";
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}

function openCreate() {
  editing.value = null;
  form.value = blankOpenStackForm();
  show.value = true;
}

function openEdit(r: OpenStackInstance) {
  editing.value = r;
  form.value = formFromInstance(r);
  show.value = true;
}

async function save() {
  saving.value = true;
  try {
    const payload = buildOpenStackPayload(form.value, !!editing.value);
    if (editing.value) await OpenStack.update(editing.value.id, payload);
    else await OpenStack.create(payload);
    show.value = false;
    await load();
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { saving.value = false; }
}

// ── 測試 ──
const testShow = ref(false);
const testingId = ref<string | null>(null);
const testFor = ref<OpenStackInstance | null>(null);
const testResult = ref<OpenStackTestResult | null>(null);

async function runTest(r: OpenStackInstance) {
  testFor.value = r;
  testResult.value = null;
  testShow.value = true;
  testingId.value = r.id;
  try {
    testResult.value = await OpenStack.test(r.id);
    // 測試會更新 capabilities：重新讀清單，頁籤裡的能力清單才是新的
    void load();
  } catch (e) {
    testShow.value = false;
    msg.error(apiErrMsg(e));
  } finally { testingId.value = null; }
}

function errorText(e: NonNullable<OpenStackTestResult["error"]>): string {
  if (te(`errors.${e.code}`)) return t(`errors.${e.code}`, { reason: e.message ?? "", ...(e.params ?? {}) });
  return e.message || e.code;
}

// ── 立即同步：背景作業。按鈕在作業跑完前反灰＋轉圈（看共用的作業面板狀態，作業被關掉或遺失也會恢復） ──
const { tracked, isActive } = useTaskTracker();
const syncTask = ref<Record<string, string>>({});
const posting = ref<Set<string>>(new Set());
function isSyncing(id: string): boolean {
  if (posting.value.has(id)) return true;
  const tid = syncTask.value[id];
  const x = tid ? tracked.value.find((y) => y.id === tid) : undefined;
  return !!x && isActive(x);
}
async function runSync(r: OpenStackInstance) {
  if (isSyncing(r.id)) return;
  posting.value = new Set([...posting.value, r.id]);
  try {
    const out = await OpenStack.sync(r.id);
    syncTask.value = { ...syncTask.value, [r.id]: out.task_id };
    trackTask(out.task_id, { onDone: () => void load() });
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally {
    const next = new Set(posting.value);
    next.delete(r.id);
    posting.value = next;
  }
}

async function remove(r: OpenStackInstance) {
  try { await OpenStack.remove(r.id); await load(); }
  catch (e) { msg.error(apiErrMsg(e)); }
}

const query = ref("");
const filtered = computed(() => {
  const q = query.value.trim().toLowerCase();
  if (!q) return rows.value;
  return rows.value.filter((r) => [r.name, r.auth_url, r.username, r.project_name, r.app_credential_id, r.region,
    r.description].some((v) => String(v ?? "").toLowerCase().includes(q)));
});

function iconAction(icon: any, label: string, onClick: () => void,
                    opts: { type?: any; testid?: string; loading?: boolean } = {}) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type: opts.type, "data-testid": opts.testid,
      loading: opts.loading, disabled: opts.loading,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}

function authText(r: OpenStackInstance): string {
  if (r.auth_method === "app_credential") return `${t("openstack.auth_app_credential")}${r.app_credential_id ? ` · ${r.app_credential_id}` : ""}`;
  const who = [r.username, r.project_name].filter(Boolean).join(" / ");
  return who ? `${t("openstack.auth_password")} · ${who}` : t("openstack.auth_password");
}

const cols = computed<DataTableColumns<OpenStackInstance>>(() => [
  {
    title: t("common.name"), key: "name", minWidth: 150, ellipsis: { tooltip: true },
    render: (r) => h(NButton, { text: true, type: "primary", onClick: () => { tab.value = `inst:${r.id}`; } },
      { default: () => r.name }),
  },
  withExportValue({
    title: t("openstack.product"), key: "product", width: 150,
    render: (r) => h(NTag, { size: "small", bordered: false, type: r.product === "cubecos" ? "info" : "default" },
      { default: () => (r.product === "cubecos" ? "Bigstack CubeCOS" : "OpenStack") }),
  }, (r) => (r.product === "cubecos" ? "Bigstack CubeCOS" : "OpenStack")),
  { title: t("openstack.auth_url"), key: "auth_url", minWidth: 220, ellipsis: { tooltip: true } },
  withExportValue({ title: t("openstack.col_auth"), key: "auth", minWidth: 200, ellipsis: { tooltip: true },
    render: (r) => authText(r) }, (r) => authText(r)),
  withExportValue({ title: t("openstack.col_endpoint"), key: "interface", width: 150,
    render: (r) => `${r.region || "—"} · ${r.interface}` }, (r) => `${r.region || ""} ${r.interface}`),
  withExportValue({
    title: t("openstack.col_counts"), key: "counts", width: 140,
    render: (r) => h(NTooltip, null, {
      trigger: () => h("span", { "data-testid": "os-counts" },
        `${r.vm_count ?? 0} / ${r.port_count ?? 0} / ${r.product === "cubecos" ? (r.node_count ?? 0) : "—"}`),
      default: () => t("openstack.counts_tip", { vms: r.vm_count ?? 0, ports: r.port_count ?? 0, nodes: r.node_count ?? 0 }),
    }),
  }, (r) => `${r.vm_count ?? 0} / ${r.port_count ?? 0} / ${r.node_count ?? 0}`),
  withExportValue({
    title: t("common.enabled"), key: "enabled", width: 80,
    render: (r) => h(NTag, { size: "small", bordered: false, type: r.enabled ? "success" : "default" },
      { default: () => (r.enabled ? t("common.yes") : t("common.no")) }),
  }, (r) => (r.enabled ? t("common.yes") : t("common.no"))),
  {
    title: t("cols.last_sync"), key: "last_sync_at", width: 170,
    render: (r) => (r.last_sync_at ? fmtDateTime(r.last_sync_at) : "—"),
  },
  {
    title: t("cols.last_error"), key: "last_error", minWidth: 180, ellipsis: { tooltip: true },
    render: (r) => (r.last_error ? h("span", { style: "color:#d03050" }, r.last_error) : "—"),
  },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 176, fixed: "right",
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openEdit(r), { type: "success", testid: "os-edit" }),
      iconAction(TestIcon, t("common.test"), () => runTest(r), { testid: "os-test", loading: testingId.value === r.id }),
      iconAction(SyncIcon, t("common.pull"), () => runSync(r), { type: "primary", testid: "os-sync", loading: isSyncing(r.id) }),
      h(NPopconfirm, { onPositiveClick: () => remove(r) },
        { trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, { type: "error", testid: "os-delete" }),
          default: () => t("common.confirm_delete") }),
    ]),
  },
]);

const ALL_KEYS = ["name", "product", "auth_url", "auth", "interface", "counts", "enabled", "last_sync_at", "last_error"];
const colPrefs = useColumnPrefs("openstack_admin", ALL_KEYS, ALL_KEYS);
const visibleKeys = computed(() => colPrefs.visibleKeys.value);
function setVisible(keys: string[]) { colPrefs.setVisible(keys); }
function resetCols() { colPrefs.reset(); }
const pickerItems = computed(() => cols.value
  .filter((c: any) => c.key !== "actions")
  .map((c: any) => ({ key: String(c.key), label: String(c.title) })));
// 操作欄永遠留著；其餘依偏好顯示
const visibleCols = computed(() => colPrefs.orderColumns(cols.value.filter(
  (c: any) => c.key === "actions" || visibleKeys.value.includes(String(c.key)))));

onMounted(() => { void load(); void loadSubnetOptions(); });
</script>

<style scoped>
.hint { font-size: 11px; opacity: .7; }
.hint.warn { color: #f0a020; opacity: 1; }
.px-help { margin: 0; padding-left: 18px; line-height: 1.9; }
.tab-l { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
.lbl-row { display: inline-flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; column-gap: 12px; }
.mono :deep(textarea) { font-family: var(--font-mono, ui-monospace, monospace); font-size: 12px; }
@media (max-width: 640px) {
  .grid2 { grid-template-columns: 1fr; }
}
</style>
