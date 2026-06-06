<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { fmtDateTime } from "@/utils/datetime";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NIcon, NButton, NModal, NForm, NFormItem,
  NInput, NSwitch, NPopconfirm, NTag, NInputGroup, NAlert, NSelect, NTooltip,
  useMessage, type DataTableColumns,
} from "naive-ui";
import {
  ScanAgentsIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SaveIcon, CancelIcon,
  InfoIcon, CloneIcon,
} from "@/icons";
import {
  listScanAgents, createScanAgent, updateScanAgent, deleteScanAgent, rotateScanAgentKey,
  getAgentSubnets, setAgentSubnets,
  type ScanAgent,
} from "@/api/phase3";
import { listSubnets } from "@/api/subnets";
import { autoSort } from "@/composables/useTableSort";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
const { t } = useI18n();

const { visibleKeys: saVis, setVisible: saSet, reset: saReset } = useColumnPrefs(
  "scan_agents",
  ["name", "enabled", "has_key", "agent_version", "source_ip", "subnet_count", "last_seen_at", "last_error", "actions"],
  ["name", "enabled", "has_key", "agent_version", "source_ip", "subnet_count", "last_seen_at", "last_error", "actions"],
);
const saPicker = [
  { key: "name", label: t("cols.name") },
  { key: "enabled", label: t("cols.enabled") },
  { key: "has_key", label: t("cols.key") },
  { key: "agent_version", label: t("cols.version") },
  { key: "source_ip", label: t("cols.source_ip") },
  { key: "subnet_count", label: t("cols.subnet") },
  { key: "last_seen_at", label: t("cols.last_report") },
  { key: "last_error", label: t("cols.last_error") },
  { key: "actions", label: t("cols.actions") },
];

const msg = useMessage();
const rows = ref<ScanAgent[]>([]);
import { useTableQuickFilter } from "@/composables/useTableQuickFilter";
const { query: filterQ, filtered: filteredRows } = useTableQuickFilter(rows);
const loading = ref(false);
const show = ref(false);
const showHelp = ref(false);
const editing = ref<ScanAgent | null>(null);
const form = ref({ name: "", description: "", enabled: true, subnet_ids: [] as string[] });
const subnetOpts = ref<{ label: string; value: string }[]>([]);
async function loadSubnetOpts() {
  try {
    const res = await listSubnets({ page: 1, pageSize: 500 });
    subnetOpts.value = res.items.map((s) => ({
      label: s.description ? `${s.cidr} (${s.description})` : s.cidr, value: s.id,
    }));
  } catch { /* silent */ }
}

// 一次性金鑰揭露 modal
const showKey = ref(false);
const revealedKey = ref("");
const revealedName = ref("");

const serverOrigin = window.location.origin;
const installerOneLiner = computed(() =>
  `curl -fsSLk ${serverOrigin}/api/v1/scan-agents/installer.sh | sudo `
  + `JT_IPAM_URL=${serverOrigin} JT_IPAM_AGENT_KEY=${revealedKey.value || "<KEY>"} JT_IPAM_INSECURE=1 bash`,
);

async function refresh() {
  loading.value = true;
  try { rows.value = (await listScanAgents()).items; }
  catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}
