<template>
  <n-card>
    <template #header>
      <CardTitle :icon="VirtualizationIcon" :text="t('esxi.page_title')">
        <n-tag size="small" type="warning" :bordered="false" style="margin-left:8px">Beta</n-tag>
      </CardTitle>
    </template>

    <!-- 沒有實機驗證過就要說出來，而不是等使用者自己踩到欄位對不上 -->
    <n-alert type="warning" :bordered="false" :show-icon="true" style="margin-bottom:12px">
      {{ t("esxi.beta_note") }}
    </n-alert>

    <n-space align="center" style="margin: 8px 0">
      <n-input v-model:value="query" clearable style="width:180px" :placeholder="t('common.filter')" />
      <n-button :loading="loading" @click="load">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}
      </n-button>
      <n-button type="primary" @click="openCreate">
        <template #icon><n-icon><PlusIcon /></n-icon></template>{{ t("esxi.create_title") }}
      </n-button>
      <!-- 整合設定好之後，抓到的虛擬機在另一頁：直接帶過去（客戶 2026-10-21 設好 Hyper-V 找不到 VM 清單） -->
      <n-button type="info" ghost data-testid="esxi-view-vms" @click="router.push({ name: 'virt_vmware' })">
        <template #icon><n-icon><VirtualizationIcon /></n-icon></template>{{ t("virt.view_vms") }}
      </n-button>
      <ColumnPicker :all="pickerItems" :visible="visibleKeys"
                    @update:visible="setVisible" @reset="resetCols"
                    :order="colPrefs.order.value" @update:order="colPrefs.setOrder" />
      <ExportButton :columns="visibleCols" :rows="filtered" filename="vmware"
                    :title="t('esxi.page_title')" />
    </n-space>

    <n-data-table :columns="visibleCols" :data="filtered" :loading="loading" size="small"
                  :bordered="false" :row-key="(r: ESXiInstance) => r.id" :pagination="pg" />

    <!-- 版面與「整合 Proxmox VE」一致：標籤在上、同一列放開關與間隔、附限定範圍與說明 -->
    <n-modal v-model:show="show" preset="card" :title="modalTitle" style="width:520px">
      <n-form label-placement="top">
        <n-form-item :label="t('common.name')">
          <n-input v-model:value="form.name" />
        </n-form-item>
        <n-form-item :label="t('esxi.api_url_primary')">
          <n-input v-model:value="form.api_url" placeholder="https://vcenter.example.com" />
        </n-form-item>
        <n-form-item :label="t('esxi.extra_urls')">
          <n-input v-model:value="form.extra_api_urls" type="textarea" :rows="2"
                   :placeholder="t('esxi.extra_urls_ph')" />
        </n-form-item>
        <n-form-item :label="t('common.username')">
          <n-input v-model:value="form.username" placeholder="readonly@vsphere.local" />
        </n-form-item>
        <n-form-item :label="t('common.password')">
          <n-input v-model:value="form.password" type="password" show-password-on="click"
                   :placeholder="editing ? t('virt.secret_keep') : ''" />
        </n-form-item>
        <n-space align="center" :size="24">
          <n-form-item :label="t('common.enabled')"><n-switch v-model:value="form.enabled" /></n-form-item>
          <n-form-item :label="t('virt.verify_tls')"><n-switch v-model:value="form.verify_tls" /></n-form-item>
          <n-form-item :label="t('virt.interval')">
            <n-input-number v-model:value="form.sync_interval_seconds" :min="30" :max="86400" />
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
            <!-- 開了就等於放棄一道偵測，這件事必須在開關旁邊講 -->
            <n-alert v-if="form.auto_create_ips" type="warning" :show-icon="false" :bordered="false"
                     style="margin-top:6px">
              {{ t("virt_autocreate.risk") }}
            </n-alert>
          </div>
        </n-form-item>
        <div style="margin: -8px 0 4px">
          <span style="font-size: 11px; opacity: .7">{{ t("virt.scope_hint") }}</span>
        </div>
        <n-form-item :label="t('common.description')">
          <n-input v-model:value="form.description" type="textarea" :rows="2" />
        </n-form-item>
        <n-alert type="info" :title="t('esxi.help_title')" :bordered="false" style="margin-top:4px">
          <ol class="px-help">
            <li>{{ t("esxi.help_step1") }}</li>
            <li>{{ t("esxi.help_step2") }}</li>
            <li>{{ t("esxi.help_step3") }}</li>
          </ol>
        </n-alert>
        <n-alert type="success" :bordered="false" style="margin-top:8px">
          {{ t("esxi.failover_hint") }}
        </n-alert>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="show = false"><template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}</n-button>
          <n-button type="primary" :loading="saving" @click="save"><template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 逐步診斷：沒有實機時這是唯一能看出卡在哪一步的方式 -->
    <n-modal v-model:show="diagShow" preset="card" style="width:640px;max-width:94vw"
             :title="t('esxi.diag')">
      <n-spin :show="diagLoading">
        <div v-for="s in diagSteps" :key="s.step" class="diag-row">
          <n-tag :type="s.ok ? 'success' : 'error'" size="small" :bordered="false">
            {{ s.ok ? "OK" : "FAIL" }}
          </n-tag>
          <b style="margin:0 8px">{{ s.step }}</b>
          <span style="opacity:.75">{{ s.detail }}</span>
        </div>
        <div v-if="!diagLoading && !diagSteps.length" style="opacity:.6">—</div>
      </n-spin>
    </n-modal>
  </n-card>
