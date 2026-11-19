<template>
  <n-card>
    <template #header>
      <CardTitle :icon="VirtualizationIcon" :text="t('k8s.title')" />
    </template>

    <n-alert type="info" :bordered="false" style="margin-bottom:12px">{{ t("k8s.intro") }}</n-alert>

    <!-- 照整合頁的慣例：第一個頁籤是叢集清單，之後每個叢集一個頁籤 -->
    <n-tabs v-model:value="tab" type="line" data-testid="k8s-tabs">
      <n-tab-pane name="clusters">
        <template #tab>
          <span class="tab-l"><n-icon :size="16"><VirtualizationIcon /></n-icon>{{ `${t("k8s.clusters")} (${rows.length})` }}</span>
        </template>
        <n-space align="center" style="margin: 0 0 8px">
          <n-button :loading="loading" @click="load">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}
          </n-button>
          <n-button type="primary" data-testid="k8s-create" @click="openCreate">
            <template #icon><n-icon><PlusIcon /></n-icon></template>{{ t("k8s.create_title") }}
          </n-button>
          <n-button :loading="manifestBusy" :disabled="manifestBusy" data-testid="k8s-manifest" @click="downloadManifest">
            <template #icon><n-icon><DownloadIcon /></n-icon></template>{{ t("k8s.download_manifest") }}
          </n-button>
        </n-space>
        <n-data-table :columns="cols" :data="rows" :loading="loading" size="small" :scroll-x="1200" :bordered="false"
                      :row-key="(r: K8sCluster) => r.id" data-testid="k8s-table" />
      </n-tab-pane>

      <n-tab-pane v-for="r in rows" :key="r.id" :name="`c:${r.id}`" display-directive="if">
        <template #tab>
          <span class="tab-l" :data-testid="`k8s-tab-${r.name}`"><n-icon :size="16"><ServiceIcon /></n-icon>{{ r.name }}</span>
        </template>
        <K8sClusterPanel :cluster="r" :testing="testingId === r.id" :syncing="isSyncing(r.id)"
                         @test="runTest(r)" @sync="runSync(r)" />
      </n-tab-pane>
    </n-tabs>

    <!-- 新增／編輯 -->
    <n-modal v-model:show="show" preset="card" :title="editing ? t('k8s.edit_title') : t('k8s.create_title')"
             style="width:640px;max-width:94vw">
      <n-form label-placement="top">
        <n-form-item :label="t('common.name')">
          <n-input v-model:value="form.name" data-testid="k8s-name" />
        </n-form-item>
        <n-form-item :label="t('k8s.connect_by')">
          <n-radio-group v-model:value="form.mode" data-testid="k8s-mode">
            <n-radio value="token">{{ t("k8s.mode_token") }}</n-radio>
            <n-radio value="kubeconfig">{{ t("k8s.mode_kubeconfig") }}</n-radio>
          </n-radio-group>
        </n-form-item>
        <template v-if="form.mode === 'kubeconfig'">
          <n-form-item :label="editing ? t('k8s.kubeconfig_keep') : 'kubeconfig'">
            <n-space vertical :size="4" style="width:100%">
              <n-input v-model:value="form.kubeconfig" type="textarea" :rows="6" class="mono" data-testid="k8s-kubeconfig"
                       :placeholder="t('k8s.kubeconfig_ph')" />
              <span class="hint">{{ t("k8s.kubeconfig_hint") }}</span>
            </n-space>
          </n-form-item>
          <n-form-item :label="t('k8s.context')">
            <n-input v-model:value="form.kubeconfig_context" :placeholder="t('k8s.context_ph')" />
          </n-form-item>
        </template>
        <template v-else>
          <n-form-item :label="t('k8s.api_url')">
            <n-input v-model:value="form.api_url" data-testid="k8s-api-url" placeholder="https://k8s.example.net:6443" />
          </n-form-item>
          <n-form-item :label="editing ? t('k8s.token_keep') : t('k8s.token')">
            <n-input v-model:value="form.token" type="password" show-password-on="click" data-testid="k8s-token"
                     :input-props="{ autocomplete: 'new-password' }" />
          </n-form-item>
          <n-form-item :label="t('k8s.ca_cert')">
            <n-space vertical :size="4" style="width:100%">
              <n-input v-model:value="form.ca_cert" type="textarea" :rows="3" class="mono" data-testid="k8s-ca"
                       :placeholder="editing?.has_ca_cert ? t('k8s.ca_keep') : '-----BEGIN CERTIFICATE-----'" />
              <span class="hint">{{ t("k8s.ca_hint") }}</span>
            </n-space>
          </n-form-item>
          <n-form-item :label="t('firewall_admin.verify_tls')">
            <n-switch v-model:value="form.verify_tls" />
          </n-form-item>
        </template>
        <n-space align="center" :size="24">
          <n-form-item :label="t('common.enabled')"><n-switch v-model:value="form.enabled" /></n-form-item>
          <n-form-item :label="t('virt.interval')">
            <n-input-number v-model:value="form.sync_interval_seconds" :min="60" :max="86400" />
          </n-form-item>
        </n-space>
        <n-form-item :label="t('k8s.create_ip_ranges')">
          <n-space vertical :size="4" style="width:100%">
            <n-switch v-model:value="form.create_ip_ranges" data-testid="k8s-ranges" />
            <span class="hint">{{ t("k8s.create_ip_ranges_hint") }}</span>
          </n-space>
        </n-form-item>
        <n-form-item :label="t('k8s.pod_network_routable')">
          <n-space vertical :size="4" style="width:100%">
            <n-switch v-model:value="form.pod_network_routable" data-testid="k8s-pod-routable" />
            <span class="hint">{{ t("k8s.pod_network_routable_hint") }}</span>
          </n-space>
        </n-form-item>
        <n-form-item :label="t('virt.scope_subnets')">
          <div style="width:100%">
            <n-select v-model:value="form.scope_subnet_ids" :options="subnetOptions" multiple filterable clearable
                      :placeholder="t('virt.scope_all')" />
            <ScopeOverlapWarning :scope-empty="!form.scope_subnet_ids?.length" />
          </div>
        </n-form-item>
        <n-form-item :label="t('common.description')">
          <n-input v-model:value="form.description" type="textarea" :rows="2" />
        </n-form-item>
        <n-alert type="info" :title="t('k8s.help_title')" :bordered="false">
          <ol class="px-help">
            <li>{{ t("k8s.help_1") }}</li>
            <li>{{ t("k8s.help_2") }}</li>
            <li>{{ t("k8s.help_3") }}</li>
          </ol>
        </n-alert>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="show = false">
            <template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}
          </n-button>
          <n-button type="primary" :loading="saving" :disabled="saving" data-testid="k8s-save" @click="save">
            <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
          </n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 測試連線：版本＋逐項讀不讀得到 -->
    <n-modal v-model:show="testShow" preset="card" style="width:560px;max-width:94vw"
             :title="testFor ? `${t('k8s.test_title')}：${testFor.name}` : t('k8s.test_title')">
      <n-spin :show="!!testingId">
        <div v-if="health" data-testid="k8s-health">
          <div style="margin-bottom:8px"><b>{{ t("k8s.version") }}：</b>{{ health.version ?? "—" }}</div>
          <div v-for="c in health.checks" :key="c.key" class="chk" :data-testid="`k8s-check-${c.key}`">
            <n-tag size="small" :bordered="false" :type="c.ok === true ? 'success' : c.ok === false ? 'error' : 'default'">
              {{ c.ok === true ? t("k8s.check_ok") : c.ok === false ? t("k8s.check_fail") : t("k8s.check_absent") }}
            </n-tag>
            <span class="chk-l">{{ t(`k8s.check_${c.key}`) }}</span>
            <span v-if="c.ok" class="hint">{{ t("k8s.check_count", { n: c.count ?? 0 }) }}</span>
            <span v-else-if="c.ok === false" class="hint warn">{{ c.code && te(`errors.${c.code}`) ? t(`errors.${c.code}`, { path: '' }) : c.error }}</span>
          </div>
        </div>
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
 * Kubernetes 整合（唯讀）：節點 IP、LoadBalancer／externalIPs、LB 位址集區、叢集網段，以及跟 IPAM 打架的地方。
 * 照 OpenStack 頁：第一個頁籤是清單，之後每個叢集一個頁籤。
 */
