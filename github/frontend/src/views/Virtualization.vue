<script setup lang="ts">
import { useAuthStore } from "@/stores/auth";
const _authBtn = useAuthStore();
import { computed, h, onMounted, ref } from "vue";
import { fmtDateTime } from "@/utils/datetime";
import { useI18n } from "vue-i18n";
import {
  NCard, NTabs, NTabPane, NDataTable, NSpace, NIcon, NButton, NTag, NTooltip,
  NModal, NForm, NFormItem, NInput, NSelect, NSwitch, NInputNumber, NPopconfirm, NAlert,
  useMessage, type DataTableColumns,
} from "naive-ui";
import {
  VirtualizationIcon, RefreshIcon, SyncIcon, PlusIcon, TestIcon, DeleteIcon,
  EditIcon, CloneIcon, AdvancedIcon, DevicesIcon,
} from "@/icons";
import { Virt, type ProxmoxInstance } from "@/api/phase3";
import { autoSort } from "@/composables/useTableSort";
import { useCustomers } from "@/composables/useCustomers";
import { useRoute } from "vue-router";

const { t } = useI18n();
const route = useRoute();
// 管理區（virt_admin）：只放 Proxmox 連線；功能/進階區（virt）：叢集 + VM
const adminMode = computed(() => route.name === "virt_admin");
const { options: customerOptions, ensureLoaded: ensureCustomerOptsLoaded } = useCustomers();

// VM 狀態翻譯（running/stopped/paused/suspended…）；沒對到就原樣顯示
function vmStatusLabel(s: string | null | undefined): string {
  if (!s) return "—";
  const key = `virt.vm_status.${s}`;
  const tr = t(key);
  return tr === key ? s : tr;
}
const msg = useMessage();
const tab = ref<"clusters" | "vms" | "proxmox">("clusters");

const clusters = ref<any[]>([]);
const vms = ref<any[]>([]);
const proxmox = ref<any[]>([]);
const loading = ref(false);

async function refresh() {
  loading.value = true;
  try {
    [clusters.value, vms.value, proxmox.value]
      = await Promise.all([Virt.clusters(), Virt.vms(), Virt.proxmox()]);
  } catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}