</template>

<script setup lang="ts">
/**
 * ESXi / vCenter 整合設定（Beta）。
 *
 * 與 Proxmox 各自獨立設定、獨立同步，但寫進同一組虛擬化資料表 ——
 * 所以拓樸、AI 對話、MCP 的 list_vms 不必為了新平台改一行。
 */
import { computed, h, onMounted, ref } from "vue";
import {
  NAlert, NButton, NCard, NDataTable, NForm, NFormItem, NIcon, NInput, NInputNumber,
  NModal, NPopconfirm, NSelect, NSpace, NSpin, NSwitch, NTag, useMessage,
  type DataTableColumns,
} from "naive-ui";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import CardTitle from "@/components/CardTitle.vue";
import ScopeOverlapWarning from "@/components/ScopeOverlapWarning.vue";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { listSubnets } from "@/api/subnets";
import { ESXi, type ESXiDiagStep, type ESXiInstance, type ESXiPayload } from "@/api/esxi";
import { apiErrMsg } from "@/api/client";
import { DeleteIcon, EditIcon, PlusIcon, RefreshIcon, SyncIcon, TestIcon, VirtualizationIcon, CancelIcon, SaveIcon } from "@/icons";
import { useTablePagination } from "@/composables/useTablePagination";
import { fmtDateTime } from "@/utils/datetime";

const { t } = useI18n();
const router = useRouter();
const msg = useMessage();
const pg = useTablePagination();

const rows = ref<ESXiInstance[]>([]);
const loading = ref(false);
const show = ref(false);
const saving = ref(false);
const editing = ref<ESXiInstance | null>(null);
const diagShow = ref(false);
const diagLoading = ref(false);
const diagSteps = ref<ESXiDiagStep[]>([]);

const form = ref({
  name: "", api_url: "", extra_api_urls: "", username: "", password: "",
  enabled: true, verify_tls: true, sync_interval_seconds: 300, description: "",
  scope_subnet_ids: [] as string[],
  auto_create_ips: false,
});

const modalTitle = computed(() =>
  (editing.value ? t("esxi.edit_title") : t("esxi.create_title")));

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
  try { rows.value = await ESXi.list(); }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}

function openCreate() {
  editing.value = null;
  form.value = { name: "", api_url: "", extra_api_urls: "", username: "", password: "",
    enabled: true, verify_tls: true, sync_interval_seconds: 300, description: "",
    scope_subnet_ids: [], auto_create_ips: false };
  show.value = true;
}

function openEdit(r: ESXiInstance) {
  editing.value = r;
  form.value = {
    name: r.name, api_url: r.api_url, extra_api_urls: r.extra_api_urls ?? "",
    username: r.username, password: "",
    enabled: r.enabled, verify_tls: r.verify_tls,
    sync_interval_seconds: r.sync_interval_seconds, description: r.description ?? "",
    scope_subnet_ids: r.scope_subnet_ids ?? [],
    auto_create_ips: !!(r as any).auto_create_ips,
  };
  show.value = true;
}