import { computed, h, onMounted, ref } from "vue";
import {
  NAlert, NButton, NCard, NDataTable, NForm, NFormItem, NIcon, NInput, NInputNumber, NModal, NPopconfirm,
  NRadio, NRadioGroup, NSelect, NSpace, NSpin, NSwitch, NTabPane, NTabs, NTag, NTooltip, useMessage,
  type DataTableColumns,
} from "naive-ui";
import { useI18n } from "vue-i18n";
import CardTitle from "@/components/CardTitle.vue";
import ScopeOverlapWarning from "@/components/ScopeOverlapWarning.vue";
import K8sClusterPanel from "@/components/K8sClusterPanel.vue";
import { trackTask, useTaskTracker } from "@/composables/useTaskTracker";
import { listSubnets } from "@/api/subnets";
import { K8s, type K8sCluster, type K8sHealth, type K8sWrite } from "@/api/k8s";
import { apiErrMsg } from "@/api/client";
import {
  CancelIcon, DeleteIcon, DownloadIcon, EditIcon, PlusIcon, RefreshIcon, SaveIcon, ServiceIcon, SyncIcon, TestIcon,
  VirtualizationIcon,
} from "@/icons";
import { fmtDateTime } from "@/utils/datetime";
import { saveBlob } from "@/utils/saveFile";

