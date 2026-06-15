<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  NModal,
  NForm,
  NFormItem,
  NInput,
  NSelect,
  NInputNumber,
  NCheckbox,
  NCheckboxGroup,
  NSpace,
  NTag,
  NTooltip,
  NButton,
  NIcon,
  NPopover,
  useMessage,
} from "naive-ui";
import {
  listSubnets,
  createSubnet,
  updateSubnet,
  type SubnetUpdate,
} from "@/api/subnets";
import { listSections } from "@/api/sections";
import { listScanAgents } from "@/api/phase3";
import { listVLANs, listVRFs, listLocations, type VLAN, type VRF } from "@/api/basic";
import type { Subnet, Section } from "@/types";
import { EditIcon, PlusIcon, SaveIcon, CancelIcon } from "@/icons";
import { SUDO } from "@/utils/sudo";
import { useCustomers } from "@/composables/useCustomers";
import { useSubnetTree } from "@/composables/useSubnetTree";
import { useScanProbes, probeLabel } from "@/api/scanProbes";

const props = defineProps<{
  show: boolean;
  editing: Subnet | null;
  // 從子網路詳情「新增下層子網路」帶過來時，預選此區段（僅新增模式套用）
  presetSectionId?: string | null;
}>();
const emit = defineEmits<{
  (e: "update:show", v: boolean): void;
  (e: "saved"): void;
}>();

const { t, locale } = useI18n();
const msg = useMessage();
const { catalog } = useScanProbes();
const { options: customerOptions, ensureLoaded: ensureCustomerOptsLoaded } = useCustomers();
const { bump: bumpSubnetTree } = useSubnetTree();

const showModel = computed({
  get: () => props.show,
  set: (v: boolean) => emit("update:show", v),
});

const sections = ref<Section[]>([]);
const vlans = ref<VLAN[]>([]);
const vrfs = ref<VRF[]>([]);
const allSubnets = ref<Subnet[]>([]);
const scanAgentOpts = ref<{ label: string; value: string }[]>([]);
const agentAvail = ref<Record<string, string[]>>({});
// 選定掃描代理「實際能跑」的探測集合；代理沒回報(空)時回 null = 不限制
const selectedAgentProbes = computed<Set<string> | null>(() => {
  const id = form.value.scan_agent_id;
  if (!id) return null;
  const av = agentAvail.value[id];
  return av && av.length ? new Set(av) : null;
});
function probeUnsupported(key: string): boolean {
  const s = selectedAgentProbes.value;
  return !!s && !s.has(key);
}
// 探測所需的工具 / 安裝指令（與掃描代理頁一致）
const PROBE_INSTALL: Record<string, string> = {
  os: `${SUDO} apt install nmap`,
  ports: `${SUDO} apt install nmap`,
  netbios: `${SUDO} apt install samba-common-bin   # 提供 nmblookup`,
  mdns: `${SUDO} apt install avahi-utils   # 提供 avahi-resolve`,
};
function probeInstall(key: string): string {
  return (
    PROBE_INSTALL[key] ??
    "請確認掃描代理主機具備該探測所需的系統工具與權限（例如 root / cap_net_raw、可連到 DNS 等）。"
  );
}
const locationOpts = ref<{ label: string; value: string }[]>([]);

const sectionOpts = computed(() => sections.value.map((s) => ({ label: s.name, value: s.id })));
const vlanOpts = computed(() => vlans.value.map((v) => ({ label: `${v.number} · ${v.name}`, value: v.id })));
const vrfOpts = computed(() => vrfs.value.map((v) => ({ label: v.rd ? `${v.name} (${v.rd})` : v.name, value: v.id })));
// 上層子網路選項：所有子網路，排除自己
const masterOptions = computed(() => allSubnets.value
  .filter((s) => s.id !== props.editing?.id)
  .map((s) => ({ label: s.description ? `${s.cidr} (${s.description})` : s.cidr, value: s.id })));

