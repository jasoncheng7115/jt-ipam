<script setup lang="ts">
/**
 * 裝置的電源（交接包 physical-v1.2 設計圖 02）：整機規劃負載在上、電源埠與分攤比例在下，右邊是計算預覽與接線檢查。
 *
 * - 整台裝置的規劃負載只填一次；PSU 額定容量不是耗電量，也不加總
 * - 分攤比例由管理員設定：有「平均分攤」按鈕，按了會先說明這是假設；不因為有兩顆就自動存 50/50
 * - 未知可以存：不強迫填猜的值，接線也照常可以維護
 * - 規劃與分攤一次存（同一個交易）；別人剛改過會 409，請重新整理再存
 */
import { computed, h, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  NAlert, NButton, NCard, NDataTable, NForm, NFormItem, NGrid, NGridItem, NIcon, NInput, NInputNumber, NModal,
  NPopconfirm, NSelect, NSpace, NSpin, NSwitch, NTag, NTooltip, useDialog, useMessage, type DataTableColumns,
} from "naive-ui";
import {
  Physical, createDevicePowerPort, deleteDevicePowerPort, getPowerProfile, listDevicePowerPorts, putPowerProfile,
  updateDevicePowerPort,
  type FailoverCapability, type PowerBasis, type PowerProfile, type PowerProfilePort, type SupplyMode,
} from "@/api/phase3";
import { CancelIcon, CheckIcon, DeleteIcon, EditIcon, PlusIcon, PowerIcon, RefreshIcon, SaveIcon } from "@/icons";
import { Percentage as SplitIcon } from "@iconoir/vue";
import { apiErrMsg } from "@/api/client";
import { autoSort } from "@/composables/useTableSort";

const props = defineProps<{ deviceId: string; deviceName: string; admin: boolean }>();
const { t } = useI18n();
const msg = useMessage();
const dialog = useDialog();

const profile = ref<PowerProfile | null>(null);
const loading = ref(false);
const saving = ref(false);
// 表單（存檔前的草稿）；分攤比例另外放，才能跟埠的清單分開編輯
const form = ref({
  planning_input_w: null as number | null, value_basis: null as PowerBasis | null, notes: "",
  supply_mode: "unknown" as SupplyMode, failover_capability: "unknown" as FailoverCapability,
  primary_port_id: null as string | null, include_in_planning: true, exclude_reason: "",
});
const shares = ref<Record<string, number | null>>({});
// 跟伺服器上的值比：有改才讓「還原」可以按
const saved = ref("");
const snapshot = () => JSON.stringify([form.value, shares.value]);
const dirty = computed(() => !!profile.value && snapshot() !== saved.value);

function fill(p: PowerProfile) {
  profile.value = p;
  form.value = {
    planning_input_w: p.planning_input_w, value_basis: p.value_basis, notes: p.notes ?? "",
    supply_mode: p.supply_mode, failover_capability: p.failover_capability, primary_port_id: p.primary_port_id,
    include_in_planning: p.include_in_planning, exclude_reason: p.exclude_reason ?? "",
  };
  shares.value = Object.fromEntries(p.ports.map((x) => [x.id, x.normal_share_pct]));
  saved.value = snapshot();
}
async function refresh() {
  loading.value = true;
  try { fill(await getPowerProfile(props.deviceId)); }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}

const ports = computed<PowerProfilePort[]>(() => profile.value?.ports ?? []);
const shareTotal = computed(() => ports.value.reduce((s, p) => s + (shares.value[p.id] ?? 0), 0));
const sharesSet = computed(() => ports.value.length > 0 && ports.value.every((p) => shares.value[p.id] != null));
// 畫面上即時試算（跟後端同一套：一顆電源就是 100%；兩顆以上要剛好 100%）
const draftAlloc = computed<Record<string, number | null>>(() => {
  const load = form.value.planning_input_w;
  const ps = ports.value;
  if (load == null || !ps.length) return {};
  if (ps.length === 1) return { [ps[0].id]: load };
  if (!sharesSet.value || Math.abs(shareTotal.value - 100) > 0.001) return {};
  let given = 0;
  return Object.fromEntries(ps.map((p, i) => {
    const w = i === ps.length - 1 ? Math.round((load - given) * 100) / 100 : Math.round(load * (shares.value[p.id] ?? 0)) / 100;
    given += w;
    return [p.id, w];
  }));
});
const allocTotal = computed(() => Object.values(draftAlloc.value).reduce<number>((sum, w) => sum + (w ?? 0), 0));
const preview = computed(() => {
  const by: Record<string, { feed: string; watts: number }> = {};
  let unassigned = 0;
  for (const p of ports.value) {
    const w = draftAlloc.value[p.id];
    if (w == null) continue;
    if (p.feed_id) { by[p.feed_id] ??= { feed: p.feed ?? "", watts: 0 }; by[p.feed_id].watts += w; }
    else unassigned += w;
  }
  const load = form.value.planning_input_w;
  const unknown = load != null && ports.value.length > 1 && !Object.keys(draftAlloc.value).length ? load : 0;
  return { feeds: Object.values(by), unassigned: load != null && !ports.value.length ? load : unassigned, unknown };
});