async function save() {
  saving.value = true;
  try {
    const payload: ESXiPayload = {
      name: form.value.name, api_url: form.value.api_url, username: form.value.username,
      enabled: form.value.enabled, verify_tls: form.value.verify_tls,
      sync_interval_seconds: form.value.sync_interval_seconds,
      description: form.value.description || null,
      extra_api_urls: form.value.extra_api_urls || null,
      scope_subnet_ids: form.value.scope_subnet_ids?.length ? form.value.scope_subnet_ids : null,
      auto_create_ips: form.value.auto_create_ips,
    };
    // 編輯時密碼留空＝不變更（不要把既有密碼洗成空字串）
    if (form.value.password) payload.password = form.value.password;
    if (editing.value) await ESXi.update(editing.value.id, payload);
    else await ESXi.create(payload);
    show.value = false;
    await load();
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { saving.value = false; }
}

async function runTest(r: ESXiInstance) {
  diagShow.value = true;
  diagLoading.value = true;
  diagSteps.value = [];
  try { diagSteps.value = (await ESXi.test(r.id)).steps; }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { diagLoading.value = false; }
}

async function runSync(r: ESXiInstance) {
  try {
    const out = await ESXi.sync(r.id);
    msg.success(t("esxi.synced", { n: out.vms ?? 0, m: out.matched_ip ?? 0 }));
    await load();
  } catch (e) { msg.error(apiErrMsg(e)); }
}

async function remove(r: ESXiInstance) {
  try { await ESXi.remove(r.id); await load(); }
  catch (e) { msg.error(apiErrMsg(e)); }
}

const query = ref("");

/** 依可見欄位偏好與篩選字串收斂表格內容（與 Proxmox 連線頁同一套操作）。 */
const filtered = computed(() => {
  const q = query.value.trim().toLowerCase();
  if (!q) return rows.value;
  return rows.value.filter((r) => [r.name, r.api_url, r.username, r.description]
    .some((v) => String(v ?? "").toLowerCase().includes(q)));
});

const cols: DataTableColumns<ESXiInstance> = [
  { title: () => t("common.name"), key: "name" },
  {
    title: "URL", key: "api_url",
    render: (r) => {
      const extra = (r.extra_api_urls || "").replace(/,/g, "\n").split("\n")
        .map((x) => x.trim()).filter(Boolean).length;
      return h("span", null, [
        r.api_url,
        extra
          ? h(NTag, { size: "tiny", type: "info", bordered: false, style: "margin-left:6px",
                      title: r.extra_api_urls ?? "" }, { default: () => `+${extra}` })
          : null,
      ]);
    },
  },
  { title: () => t("common.username"), key: "username" },
  {
    title: () => t("common.enabled"), key: "enabled",
    render: (r) => h(NTag, { size: "small", bordered: false, type: r.enabled ? "success" : "default" },
      { default: () => (r.enabled ? t("common.yes") : t("common.no")) }),
  },
  {
    title: () => t("cols.last_sync"), key: "last_sync_at",
    render: (r) => (r.last_sync_at ? fmtDateTime(r.last_sync_at) : "—"),
  },
  {
    title: () => t("cols.last_error"), key: "last_error",
    render: (r) => (r.last_error
      ? h("span", { style: "color:#d03050", title: r.last_error }, r.last_error.slice(0, 60))
      : "—"),
  },
  {
    title: () => t("common.actions"), key: "actions",
    render: (r) => h(NSpace, { size: 4 }, {
      default: () => [
        h(NButton, { size: "tiny", onClick: () => runTest(r) },
          { icon: () => h(NIcon, null, { default: () => h(TestIcon) }) }),
        h(NButton, { size: "tiny", onClick: () => runSync(r) },
          { icon: () => h(NIcon, null, { default: () => h(SyncIcon) }) }),
        h(NButton, { size: "tiny", onClick: () => openEdit(r) },
          { icon: () => h(NIcon, null, { default: () => h(EditIcon) }) }),
        h(NPopconfirm, { onPositiveClick: () => remove(r) }, {
          trigger: () => h(NButton, { size: "tiny", type: "error", ghost: true },
            { icon: () => h(NIcon, null, { default: () => h(DeleteIcon) }) }),
          default: () => t("common.confirm_delete"),
        }),
      ],
    }),
  },
];


const ALL_KEYS = ["name", "api_url", "username", "enabled", "last_sync_at", "last_error"];
const colPrefs = useColumnPrefs("esxi_admin", ALL_KEYS, ALL_KEYS);
const visibleKeys = computed(() => colPrefs.visibleKeys.value);
function setVisible(keys: string[]) { colPrefs.setVisible(keys); }
function resetCols() { colPrefs.reset(); }
const pickerItems = computed(() => ALL_KEYS.map((k) => ({
  key: k, label: String(colLabel[k] ?? k),
})));
const colLabel: Record<string, string> = {
  name: t("common.name"), api_url: "URL", username: t("common.username"),
  enabled: t("common.enabled"), last_sync_at: t("cols.last_sync"),
  last_error: t("cols.last_error"),
};
// 操作欄永遠留著；其餘依偏好顯示
const visibleCols = computed(() => colPrefs.orderColumns(cols.filter(
  (c: any) => c.key === "actions" || visibleKeys.value.includes(String(c.key)))));

onMounted(() => { void load(); void loadSubnetOptions(); });
</script>

<style scoped>
.hint { font-size: 12px; opacity: .6; }
.px-help { margin: 0; padding-left: 18px; line-height: 1.9; }
.diag-row { padding: 6px 0; font-size: 13px; border-bottom: 1px solid var(--n-border-color, #eee); }
</style>
