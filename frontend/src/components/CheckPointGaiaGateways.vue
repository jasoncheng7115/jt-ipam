<script setup lang="ts">
/**
 * Check Point「閘道」頁籤：第一階段同步回來的閘道，加上第二階段的 Gaia API 連線（使用者 2026-10-08：
 * 要 DHCP 租約、ARP 表之類）。一列一台閘道；沒有出現在管理伺服器清單裡的閘道可以手動新增。
 *
 * - 唯讀（預設）：DHCP 伺服器設定 → 發放範圍與發給用戶端的閘道/DNS
 * - 選用：允許執行寫死的讀取指令（ARP 表、租約檔）—— Gaia API 沒有讀這些的指令，要能執行指令的帳號
 */
import { computed, h, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { trackTask } from "@/composables/useTaskTracker";
import {
  NAlert, NButton, NCheckbox, NDataTable, NForm, NFormItem, NIcon, NInput, NInputNumber, NModal, NPopconfirm,
  NSelect, NSpace, NSwitch, NTag, NTooltip, useMessage, type DataTableColumns,
} from "naive-ui";
import { fmtDateTime } from "@/utils/datetime";
import { apiErrMsg } from "@/api/client";
import type { CheckPointGateway } from "@/api/checkpoint";
import {
  createGaiaTarget, deleteGaiaTarget, listGaiaDhcp, listGaiaTargets, syncGaiaTarget, testGaiaTarget, updateGaiaTarget,
  type GaiaDhcpSubnet, type GaiaTarget, type GaiaTargetWrite,
} from "@/api/checkpointGaia";
import {
  CancelIcon, DeleteIcon, EditIcon, InfoIcon, ListIcon, PlusIcon, SaveIcon, SyncIcon, TestIcon, WarnIcon,
} from "@/icons";

const props = defineProps<{
  serverId: string | null;
  gateways: CheckPointGateway[];
  loading: boolean;
  subnetOptions: { label: string; value: string }[];
}>();

const { t } = useI18n();
const msg = useMessage();

const targets = ref<GaiaTarget[]>([]);
const tLoading = ref(false);
async function loadTargets() {
  if (!props.serverId) { targets.value = []; return; }
  tLoading.value = true;
  try { targets.value = await listGaiaTargets(props.serverId); }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { tLoading.value = false; }
}
watch(() => [props.serverId, props.gateways], () => { void loadTargets(); }, { immediate: true });

interface Row { key: string; gw: CheckPointGateway | null; target: GaiaTarget | null }
const rows = computed<Row[]>(() => {
  const used = new Set<string>();
  const out: Row[] = props.gateways.map((g) => {
    const tg = targets.value.find((x) => x.gateway_uid === g.uid && (x.domain || "") === (g.domain || "")) ?? null;
    if (tg) used.add(tg.id);
    return { key: `gw:${g.domain}:${g.uid}`, gw: g, target: tg };
  });
  // 管理伺服器清單裡沒有的閘道（手動新增，或那台閘道已經不在清單上）：照樣列出來，設定才找得到
  for (const tg of targets.value) {
    if (!used.has(tg.id)) out.push({ key: `t:${tg.id}`, gw: null, target: tg });
  }
  return out;
});

function iconAction(icon: any, label: string, onClick: () => void, type?: any, testid?: string) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type, "aria-label": label, "data-testid": testid,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}

function gaiaTag(tg: GaiaTarget | null) {
  if (!tg) return h(NTag, { size: "small", bordered: false }, () => t("checkpoint.gaia_none"));
  if (!tg.enabled) return h(NTag, { size: "small", bordered: false }, () => t("common.disabled"));
  // 標籤照實際能做到的：開了讀取指令、但帳號沒權限執行，實際上就是唯讀
  const sk = tg.last_summary?.skipped ?? {};
  const scripts = tg.allow_scripts && sk.arp !== "no_permission" && sk.leases !== "no_permission";
  const tag = h(NTag, { size: "small", type: scripts ? "warning" : "info", bordered: false },
    () => scripts ? t("checkpoint.gaia_with_scripts") : t("checkpoint.gaia_read_only"));
  // 略過的原因放在標籤旁邊（摘要欄在窄螢幕要橫向捲動才看得到）
  const skippedText = skippedParts(tg).join("；");
  if (!tg.last_error && !skippedText) return tag;
  return h(NSpace, { size: 4, wrapItem: false, align: "center" }, () => [tag,
    skippedText ? h(NTooltip, null, {
      trigger: () => h(NIcon, { depth: 3, "data-testid": "cpg-skipped" }, () => h(InfoIcon)), default: () => skippedText }) : null,
    tg.last_error ? h(NTooltip, null, {
      trigger: () => h(NIcon, { color: "#d03050" }, () => h(WarnIcon)), default: () => tg.last_error }) : null]);
}

