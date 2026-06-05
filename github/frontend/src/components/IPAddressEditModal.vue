<script setup lang="ts">
/**
 * 通用 IP 編輯 modal。
 * SubnetDetail / Addresses 都用同一個。
 *
 * 預設 read-only 顯示完整欄位；按「編輯」進 edit 模式才能改。
 */
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  NModal, NCard, NSpace, NButton, NDescriptions, NDescriptionsItem,
  NForm, NFormItem, NInput, NSelect, NSwitch, NPopconfirm, NTag, NIcon,
  NCollapse, NCollapseItem, NTimeline, NTimelineItem, NText, NEmpty, NSpin,
  NTooltip,
  useMessage,
} from "naive-ui";
import type { IPAddress } from "@/types";
import { updateAddress, deleteAddress, createAddress, type IPAddressUpdate } from "@/api/addresses";
import { getAddressHistory, getAddressSwitchPort, type IPChangeLog, type SwitchPortInfo } from "@/api/ip_history";
import { getHostnameSources, clearHostnameSource, type HostnameSources } from "@/api/hostname";
import { EditIcon, SaveIcon, CancelIcon, DeleteIcon, PlusIcon, LinkIcon } from "@/icons";
import { ArrowLeft as ArrowLeftIcon } from "@iconoir/vue";
import { fmtDateTime } from "@/utils/datetime";
import { useCustomers } from "@/composables/useCustomers";
import { useRouter } from "vue-router";
import { listDevices, type Device } from "@/api/basic";
import { getAddressRelations, type RelationNode } from "@/api/relations";
import { listDhcpRanges } from "@/api/integrations";
import RelationChain from "@/components/RelationChain.vue";
import SwitchPortLabel from "@/components/SwitchPortLabel.vue";

const router = useRouter();
const { options: customerOptions, labelFor: customerLabelFor, ensureLoaded: ensureCustomersLoaded } = useCustomers();
const devices = ref<Device[]>([]);

async function loadDevices() {
  if (devices.value.length) return;
  try {
    const r = await listDevices();
    devices.value = r.items;
  } catch { /* silent */ }
}

function deviceLabel(id: string | null | undefined): string {
  if (!id) return "—";
  return devices.value.find((d) => d.id === id)?.name ?? id.slice(0, 8) + "…";
}
const deviceOptions = computed(() =>
  devices.value.map((d) => ({ label: d.name, value: d.id })));

// DHCP 發放範圍：標示本 IP 是否落在某 DHCP 主機的動態池內
interface DhcpInfo { server: string; source: string; start: string; end: string; }
const dhcpRanges = ref<{ a: number; b: number; server: string; source: string; start: string; end: string }[]>([]);
function _ip2int(ip: string): number | null {
  const p = ip.trim().split(".");
  if (p.length !== 4) return null;
  let n = 0;
  for (const o of p) { const v = Number(o); if (!Number.isInteger(v) || v < 0 || v > 255) return null; n = n * 256 + v; }
  return n >>> 0;
}
async function loadDhcpRanges() {
  if (dhcpRanges.value.length) return;
  try {
    const rows = await listDhcpRanges();
    const out: typeof dhcpRanges.value = [];
    for (const r of rows) {
      const a = _ip2int(r.start_ip), b = _ip2int(r.end_ip);
      if (a != null && b != null) out.push({ a: Math.min(a, b), b: Math.max(a, b), server: r.firewall_name || "—", source: (r.source || "").toUpperCase(), start: r.start_ip, end: r.end_ip });
    }
    dhcpRanges.value = out;
  } catch { /* silent */ }
}
const dhcpInfo = computed<DhcpInfo | null>(() => {
  const ip = (props.address?.ip ?? "").split("/")[0];
  const n = ip ? _ip2int(ip) : null;
  if (n == null) return null;
  const r = dhcpRanges.value.find((x) => n >= x.a && n <= x.b);
  return r ? { server: r.server, source: r.source, start: r.start, end: r.end } : null;
});

const relations = ref<RelationNode[]>([]);
async function loadRelations() {
  relations.value = [];
  if (!props.address?.id) return;
  try { relations.value = await getAddressRelations(props.address.id); } catch { /* silent */ }
}

