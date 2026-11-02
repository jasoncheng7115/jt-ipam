<template>
  <n-modal :show="show" preset="card" :title="t('change_impact.new_plan')" style="width: min(560px, 96vw)"
           :mask-closable="!busy" data-testid="cip-wizard" @update:show="(v: boolean) => !v && close()">
    <n-form label-placement="top" :disabled="busy">
      <n-form-item v-if="!presetScenario" :label="t('change_impact.f_scenario')">
        <n-radio-group v-model:value="scenario" name="scenario">
          <n-radio-button v-for="sv in ALL_SCENARIOS" :key="sv" :value="sv" :data-testid="`cip-wiz-scn-${sv}`">
            {{ t(`change_impact.scenario.${sv}`) }}
          </n-radio-button>
        </n-radio-group>
      </n-form-item>
      <!-- 範圍：先選子網路（可用單位、區段縮小），再輸入 IP。只輸入 IP 的話，重疊網段可能選錯（使用者 2026-10-07）。
           清單只有可以修改的子網路，單位與區段選項也從那些子網路推出來，看不到的不會出現 -->
      <n-form-item v-if="scenario === 'ip_renumber' && !presetTargetId" :label="t('change_impact.f_subnet')">
        <div class="cip-wiz-scope">
          <n-alert v-if="scopeLoaded && noWritable" type="warning" :bordered="false" data-testid="cip-wiz-no-subnets">
            {{ t("change_impact.no_writable_subnets") }}
          </n-alert>
          <template v-else>
            <div v-if="scope.customers.length || scope.sections.length > 1" class="cip-wiz-filters">
              <n-select v-if="scope.customers.length" v-model:value="customerId" clearable size="small"
                        :options="scope.customers.map((c) => ({ label: c.name, value: c.id }))"
                        :placeholder="t('change_impact.f_customer_ph')" data-testid="cip-wiz-customer"
                        @update:value="onFilter" />
              <n-select v-if="scope.sections.length > 1" v-model:value="sectionId" clearable size="small"
                        :options="scope.sections.map((c) => ({ label: c.name, value: c.id }))"
                        :placeholder="t('change_impact.f_section_ph')" data-testid="cip-wiz-section"
                        @update:value="onFilter" />
            </div>
            <n-select v-model:value="subnetId" filterable remote clearable :loading="snLoading"
                      :options="subnetOptions" :placeholder="t('change_impact.f_subnet_ph')"
                      data-testid="cip-wiz-subnet-pick" @search="onSubnetSearch" @update:value="onSubnet" />
            <div v-if="scope.truncated" class="cip-wiz-hint">{{ t("change_impact.subnets_truncated", { n: scope.subnets.length }) }}</div>
          </template>
        </div>
      </n-form-item>
      <!-- 目標：IP 改址選一筆 IP 記錄（同一個位址好幾筆時要選，不猜）；除役選一台裝置 -->
      <n-form-item :label="targetLabel">
        <span v-if="presetTargetId" class="cip-wiz-target" data-testid="cip-wiz-target">{{ presetLabel }}</span>
        <template v-else-if="scenario === 'ip_renumber'">
          <n-input v-model:value="oldIp" :disabled="!subnetId"
                   :placeholder="subnet ? t('change_impact.f_old_ip_in', { cidr: subnet.cidr }) : t('change_impact.f_pick_subnet_first')"
                   data-testid="cip-wiz-old-ip" @blur="lookupOld" />
          <n-select v-if="oldCandidates.length > 1" v-model:value="targetId" style="margin-top: 6px"
                    :options="oldCandidates.map((c) => ({ label: `${c.ip} (${c.subnet})${c.hostname ? ' · ' + c.hostname : ''}`, value: c.id }))"
                    :placeholder="t('change_impact.f_pick_record')" />
        </template>
        <n-select v-else v-model:value="targetId" filterable remote clearable :loading="devLoading"
                  :options="devOptions" :placeholder="t('change_impact.f_device_ph')" @search="searchDevices" />
      </n-form-item>
      <!-- M2：一起維護／停機的其他裝置（同一個情境一次算，不是把單台結果相加）與節點停機的情境模式 -->
      <n-form-item v-if="isM2" :label="t('change_impact.f_also_down')">
        <n-select v-model:value="alsoDown" multiple filterable remote clearable :loading="alsoLoading"
                  :options="alsoOptions" :max-tag-count="6"
                  :placeholder="t('change_impact.f_also_down_ph')" data-testid="cip-wiz-also-down"
                  @search="searchAlso" />
      </n-form-item>
      <n-form-item v-if="scenario === 'node_downtime'" :label="t('change_impact.f_mode')">
        <n-radio-group v-model:value="mode" name="mode" data-testid="cip-wiz-mode">
          <n-space vertical :size="4">
            <n-radio v-for="m in ['direct', 'migrate_first', 'ha_failure']" :key="m" :value="m">
              {{ t(`change_impact.param.mode.${m}`) }}
            </n-radio>
          </n-space>
        </n-radio-group>
      </n-form-item>
      <n-form-item v-if="scenario === 'ip_renumber'" :label="t('change_impact.f_new_ip')">
        <n-input v-model:value="newIp" placeholder="198.51.100.80" data-testid="cip-wiz-new-ip" />
      </n-form-item>
      <n-form-item v-if="subnetChoices.length" :label="t('change_impact.f_target_subnet')">
        <n-select v-model:value="targetSubnetId" :options="subnetChoices" data-testid="cip-wiz-subnet" />
      </n-form-item>
      <n-form-item :label="t('change_impact.f_title')">
        <n-input v-model:value="title" :placeholder="defaultTitle" maxlength="200" />
      </n-form-item>
      <n-form-item :label="t('change_impact.f_window')">
        <jt-date-picker v-model:value="windowRange" type="datetimerange" clearable style="width: 100%" />
      </n-form-item>
    </n-form>
    <n-alert v-if="error" type="error" :bordered="false" style="margin-bottom: 10px">{{ error }}</n-alert>
    <div class="cip-wiz-note">{{ t("change_impact.wizard_note") }}</div>
    <template #footer>
      <n-space justify="end">
        <n-button :disabled="busy" @click="close">
          <template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}
        </n-button>
        <n-button type="primary" :loading="busy" :disabled="!canSubmit" data-testid="cip-wiz-submit" @click="submit">
          <template #icon><n-icon><ChangeImpactIcon /></n-icon></template>{{ t("change_impact.create_and_run") }}
        </n-button>
      </n-space>
    </template>
  </n-modal>