const form = ref({
  section_id: "" as string,
  cidr: "",
  description: "",
  vlan_id: null as string | null,
  vrf_id: null as string | null,
  master_subnet_id: null as string | null,
  customer_id: null as string | null,
  is_pool: false,
  is_full: false,
  scan_enabled: false,
  scan_method: ["icmp"] as string[],
  threshold_pct: null as number | null,
  scan_agent_id: null as string | null,
  gateway: "" as string,
  dns_servers: "" as string,
  location_id: null as string | null,
  allow_overlap: false,
});

const saving = ref(false);

async function loadAuxOpts() {
  try {
    const [secs, vls, vfs] = await Promise.all([
      listSections(1, 500), listVLANs(), listVRFs(),
    ]);
    sections.value = secs.items;
    vlans.value = vls.items;
    vrfs.value = vfs.items;
  } catch { /* silent */ }
  try {
    const ag = await listScanAgents();
    scanAgentOpts.value = ag.items.map((a) => ({ label: a.name, value: a.id }));
    // 記錄每個代理「實際能跑」的探測（available_probes）→ 子網路勾選時據此反灰不支援項
    agentAvail.value = Object.fromEntries(
      ag.items.map((a) => [a.id, (a as any).available_probes ?? []]),
    );
  } catch { /* silent */ }
  try {
    const loc = await listLocations();
    locationOpts.value = loc.items.map((l) => ({ label: l.name, value: l.id }));
  } catch { /* silent */ }
  try {
    const res = await listSubnets({ page: 1, pageSize: 500 });
    allSubnets.value = res.items;
  } catch { /* silent */ }
  void ensureCustomerOptsLoaded();
}

function resetForm() {
  const r = props.editing;
  if (r) {
    form.value = {
      section_id: r.section_id,
      cidr: r.cidr,
      description: r.description ?? "",
      vlan_id: r.vlan_id,
      vrf_id: r.vrf_id,
      master_subnet_id: (r as any).master_subnet_id ?? null,
      customer_id: r.customer_id ?? null,
      is_pool: r.is_pool, is_full: r.is_full,
      scan_enabled: r.scan_enabled,
      scan_method: [...(r.scan_method ?? ["icmp"])],
      threshold_pct: r.threshold_pct,
      scan_agent_id: r.scan_agent_id ?? null,
      gateway: r.gateway ?? "",
      dns_servers: r.dns_servers ?? "",
      location_id: r.location_id ?? null,
      allow_overlap: false,
    };
  } else {
    form.value = {
      section_id: props.presetSectionId || sections.value[0]?.id || "",
      cidr: "",
      description: "",
      vlan_id: null, vrf_id: null, master_subnet_id: null, customer_id: null,
      is_pool: false, is_full: false,
      scan_enabled: false, scan_method: ["icmp"],
      threshold_pct: null,
      scan_agent_id: null,
      gateway: "", dns_servers: "", location_id: null,
      allow_overlap: false,
    };
  }
}

// 開啟時載入選項並重置表單
watch(() => props.show, (open) => {
  if (open) {
    void (async () => {
      await loadAuxOpts();
      resetForm();
    })();
  }
});