// 此 IP 被哪些 NAT 規則引用（src/dst）
const relatedNat = ref<{ id: string; name: string; type: string; src_interface: string | null;
  src_port: number | null; dst_port: number | null; source_label: string | null }[]>([]);
async function loadRelatedNat() {
  relatedNat.value = [];
  if (!props.address?.id) return;
  try {
    const { listNATs } = await import("@/api/phase3");
    const res = await listNATs({ ipId: props.address.id });
    relatedNat.value = res.items as any;
  } catch { /* silent */ }
}
function goNat() {
  emit("update:show", false);
  void router.push({ name: "nat" });
}

function goDevice(id: string | null | undefined) {
  if (!id) return;
  void router.push({ name: "device-detail", params: { id } });
}

// 後端原始值 → i18n 顯示；找不到 key 就回原值
function labelState(v: string | null | undefined): string {
  if (!v) return "—";
  const key = `addresses.state_${v}`;
  const out = t(key);
  return out === key ? v : out;
}
function labelSource(v: string | null | undefined): string {
  if (!v) return "—";
  const key = `addresses.source_${v}`;
  const out = t(key);
  return out === key ? v : out;
}
function labelEffective(v: string | null | undefined): string {
  if (!v) return "—";
  // 後端可能塞 "online (scanner)" 之類有附註的字串；只翻譯主詞
  const m = /^(\w+)(.*)$/.exec(v);
  if (!m) return v;
  const base = m[1].toLowerCase();
  let rest = m[2];
  // 來源附註本地化：(scanner) → (掃描代理)
  rest = rest.replace(/\(([^)]+)\)/g, (_full, src: string) => {
    const sk = `addresses.source_${src.trim().toLowerCase()}`;
    const sv = t(sk);
    return `(${sv === sk ? src : sv})`;
  });
  const key = `addresses.effective_${base}`;
  const out = t(key);
  return (out === key ? m[1] : out) + rest;
}

// 不偵測（exclude_from_ping）或 subnet 沒掃描時，後端的「離線」不該照搬 → 顯示未知，與狀態燈一致
const effectiveDisplay = computed(() => {
  const a = props.address;
  const v = a?.effective_status;
  if (!v) return "—";
  const noProbe = !!a?.exclude_from_ping || a?.subnet_scan_enabled === false;
  if (noProbe && /^offline/i.test(v)) return t("addresses.effective_unknown");
  return labelEffective(v);
});

const props = defineProps<{
  show: boolean;
  address: IPAddress | null;
  // create 模式：address 留 null，傳 createContext = { subnet_id, ip }
  createContext?: { subnet_id: string; ip: string } | null;
  // inline：當成獨立頁面內容渲染（不包 n-modal）；給 IPDetail 頁用
  inline?: boolean;
}>();

const emit = defineEmits<{
  (e: "update:show", v: boolean): void;
  (e: "saved", v: IPAddress): void;
  (e: "deleted", id: string): void;
  (e: "created", v: IPAddress): void;
  (e: "back"): void;
}>();

const { t } = useI18n();
const msg = useMessage();

const editMode = ref(false);
const saving = ref(false);
const deleting = ref(false);

const isCreate = computed(() => !props.address && !!props.createContext);

interface FormState {
  hostname: string;
  description: string;
  state: string;
  mac: string;
  owner: string;
  switch_port: string;
  exclude_from_ping: boolean;
  ptr_ignore: boolean;
  note: string;
  customer_id: string | null;
  device_id: string | null;
  hostname_source_pin: string;  // "" = 自動 (跟全域優先序)
}

const form = ref<FormState>(emptyForm());

function emptyForm(): FormState {
  return {
    hostname: "", description: "", state: "active", mac: "",
    owner: "", switch_port: "",
    exclude_from_ping: false, ptr_ignore: false, note: "",
    customer_id: null,
    device_id: null,
    hostname_source_pin: "",
  };
}