const { t, te } = useI18n();
const msg = useMessage();

const tab = ref("clusters");
const rows = ref<K8sCluster[]>([]);
const loading = ref(false);
const show = ref(false);
const saving = ref(false);
const editing = ref<K8sCluster | null>(null);

function blank() {
  return {
    name: "", mode: "token" as "token" | "kubeconfig", kubeconfig: "", kubeconfig_context: "",
    api_url: "", token: "", ca_cert: "", verify_tls: true, enabled: true, sync_interval_seconds: 300,
    create_ip_ranges: true, pod_network_routable: false, scope_subnet_ids: [] as string[], description: "",
  };
}
const form = ref(blank());

const subnetOptions = ref<{ label: string; value: string }[]>([]);
async function loadSubnetOptions() {
  try {
    const out: { label: string; value: string }[] = [];
    for (let page = 1; page <= 200; page++) {
      const r = await listSubnets({ page, pageSize: 500 });
      for (const sn of r.items as { id: string; cidr: string; description?: string | null }[]) {
        out.push({ label: sn.description ? `${sn.cidr}（${sn.description}）` : sn.cidr, value: sn.id });
      }
      if (r.items.length < 500 || out.length >= (r.total ?? 0)) break;
    }
    subnetOptions.value = out;
  } catch { /* 讀不到就讓下拉是空的，不擋設定 */ }
}