</template>

<script setup lang="ts">
import JtDatePicker from "@/components/JtDatePicker.vue";
import { computed, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import {
  NAlert, NButton, NForm, NFormItem, NIcon, NInput, NModal, NRadio, NRadioButton, NRadioGroup, NSelect,
  NSpace,
} from "naive-ui";
import { apiErrMsg } from "@/api/client";
import {
  ALL_SCENARIOS, M2_SCENARIOS, candidates, createPlan, startRun, targetSubnets, type ScenarioType, type TargetSubnets,
} from "@/api/changeImpact";
import { CancelIcon, ChangeImpactIcon } from "@/icons";
import { listDevices } from "@/api/basic";

const props = defineProps<{
  show: boolean;
  presetScenario?: ScenarioType;
  presetTargetId?: string;
  presetLabel?: string;
}>();
const emit = defineEmits<{ (e: "update:show", v: boolean): void }>();
const { t } = useI18n();
const router = useRouter();

const scenario = ref<ScenarioType>(props.presetScenario ?? "ip_renumber");
const targetId = ref<string | null>(props.presetTargetId ?? null);
const oldIp = ref("");
const oldCandidates = ref<{ id: string; ip: string; subnet: string; hostname: string | null }[]>([]);
const newIp = ref("");
const title = ref("");
const windowRange = ref<[number, number] | null>(null);
const targetSubnetId = ref<string | null>(null);
const subnetChoices = ref<{ label: string; value: string }[]>([]);
const busy = ref(false);
const error = ref("");
const devOptions = ref<{ label: string; value: string }[]>([]);
const devLoading = ref(false);
// 範圍（子網路）
const scope = ref<TargetSubnets>({ subnets: [], sections: [], customers: [], truncated: false });
const scopeLoaded = ref(false);
const noWritable = ref(false);
const customerId = ref<string | null>(null);
const sectionId = ref<string | null>(null);
const subnetId = ref<string | null>(null);
const snLoading = ref(false);
const snQuery = ref("");
let snTimer: ReturnType<typeof setTimeout> | undefined;
const pickedSubnet = ref<TargetSubnets["subnets"][number] | null>(null);
const subnet = computed(() => scope.value.subnets.find((s) => s.id === subnetId.value) ?? pickedSubnet.value);
const subnetOptions = computed(() => scope.value.subnets.map((s) => ({
  value: s.id,
  label: [s.cidr, s.description].filter(Boolean).join(" · ")
    + `（${[s.section_name, s.customer_name, s.vrf_name ? `VRF ${s.vrf_name}` : ""].filter(Boolean).join(" · ")}）`,
})));
function looksLikeIp(q: string): boolean {
  return /^\d{1,3}(\.\d{1,3}){3}$/.test(q) || (q.includes(":") && /^[0-9a-fA-F:]+$/.test(q));
}

watch(() => props.show, (v) => {
  if (!v) return;
  scenario.value = props.presetScenario ?? "ip_renumber";
  targetId.value = props.presetTargetId ?? null;
  oldIp.value = ""; oldCandidates.value = []; newIp.value = ""; title.value = ""; windowRange.value = null;
  targetSubnetId.value = null; subnetChoices.value = []; error.value = "";
  customerId.value = null; sectionId.value = null; subnetId.value = null; pickedSubnet.value = null;
  alsoDown.value = []; mode.value = "direct";
  snQuery.value = ""; scopeLoaded.value = false; noWritable.value = false;
  if (!props.presetTargetId) void loadSubnets(true);
});

async function loadSubnets(first = false) {
  snLoading.value = true;
  try {
    const r = await targetSubnets({ q: snQuery.value, sectionId: sectionId.value, customerId: customerId.value });
    // 單位、區段選項以第一次（沒有任何條件）拿到的為準，篩選後不會縮掉別的選項
    scope.value = first ? r : { ...r, sections: scope.value.sections, customers: scope.value.customers };
    if (first) noWritable.value = !r.subnets.length;
    scopeLoaded.value = true;
  } catch (e) { error.value = apiErrMsg(e); } finally { snLoading.value = false; }
}
function onSubnetSearch(q: string) {
  snQuery.value = q.trim();
  if (snTimer) clearTimeout(snTimer);
  snTimer = setTimeout(() => void loadSubnets(), 250);
}
function onFilter() {
  subnetId.value = null; pickedSubnet.value = null; onSubnet(null);
  void loadSubnets();
}
function onSubnet(id: string | null) {
  pickedSubnet.value = scope.value.subnets.find((s) => s.id === id) ?? null;
  oldCandidates.value = []; targetId.value = null; error.value = "";
  // 在子網路搜尋框打的是 IP：選好子網路就直接帶進「要改的 IP」
  if (id && !oldIp.value && looksLikeIp(snQuery.value)) {
    oldIp.value = snQuery.value;
  }
  if (id && oldIp.value) void lookupOld();
}

const isM2 = computed(() => M2_SCENARIOS.includes(scenario.value));
const targetLabel = computed(() => ({
  ip_renumber: t("change_impact.f_old_ip"), device_decommission: t("change_impact.f_device"),
  switch_maintenance: t("change_impact.f_device_maintenance"), node_downtime: t("change_impact.f_device_downtime"),
})[scenario.value]);
const alsoDown = ref<string[]>([]);
const mode = ref<"direct" | "migrate_first" | "ha_failure">("direct");
const defaultTitle = computed(() => {
  const who = props.presetLabel || oldIp.value || devOptions.value.find((o) => o.value === targetId.value)?.label || "";
  if (scenario.value === "ip_renumber") return t("change_impact.default_title_renumber", { from: who, to: newIp.value || "…" });
  if (scenario.value === "switch_maintenance") return t("change_impact.default_title_maintenance", { device: who });
  if (scenario.value === "node_downtime") return t("change_impact.default_title_downtime", { device: who });
  return t("change_impact.default_title_decom", { device: who });
});
const canSubmit = computed(() => !!targetId.value && (scenario.value !== "ip_renumber" || !!newIp.value.trim()));

async function lookupOld() {
  const ip = oldIp.value.trim();
  oldCandidates.value = [];
  if (!ip || (!props.presetTargetId && !subnetId.value)) { targetId.value = null; return; }
  try {
    oldCandidates.value = await candidates(ip, subnetId.value);
    targetId.value = oldCandidates.value.length === 1 ? oldCandidates.value[0].id : null;
    error.value = oldCandidates.value.length ? "" : t("change_impact.no_such_record");
  } catch (e) { error.value = apiErrMsg(e); }
}
async function searchDevices(q: string) {
  devLoading.value = true;
  try {
    const r = await listDevices({ q, pageSize: 20 });
    // 同名裝置要分得出來：帶上主要 IP
    devOptions.value = r.items.map((d) => ({ label: d.ip ? `${d.name} · ${d.ip}` : d.name, value: d.id }));
  } finally { devLoading.value = false; }
}

// 一起停機的裝置：自己的選項清單（跟根目標的下拉分開，搜尋時才不會把已選的名稱蓋掉）
const alsoOpts = ref<{ label: string; value: string }[]>([]);
const alsoLoading = ref(false);
const alsoLabels = new Map<string, string>();
async function searchAlso(q: string) {
  alsoLoading.value = true;
  try {
    const r = await listDevices({ q, pageSize: 20 });
    alsoOpts.value = r.items.map((d) => ({ label: d.ip ? `${d.name} · ${d.ip}` : d.name, value: d.id }));
    for (const o of alsoOpts.value) alsoLabels.set(o.value, o.label);
  } finally { alsoLoading.value = false; }
}
const alsoOptions = computed(() => {
  const out = new Map(alsoOpts.value.map((o) => [o.value, o]));
  for (const v of alsoDown.value) if (!out.has(v)) out.set(v, { value: v, label: alsoLabels.get(v) ?? v });
  return [...out.values()].filter((o) => o.value !== (props.presetTargetId ?? targetId.value));
});

function close() { emit("update:show", false); }

async function submit() {
  if (!targetId.value) return;
  busy.value = true;
  error.value = "";
  try {
    const params: Record<string, unknown> = {};
    if (scenario.value === "ip_renumber") params.new_ip = newIp.value.trim();
    if (isM2.value && alsoDown.value.length) params.also_down = alsoDown.value;
    if (scenario.value === "node_downtime") params.mode = mode.value;
    if (targetSubnetId.value) params.target_subnet_id = targetSubnetId.value;
    const plan = await createPlan({
      title: title.value.trim() || defaultTitle.value, scenario_type: scenario.value,
      target_type: scenario.value === "ip_renumber" ? "ip_address" : "device", target_id: targetId.value,
      parameters: params,
      planned_start: windowRange.value ? new Date(windowRange.value[0]).toISOString() : null,
      planned_end: windowRange.value ? new Date(windowRange.value[1]).toISOString() : null,
    }, crypto.randomUUID?.());
    await startRun(plan.id);
    close();
    void router.push({ name: "change_impact_plan", params: { id: plan.id } });
  } catch (e: any) {
    // 新位址落在好幾個重疊的子網路：讓使用者選，不猜（後端附上候選）
    const d = e?.response?.data?.detail;
    if (d?.code === "impact_ambiguous_target" && Array.isArray(d.candidates)) {
      const cidrs = String(d.params?.subnets || "").split(",").map((s: string) => s.trim());
      subnetChoices.value = d.candidates.map((id: string, i: number) => ({ label: cidrs[i] || id, value: id }));
    }
    error.value = apiErrMsg(e);
  } finally {
    busy.value = false;
  }
}
</script>

<style scoped>
.cip-wiz-target { font-weight: 600; font-variant-numeric: tabular-nums; }
.cip-wiz-note { font-size: 12.5px; opacity: .7; line-height: 1.6; }
.cip-wiz-scope { display: flex; flex-direction: column; gap: 6px; width: 100%; }
.cip-wiz-filters { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 6px; }
.cip-wiz-hint { font-size: 12px; opacity: .7; }
</style>
