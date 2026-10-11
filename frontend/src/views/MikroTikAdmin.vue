<script setup lang="ts">
import { srvText, type ServerMessage } from "@/utils/wsError";
/**
 * MikroTik RouterOS 整合 —— 與其他防火牆整合各自獨立設定，唯讀拉取（不會更動裝置設定）。
 *
 * 這一頁比其他整合多一組「保護參數」，而且**重的區段預設關**：客戶的 MikroTik 多半是
 * 主力路由器，全表 ARP 可能是上萬列。流程刻意設計成「先測、看數字、再決定要開哪些」——
 * 所以連線診斷會回報每支端點的**列數與耗時**，不只是通不通。
 */
import { computed, h, onMounted, ref } from "vue";
import { fmtDateTime } from "@/utils/datetime";
import { useI18n } from "vue-i18n";
import { trackTask } from "@/composables/useTaskTracker";
import ScopeOverlapWarning from "@/components/ScopeOverlapWarning.vue";
import {
  NCard, NDataTable, NSpace, NButton, NTag, NIcon, NTooltip, NAlert, NModal, NForm,
  NFormItem, NInput, NInputNumber, NSwitch, NSelect, NCheckbox, NPopconfirm, NDivider,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { listSubnets } from "@/api/subnets";
import { listDevices, getDevice } from "@/api/basic";
import {
  listMikroTik, createMikroTik, updateMikroTik, deleteMikroTik,
  testMikroTik, syncMikroTik,
  type MikroTikRouter, type MikroTikDiagnosis,
} from "@/api/mikrotik";
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

const COLS = ["name", "api_url", "enabled", "model", "sync_flags", "last_sync_at",
  "last_error", "actions"];
const { visibleKeys: vis, setVisible: setVis, reset: resetVis, order, setOrder, orderColumns } =
  useColumnPrefs("mikrotik", COLS, COLS);
const picker = computed(() => [
  { key: "name", label: t("cols.name") },
  { key: "api_url", label: "API URL" },
  { key: "enabled", label: t("cols.status") },
  { key: "model", label: t("mikrotik.model") },
  { key: "sync_flags", label: t("cols.sync_items") },
  { key: "last_sync_at", label: t("cols.last_sync") },
  { key: "last_error", label: t("cols.last_error") },
  { key: "actions", label: t("cols.actions") },
]);

const rows = ref<MikroTikRouter[]>([]);
const loading = ref(false);
const show = ref(false);
const editing = ref<MikroTikRouter | null>(null);

const diag = ref<MikroTikDiagnosis | null>(null);
const diagFor = ref<string>("");
const diagOpen = ref(false);

function blankForm() {
  return {
    name: "", api_url: "", api_username: "", api_password: "",
    enabled: true, verify_tls: true,
    sync_dhcp: true, sync_dhcp_ranges: true, sync_firewall: true,
    sync_nat: true, sync_address_lists: true, sync_vpn: true,
    // 預設關：先看診斷的列數與耗時再決定
    sync_arp: false,
    // 第二階段：介面、鄰居輕量預設開；FDB 可能上萬列，預設關
    sync_interfaces: true, sync_neighbors: true, sync_fdb: false,
    device_id: null as string | null,
    cpu_load_limit: 70, section_delay_ms: 300, max_response_mb: 8,
    sync_interval_seconds: 900, description: "",
    scope_subnet_ids: [] as string[],
  };
}
const form = ref(blankForm());

/** 對應裝置：伺服器端搜尋（裝置可能上萬台，只抓一頁會找不到） */
const deviceOptions = ref<{ label: string; value: string }[]>([]);
const deviceSearching = ref(false);
let deviceSeq = 0;
async function searchDevices(q: string) {
  const seq = ++deviceSeq;
  deviceSearching.value = true;
  try {
    const r = await listDevices({ q, pageSize: 20 });
    if (seq !== deviceSeq) return;
    const keep = deviceOptions.value.filter((o) => o.value === form.value.device_id);
    deviceOptions.value = [...keep, ...r.items.filter((d) => d.id !== form.value.device_id)
      .map((d) => ({ label: d.name, value: d.id }))];
  } catch { /* silent */ }
  finally { if (seq === deviceSeq) deviceSearching.value = false; }
}
async function ensureDeviceOption(id: string | null) {
  if (!id || deviceOptions.value.some((o) => o.value === id)) return;
  try {
    const d = await getDevice(id);
    deviceOptions.value = [{ label: d.name, value: d.id }, ...deviceOptions.value];
  } catch { /* 沒權限或已刪除 */ }
}

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
  try { rows.value = (await listMikroTik()).items; }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}