async function load() {
  loading.value = true;
  try {
    rows.value = await K8s.list();
    if (tab.value.startsWith("c:") && !rows.value.some((r) => `c:${r.id}` === tab.value)) tab.value = "clusters";
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}

function openCreate() { editing.value = null; form.value = blank(); show.value = true; }
function openEdit(r: K8sCluster) {
  editing.value = r;
  form.value = { ...blank(), name: r.name, api_url: r.api_url, verify_tls: r.verify_tls, enabled: r.enabled,
    sync_interval_seconds: r.sync_interval_seconds, create_ip_ranges: r.create_ip_ranges,
    pod_network_routable: r.pod_network_routable, scope_subnet_ids: r.scope_subnet_ids ?? [], description: r.description ?? "" };
  show.value = true;
}

async function save() {
  const f = form.value;
  const p: K8sWrite = { name: f.name, enabled: f.enabled, sync_interval_seconds: f.sync_interval_seconds,
    create_ip_ranges: f.create_ip_ranges, pod_network_routable: f.pod_network_routable,
    scope_subnet_ids: f.scope_subnet_ids, description: f.description ?? "" };
  if (f.mode === "kubeconfig") {
    if (f.kubeconfig.trim()) {
      p.kubeconfig = f.kubeconfig;
      if (f.kubeconfig_context.trim()) p.kubeconfig_context = f.kubeconfig_context.trim();
    }
  } else {
    p.api_url = f.api_url.trim();
    p.verify_tls = f.verify_tls;
    p.auth_method = "token";
    if (f.token.trim()) p.token = f.token.trim();
    if (f.ca_cert.trim()) p.ca_cert = f.ca_cert.trim();
  }
  saving.value = true;
  try {
    if (editing.value) await K8s.update(editing.value.id, p);
    else await K8s.create(p);
    show.value = false;
    await load();
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { saving.value = false; }
}

const manifestBusy = ref(false);
async function downloadManifest() {
  manifestBusy.value = true;
  try {
    const text = await K8s.manifest();
    saveBlob("jt-ipam-readonly.yaml", new Blob([text], { type: "application/yaml" }), "application/yaml",
             { source: "k8s_manifest" });
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { manifestBusy.value = false; }
}

// ── 測試 ──
const testShow = ref(false);
const testingId = ref<string | null>(null);
const testFor = ref<K8sCluster | null>(null);
const health = ref<K8sHealth | null>(null);
async function runTest(r: K8sCluster) {
  testFor.value = r;
  health.value = null;
  testShow.value = true;
  testingId.value = r.id;
  try { health.value = await K8s.test(r.id); }
  catch (e) { testShow.value = false; msg.error(apiErrMsg(e)); }
  finally { testingId.value = null; }
}

// ── 同步：背景作業，跑完前按鈕反灰＋轉圈 ──
const { tracked, isActive } = useTaskTracker();
const syncTask = ref<Record<string, string>>({});
const posting = ref<Set<string>>(new Set());
function isSyncing(id: string): boolean {
  if (posting.value.has(id)) return true;
  const tid = syncTask.value[id];
  const x = tid ? tracked.value.find((y) => y.id === tid) : undefined;
  return !!x && isActive(x);
}
async function runSync(r: K8sCluster) {
  if (isSyncing(r.id)) return;
  posting.value = new Set([...posting.value, r.id]);
  try {
    const out = await K8s.sync(r.id);
    syncTask.value = { ...syncTask.value, [r.id]: out.task_id };
    trackTask(out.task_id, { onDone: () => void load() });
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally {
    const next = new Set(posting.value);
    next.delete(r.id);
    posting.value = next;
  }
}

async function remove(r: K8sCluster) {
  try { await K8s.remove(r.id); await load(); }
  catch (e) { msg.error(apiErrMsg(e)); }
}

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

const cols = computed<DataTableColumns<K8sCluster>>(() => [
  { title: t("common.name"), key: "name", minWidth: 150, ellipsis: { tooltip: true },
    render: (r) => h(NButton, { text: true, type: "primary", onClick: () => { tab.value = `c:${r.id}`; } },
      { default: () => r.name }) },
  { title: t("k8s.api_url"), key: "api_url", minWidth: 220, ellipsis: { tooltip: true } },
  { title: t("k8s.version"), key: "server_version", width: 120, render: (r) => r.server_version ?? "—" },
  { title: t("k8s.auth"), key: "auth_method", width: 140,
    render: (r) => (r.auth_method === "client_cert" ? t("k8s.auth_client_cert") : t("k8s.auth_token")) },
  { title: t("common.enabled"), key: "enabled", width: 80,
    render: (r) => h(NTag, { size: "small", bordered: false, type: r.enabled ? "success" : "default" },
      { default: () => (r.enabled ? t("common.yes") : t("common.no")) }) },
  { title: t("cols.last_sync"), key: "last_sync_at", width: 170, render: (r) => (r.last_sync_at ? fmtDateTime(r.last_sync_at) : "—") },
  { title: t("cols.last_error"), key: "last_error", minWidth: 180, ellipsis: { tooltip: true },
    render: (r) => (r.last_error ? h("span", { style: "color:#d03050" }, r.last_error) : "—") },
  { title: t("common.actions"), key: "actions", className: "col-actions", width: 176, fixed: "right",
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openEdit(r), { type: "success", testid: "k8s-edit" }),
      iconAction(TestIcon, t("common.test"), () => runTest(r), { testid: "k8s-test", loading: testingId.value === r.id }),
      iconAction(SyncIcon, t("common.pull"), () => runSync(r), { type: "primary", testid: "k8s-sync", loading: isSyncing(r.id) }),
      h(NPopconfirm, { onPositiveClick: () => remove(r) },
        { trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, { type: "error", testid: "k8s-delete" }),
          default: () => t("common.confirm_delete") }),
    ]) },
]);

onMounted(() => { void load(); void loadSubnetOptions(); });
</script>

<style scoped>
.hint { font-size: 11px; opacity: .7; }
.hint.warn { color: #f0a020; opacity: 1; }
.px-help { margin: 0; padding-left: 18px; line-height: 1.9; }
.tab-l { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
.mono :deep(textarea) { font-family: var(--font-mono, ui-monospace, monospace); font-size: 12px; }
.chk { display: flex; align-items: baseline; gap: 8px; padding: 4px 0; }
.chk-l { min-width: 160px; }
</style>