function openCreate() {
  editing.value = null;
  form.value = { name: "", description: "", enabled: true, subnet_ids: [] };
  void loadSubnetOpts();
  show.value = true;
}
function openEdit(r: ScanAgent) {
  editing.value = r;
  form.value = { name: r.name, description: r.description ?? "", enabled: r.enabled, subnet_ids: [] };
  void loadSubnetOpts();
  void getAgentSubnets(r.id).then((ids) => { form.value.subnet_ids = ids; }).catch(() => {});
  show.value = true;
}
async function submit() {
  try {
    if (editing.value) {
      await updateScanAgent(editing.value.id, {
        description: form.value.description || undefined,
        enabled: form.value.enabled,
      });
      await setAgentSubnets(editing.value.id, form.value.subnet_ids);
      show.value = false;
    } else {
      const created = await createScanAgent({
        name: form.value.name,
        description: form.value.description || undefined,
        enabled: form.value.enabled,
      });
      show.value = false;
      revealedKey.value = created.enroll_key;
      revealedName.value = created.name;
      showKey.value = true;   // 顯示一次性金鑰 + 安裝指令
    }
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function rotate(r: ScanAgent) {
  try {
    const res = await rotateScanAgentKey(r.id);
    revealedKey.value = res.enroll_key;
    revealedName.value = res.name;
    showKey.value = true;
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function del(r: ScanAgent) {
  try { await deleteScanAgent(r.id); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
function copy(text: string) {
  void navigator.clipboard?.writeText(text);
  msg.success(t("scanAgentHelp.copied"));
}

function iconAction(icon: any, label: string, onClick: () => void, type?: any) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}
const allCols = computed<DataTableColumns<ScanAgent>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 160, ellipsis: { tooltip: true } },
  {
    title: t("common.enabled"), key: "enabled", width: 100,
    render: (r) => h(NTag, { size: "small", type: r.enabled ? "success" : "default" },
      () => r.enabled ? t("common.enabled") : t("common.disabled")),
  },
  {
    title: t("scanAgentHelp.col_key"), key: "has_key", width: 120,
    render: (r) => h(NTag, { size: "small", type: r.has_key ? "info" : "warning" },
      () => r.has_key ? t("scanAgentHelp.key_set") : t("scanAgentHelp.key_none")),
  },
  {
    title: t("scanAgentHelp.col_version"), key: "agent_version", width: 110,
    render: (r) => r.agent_version
      ? h(NTag, { size: "small", type: "success", bordered: false }, () => `v${r.agent_version}`)
      : "—",
  },
  {
    title: t("cols.source_ip"), key: "source_ip", width: 140,
    render: (r) => r.last_source_ip
      ? h("span", { style: "font-family:monospace" }, r.last_source_ip) : "—",
  },
  {
    title: t("scanAgentHelp.col_subnets"), key: "subnet_count", width: 90,
    render: (r) => r.subnet_count ?? 0,
  },
  { title: t("scanAgentHelp.col_last_seen"), key: "last_seen_at", width: 170, render: (r) => fmtDateTime(r.last_seen_at) },
  { title: t("scanAgentHelp.col_last_error"), key: "last_error", minWidth: 160, ellipsis: { tooltip: true }, render: (r) => r.last_error ?? "—" },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 136,
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openEdit(r)),
      iconAction(RefreshIcon, t("scanAgentHelp.rotate"), () => rotate(r)),
      h(NPopconfirm, { onPositiveClick: () => del(r) }, {
        trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]));
const cols = computed<DataTableColumns<ScanAgent>>(() =>
  allCols.value.filter((c: any) => saVis.value.includes(c.key)),
);

onMounted(() => { void refresh(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><ScanAgentsIcon /></n-icon>
        <span>{{ t("nav.scan_agents") }}</span>
      </n-space>
    </template>
    <n-space style="margin-bottom: 12px" align="center">
      <n-input v-model:value="filterQ" :placeholder="t('common.filter')" clearable style="width: 160px" />
      <n-button @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
      <n-button type="primary" @click="openCreate">
        <template #icon><n-icon><PlusIcon /></n-icon></template>
        {{ t("common.create") }}
      </n-button>
      <n-button quaternary @click="showHelp = true">
        <template #icon><n-icon><InfoIcon /></n-icon></template>
        {{ t("scanAgentHelp.button") }}
      </n-button>
      <ColumnPicker :all="saPicker" :visible="saVis"
                    @update:visible="saSet" @reset="saReset" />
      <ExportButton :columns="cols" :rows="rows" filename="scan-agents" :title="t('nav.scan_agents')" />
    </n-space>
    <n-data-table :columns="cols" :data="filteredRows" :loading="loading" :bordered="false" :scroll-x="1046" />

    <!-- 建立 / 編輯 -->
    <n-modal v-model:show="show" preset="card" style="width: 460px">
      <template #header>
        <n-space align="center">
          <n-icon :size="20"><component :is="editing ? EditIcon : PlusIcon" /></n-icon>
          <span>{{ editing ? t("common.edit") : t("common.create") }}</span>
        </n-space>
      </template>
      <n-form>
        <n-form-item :label="t('common.name')">
          <n-input v-model:value="form.name" :disabled="!!editing" />
        </n-form-item>
        <n-form-item :label="t('sections.description')">
          <n-input v-model:value="form.description" type="textarea" :rows="2" />
        </n-form-item>
        <n-form-item :label="t('common.enabled')">
          <n-switch v-model:value="form.enabled" />
        </n-form-item>
        <n-form-item :label="t('scanAgentHelp.assign_subnets')">
          <n-select v-model:value="form.subnet_ids" :options="subnetOpts"
                    multiple filterable clearable
                    :placeholder="t('scanAgentHelp.assign_subnets_ph')" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="show = false">
            <template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}
          </n-button>
          <n-button type="primary" @click="submit">
            <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
          </n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 一次性金鑰 + 安裝指令 -->
    <n-modal v-model:show="showKey" preset="card"
             :title="t('scanAgentHelp.key_title')" style="width: 680px; max-width: 92vw">
      <div class="agent-help">
        <p class="warn">{{ t("scanAgentHelp.key_warn", { name: revealedName }) }}</p>
        <h4>{{ t("scanAgentHelp.key_label") }}</h4>
        <n-input-group>
          <n-input :value="revealedKey" readonly />
          <n-button @click="copy(revealedKey)">{{ t("scanAgentHelp.copy") }}</n-button>
        </n-input-group>
        <h4>{{ t("scanAgentHelp.oneliner_label") }}</h4>
        <pre>{{ installerOneLiner }}</pre>
        <n-button size="small" @click="copy(installerOneLiner)">{{ t("scanAgentHelp.copy") }}</n-button>
        <div class="paths-box">
          <div class="paths-title">{{ t("scanAgentHelp.paths_title") }}</div>
          <div class="path-row"><span class="pl">{{ t('scanAgentHelp.path_program') }}</span><code>/opt/jt-ipam-agent/jt_ipam_agent.py</code></div>
          <div class="path-row"><span class="pl">{{ t('scanAgentHelp.path_config') }}</span><code>/etc/jt-ipam-agent.env</code></div>
          <div class="path-row"><span class="pl">{{ t('scanAgentHelp.path_service') }}</span><code>jt-ipam-scan-agent</code></div>
          <div class="path-row"><span class="pl">{{ t('scanAgentHelp.path_logs') }}</span><code>journalctl -u jt-ipam-scan-agent -f</code></div>
          <div class="paths-note">{{ t("scanAgentHelp.path_python") }}</div>
        </div>
        <p class="muted">{{ t("scanAgentHelp.key_note") }}</p>
      </div>
    </n-modal>

    <!-- 安裝說明 -->
    <n-modal v-model:show="showHelp" preset="card"
             :title="t('scanAgentHelp.title')" style="width: 680px; max-width: 92vw">
      <div class="agent-help">
        <n-alert type="info" :bordered="false" :show-icon="true" style="margin-bottom: 16px">
          {{ t("scanAgentHelp.intro") }}
        </n-alert>

        <ol class="help-steps">
          <li><span class="sn">1</span><span>{{ t("scanAgentHelp.step1") }}</span></li>
          <li><span class="sn">2</span><span>{{ t("scanAgentHelp.step2") }}</span></li>
          <li><span class="sn">3</span><span>{{ t("scanAgentHelp.step3") }}</span></li>
        </ol>

        <h4>{{ t("scanAgentHelp.oneliner_label") }}</h4>
        <div class="code-row">
          <pre>{{ installerOneLiner }}</pre>
          <n-button size="small" secondary @click="copy(installerOneLiner)">
            <template #icon><n-icon><CloneIcon /></n-icon></template>
            {{ t("scanAgentHelp.copy") }}
          </n-button>
        </div>

        <div class="paths-box">
          <div class="paths-title">{{ t("scanAgentHelp.paths_title") }}</div>
          <div class="path-row"><span class="pl">{{ t('scanAgentHelp.path_program') }}</span><code>/opt/jt-ipam-agent/jt_ipam_agent.py</code></div>
          <div class="path-row"><span class="pl">{{ t('scanAgentHelp.path_config') }}</span><code>/etc/jt-ipam-agent.env</code></div>
          <div class="path-row"><span class="pl">{{ t('scanAgentHelp.path_service') }}</span><code>jt-ipam-scan-agent</code></div>
          <div class="path-row"><span class="pl">{{ t('scanAgentHelp.path_logs') }}</span><code>journalctl -u jt-ipam-scan-agent -f</code></div>
          <div class="paths-note">{{ t("scanAgentHelp.path_python") }}</div>
        </div>
        <n-alert type="default" :bordered="false" :show-icon="true" style="margin-top: 12px">
          {{ t("scanAgentHelp.note") }}
        </n-alert>
      </div>
    </n-modal>
  </n-card>
</template>

<style scoped>
.agent-help h4 { margin: 16px 0 6px; font-size: 14px; }
.agent-help .help-steps { list-style: none; padding: 0; margin: 0; }
.agent-help .help-steps li {
  display: flex; align-items: flex-start; gap: 10px;
  margin: 8px 0; line-height: 1.6; font-size: 14px;
}
.agent-help .help-steps .sn {
  flex: 0 0 auto;
  width: 22px; height: 22px; margin-top: 1px;
  display: inline-flex; align-items: center; justify-content: center;
  border-radius: 50%; background: var(--primary-color, #18a058);
  color: #fff; font-size: 12px; font-weight: 600;
}
.agent-help .code-row { display: flex; align-items: flex-start; gap: 8px; }
.agent-help pre {
  flex: 1 1 auto; margin: 0;
  background: rgba(127,127,127,0.12);
  padding: 10px 12px; border-radius: 6px; overflow-x: auto;
  font-size: 12px; line-height: 1.5; white-space: pre-wrap; word-break: break-all;
}
.agent-help .muted { opacity: .7; font-size: 12px; margin-top: 10px; }
.agent-help .warn { color: #e0a23c; font-weight: 500; }
.agent-help .paths-box {
  margin-top: 14px;
  border: 1px solid rgba(127,127,127,0.2);
  border-radius: 8px;
  padding: 12px 14px;
  background: rgba(127,127,127,0.04);
}
.agent-help .paths-title { font-size: 13px; font-weight: 600; margin-bottom: 8px; opacity: .85; }
.agent-help .path-row {
  display: flex; align-items: center; gap: 10px;
  padding: 3px 0; font-size: 12.5px;
}
.agent-help .path-row .pl {
  flex: 0 0 52px; text-align: right;
  opacity: .6;
}
.agent-help .path-row code {
  background: rgba(127,127,127,0.14);
  padding: 2px 8px; border-radius: 5px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px;
  word-break: break-all;
}
.agent-help .paths-note { margin-top: 8px; font-size: 12px; opacity: .6; }
</style>