// 目前編輯中的 IP（去 /prefix）；找名稱或管理 IP 等於本 IP、但尚未連結的裝置 → 一鍵關聯
const currentIpHost = computed(() => (props.address?.ip ?? "").split("/")[0].trim());
const matchingDevice = computed<Device | null>(() => {
  if (form.value.device_id) return null;
  const ip = currentIpHost.value;
  // 以「主機名稱」或「IP」找尚未連結的裝置：hostname=nas2 → 裝置 nas2
  const hn = (form.value.hostname || props.address?.hostname || "").trim().toLowerCase();
  if (!ip && !hn) return null;
  return devices.value.find((d) => {
    const dip = d.ip ? d.ip.split("/")[0].trim() : "";
    const dname = (d.name || "").trim().toLowerCase();
    const dfqdn = String((d as any).fqdn || "").trim().toLowerCase();
    return (!!ip && dip === ip)            // 裝置主要 IP == 本 IP
      || (!!hn && dname === hn)            // 裝置名稱 == 本 IP 主機名稱
      || (!!hn && !!dfqdn && dfqdn === hn) // 裝置 FQDN == 本 IP 主機名稱
      || (!!ip && dname === ip);           // 舊行為：裝置名稱剛好是 IP 字串
  }) ?? null;
});
function linkMatchingDevice() {
  if (matchingDevice.value) form.value.device_id = matchingDevice.value.id;
}

function fromAddress(a: IPAddress): FormState {
  return {
    hostname: a.hostname ?? "",
    description: a.description ?? "",
    state: a.state ?? "active",
    mac: a.mac ?? "",
    owner: a.owner ?? "",
    switch_port: a.switch_port ?? "",
    exclude_from_ping: !!a.exclude_from_ping,
    ptr_ignore: !!a.ptr_ignore,
    note: a.note ?? "",
    customer_id: a.customer_id ?? null,
    device_id: (a as any).device_id ?? null,
    hostname_source_pin: a.hostname_source_pin ?? "",
  };
}

const stateOptions = computed(() => [
  { label: labelState("active"), value: "active" },
  { label: labelState("reserved"), value: "reserved" },
  { label: labelState("offline"), value: "offline" },
  { label: labelState("dhcp"), value: "dhcp" },
  { label: labelState("used"), value: "used" },
]);

watch(
  () => [props.show, props.address?.id, props.createContext?.ip],
  () => {
    // create 模式自動進 edit form；既有 IP 進 view
    editMode.value = isCreate.value;
    form.value = props.address ? fromAddress(props.address) : emptyForm();
    if (props.show) {
      void ensureCustomersLoaded();
      void loadDevices();
      void loadDhcpRanges();
      void loadRelations();
      void loadRelatedNat();
    }
  },
  { immediate: true },
);

const stateType = computed<"success" | "info" | "warning" | "error" | "default">(() => {
  const s = props.address?.state ?? "active";
  return s === "active" ? "success"
       : s === "reserved" ? "info"
       : s === "offline" ? "error"
       : s === "dhcp" ? "warning"
       : "default";
});

// ── 異動記錄 (feature B)：展開時才載入；每頁 100 筆，可「載入更多」 ──
const HISTORY_PAGE = 100;
const history = ref<IPChangeLog[]>([]);
const historyLoading = ref(false);
const historyLoaded = ref(false);
const historyHasMore = ref(false);

async function loadHistory(more = false) {
  if (!props.address?.id) return;
  if (!more && historyLoaded.value) return;
  historyLoading.value = true;
  try {
    const offset = more ? history.value.length : 0;
    const page = await getAddressHistory(props.address.id, HISTORY_PAGE, offset);
    history.value = more ? [...history.value, ...page] : page;
    historyHasMore.value = page.length === HISTORY_PAGE;
    historyLoaded.value = true;
  } catch { /* silent */ } finally {
    historyLoading.value = false;
  }
}

function onHistoryToggle(names: Array<string | number>) {
  if (names.includes("history")) void loadHistory();
}

function eventLabel(e: string): string {
  const key = `ipChanges.event.${e}`;
  const out = t(key);
  return out === key ? e : out;
}

const HISTORY_TYPE: Record<string, "default" | "info" | "success" | "warning" | "error"> = {
  created: "success", deleted: "error", online: "success", offline: "warning",
  hostname_changed: "info", mac_changed: "info", arp_changed: "info",
  state_changed: "warning", edited: "default",
};