/** 略過的項目：同一個原因合併成一句（沒權限時 ARP 與租約一起略過） */
function skippedParts(tg: GaiaTarget | null): string[] {
  const byReason = new Map<string, string[]>();
  for (const [what, why] of Object.entries(tg?.last_summary?.skipped ?? {})) {
    if (!why) continue;
    byReason.set(why, [...(byReason.get(why) ?? []), t(`checkpoint.gaia_sync_${what}`)]);
  }
  return [...byReason].map(([why, what]) =>
    t("checkpoint.gaia_skipped", { what: what.join(t("checkpoint.gaia_and")), why: t(`checkpoint.gaia_skip_${why}`) }));
}

function summaryText(tg: GaiaTarget | null): string {
  const s = tg?.last_summary;
  if (!s) return "—";
  const parts: string[] = [];
  if (s.api_version) parts.push(`Gaia API ${s.api_version}`);
  if (s.dhcp_subnets != null) parts.push(t("checkpoint.gaia_n_subnets", { n: s.dhcp_subnets }));
  if (s.pools != null) parts.push(t("checkpoint.gaia_n_pools", { n: s.pools }));
  if (s.arp_rows != null) parts.push(t("checkpoint.gaia_n_arp", { n: s.arp_rows }));
  if (s.leases != null) parts.push(t("checkpoint.gaia_n_leases", { n: s.leases }));
  parts.push(...skippedParts(tg));
  return parts.join(" · ") || "—";
}

const cols = computed<DataTableColumns<Row>>(() => [
  { title: t("checkpoint.domain"), key: "domain", width: 110, render: (r) => r.gw?.domain || r.target?.domain || "—" },
  { title: t("cols.name"), key: "name", minWidth: 160, ellipsis: { tooltip: true },
    render: (r) => r.gw?.name ?? r.target?.name ?? "—" },
  { title: t("cols.type"), key: "type", width: 170, ellipsis: { tooltip: true },
    render: (r) => r.gw ? (r.gw.type || "—") : t("checkpoint.gaia_manual") },
  { title: t("checkpoint.address"), key: "ipv4_address", width: 140, render: (r) => r.gw?.ipv4_address || "—" },
  { title: t("checkpoint.version"), key: "version", width: 100, render: (r) => r.gw?.version || "—" },
  { title: "Gaia", key: "gaia", width: 150, render: (r) => gaiaTag(r.target) },
  { title: t("checkpoint.last_seen"), key: "summary", minWidth: 240, ellipsis: { tooltip: true },
    render: (r) => summaryText(r.target) },
  { title: t("cols.last_sync"), key: "last_sync_at", width: 170, render: (r) => fmtDateTime(r.target?.last_sync_at) },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 210, fixed: "right",
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => r.target ? [
      iconAction(EditIcon, t("checkpoint.gaia_edit"), () => openEdit(r), "success", "cpg-edit"),
      iconAction(TestIcon, t("common.test"), () => test(r.target!), undefined, "cpg-test"),
      iconAction(SyncIcon, t("common.pull"), () => sync(r.target!), "primary", "cpg-sync"),
      iconAction(ListIcon, t("checkpoint.gaia_dhcp"), () => openDhcp(r.target!), undefined, "cpg-dhcp"),
      h(NPopconfirm, { onPositiveClick: () => del(r.target!) }, {
        trigger: () => iconAction(DeleteIcon, t("checkpoint.gaia_delete"), () => {}, "error", "cpg-delete"),
        default: () => t("checkpoint.gaia_delete_confirm"),
      }),
    ] : [iconAction(PlusIcon, t("checkpoint.gaia_setup"), () => openEdit(r), "success", "cpg-setup")]),
  },
]);

// ── 設定對話框 ──
const show = ref(false);
const editing = ref<GaiaTarget | null>(null);
const editingGw = ref<CheckPointGateway | null>(null);
function blank(gw: CheckPointGateway | null) {
  const addr = gw?.ipv4_address;
  return {
    name: gw?.name ?? "", gaia_url: addr ? `https://${addr}/gaia_api` : "", username: "", secret: "",
    verify_tls: true, enabled: true, sync_interval_seconds: 300, sync_dhcp: true, allow_scripts: true,
    sync_arp: true, sync_leases: true, scope_subnet_ids: [] as string[], description: "",
  };
}
const form = ref(blank(null));
function openEdit(r: Row) {
  editing.value = r.target;
  editingGw.value = r.gw;
  if (r.target) {
    const x = r.target;
    form.value = {
      name: x.name, gaia_url: x.gaia_url, username: x.username, secret: "", verify_tls: x.verify_tls,
      enabled: x.enabled, sync_interval_seconds: x.sync_interval_seconds, sync_dhcp: x.sync_dhcp,
      allow_scripts: x.allow_scripts, sync_arp: x.sync_arp, sync_leases: x.sync_leases,
      scope_subnet_ids: x.scope_subnet_ids ?? [], description: x.description ?? "",
    };
  } else {
    form.value = blank(r.gw);
  }
  show.value = true;
}
function openManual() { openEdit({ key: "new", gw: null, target: null }); }
defineExpose({ openManual });