function openCreate() {
  editing.value = null;
  form.value = blankForm();
  void searchDevices("");
  show.value = true;
}

function openEdit(r: MikroTikRouter) {
  editing.value = r;
  form.value = {
    name: r.name, api_url: r.api_url, api_username: r.api_username, api_password: "",
    enabled: r.enabled, verify_tls: r.verify_tls,
    sync_dhcp: r.sync_dhcp, sync_dhcp_ranges: r.sync_dhcp_ranges,
    sync_firewall: r.sync_firewall, sync_nat: r.sync_nat,
    sync_address_lists: r.sync_address_lists, sync_vpn: r.sync_vpn,
    sync_arp: r.sync_arp,
    sync_interfaces: r.sync_interfaces, sync_neighbors: r.sync_neighbors, sync_fdb: r.sync_fdb,
    device_id: r.device_id ?? null,
    cpu_load_limit: r.cpu_load_limit, section_delay_ms: r.section_delay_ms,
    max_response_mb: r.max_response_mb,
    sync_interval_seconds: r.sync_interval_seconds,
    description: r.description ?? "",
    scope_subnet_ids: r.scope_subnet_ids ?? [],
  };
  void searchDevices("").then(() => ensureDeviceOption(r.device_id ?? null));
  show.value = true;
}

async function submit() {
  const payload: Record<string, unknown> = {
    name: form.value.name, api_url: form.value.api_url,
    api_username: form.value.api_username,
    enabled: form.value.enabled, verify_tls: form.value.verify_tls,
    sync_dhcp: form.value.sync_dhcp, sync_dhcp_ranges: form.value.sync_dhcp_ranges,
    sync_firewall: form.value.sync_firewall, sync_nat: form.value.sync_nat,
    sync_address_lists: form.value.sync_address_lists, sync_vpn: form.value.sync_vpn,
    sync_arp: form.value.sync_arp,
    sync_interfaces: form.value.sync_interfaces, sync_neighbors: form.value.sync_neighbors,
    sync_fdb: form.value.sync_fdb,
    device_id: form.value.device_id,
    cpu_load_limit: form.value.cpu_load_limit,
    section_delay_ms: form.value.section_delay_ms,
    max_response_mb: form.value.max_response_mb,
    sync_interval_seconds: form.value.sync_interval_seconds,
    description: form.value.description || undefined,
    scope_subnet_ids: form.value.scope_subnet_ids,
  };
  try {
    if (editing.value) {
      if (form.value.api_password) payload.api_password = form.value.api_password;
      await updateMikroTik(editing.value.id, payload);
    } else {
      payload.api_password = form.value.api_password;
      await createMikroTik(payload as never);
    }
    show.value = false;
    msg.success(t("common.ok"));
    await refresh();
  } catch (e) { msg.error(apiErrMsg(e)); }
}

async function test(r: MikroTikRouter) {
  try {
    diag.value = await testMikroTik(r.id);
    diagFor.value = r.name;
    diagOpen.value = true;
  } catch (e) { msg.error(apiErrMsg(e)); }
}

async function sync(id: string) {
  try {
    const r = await syncMikroTik(id);
    trackTask(r.task_id, { onDone: () => void refresh() });
  } catch (e) { msg.error(apiErrMsg(e)); }
}