async function submit() {
  if (!form.value.section_id) { msg.error(t("subnets.err_section_required")); return; }
  if (!props.editing && !form.value.cidr.trim()) { msg.error(t("subnets.err_cidr_required")); return; }
  saving.value = true;
  try {
    if (props.editing) {
      // CIDR 不允許改；其餘 patch
      const patch: SubnetUpdate = {
        section_id: form.value.section_id,
        description: form.value.description.trim() || null,
        vlan_id: form.value.vlan_id ?? null,
        vrf_id: form.value.vrf_id ?? null,
        master_subnet_id: form.value.master_subnet_id ?? null,
        customer_id: form.value.customer_id ?? null,
        is_pool: form.value.is_pool,
        is_full: form.value.is_full,
        scan_enabled: form.value.scan_enabled,
        scan_method: form.value.scan_method,
        threshold_pct: form.value.threshold_pct ?? null,
        scan_agent_id: form.value.scan_agent_id ?? null,
        gateway: form.value.gateway.trim() || null,
        dns_servers: form.value.dns_servers.trim() || null,
        location_id: form.value.location_id ?? null,
      };
      await updateSubnet(props.editing.id, patch);
    } else {
      await createSubnet({
        section_id: form.value.section_id,
        cidr: form.value.cidr.trim(),
        description: form.value.description.trim() || null,
        vlan_id: form.value.vlan_id ?? null,
        vrf_id: form.value.vrf_id ?? null,
        customer_id: form.value.customer_id ?? null,
        is_pool: form.value.is_pool, is_full: form.value.is_full,
        scan_enabled: form.value.scan_enabled,
        scan_method: form.value.scan_method,
        threshold_pct: form.value.threshold_pct ?? null,
        scan_agent_id: form.value.scan_agent_id ?? null,
        gateway: form.value.gateway.trim() || null,
        dns_servers: form.value.dns_servers.trim() || null,
        location_id: form.value.location_id ?? null,
        allow_overlap: form.value.allow_overlap,
      });
    }
    msg.success(t("common.ok"));
    emit("saved");
    showModel.value = false;
    bumpSubnetTree();   // 左選單子網路樹同步刷新（含新增子網段繼承的單位分組）
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("common.save_failed"));
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <n-modal v-model:show="showModel" preset="card" style="width: 640px">
    <template #header>
      <n-space align="center">
        <n-icon :size="20">
          <component :is="editing ? EditIcon : PlusIcon" />
        </n-icon>
        <span>{{ (editing ? t("common.edit") : t("common.create")) + " " + t("subnets.title") }}</span>
      </n-space>
    </template>
    <n-form label-placement="left" label-width="120">
      <n-form-item label="CIDR" required>
        <n-input v-model:value="form.cidr" placeholder="192.168.1.0/24"
                 :disabled="!!editing" />
      </n-form-item>
      <n-form-item v-if="!editing" :label="t('subnets.allow_overlap')">
        <n-checkbox v-model:checked="form.allow_overlap">{{ t("subnets.allow_overlap_hint") }}</n-checkbox>
      </n-form-item>
      <n-form-item :label="t('subnets.section')" required>
        <n-select v-model:value="form.section_id" :options="sectionOpts" filterable />
      </n-form-item>
      <n-form-item :label="t('common.description')">
        <n-input v-model:value="form.description" type="textarea" :rows="2" />
      </n-form-item>
      <n-form-item label="VLAN">
        <n-select v-model:value="form.vlan_id" :options="vlanOpts" clearable filterable />
      </n-form-item>
      <n-form-item label="VRF">
        <n-select v-model:value="form.vrf_id" :options="vrfOpts" clearable filterable />
      </n-form-item>
      <n-form-item v-if="editing" :label="t('subnets.master')">
        <n-select v-model:value="form.master_subnet_id" :options="masterOptions"
                  :placeholder="t('common.not_specified')" clearable filterable />
      </n-form-item>
      <n-form-item :label="t('cols.unit')">
        <n-select v-model:value="form.customer_id" :options="customerOptions"
                  :placeholder="t('common.not_specified')" clearable filterable />
      </n-form-item>
      <n-form-item :label="t('subnets.gateway')">
        <n-input v-model:value="form.gateway" :placeholder="t('subnets.gateway_ph')" />
      </n-form-item>
      <n-form-item :label="t('subnets.dns_servers')">
        <n-input v-model:value="form.dns_servers" :placeholder="t('subnets.dns_servers_ph')" />
      </n-form-item>
      <n-form-item :label="t('subnets.location')">
        <n-select v-model:value="form.location_id" :options="locationOpts"
                  :placeholder="t('common.not_specified')" clearable filterable />
      </n-form-item>
      <n-form-item :label="t('subnets.pool_full')">
        <n-space>
          <n-checkbox v-model:checked="form.is_pool">{{ t("subnets.is_pool") }}</n-checkbox>
          <n-checkbox v-model:checked="form.is_full">{{ t("subnets.is_full") }}</n-checkbox>
        </n-space>
      </n-form-item>
      <n-form-item :label="t('subnets.scan')">
        <n-space vertical style="width: 100%">
          <n-checkbox v-model:checked="form.scan_enabled">{{ t("subnets.scan_enable") }}</n-checkbox>
          <div v-if="catalog.probes.length"
               :style="{ opacity: form.scan_enabled ? 1 : 0.5, pointerEvents: form.scan_enabled ? 'auto' : 'none',
                         marginLeft: '8px', paddingLeft: '14px', borderLeft: '2px solid var(--n-border-color, rgba(0,0,0,.08))' }">
            <div style="font-size: 13px; margin-bottom: 4px; opacity:.7">{{ t("scan_probes.subnet_probes") }}</div>
            <n-checkbox-group v-model:value="form.scan_method" :disabled="!form.scan_enabled">
              <n-space vertical size="small">
                <n-checkbox v-for="p in catalog.probes" :key="p.key" :value="p.key"
                            :disabled="probeUnsupported(p.key)">
                  {{ probeLabel(p, locale) }}
                  <n-tooltip v-if="p.intrusive" trigger="hover">
                    <template #trigger>
                      <n-tag size="tiny" type="warning" style="margin-left: 4px;">
                        {{ t("scan_probes.intrusive") }}
                      </n-tag>
                    </template>
                    {{ t("scan_probes.intrusive_warn") }}
                  </n-tooltip>
                  <span v-if="probeUnsupported(p.key)" style="margin-left:4px; font-size:11px; opacity:.6">
                    （{{ t("scan_probes.agent_unsupported") }}）
                  </span>
                  <n-popover v-if="probeUnsupported(p.key)" trigger="click" placement="right">
                    <template #trigger>
                      <n-button text size="tiny" type="primary" style="font-size:11px"
                                @click.stop.prevent>
                        {{ t("scan_probes.install_help") }}
                      </n-button>
                    </template>
                    <div style="max-width:320px">
                      <div style="font-size:12.5px; line-height:1.6; margin-bottom:6px">
                        {{ t("scan_probes.install_help_intro") }}
                      </div>
                      <code style="display:block; padding:6px 8px; border-radius:4px;
                                   background:rgba(0,0,0,.05); font-size:12px;
                                   white-space:pre-wrap; word-break:break-all">{{ probeInstall(p.key) }}</code>
                    </div>
                  </n-popover>
                </n-checkbox>
              </n-space>
            </n-checkbox-group>
          </div>
        </n-space>
      </n-form-item>
      <n-form-item v-if="form.scan_enabled" :label="t('subnets.scan_agent')">
        <n-select v-model:value="form.scan_agent_id" :options="scanAgentOpts"
                  clearable
                  :placeholder="t('subnets.scan_agent_ph')" />
      </n-form-item>
      <n-form-item :label="t('subnets.threshold_pct')">
        <n-input-number v-model:value="form.threshold_pct" :min="0" :max="100" clearable
                        :placeholder="t('subnets.threshold_ph')" />
      </n-form-item>
    </n-form>
    <n-space justify="end">
      <n-button @click="showModel = false">
        <template #icon><n-icon><CancelIcon /></n-icon></template>
        {{ t("common.cancel") }}
      </n-button>
      <n-button type="primary" :loading="saving" @click="submit">
        <template #icon><n-icon><SaveIcon /></n-icon></template>
        {{ t("common.save") }}
      </n-button>
    </n-space>
  </n-modal>
</template>