const W = (n: number | null | undefined) => (n == null ? "—" : `${n.toLocaleString()} W`);
const BASIS: PowerBasis[] = ["manual_estimate", "vendor_reference", "measurement_reference"];
const MODES: SupplyMode[] = ["unknown", "single", "shared", "active_standby", "custom"];
const FAILOVER: FailoverCapability[] = ["unknown", "confirmed_full_load", "insufficient"];
const basisOpts = computed(() => BASIS.map((v) => ({ label: t(`power_profile.basis_${v}`), value: v })));
const modeOpts = computed(() => MODES.map((v) => ({ label: t(`power_profile.mode_${v}`), value: v })));
const failOpts = computed(() => FAILOVER.map((v) => ({ label: t(`power_profile.failover_${v}`), value: v })));
const portOpts = computed(() => ports.value.map((p) => ({ label: p.name, value: p.id })));

function evenSplit() {
  const n = ports.value.length;
  if (n < 2) return;
  dialog.info({
    title: t("power_profile.even_title"), content: t("power_profile.even_body", { n }),
    positiveText: t("power_profile.even_ok"), negativeText: t("common.cancel"),
    onPositiveClick: () => {
      const base = Math.floor((100 / n) * 100) / 100;
      ports.value.forEach((p, i) => { shares.value[p.id] = i === n - 1 ? Math.round((100 - base * (n - 1)) * 100) / 100 : base; });
    },
  });
}