async function del(id: string) {
  try { await deleteMikroTik(id); await refresh(); }
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

const SYNC_TAGS: [keyof MikroTikRouter, string][] = [
  ["sync_dhcp", "DHCP"], ["sync_dhcp_ranges", "pool"], ["sync_firewall", "filter"],
  ["sync_nat", "NAT"], ["sync_address_lists", "addr-list"], ["sync_vpn", "VPN"],
  ["sync_arp", "ARP"], ["sync_interfaces", "if"], ["sync_neighbors", "LLDP"], ["sync_fdb", "FDB"],
];

/** 上一輪有區段因為「還沒對應裝置」而略過（待設定，不算失敗）。 */
function needsDevice(r: MikroTikRouter): boolean {
  const cost = (r.last_cost ?? {}) as Record<string, { skipped?: string } | undefined>;
  return Object.values(cost).some((v) => v && typeof v === "object" && v.skipped === "no_device");
}

/** 上一輪是否因為路由器忙碌而提早停止（`last_cost.stopped`）。 */
function stoppedReason(r: MikroTikRouter): string | null {
  const stopped = (r.last_cost as { stopped?: ServerMessage } | null)?.stopped;
  if (!stopped) return null;
  // 後端給代碼與參數，句子在這裡組（`reason` 是舊欄位，留著當退路）
  return srvText(stopped, (stopped as { reason?: string }).reason ?? "") || null;
}

const allCols = computed<DataTableColumns<MikroTikRouter>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 150, ellipsis: { tooltip: true } },
  { title: "API URL", key: "api_url", minWidth: 180, ellipsis: { tooltip: true } },
  {
    title: t("common.status"), key: "enabled", width: 100,
    render: (r) => h(NTag, { type: r.enabled ? "success" : "default", size: "small" },
      () => r.enabled ? t("common.enabled") : t("common.disabled")),
  },
  {
    title: t("mikrotik.model"), key: "model", width: 160, ellipsis: { tooltip: true },
    render: (r) => [r.board_name, r.routeros_version && `v${r.routeros_version}`]
      .filter(Boolean).join(" ") || "—",
  },
  {
    title: t("common.sync"), key: "sync_flags", minWidth: 200,
    render: (r) => h(NSpace, { size: 3, wrap: true }, () =>
      SYNC_TAGS.filter(([k]) => r[k]).map(([, label]) =>
        h(NTag, { size: "tiny", type: "info", bordered: false }, () => label))),
  },
  {
    title: t("cols.last_sync"), key: "last_sync_at", width: 165,
    render: (r) => {
      const reason = stoppedReason(r);
      const noDev = needsDevice(r)
        ? h(NTooltip, null, {
          trigger: () => h(NTag, { size: "tiny", type: "warning", bordered: false },
            () => t("mikrotik.no_device_tag")),
          default: () => t("errors.ros_no_device"),
        })
        : null;
      if (noDev && !reason) {
        return h(NSpace, { size: 4, align: "center", wrapItem: false },
          () => [fmtDateTime(r.last_sync_at), noDev]);
      }
      // 提早停止不是失敗（`last_error` 是空的）—— 但畫面上一定要看得出來，
      // 否則使用者只會覺得「有些資料怎麼沒更新」。
      return reason
        ? h(NSpace, { size: 4, align: "center", wrapItem: false }, () => [
          fmtDateTime(r.last_sync_at),
          h(NTooltip, null, {
            trigger: () => h(NTag, { size: "tiny", type: "warning", bordered: false },
              () => t("mikrotik.stopped_tag")),
            default: () => reason,
          }),
          noDev,
        ])
        : fmtDateTime(r.last_sync_at);
    },
  },
  {
    title: t("cols.last_error"), key: "last_error", minWidth: 150,
    ellipsis: { tooltip: true }, render: (r) => r.last_error ?? "—",
  },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 176,
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openEdit(r)),
      iconAction(TestIcon, t("mikrotik.diagnose"), () => test(r)),
      iconAction(SyncIcon, t("common.pull"), () => sync(r.id), "primary"),
      h(NPopconfirm, { onPositiveClick: () => del(r.id) }, {
        trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]));