async function submit() {
  const f = form.value;
  if (!props.serverId) return;
  if (!editing.value && !f.secret.trim()) { msg.warning(t("checkpoint.gaia_password_required")); return; }
  const payload: GaiaTargetWrite = {
    name: f.name.trim(), gaia_url: f.gaia_url.trim(), username: f.username.trim(), verify_tls: f.verify_tls,
    enabled: f.enabled, sync_interval_seconds: f.sync_interval_seconds, sync_dhcp: f.sync_dhcp,
    allow_scripts: f.allow_scripts, sync_arp: f.sync_arp, sync_leases: f.sync_leases,
    scope_subnet_ids: f.scope_subnet_ids, description: f.description || undefined,
  };
  if (f.secret.trim()) payload.secret = f.secret.trim();
  try {
    if (editing.value) await updateGaiaTarget(editing.value.id, payload);
    else await createGaiaTarget(props.serverId, {
      ...payload, gateway_uid: editingGw.value?.uid ?? null, domain: editingGw.value?.domain ?? "" });
    show.value = false;
    msg.success(t("common.ok"));
    await loadTargets();
  } catch (e) { msg.error(apiErrMsg(e)); }
}

async function test(tg: GaiaTarget) {
  try {
    const r = await testGaiaTarget(tg.id);
    msg.success(t("checkpoint.gaia_test_ok", {
      version: r.version || "?", subnets: r.dhcp_subnets ?? "?", scripts: t(`checkpoint.gaia_scripts_${r.scripts}`),
    }), { duration: 8000 });
    const errs = Object.entries(r.errors ?? {}).map(([k, v]) => `${k}: ${v}`);
    if (errs.length) msg.warning(t("checkpoint.test_partial", { detail: errs.join("；") }), { duration: 12_000, closable: true });
  } catch (e) { msg.error(apiErrMsg(e), { duration: 10_000, closable: true }); }
}

async function sync(tg: GaiaTarget) {
  try {
    const r = await syncGaiaTarget(tg.id);
    trackTask(r.task_id, { onDone: () => void loadTargets() });
  } catch (e) { msg.error(apiErrMsg(e)); }
}

async function del(tg: GaiaTarget) {
  try { await deleteGaiaTarget(tg.id); await loadTargets(); }
  catch (e) { msg.error(apiErrMsg(e)); }
}

// ── DHCP 子網路 ──
const dhcpShow = ref(false);
const dhcpFor = ref<GaiaTarget | null>(null);
const dhcpRows = ref<GaiaDhcpSubnet[]>([]);
const dhcpLoading = ref(false);
async function openDhcp(tg: GaiaTarget) {
  dhcpFor.value = tg;
  dhcpShow.value = true;
  dhcpLoading.value = true;
  try { dhcpRows.value = await listGaiaDhcp(tg.id); }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { dhcpLoading.value = false; }
}
function poolText(p: GaiaDhcpSubnet["pools"][number]) {
  const base = `${p.start} – ${p.end}`;
  if (p.include === "exclude") return `${t("checkpoint.gaia_pool_exclude")} ${base}`;
  return p.enabled ? base : `${base} (${t("common.disabled")})`;
}
const dhcpCols = computed<DataTableColumns<GaiaDhcpSubnet>>(() => [
  { title: t("checkpoint.gaia_subnet"), key: "subnet_cidr", width: 160 },
  { title: t("cols.status"), key: "enabled", width: 90,
    render: (r) => h(NTag, { size: "small", type: r.enabled ? "success" : "default", bordered: false },
      () => r.enabled ? t("common.enabled") : t("common.disabled")) },
  { title: t("checkpoint.gaia_pools"), key: "pools", minWidth: 240,
    render: (r) => r.pools.length ? h("div", null, r.pools.map((p) => h("div", null, poolText(p)))) : "—" },
  { title: t("checkpoint.gaia_router"), key: "default_gateway", width: 140, render: (r) => r.default_gateway || "—" },
  { title: "DNS", key: "dns_servers", minWidth: 160, render: (r) => r.dns_servers.join(", ") || "—" },
  { title: t("checkpoint.gaia_domain_name"), key: "domain_name", width: 160, ellipsis: { tooltip: true },
    render: (r) => r.domain_name || "—" },
  { title: t("cols.last_sync"), key: "synced_at", width: 170, render: (r) => fmtDateTime(r.synced_at) },
]);
</script>