async function save() {
  if (!profile.value) return;
  if (!form.value.include_in_planning && !form.value.exclude_reason.trim()) {
    msg.error(t("errors.power_exclude_reason_required")); return;
  }
  saving.value = true;
  try {
    fill(await putPowerProfile(props.deviceId, {
      revision: profile.value.revision,
      planning_input_w: form.value.planning_input_w, value_basis: form.value.value_basis,
      notes: form.value.notes.trim() || null, supply_mode: form.value.supply_mode,
      failover_capability: form.value.failover_capability,
      primary_port_id: form.value.supply_mode === "active_standby" ? form.value.primary_port_id : null,
      include_in_planning: form.value.include_in_planning,
      exclude_reason: form.value.include_in_planning ? null : form.value.exclude_reason.trim(),
      ports: ports.value.map((p) => ({ id: p.id, normal_share_pct: shares.value[p.id] ?? null })),
    }));
    msg.success(t("common.saved"));
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { saving.value = false; }
}

async function confirmRating(p: PowerProfilePort) {
  try { await updateDevicePowerPort(p.id, { rating_confirmed: true }); await refresh(); }
  catch (e) { msg.error(apiErrMsg(e)); }
}

// ── 電源埠新增 / 編輯 ──
const outlets = ref<{ id: string; label: string; path?: string | null; occupied_by?: string[] | null }[]>([]);
async function loadOutlets() {
  try { outlets.value = (await Physical.outlets()) as any; } catch { /* 讀不到就只能不選插座 */ }
}
const showEdit = ref(false);
const editId = ref<string | null>(null);
const pform = ref<{ name: string; outlet_id: string | null; max_watts: number | null; description: string }>(
  { name: "", outlet_id: null, max_watts: null, description: "" });
// 插座選單：完整路徑＋占用（被別顆電源用掉的反灰；後端也會擋）
const outletOpts = computed(() => outlets.value.map((o) => {
  const others = (o.occupied_by ?? []).filter((x) => !(editId.value && x === `${props.deviceName} ${pform.value.name}`));
  const taken = others.length > 0 && o.id !== currentOutlet.value;
  return { label: `${o.path || o.label}${others.length ? `（${t("power_profile.outlet_used_by", { who: others.join("、") })}）` : ""}`,
           value: o.id, disabled: taken };
}));
const currentOutlet = ref<string | null>(null);
function openCreate() {
  editId.value = null; currentOutlet.value = null;
  pform.value = { name: "", outlet_id: null, max_watts: null, description: "" };
  showEdit.value = true; void loadOutlets();
}
async function openEdit(p: PowerProfilePort) {
  editId.value = p.id; currentOutlet.value = p.outlet_id;
  let full: { description: string | null } | undefined;
  try { full = (await listDevicePowerPorts(props.deviceId)).find((x) => x.id === p.id); } catch { /* 只少了說明 */ }
  pform.value = { name: p.name, outlet_id: p.outlet_id, max_watts: p.max_watts, description: full?.description ?? "" };
  showEdit.value = true; void loadOutlets();
}
async function savePort() {
  if (!pform.value.name.trim()) { msg.error(t("ports.name_required")); return; }
  try {
    const body = { name: pform.value.name.trim(), outlet_id: pform.value.outlet_id, max_watts: pform.value.max_watts,
                   description: pform.value.description.trim() || null };
    if (editId.value) await updateDevicePowerPort(editId.value, body);
    else await createDevicePowerPort({ device_id: props.deviceId, ...body });
    showEdit.value = false;
    await refresh();
  } catch (e) { msg.error(apiErrMsg(e)); }
}
async function del(id: string) {
  try { await deleteDevicePowerPort(id); await refresh(); }
  catch (e) { msg.error(apiErrMsg(e)); }
}

const cols = computed<DataTableColumns<PowerProfilePort>>(() => autoSort<PowerProfilePort>([
  { title: t("power_ports.name"), key: "name", minWidth: 90 },
  { title: t("power_profile.rated"), key: "max_watts", width: 150, render: (r: PowerProfilePort) => h(NSpace, { size: 4, align: "center", wrapItem: false }, () => [
    h("span", null, W(r.max_watts)),
    r.legacy_rating ? h(NTooltip, null, {
      trigger: () => h(NTag, { size: "tiny", type: "warning", bordered: false, "data-testid": `legacy-${r.name}` },
        () => t("power_profile.legacy")),
      default: () => t("power_profile.legacy_hint"),
    }) : null,
    r.legacy_rating && props.admin ? h(NButton, { size: "tiny", quaternary: true, type: "success",
      title: t("power_profile.legacy_confirm"), "data-testid": `confirm-${r.name}`, onClick: () => confirmRating(r) },
      { icon: () => h(NIcon, null, () => h(CheckIcon)) }) : null,
  ]) },
  { title: t("power_ports.outlet"), key: "outlet_path", minWidth: 200, ellipsis: { tooltip: true },
    render: (r: PowerProfilePort) => r.outlet_path ?? h("span", { style: "opacity:.5" }, t("power_ports.unconnected")) },
  { title: t("power_profile.share"), key: "share", width: 130, sorter: false, render: (r: PowerProfilePort) => (props.admin && ports.value.length > 1
    ? h(NInputNumber, { value: shares.value[r.id], min: 0, max: 100, precision: 2, size: "small", clearable: true,
                        style: "width:110px", "data-testid": `share-${r.name}`,
                        "onUpdate:value": (v: number | null) => { shares.value[r.id] = v; } },
        { suffix: () => "%" })
    : ports.value.length === 1 ? "100%" : (shares.value[r.id] == null ? "—" : `${shares.value[r.id]}%`)) },
  { title: t("power_profile.allocated"), key: "allocated_w", width: 110,
    render: (r: PowerProfilePort) => W(draftAlloc.value[r.id] ?? null),
    sorter: (a: PowerProfilePort, b: PowerProfilePort) => {
      const x = draftAlloc.value[a.id] ?? null, y = draftAlloc.value[b.id] ?? null;
      return x == null || y == null ? Number(x == null) - Number(y == null) : x - y;
    } },
  ...(props.admin ? [{
    title: t("common.actions"), key: "_", width: 92, className: "col-actions",
    render: (r: PowerProfilePort) => h(NSpace, { size: 2, wrapItem: false }, () => [
      h(NButton, { size: "small", quaternary: true, type: "success", "data-testid": `psu-edit-${r.name}`, onClick: () => openEdit(r) },
        { icon: () => h(NIcon, null, () => h(EditIcon)) }),
      h(NPopconfirm, { onPositiveClick: () => del(r.id) }, {
        trigger: () => h(NButton, { size: "small", quaternary: true, type: "error" },
          { icon: () => h(NIcon, null, () => h(DeleteIcon)) }),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  }] : []),
]));

watch(() => props.deviceId, () => { void refresh(); });
onMounted(() => { void refresh(); });
</script>

<template>
  <n-card size="small" data-testid="device-power">
    <template #header>
      <span style="display:inline-flex;align-items:center;gap:8px">
        <n-icon :size="18"><PowerIcon /></n-icon>
        {{ t("power_ports.title") }} ({{ ports.length }})
      </span>
    </template>
    <n-spin :show="loading">
      <n-grid :cols="3" :x-gap="14" :y-gap="14" responsive="screen" item-responsive>
        <!-- 整機規劃負載 -->
        <n-grid-item span="3 m:2">
          <div class="dp-sec">{{ t("power_profile.load_title") }}</div>
          <n-form label-placement="left" label-width="auto" :disabled="!admin" size="small">
            <n-form-item :label="t('power_profile.load')">
              <n-input-number v-model:value="form.planning_input_w" :min="0" :max="10000000" :precision="2" clearable
                              :placeholder="t('power_profile.unknown')" style="width:200px" data-testid="power-load">
                <template #suffix>W</template>
              </n-input-number>
            </n-form-item>
            <n-form-item :label="t('power_profile.basis')">
              <n-select v-model:value="form.value_basis" :options="basisOpts" clearable style="width:240px"
                        :placeholder="t('power_profile.unknown')" data-testid="power-basis" />
            </n-form-item>
            <n-form-item :label="t('power_profile.mode')">
              <n-select v-model:value="form.supply_mode" :options="modeOpts" style="width:240px" data-testid="power-mode" />
            </n-form-item>
            <n-form-item v-if="form.supply_mode === 'active_standby'" :label="t('power_profile.primary')">
              <n-select v-model:value="form.primary_port_id" :options="portOpts" clearable style="width:240px" />
            </n-form-item>
            <n-form-item :label="t('power_profile.failover')">
              <n-select v-model:value="form.failover_capability" :options="failOpts" style="width:240px" data-testid="power-failover" />
            </n-form-item>
            <n-form-item :label="t('power_profile.include')">
              <n-space vertical :size="4" style="width:100%">
                <n-switch v-model:value="form.include_in_planning" data-testid="power-include" />
                <n-input v-if="!form.include_in_planning" v-model:value="form.exclude_reason" maxlength="1024"
                         :placeholder="t('power_profile.exclude_reason_ph')" data-testid="power-exclude-reason" />
              </n-space>
            </n-form-item>
            <n-form-item :label="t('common.description')">
              <n-input v-model:value="form.notes" maxlength="2048" :placeholder="t('power_profile.notes_ph')" data-testid="power-notes" />
            </n-form-item>
          </n-form>
          <div class="hint">{{ t("power_profile.load_hint") }}</div>
        </n-grid-item>

        <!-- 計算預覽＋接線檢查 -->
        <n-grid-item span="3 m:1">
          <div class="dp-sec">{{ t("power_profile.preview") }}</div>
          <div class="dp-big" data-testid="power-preview-load">{{ form.planning_input_w == null ? t("power_profile.unknown") : W(form.planning_input_w) }}</div>
          <div v-for="f in preview.feeds" :key="f.feed" class="dp-row" data-testid="power-preview-feed">
            <span>{{ f.feed }}</span><b>{{ W(f.watts) }}</b>
          </div>
          <div v-if="preview.unassigned" class="dp-row"><span>{{ t("rack_power.unassigned_short") }}</span><b>{{ W(preview.unassigned) }}</b></div>
          <div v-if="preview.unknown" class="dp-row"><span>{{ t("power_issue.share_unknown") }}</span><b>{{ W(preview.unknown) }}</b></div>
          <div class="hint" style="margin-top:6px">{{ t("power_profile.preview_hint", { n: ports.length }) }}</div>

          <div class="dp-sec" style="margin-top:14px">{{ t("power_profile.wiring") }}</div>
          <template v-if="profile">
            <n-alert v-if="profile.wiring.distinct_feeds >= 2" type="success" :bordered="false" class="dp-alert">
              {{ t("power_profile.wiring_feeds", { n: profile.wiring.distinct_feeds }) }}
            </n-alert>
            <n-alert v-if="profile.wiring.same_feed" type="warning" :bordered="false" class="dp-alert" data-testid="power-same-feed">
              {{ t("power_profile.wiring_same_feed") }}
            </n-alert>
            <n-alert v-if="profile.wiring.unconnected.length" type="warning" :bordered="false" class="dp-alert">
              {{ t("power_profile.wiring_unconnected", { list: profile.wiring.unconnected.join("、") }) }}
            </n-alert>
            <n-alert type="info" :bordered="false" class="dp-alert">{{ t("power_profile.wiring_upstream") }}</n-alert>
          </template>
        </n-grid-item>
      </n-grid>

      <!-- 電源埠與分配 -->
      <n-space align="center" justify="space-between" style="margin:14px 0 6px">
        <div class="dp-sec" style="margin:0">{{ t("power_profile.ports_title") }}</div>
        <n-space :size="8">
          <n-button v-if="admin && ports.length > 1" size="small" data-testid="power-even" @click="evenSplit">
            <template #icon><n-icon><SplitIcon /></n-icon></template>{{ t("power_profile.even") }}
          </n-button>
          <n-button v-if="admin" size="small" type="primary" data-testid="psu-add" @click="openCreate">
            <template #icon><n-icon><PlusIcon /></n-icon></template>{{ t("power_ports.add") }}
          </n-button>
        </n-space>
      </n-space>
      <n-data-table :columns="cols" :data="ports" :bordered="false" size="small" :scroll-x="700"
                    :row-key="(r: PowerProfilePort) => r.id" data-testid="power-ports">
        <template #empty><n-space justify="center" style="opacity:.6">{{ t("power_ports.empty") }}</n-space></template>
      </n-data-table>
      <div v-if="ports.length > 1" class="dp-total" data-testid="power-share-total">
        {{ t("power_profile.total", { pct: Math.round(shareTotal * 100) / 100, w: W(allocTotal) }) }}
        <span v-if="sharesSet && Math.abs(shareTotal - 100) > 0.001" class="dp-warn">{{ t("power_profile.total_not_100") }}</span>
      </div>
      <n-alert type="info" :bordered="false" style="margin-top:8px">{{ t("power_profile.rated_note") }}</n-alert>

      <n-space v-if="admin" justify="end" style="margin-top:12px">
        <n-button size="small" :disabled="!dirty || saving" @click="refresh">
          <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("power_profile.reset") }}
        </n-button>
        <n-button size="small" type="primary" :loading="saving" :disabled="saving" data-testid="power-save" @click="save">
          <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("power_profile.save") }}
        </n-button>
      </n-space>
    </n-spin>

    <n-modal v-model:show="showEdit" preset="card" style="width: 520px; max-width: 100vw"
             :title="editId ? t('common.edit') : t('power_ports.add')">
      <n-form label-placement="top">
        <n-form-item :label="t('power_ports.name')">
          <n-input v-model:value="pform.name" placeholder="PSU1" data-testid="psu-name" />
        </n-form-item>
        <n-form-item :label="t('power_ports.outlet')">
          <n-select v-model:value="pform.outlet_id" :options="outletOpts" clearable filterable
                    :placeholder="t('power_ports.unconnected')" data-testid="psu-outlet" />
        </n-form-item>
        <n-form-item :label="t('power_profile.rated')">
          <n-space vertical :size="2">
            <n-input-number v-model:value="pform.max_watts" :min="0" clearable style="width: 160px" data-testid="psu-rated">
              <template #suffix>W</template>
            </n-input-number>
            <span class="hint">{{ t("power_profile.rated_note") }}</span>
          </n-space>
        </n-form-item>
        <n-form-item :label="t('common.description')">
          <n-input v-model:value="pform.description" type="textarea" :autosize="{ minRows: 2 }" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button size="small" @click="showEdit = false">
            <template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}
          </n-button>
          <n-button size="small" type="primary" data-testid="psu-save" @click="savePort">
            <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </n-card>
</template>

<style scoped>
.hint { font-size: 12px; opacity: .7; line-height: 1.5; }
.dp-sec { font-weight: 600; margin-bottom: 8px; }
.dp-big { font-size: 26px; font-weight: 700; margin-bottom: 8px; font-variant-numeric: tabular-nums; }
.dp-row { display: flex; justify-content: space-between; padding: 6px 10px; margin-bottom: 4px; border-radius: 6px;
          background: rgba(127, 127, 127, .08); font-variant-numeric: tabular-nums; }
.dp-alert { margin-bottom: 6px; }
.dp-total { text-align: right; margin-top: 6px; font-variant-numeric: tabular-nums; }
.dp-warn { color: #d03050; margin-left: 8px; }
</style>