const cols = computed<DataTableColumns<MikroTikRouter>>(() =>
  orderColumns(allCols.value.filter((c) => vis.value.includes((c as { key: string }).key))),
);

onMounted(() => { void refresh(); void loadSubnetOptions(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><FirewallIcon /></n-icon>
        <span>{{ t("mikrotik.title") }}</span>
        <n-tag size="small" type="warning" :bordered="false">Beta</n-tag>
      </n-space>
    </template>

    <n-alert type="info" :bordered="false" style="margin-bottom: 12px">
      {{ t("mikrotik.intro") }}
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

    <n-data-table :columns="cols" :data="rows" :loading="loading" :bordered="false"
                  :scroll-x="1250" />

    <!-- 新增 / 編輯 -->
    <n-modal v-model:show="show" preset="card"
             :title="editing ? t('common.edit') : `${t('common.create')} — ${t('mikrotik.title')}`"
             style="width: 620px; max-width: calc(100vw - 32px)">
      <n-form>
        <n-form-item :label="t('common.name')"><n-input v-model:value="form.name" /></n-form-item>
        <n-form-item label="API URL">
          <div style="width: 100%">
            <n-input v-model:value="form.api_url" placeholder="https://192.0.2.1" />
            <div class="mt-hint">{{ t("mikrotik.url_hint") }}</div>
          </div>
        </n-form-item>
        <n-form-item :label="t('common.username')">
          <n-input v-model:value="form.api_username" placeholder="ipam-readonly" />
        </n-form-item>
        <n-form-item :label="editing ? t('mikrotik.pw_keep') : t('common.password')">
          <n-input v-model:value="form.api_password" type="password" show-password-on="click" />
        </n-form-item>
        <div class="mt-hint" style="margin: -8px 0 12px">{{ t("mikrotik.pw_hint") }}</div>
        <n-form-item :label="t('firewall_admin.verify_tls')">
          <n-switch v-model:value="form.verify_tls" />
        </n-form-item>

        <n-form-item :label="t('mikrotik.pull_what')">
          <div style="width: 100%">
            <n-space :size="16" :wrap="true">
              <n-checkbox v-model:checked="form.sync_dhcp">
                {{ t("pfsense_admin.dhcp_leases") }}
              </n-checkbox>
              <n-checkbox v-model:checked="form.sync_dhcp_ranges">
                {{ t("mikrotik.dhcp_ranges") }}
              </n-checkbox>
              <n-checkbox v-model:checked="form.sync_firewall">
                {{ t("mikrotik.rules") }}
              </n-checkbox>
              <n-checkbox v-model:checked="form.sync_nat">NAT</n-checkbox>
              <n-checkbox v-model:checked="form.sync_address_lists">
                {{ t("mikrotik.address_lists") }}
              </n-checkbox>
              <n-checkbox v-model:checked="form.sync_vpn">VPN</n-checkbox>
              <n-checkbox v-model:checked="form.sync_arp">ARP</n-checkbox>
              <n-checkbox v-model:checked="form.sync_interfaces">
                {{ t("mikrotik.interfaces") }}
              </n-checkbox>
              <n-checkbox v-model:checked="form.sync_neighbors">
                {{ t("mikrotik.neighbors") }}
              </n-checkbox>
              <n-checkbox v-model:checked="form.sync_fdb">FDB</n-checkbox>
            </n-space>
            <div class="mt-hint">{{ t("mikrotik.heavy_hint") }}</div>
          </div>
        </n-form-item>
        <n-form-item :label="t('mikrotik.device')">
          <div style="width: 100%">
            <n-select v-model:value="form.device_id" :options="deviceOptions" filterable remote
                      clearable :loading="deviceSearching" :placeholder="t('mikrotik.device_auto')"
                      @search="searchDevices" />
            <div class="mt-hint">{{ t("mikrotik.device_hint") }}</div>
          </div>
        </n-form-item>

        <n-divider style="margin: 4px 0 14px">{{ t("mikrotik.guard_title") }}</n-divider>
        <n-alert type="warning" :bordered="false" style="margin-bottom: 12px">
          {{ t("mikrotik.guard_intro") }}
        </n-alert>
        <n-form-item :label="t('mikrotik.cpu_limit')">
          <div style="width: 100%">
            <n-input-number v-model:value="form.cpu_load_limit" :min="0" :max="100"
                            style="width: 140px" />
            <div class="mt-hint">{{ t("mikrotik.cpu_limit_hint") }}</div>
          </div>
        </n-form-item>
        <n-form-item :label="t('mikrotik.section_delay')">
          <div style="width: 100%">
            <n-input-number v-model:value="form.section_delay_ms" :min="0" :max="10000"
                            style="width: 140px" />
            <div class="mt-hint">{{ t("mikrotik.section_delay_hint") }}</div>
          </div>
        </n-form-item>
        <n-form-item :label="t('mikrotik.max_response')">
          <div style="width: 100%">
            <n-input-number v-model:value="form.max_response_mb" :min="1" :max="256"
                            style="width: 140px" />
            <div class="mt-hint">{{ t("mikrotik.max_response_hint") }}</div>
          </div>
        </n-form-item>
        <n-form-item :label="t('adguard_admin.sync_interval')">
          <div style="width: 100%">
            <n-input-number v-model:value="form.sync_interval_seconds" :min="60" :max="86400"
                            style="width: 140px" />
            <div class="mt-hint">{{ t("mikrotik.interval_hint") }}</div>
          </div>
        </n-form-item>
        <n-divider style="margin: 4px 0 14px" />

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

    <!-- 連線診斷：逐端點的列數與耗時，才知道哪些區段該開 -->
    <n-modal v-model:show="diagOpen" preset="card"
             :title="`${t('mikrotik.diagnose')} — ${diagFor}`"
             style="width: 620px; max-width: calc(100vw - 32px)">
      <template v-if="diag">
        <n-space vertical :size="10">
          <div>
            <strong>{{ t("mikrotik.model") }}：</strong>
            {{ [diag.board_name, diag.version && `RouterOS ${diag.version}`]
              .filter(Boolean).join(" ") || "—" }}
            <span v-if="diag.identity"> · {{ diag.identity }}</span>
          </div>
          <div>
            <strong>CPU：</strong>{{ diag.cpu_load ?? "—" }}%
            <span v-if="diag.cpu_load_after != null"> → {{ diag.cpu_load_after }}%</span>
            <span v-if="diag.uptime" class="diag-note"> · uptime {{ diag.uptime }}</span>
          </div>
          <n-alert :type="diag.ok_count === diag.checks.length ? 'success' : 'warning'"
                   :bordered="false">
            {{ t("mikrotik.diag_summary", { ok: diag.ok_count, total: diag.checks.length }) }}
          </n-alert>
          <div class="mt-hint">{{ t("mikrotik.diag_hint") }}</div>
          <div v-for="c in diag.checks" :key="c.endpoint" class="diag-row">
            <n-tag :type="c.ok ? (c.absent ? 'default' : 'success') : 'error'" size="small"
                   :bordered="false">
              {{ c.ok ? (c.absent ? "—" : "OK") : "ERR" }}
            </n-tag>
            <code>{{ c.endpoint }}</code>
            <span v-if="c.absent" class="diag-note">{{ t("mikrotik.diag_absent") }}</span>
            <span v-else-if="c.ok" class="diag-note">
              {{ t("mikrotik.diag_rows", { n: c.rows ?? 0, s: c.seconds ?? 0 }) }}
            </span>
            <span v-else class="diag-note">{{ c.error }}</span>
          </div>
        </n-space>
      </template>
    </n-modal>
  </n-card>
</template>

<style scoped>
.diag-row { display: flex; align-items: center; gap: 8px; font-size: 13px; flex-wrap: wrap; }
.diag-note { opacity: .7; }
.mt-hint { font-size: 11px; opacity: .7; margin-top: 4px; }
</style>