<template>
  <div>
    <n-data-table :columns="cols" :data="rows" :row-key="(r: Row) => r.key" :loading="loading || tLoading"
                  :bordered="false" :scroll-x="1500" data-testid="cp-gateways" />

    <n-modal v-model:show="show" preset="card" style="width: min(640px, 100%)"
             :title="editing ? `${t('checkpoint.gaia_edit')} — ${editing.name}` : t('checkpoint.gaia_setup')">
      <n-alert type="default" :bordered="false" :show-icon="true" style="margin-bottom: 12px">
        {{ t("checkpoint.gaia_setup_hint") }}
      </n-alert>
      <n-form>
        <n-form-item :label="t('common.name')">
          <n-input v-model:value="form.name" data-testid="cpg-name" />
        </n-form-item>
        <n-form-item :label="t('checkpoint.gaia_url')">
          <div style="width: 100%">
            <n-input v-model:value="form.gaia_url" placeholder="https://gw.example.test/gaia_api" data-testid="cpg-url" />
            <div class="form-hint">{{ t("checkpoint.gaia_url_hint") }}</div>
          </div>
        </n-form-item>
        <n-form-item :label="t('checkpoint.username')">
          <n-input v-model:value="form.username" autocomplete="off" data-testid="cpg-username" />
        </n-form-item>
        <n-form-item :label="editing?.has_secret ? t('checkpoint.gaia_password_keep') : t('checkpoint.password')">
          <n-input v-model:value="form.secret" type="password" show-password-on="click" autocomplete="new-password"
                   data-testid="cpg-secret" />
        </n-form-item>
        <n-form-item :label="t('firewall_admin.verify_tls')">
          <div style="width: 100%">
            <n-switch v-model:value="form.verify_tls" />
            <div class="form-hint">{{ t("checkpoint.gaia_tls_hint") }}</div>
          </div>
        </n-form-item>
        <n-form-item :label="t('checkpoint.pull_what')">
          <n-space vertical :size="6" style="width: 100%">
            <n-checkbox v-model:checked="form.sync_dhcp">{{ t("checkpoint.gaia_sync_dhcp") }}</n-checkbox>
            <n-checkbox v-model:checked="form.allow_scripts" data-testid="cpg-allow-scripts">
              {{ t("checkpoint.gaia_allow_scripts") }}
            </n-checkbox>
            <n-alert v-if="form.allow_scripts" type="info" :bordered="false" :show-icon="true">
              {{ t("checkpoint.gaia_scripts_warning") }}
            </n-alert>
            <n-space :size="20" style="padding-left: 24px">
              <n-checkbox v-model:checked="form.sync_arp" :disabled="!form.allow_scripts">
                {{ t("checkpoint.gaia_sync_arp") }}
              </n-checkbox>
              <n-checkbox v-model:checked="form.sync_leases" :disabled="!form.allow_scripts">
                {{ t("checkpoint.gaia_sync_leases") }}
              </n-checkbox>
            </n-space>
          </n-space>
        </n-form-item>
        <n-form-item :label="t('adguard_admin.sync_interval')">
          <n-input-number v-model:value="form.sync_interval_seconds" :min="60" :max="86400" />
        </n-form-item>
        <n-form-item :label="t('common.enable')">
          <n-switch v-model:value="form.enabled" />
        </n-form-item>
        <n-form-item :label="t('adguard_admin.scope_subnets')">
          <div style="width: 100%">
            <n-select v-model:value="form.scope_subnet_ids" :options="subnetOptions" multiple filterable clearable
                      :placeholder="t('checkpoint.gaia_scope_inherit')" />
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
        <n-button type="primary" data-testid="cpg-save" @click="submit">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </n-space>
    </n-modal>

    <n-modal v-model:show="dhcpShow" preset="card" style="width: min(1100px, 100%)"
             :title="`${t('checkpoint.gaia_dhcp')} — ${dhcpFor?.name ?? ''}`">
      <div class="form-hint" style="margin-bottom: 8px">{{ t("checkpoint.gaia_dhcp_hint") }}</div>
      <n-data-table :columns="dhcpCols" :data="dhcpRows" :loading="dhcpLoading" :bordered="false" :scroll-x="1100"
                    data-testid="cpg-dhcp-list" />
    </n-modal>
  </div>
</template>

<style scoped>
.form-hint { font-size: 11.5px; opacity: .7; margin-top: 4px; line-height: 1.5; }
</style>