// ── hostname 多來源 (feature A)：開 modal 時載入，給 pin 下拉用 ──
const hostnameSources = ref<HostnameSources | null>(null);
const hostnameSourcesLoaded = ref(false);

async function loadHostnameSources() {
  if (hostnameSourcesLoaded.value || !props.address?.id) return;
  try {
    hostnameSources.value = await getHostnameSources(props.address.id);
    hostnameSourcesLoaded.value = true;
  } catch { /* silent */ }
}

// 清掉某來源的 hostname 觀測（過時的「手動: …」等）→ 後端重算有效名稱
async function clearSource(source: string) {
  if (!props.address?.id) return;
  try {
    await clearHostnameSource(props.address.id, source);
    hostnameSourcesLoaded.value = false;
    await loadHostnameSources();
    if (props.address) emit("saved", props.address);
    msg.success(t("common.ok"));
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.server"));
  }
}

// pin 下拉選項：auto + 有觀測的來源 (顯示該來源回報的 hostname)
const pinOptions = computed(() => {
  const opts: Array<{ label: string; value: string }> = [
    { label: t("hostnameSrc.auto"), value: "" },
  ];
  for (const o of hostnameSources.value?.observations ?? []) {
    opts.push({ label: `${o.source} — ${o.hostname}`, value: o.source });
  }
  return opts;
});

// 換 IP 時清掉舊快取
watch(() => props.address?.id, () => {
  history.value = [];
  historyLoaded.value = false;
  historyHasMore.value = false;
  hostnameSources.value = null;
  hostnameSourcesLoaded.value = false;
  switchPort.value = null;
});

// FDB 推得的 switch port(feature E)
const switchPort = ref<SwitchPortInfo | null>(null);
async function loadSwitchPort() {
  if (!props.address?.id) return;
  try { switchPort.value = await getAddressSwitchPort(props.address.id); }
  catch { switchPort.value = null; }
}

watch(() => [props.show, props.address?.id], () => {
  if (props.show && props.address?.id) { void loadHostnameSources(); void loadSwitchPort(); }
});

function close() {
  // inline(頁面)模式：檢視中按取消＝返回上一頁；編輯中＝退出編輯
  if (props.inline) {
    if (editMode.value && !isCreate.value) { editMode.value = false; return; }
    emit("back");
    return;
  }
  emit("update:show", false);
}

async function save() {
  saving.value = true;
  try {
    if (isCreate.value && props.createContext) {
      const created = await createAddress({
        subnet_id: props.createContext.subnet_id,
        ip: props.createContext.ip,
        hostname: form.value.hostname.trim() || null,
        description: form.value.description.trim() || null,
        state: form.value.state,
        mac: form.value.mac.trim() || null,
        owner: form.value.owner.trim() || null,
        switch_port: form.value.switch_port.trim() || null,
        note: form.value.note.trim() || null,
        customer_id: form.value.customer_id ?? null,
        device_id: form.value.device_id ?? null,
      });
      msg.success(t("common.ok"));
      emit("created", created);
      emit("update:show", false);
      return;
    }
    if (!props.address) return;
    const payload: IPAddressUpdate = {
      hostname: form.value.hostname.trim() || null,
      description: form.value.description.trim() || null,
      state: form.value.state,
      mac: form.value.mac.trim() || null,
      owner: form.value.owner.trim() || null,
      switch_port: form.value.switch_port.trim() || null,
      exclude_from_ping: form.value.exclude_from_ping,
      ptr_ignore: form.value.ptr_ignore,
      note: form.value.note.trim() || null,
      customer_id: form.value.customer_id ?? null,
      device_id: form.value.device_id ?? null,
      hostname_source_pin: form.value.hostname_source_pin || null,
    };
    const updated = await updateAddress(props.address?.id, payload);
    hostnameSourcesLoaded.value = false;  // 重新整理來源/有效 hostname
    msg.success(t("common.ok"));
    emit("saved", updated);
    editMode.value = false;
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.network"));
  } finally {
    saving.value = false;
  }
}