async function syncProxmox(id: string) {
  const row = proxmox.value.find((r) => r.id === id);
  const target = row?.api_url ?? id.slice(0, 8);
  try {
    await Virt.syncProxmox(id);
    msg.success(t("tasks.queued_toast", { kind: "Proxmox VE sync", target }));
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function testProxmox(id: string) {
  try { await Virt.testProxmox(id); msg.success(t("virt.test_ok")); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function delProxmox(id: string) {
  try { await Virt.deleteProxmox(id); msg.success(t("common.ok")); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

// ── 新增 / 編輯叢集（含所屬單位）──
const showCluster = ref(false);
const editingClusterId = ref<string | null>(null);
const clusterForm = ref({ name: "", description: "", customer_id: null as string | null });
function openClusterCreate() {
  editingClusterId.value = null;
  clusterForm.value = { name: "", description: "", customer_id: null };
  showCluster.value = true;
}
function openClusterEdit(r: any) {
  editingClusterId.value = r.id;
  clusterForm.value = { name: r.name, description: r.description ?? "", customer_id: r.customer_id ?? null };
  showCluster.value = true;
}
async function submitCluster() {
  if (!clusterForm.value.name.trim()) { msg.error(t("virt.err_cluster_name")); return; }
  try {
    if (editingClusterId.value) {
      await Virt.updateCluster(editingClusterId.value, {
        name: clusterForm.value.name.trim(),
        description: clusterForm.value.description || undefined,
        customer_id: clusterForm.value.customer_id ?? null,
      });
    } else {
      await Virt.createCluster({ name: clusterForm.value.name.trim(), type: "proxmox",
        description: clusterForm.value.description || undefined,
        customer_id: clusterForm.value.customer_id ?? null });
    }
    showCluster.value = false;
    editingClusterId.value = null;
    clusterForm.value = { name: "", description: "", customer_id: null };
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

// ── 新增 / 編輯 / 複製 Proxmox 連線 ──
const showPx = ref(false);
const editingPxId = ref<string | null>(null);  // null = 新增/複製；有值 = 編輯
function emptyPxForm() {
  return {
    cluster_id: null as string | null,   // 留空 → 同步時依 PVE 叢集名稱自動建立
    api_url: "https://", extra_urls: "",
    auth_username: "root@pam", auth_token_id: "", token_secret: "",
    verify_tls: false, enabled: true, sync_interval_seconds: 600,
  };
}
const pxForm = ref(emptyPxForm());
const clusterOptions = computed(() => clusters.value.map((c) => ({ label: c.name, value: c.id })));

function openPxCreate() {
  editingPxId.value = null;
  pxForm.value = emptyPxForm();
  showPx.value = true;
}
function fillFromRow(r: ProxmoxInstance) {
  pxForm.value = {
    cluster_id: (r as any).cluster_id ?? clusters.value[0]?.id ?? null,
    api_url: r.api_url,
    extra_urls: (r.extra_api_urls ?? []).join("\n"),
    auth_username: r.auth_username,
    auth_token_id: r.auth_token_id,
    token_secret: "",
    verify_tls: r.verify_tls,
    enabled: r.enabled,
    sync_interval_seconds: r.sync_interval_seconds,
  };
}
function openPxEdit(r: ProxmoxInstance) {
  editingPxId.value = r.id;
  fillFromRow(r);
  showPx.value = true;
}
function openPxClone(r: ProxmoxInstance) {
  editingPxId.value = null;       // 當新增處理
  fillFromRow(r);
  pxForm.value.api_url = "https://";   // 換手新節點 → 清空主 URL 讓使用者填
  showPx.value = true;
}

const pxModalTitle = computed(() =>
  editingPxId.value ? t("virt.edit_proxmox") : t("virt.add_proxmox"));

async function submitPx() {
  const f = pxForm.value;
  if (!editingPxId.value && f.token_secret.length < 8) { msg.error(t("virt.err_token_secret")); return; }
  const extra = f.extra_urls.split(/[\n,]/).map((s) => s.trim()).filter(Boolean);
  try {
    if (editingPxId.value) {
      const payload: any = {
        api_url: f.api_url, extra_api_urls: extra,
        auth_username: f.auth_username, auth_token_id: f.auth_token_id,
        verify_tls: f.verify_tls, enabled: f.enabled,
        sync_interval_seconds: f.sync_interval_seconds,
      };
      if (f.token_secret) payload.token_secret = f.token_secret;  // 留空＝不變
      await Virt.updateProxmox(editingPxId.value, payload);
    } else {
      await Virt.createProxmox({
        cluster_id: f.cluster_id ?? undefined, api_url: f.api_url, extra_api_urls: extra,
        auth_username: f.auth_username, auth_token_id: f.auth_token_id,
        token_secret: f.token_secret, verify_tls: f.verify_tls,
        enabled: f.enabled, sync_interval_seconds: f.sync_interval_seconds,
      });
    }
    showPx.value = false;
    msg.success(t("common.ok"));
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

// 圖示 + tooltip 動作鈕（避免操作欄換行；窄螢幕也只顯示 icon）
function iconAction(icon: any, label: string, onClick: () => void, type?: any) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type, onClick },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}

const clusterCols = computed<DataTableColumns<any>>(() => autoSort([
  { title: t("common.name"), key: "name" },
  { title: t("virt.type"), key: "type" },
  { title: t("cols.unit"), key: "customer_name", width: 160, ellipsis: { tooltip: true },
    render: (r) => r.customer_name ?? "—" },
  {
    title: t("virt.cluster_mode"), key: "is_standalone", width: 120,
    render: (r) => h(NTag, { size: "small", type: r.is_standalone ? "warning" : "success" },
      () => r.is_standalone ? t("virt.standalone") : t("virt.clustered")),
  },
  { title: t("sections.description"), key: "description" },
  {
    title: t("common.actions"), key: "actions", width: 70,
    render: (r) => iconAction(EditIcon, t("common.edit"), () => openClusterEdit(r)),
  },
]));
// 每個 NIC 一行（IP / bridge / MAC 三欄同 index 對齊）— 多 IP 一看就知道對應關係
function stackedCell(arr?: string[] | null) {
  if (!arr || !arr.length) return "—";
  return h("div", { class: "nic-stack" }, arr.map((v) => h("div", { class: "nic-line" }, v)));
}
const vmCols = computed<DataTableColumns<any>>(() => autoSort([
  { title: t("common.name"), key: "name" },
  {
    title: t("virt.kind"), key: "kind", width: 70,
    render: (r) => h(NTag, { size: "small", type: r.kind === "ct" ? "warning" : "info" },
      () => r.kind === "ct" ? "CT" : "VM"),
  },
  {
    title: t("virt.cluster"), key: "cluster_id",
    render: (r) => clusters.value.find((c) => c.id === r.cluster_id)?.name ?? "—",
  },
  { title: t("virt.node"), key: "node", render: (r) => r.node ?? "—" },
  {
    title: "IP", key: "ips", minWidth: 150,
    render: (r) => stackedCell(r.ips),
  },
  {
    title: t("virt.bridge"), key: "bridges", minWidth: 100,
    render: (r) => stackedCell(r.bridges),
  },
  {
    title: "MAC", key: "macs", minWidth: 160,
    render: (r) => stackedCell(r.macs),
  },
  {
    title: t("common.status"), key: "status",
    render: (r) => h(NTag, {
      size: "small",
      type: r.status === "running" ? "success" : r.status === "stopped" ? "default" : "warning",
    }, () => vmStatusLabel(r.status)),
  },
]));
const proxmoxCols = computed<DataTableColumns<ProxmoxInstance>>(() => autoSort([
  {
    title: "API URL", key: "api_url",
    render: (r) => h(NSpace, { size: 6, align: "center", wrapItem: false }, () => [
      h("span", null, r.api_url),
      r.extra_api_urls && r.extra_api_urls.length
        ? h(NTooltip, null, {
            trigger: () => h(NTag, { size: "small", type: "info", bordered: false },
              () => `+${r.extra_api_urls.length}`),
            default: () => r.extra_api_urls.join("\n"),
          })
        : null,
    ]),
  },
  {
    title: t("common.status"), key: "enabled",
    render: (r) => h(NTag, { size: "small", type: r.enabled ? "success" : "default" },
      () => r.enabled ? t("common.enabled") : t("common.disabled")),
  },
  { title: t("virt.last_sync"), key: "last_sync_at", render: (r) => fmtDateTime(r.last_sync_at) },
  { title: t("wazuh_admin.col_last_error"), key: "last_error", render: (r) => r.last_error ?? "—" },
  {
    title: t("common.actions"), key: "_", width: 184, className: "col-actions",
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(TestIcon, t("common.test"), () => testProxmox(r.id)),
      iconAction(SyncIcon, t("common.pull"), () => syncProxmox(r.id), "primary"),
      iconAction(EditIcon, t("common.edit"), () => openPxEdit(r)),
      iconAction(CloneIcon, t("virt.clone"), () => openPxClone(r)),
      h(NPopconfirm, { onPositiveClick: () => delProxmox(r.id) }, {
        trigger: () => h(NTooltip, null, {
          trigger: () => h(NButton, { size: "small", quaternary: true, type: "error" },
            { icon: () => h(NIcon, null, () => h(DeleteIcon)) }),
          default: () => t("common.delete"),
        }),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]));
onMounted(() => {
  tab.value = adminMode.value ? "proxmox" : "clusters";
  void refresh();
  void ensureCustomerOptsLoaded();
});
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><VirtualizationIcon /></n-icon>
        <span>{{ adminMode ? t("virt.proxmox_admin_title") : t("nav.virtualization") }}</span>
      </n-space>
    </template>
    <n-space style="margin-bottom: 12px">
      <n-button @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
      <n-button v-if="tab === 'proxmox'" type="primary" :disabled="_authBtn.me?.can_edit === false" @click="openPxCreate">
        <template #icon><n-icon><PlusIcon /></n-icon></template>
        {{ t("virt.add_proxmox") }}
      </n-button>
    </n-space>
    <n-tabs v-model:value="tab" type="line">
      <n-tab-pane v-if="!adminMode" name="clusters">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><AdvancedIcon /></n-icon>{{ `${t('virt.clusters')} (${clusters.length})` }}</span>
        </template>
        <n-space style="margin-bottom: 10px">
          <n-button type="primary" size="small" :disabled="_authBtn.me?.can_edit === false" @click="openClusterCreate">
            <template #icon><n-icon><PlusIcon /></n-icon></template>
            {{ t("virt.add_cluster") }}
          </n-button>
        </n-space>
        <n-data-table :columns="clusterCols" :data="clusters" :loading="loading" :bordered="false" />
      </n-tab-pane>
      <n-tab-pane v-if="!adminMode" name="vms">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><VirtualizationIcon /></n-icon>{{ `${t('virt.vms')} (${vms.length})` }}</span>
        </template>
        <n-data-table :columns="vmCols" :data="vms" :loading="loading" :bordered="false" />
      </n-tab-pane>
      <n-tab-pane v-if="adminMode" name="proxmox">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><DevicesIcon /></n-icon>{{ `${t('virt.proxmox')} (${proxmox.length})` }}</span>
        </template>
        <n-data-table :columns="proxmoxCols" :data="proxmox" :loading="loading" :bordered="false" />
      </n-tab-pane>
    </n-tabs>

    <!-- 新增叢集 -->
    <n-modal v-model:show="showCluster" preset="card"
             :title="editingClusterId ? t('common.edit') : t('virt.add_cluster')" style="width: 420px">
      <n-form>
        <n-form-item :label="t('common.name')"><n-input v-model:value="clusterForm.name" /></n-form-item>
        <n-form-item :label="t('cols.unit')">
          <n-select v-model:value="clusterForm.customer_id" :options="customerOptions"
                    :placeholder="t('common.not_specified')" clearable filterable />
        </n-form-item>
        <n-form-item :label="t('sections.description')">
          <n-input v-model:value="clusterForm.description" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showCluster = false">{{ t("common.cancel") }}</n-button>
          <n-button type="primary" @click="submitCluster">{{ t("common.save") }}</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 新增 / 編輯 Proxmox 連線 -->
    <n-modal v-model:show="showPx" preset="card" :title="pxModalTitle" style="width: 520px">
      <n-form label-placement="top">
        <n-form-item v-if="!editingPxId && clusterOptions.length" :label="t('virt.cluster')">
          <n-select v-model:value="pxForm.cluster_id" :options="clusterOptions"
                    clearable :placeholder="t('virt.cluster_auto_ph')" />
        </n-form-item>
        <n-form-item :label="t('virt.api_url_primary')">
          <n-input v-model:value="pxForm.api_url" placeholder="https://pve.example.com:8006" />
        </n-form-item>
        <n-form-item :label="t('virt.extra_urls')">
          <n-input v-model:value="pxForm.extra_urls" type="textarea" :rows="2"
                   :placeholder="t('virt.extra_urls_ph')" />
        </n-form-item>
        <n-form-item :label="t('virt.auth_username')">
          <n-input v-model:value="pxForm.auth_username" placeholder="root@pam" />
        </n-form-item>
        <n-form-item :label="t('virt.token_id')">
          <n-input v-model:value="pxForm.auth_token_id" placeholder="ipam" />
        </n-form-item>
        <n-form-item :label="t('virt.token_secret')">
          <n-input v-model:value="pxForm.token_secret" type="password" show-password-on="click"
                   :placeholder="editingPxId ? t('virt.secret_keep') : 'xxxxxxxx-xxxx-...'" />
        </n-form-item>
        <n-space align="center" :size="24">
          <n-form-item :label="t('common.enabled')"><n-switch v-model:value="pxForm.enabled" /></n-form-item>
          <n-form-item :label="t('virt.verify_tls')"><n-switch v-model:value="pxForm.verify_tls" /></n-form-item>
          <n-form-item :label="t('virt.interval')">
            <n-input-number v-model:value="pxForm.sync_interval_seconds" :min="60" :max="86400" />
          </n-form-item>
        </n-space>
        <n-alert type="info" :title="t('virt.help_title')" :bordered="false"
                 style="margin-top: 4px">
          <ol class="px-help">
            <li>{{ t("virt.help_step1") }}</li>
            <li>
              {{ t("virt.help_step2") }}
              <span class="px-tag">{{ t("virt.help_path") }}</span>
              <span class="px-tag">PVEAuditor</span>
              <span class="px-tag">Propagate</span>
            </li>
            <li>{{ t("virt.help_step3") }}</li>
          </ol>
        </n-alert>
        <n-alert type="success" :bordered="false" :show-icon="true"
                 style="margin-top: 8px">
          {{ t("virt.multinode_hint") }}
        </n-alert>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showPx = false">{{ t("common.cancel") }}</n-button>
          <n-button type="primary" @click="submitPx">{{ t("common.save") }}</n-button>
        </n-space>
      </template>
    </n-modal>
  </n-card>
</template>

<style scoped>
.px-help {
  margin: 0;
  padding-left: 18px;
  line-height: 1.9;
  font-size: 13px;
}
.px-help li { margin-bottom: 2px; }
.px-tag {
  display: inline-block;
  margin: 0 2px;
  padding: 0 6px;
  border-radius: 4px;
  font-family: monospace;
  font-size: 12px;
  background: rgba(24, 160, 88, 0.16);
  color: var(--n-text-color, inherit);
}
.nic-stack { display: flex; flex-direction: column; }
.nic-line {
  line-height: 1.8;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.nic-line + .nic-line { border-top: 1px dashed rgba(127, 127, 127, 0.18); }
</style>