async function remove() {
  if (!props.address) return;
  deleting.value = true;
  try {
    const id = props.address?.id;
    await deleteAddress(id);
    msg.success(t("common.ok"));
    emit("deleted", id);
    close();
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.network"));
  } finally {
    deleting.value = false;
  }
}
</script>

<template>
  <component :is="inline ? 'div' : NModal" v-bind="inline ? {} : { show: props.show, 'onUpdate:show': (v: boolean) => emit('update:show', v) }">
    <n-card
      :style="inline ? 'width: 100%' : 'width: 880px; max-width: 95vw'"
      :bordered="false"
      :role="inline ? undefined : 'dialog'"
      :aria-modal="inline ? undefined : 'true'"
    >
      <!-- 標題：IP + 狀態標籤並排（比照裝置詳情的 名稱+類型標籤）-->
      <template #header>
        <span style="display:inline-flex;align-items:center;gap:10px;flex-wrap:wrap">
          <span>{{ props.address?.ip ?? props.createContext?.ip ?? '' }}</span>
          <n-tag v-if="isCreate" type="info" size="small">{{ t("common.create") }}</n-tag>
          <n-tag v-else :type="stateType" size="small">{{ labelState(props.address?.state) }}</n-tag>
          <n-tooltip v-if="dhcpInfo" :delay="0">
            <template #trigger>
              <n-tag type="warning" size="small" :bordered="false">DHCP</n-tag>
            </template>
            <div style="max-width:260px;line-height:1.5">
              <div>{{ t("addresses.dhcp_pool_hint") }}</div>
              <div style="margin-top:4px"><strong>{{ t("addresses.dhcp_server") }}：</strong>{{ dhcpInfo.server }}{{ dhcpInfo.source ? ` (${dhcpInfo.source})` : "" }}</div>
              <div>{{ t("addresses.dhcp_range") }}：{{ dhcpInfo.start }} – {{ dhcpInfo.end }}</div>
            </div>
          </n-tooltip>
        </span>
      </template>
      <!-- inline(頁面)模式：操作鈕放右上，比照裝置詳情頁 -->
      <template v-if="inline && !isCreate" #header-extra>
        <n-space align="center" :size="8" :wrap-item="false">
          <template v-if="!editMode">
            <n-button type="primary" size="small" @click="editMode = true">
              <template #icon><n-icon><EditIcon /></n-icon></template>{{ t("common.edit") }}
            </n-button>
            <n-popconfirm @positive-click="remove">
              <template #trigger>
                <n-button type="error" ghost size="small" :loading="deleting">
                  <template #icon><n-icon><DeleteIcon /></n-icon></template>{{ t("common.delete") }}
                </n-button>
              </template>
              {{ t("common.confirm_delete") }}
            </n-popconfirm>
            <n-button size="small" @click="emit('back')">
              <template #icon><n-icon><ArrowLeftIcon /></n-icon></template>{{ t("common.back") }}
            </n-button>
          </template>
          <template v-else>
            <n-popconfirm @positive-click="remove">
              <template #trigger>
                <n-button type="error" ghost size="small" :loading="deleting">
                  <template #icon><n-icon><DeleteIcon /></n-icon></template>{{ t("common.delete") }}
                </n-button>
              </template>
              {{ t("common.confirm_delete") }}
            </n-popconfirm>
            <n-button size="small" @click="close">
              <template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}
            </n-button>
            <n-button type="primary" size="small" :loading="saving" @click="save">
              <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
            </n-button>
          </template>
        </n-space>
      </template>

      <div v-if="props.address || isCreate">
        <!-- view mode -->
        <n-descriptions v-if="!editMode" bordered :column="2" size="small" label-placement="left"
                        label-align="left"
                        :label-style="{ width: '132px', whiteSpace: 'nowrap', verticalAlign: 'top' }"
                        :content-style="{ verticalAlign: 'top', wordBreak: 'break-word', minWidth: '160px' }">
          <n-descriptions-item :label="t('addresses.ip')">{{ props.address?.ip }}</n-descriptions-item>
          <n-descriptions-item :label="t('common.status')">{{ labelState(props.address?.state) }}</n-descriptions-item>
          <n-descriptions-item :label="t('addresses.hostname')">
            <span>{{ props.address?.hostname ?? "—" }}</span>
            <n-tag v-if="hostnameSources?.pin" size="tiny" type="warning" :bordered="false"
                   style="margin-left: 6px">{{ t("hostnameSrc.pinned", { src: hostnameSources.pin }) }}</n-tag>
          </n-descriptions-item>
          <n-descriptions-item :label="t('addresses.mac')">
            <span>{{ props.address?.mac ?? "—" }}</span>
            <n-tag v-if="props.address?.mac_vendor" size="tiny" type="info" bordered
                   style="margin-left: 6px">{{ props.address.mac_vendor }}</n-tag>
          </n-descriptions-item>
          <n-descriptions-item :label="t('addresses.owner')">{{ props.address?.owner ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('addresses.switch_port')">
            <template v-if="props.address?.switch_port">
              <n-tooltip v-if="props.address?.switch_port_confident === false">
                <template #trigger>
                  <switch-port-label :value="props.address.switch_port" dim />
                </template>
                {{ t("addresses.switch_port_uncertain") }}
              </n-tooltip>
              <switch-port-label v-else :value="props.address.switch_port" />
            </template>
            <span v-else>—</span>
            <n-tag v-if="switchPort?.likely_access_port?.port" size="tiny" type="info"
                   style="margin-left: 6px">
              FDB: <switch-port-label :value="`${switchPort.likely_access_port.switch} / ${switchPort.likely_access_port.port}`" />
            </n-tag>
          </n-descriptions-item>
          <n-descriptions-item :label="t('nav.devices')" :span="2">
            <a v-if="props.address?.device_id" href="#"
               style="color: var(--primary-color, #18a058); text-decoration: none;"
               @click.prevent="goDevice(props.address?.device_id)">
              {{ deviceLabel(props.address?.device_id) }}
            </a>
            <span v-else>—</span>
          </n-descriptions-item>
          <n-descriptions-item
            v-if="hostnameSources && hostnameSources.observations.length"
            :label="t('hostnameSrc.sources')" :span="2"
          >
            <n-space :size="6" style="flex-wrap: wrap">
              <n-tag
                v-for="o in hostnameSources.observations" :key="o.source"
                size="small" :bordered="false"
                :type="o.hostname === props.address?.hostname ? 'success' : 'default'"
                closable
                @close="clearSource(o.source)"
              >
                {{ labelSource(o.source) }}: {{ o.hostname }}
              </n-tag>
            </n-space>
          </n-descriptions-item>
          <n-descriptions-item :label="t('common.description')" :span="2">
            {{ props.address?.description ?? "—" }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('addresses.note')" :span="2">
            {{ props.address?.note ?? "—" }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('nav.customers')" :span="2">
            {{ customerLabelFor(props.address?.customer_id) }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('addresses.exclude_from_ping')">{{ props.address?.exclude_from_ping ? "✓" : "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('addresses.ptr_ignore')">{{ props.address?.ptr_ignore ? "✓" : "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('addresses.source')">{{ labelSource(props.address?.discovery_source) }}</n-descriptions-item>
          <n-descriptions-item :label="t('addresses.effective_status')">{{ effectiveDisplay }}</n-descriptions-item>
          <n-descriptions-item :label="t('addresses.last_seen_scanner')">{{ fmtDateTime(props.address?.last_seen_scanner) }}</n-descriptions-item>
          <n-descriptions-item :label="t('addresses.last_seen_librenms')">{{ fmtDateTime(props.address?.last_seen_librenms) }}</n-descriptions-item>
          <n-descriptions-item :label="t('addresses.last_seen_dns')">{{ fmtDateTime(props.address?.last_seen_dns) }}</n-descriptions-item>
          <n-descriptions-item :label="t('common.created_at')">{{ fmtDateTime(props.address?.created_at) }}</n-descriptions-item>
          <n-descriptions-item :label="t('common.updated_at')" :span="2">{{ fmtDateTime(props.address?.updated_at) }}</n-descriptions-item>
        </n-descriptions>

        <!-- 上下關係鏈：區段 → 子網路 → 位址 → 裝置 → 機櫃 → 機房 -->
        <div v-if="!editMode && relations.length > 1" style="margin-top: 14px">
          <div style="font-size: 12px; opacity: 0.6; margin-bottom: 6px">{{ t("relations.title") }}</div>
          <relation-chain :nodes="relations" :current-id="props.address?.id" />
        </div>

        <!-- 關聯的 NAT 規則 -->
        <div v-if="!editMode && relatedNat.length" style="margin-top: 14px">
          <div style="font-size: 12px; opacity: 0.6; margin-bottom: 6px">
            {{ t("addresses.related_nat", { n: relatedNat.length }) }}
          </div>
          <n-space vertical :size="6">
            <div v-for="n in relatedNat" :key="n.id" class="nat-ref" @click="goNat">
              <n-tag size="small" type="info" :bordered="false">{{ n.type }}</n-tag>
              <span class="nat-ref-name">{{ n.name }}</span>
              <span class="nat-ref-meta">
                <template v-if="n.src_interface">{{ n.src_interface }}</template>
                <template v-if="n.dst_port"> · :{{ n.dst_port }}</template>
              </span>
              <n-tag v-if="n.source_label" size="tiny" :bordered="false">{{ n.source_label }}</n-tag>
            </div>
          </n-space>
        </div>

        <!-- 異動記錄 (feature B)，展開才載入 -->
        <n-collapse v-if="!editMode && props.address" style="margin-top: 12px" @update:expanded-names="onHistoryToggle">
          <n-collapse-item :title="t('ipChanges.history')" name="history">
            <n-spin :show="historyLoading">
              <n-empty v-if="historyLoaded && !history.length" :description="t('ipChanges.empty')" size="small" />
              <n-timeline v-else style="padding: 4px 0">
                <n-timeline-item
                  v-for="h in history" :key="h.id"
                  :type="HISTORY_TYPE[h.event_type] ?? 'default'"
                  :time="fmtDateTime(h.created_at)"
                >
                  <template #header>
                    <n-space align="center" :size="6">
                      <strong>{{ eventLabel(h.event_type) }}</strong>
                      <n-tag size="tiny" :bordered="false">{{ labelSource(h.source) }}</n-tag>
                      <n-text v-if="h.actor_username" depth="3" style="font-size: 12px">{{ h.actor_username }}</n-text>
                    </n-space>
                  </template>
                  <n-text v-if="h.old_value != null || h.new_value != null" style="font-size: 13px">
                    <span v-if="h.field">{{ h.field }}: </span>
                    <n-text depth="3" delete>{{ h.old_value ?? "∅" }}</n-text>
                    →
                    <n-text strong>{{ h.new_value ?? "∅" }}</n-text>
                  </n-text>
                  <n-text v-if="h.note" depth="3" style="font-size: 12px; display: block">{{ h.note }}</n-text>
                </n-timeline-item>
              </n-timeline>
              <div v-if="historyHasMore" style="text-align: center; margin-top: 8px">
                <n-button size="small" :loading="historyLoading" @click="loadHistory(true)">
                  {{ t("common.load_more") }}
                </n-button>
              </div>
            </n-spin>
          </n-collapse-item>
        </n-collapse>

        <!-- edit mode -->
        <n-form v-else label-placement="top">
          <n-space :size="12" :wrap-item="false" style="flex-wrap: wrap">
            <n-form-item :label="t('addresses.hostname')" style="flex: 1 1 300px">
              <n-input v-model:value="form.hostname" placeholder="host.example.com" />
            </n-form-item>
            <n-form-item :label="t('common.status')" style="flex: 0 0 160px">
              <n-select v-model:value="form.state" :options="stateOptions" />
            </n-form-item>
          </n-space>
          <n-form-item v-if="!isCreate" :label="t('hostnameSrc.pin_label')" style="margin-bottom: 12px">
            <n-select
              v-model:value="form.hostname_source_pin"
              :options="pinOptions" :placeholder="t('hostnameSrc.auto')"
            />
            <template #feedback>
              <span style="font-size: 11px; opacity: .7; display: block; padding-bottom: 4px">{{ t("hostnameSrc.pin_hint") }}</span>
            </template>
          </n-form-item>
          <n-space :size="12" :wrap-item="false" style="flex-wrap: wrap">
            <n-form-item :label="t('addresses.mac')" style="flex: 1 1 240px">
              <n-input v-model:value="form.mac" placeholder="aa:bb:cc:dd:ee:ff" />
            </n-form-item>
            <n-form-item :label="t('addresses.owner')" style="flex: 1 1 240px">
              <n-input v-model:value="form.owner" />
            </n-form-item>
            <n-form-item :label="t('addresses.switch_port')" style="flex: 1 1 200px">
              <n-input v-model:value="form.switch_port" />
            </n-form-item>
          </n-space>
          <n-form-item :label="t('common.description')">
            <n-input v-model:value="form.description" type="textarea" :rows="2" />
          </n-form-item>
          <n-form-item :label="t('addresses.note')">
            <n-input v-model:value="form.note" type="textarea" :rows="2" />
          </n-form-item>
          <n-form-item :label="t('nav.customers')">
            <n-select v-model:value="form.customer_id" :options="customerOptions"
                      :placeholder="t('common.not_specified')" clearable filterable />
          </n-form-item>
          <n-form-item :label="t('nav.devices')">
            <n-space vertical :size="4" style="width: 100%">
              <n-select v-model:value="form.device_id" :options="deviceOptions"
                        :placeholder="t('common.not_specified')" clearable filterable />
              <n-button v-if="matchingDevice" size="tiny" dashed type="primary"
                        @click="linkMatchingDevice">
                <template #icon><n-icon><LinkIcon /></n-icon></template>
                {{ t("addresses.link_matching_device", { name: matchingDevice.name }) }}
              </n-button>
            </n-space>
          </n-form-item>
          <n-space :size="24">
            <n-form-item :label="t('addresses.exclude_from_ping')">
              <n-switch v-model:value="form.exclude_from_ping" />
            </n-form-item>
            <n-form-item :label="t('addresses.ptr_ignore')">
              <n-switch v-model:value="form.ptr_ignore" />
            </n-form-item>
          </n-space>
        </n-form>
      </div>

      <template v-if="!inline" #footer>
        <n-space justify="space-between">
          <n-popconfirm v-if="!isCreate && (!inline || editMode)" @positive-click="remove">
            <template #trigger>
              <n-button type="error" ghost size="small" :loading="deleting" :disabled="!props.address">
                <template #icon><n-icon><DeleteIcon /></n-icon></template>
                {{ t("common.delete") }}
              </n-button>
            </template>
            {{ t("common.confirm_delete") }}
          </n-popconfirm>
          <span v-else></span>
          <n-space>
            <n-button v-if="!inline || editMode || isCreate" @click="close">
              <template #icon><n-icon><CancelIcon /></n-icon></template>
              {{ t("common.cancel") }}
            </n-button>
            <n-button v-if="isCreate" type="primary" :loading="saving" @click="save">
              <template #icon><n-icon><PlusIcon /></n-icon></template>
              {{ t("common.create") }}
            </n-button>
            <n-button v-else-if="!editMode" type="primary" @click="editMode = true">
              <template #icon><n-icon><EditIcon /></n-icon></template>
              {{ t("common.edit") }}
            </n-button>
            <n-button v-else type="primary" :loading="saving" @click="save">
              <template #icon><n-icon><SaveIcon /></n-icon></template>
              {{ t("common.save") }}
            </n-button>
          </n-space>
        </n-space>
      </template>
    </n-card>
  </component>
</template>

<style scoped>
.nat-ref {
  display: flex; align-items: center; gap: 8px;
  padding: 6px 10px; border-radius: 6px;
  background: rgba(127, 127, 127, 0.06); cursor: pointer;
  transition: background .15s;
}
.nat-ref:hover { background: rgba(24, 160, 88, 0.12); }
.nat-ref-name { font-weight: 500; }
.nat-ref-meta { font-size: 12px; opacity: 0.6; font-family: monospace; }
</style>
