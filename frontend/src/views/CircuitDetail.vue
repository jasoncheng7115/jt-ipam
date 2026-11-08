<script setup lang="ts">
/**
 * 電路詳細資料（客戶許願清單 2026-10-28）：基本資料、聯絡人（電路與供應商）、WAN 位址、租用設備。
 *
 * WAN 位址都是 IPAM 的紀錄（子網路或個別 IP），每個位址列出 IPAM 狀態、使用的裝置、NAT、
 * 最近看到的時間與「使用跡象」數。跡象是證據的數量，不是判定：看不到跡象不代表沒人在用
 * （公網位址常常擋 ping），所以畫面不寫「空的」。
 * 電路的基本欄位在電路清單編輯（同一個物件只有一個編輯表單）；租用設備在裝置頁設定。
 */
import { computed, h, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { storeToRefs } from "pinia";
import {
  NAlert, NButton, NCard, NDataTable, NDescriptions, NDescriptionsItem, NEmpty, NForm, NFormItem, NIcon, NInput,
  NModal, NPopconfirm, NRadioButton, NRadioGroup, NSpace, NSpin, NTag, NTooltip, useMessage,
  type DataTableColumns,
} from "naive-ui";
import {
  AddressesIcon, CancelIcon, DeleteIcon, ImportIcon, LeaseIcon, PhysicalIcon, PlayIcon, PlusIcon, SaveIcon, UsersIcon,
} from "@/icons";
import { ArrowLeft as ArrowLeftIcon } from "@iconoir/vue";
import { apiClient, apiErrMsg } from "@/api/client";
import {
  Advanced, type CircuitAddressLink, type CircuitAddressRole, type CircuitDetail, type CircuitWanIp,
} from "@/api/phase3";
import ContactAssignments from "@/components/ContactAssignments.vue";
import { useAuthStore } from "@/stores/auth";
import { useEntityLinks } from "@/composables/useEntityLinks";
import { fmtDate, fmtRelative } from "@/utils/datetime";
import { sourceText } from "@/utils/changeImpact";

const route = useRoute();
const router = useRouter();
const { t, te } = useI18n();
const msg = useMessage();
const links = useEntityLinks(router);
const { me } = storeToRefs(useAuthStore());
const isAdmin = computed(() => !!me.value?.is_admin);

const data = ref<CircuitDetail | null>(null);
const loading = ref(false);

async function load() {
  const id = String(route.params.id);
  loading.value = true;
  try {
    data.value = await Advanced.circuitDetail(id);
  } catch (e) {
    msg.error(apiErrMsg(e));
    data.value = null;
  } finally {
    loading.value = false;
  }
}

function cardHead(icon: any, text: string) {
  return h("span", { style: "display:inline-flex;align-items:center;gap:8px" },
    [h(NIcon, { size: 18 }, () => h(icon)), text]);
}
function fmtKbps(k?: number | null): string {
  if (k == null) return "—";
  if (k >= 1_000_000) return `${+(k / 1_000_000).toFixed(2)} Gbps`;
  if (k >= 1000) return `${+(k / 1000).toFixed(0)} Mbps`;
  return `${k} kbps`;
}
const bandwidth = computed(() => {
  const c = data.value?.circuit;
  if (!c) return "—";
  if (c.down_kbps != null || c.up_kbps != null) return `↓${fmtKbps(c.down_kbps)} / ↑${fmtKbps(c.up_kbps)}`;
  return fmtKbps(c.commit_rate_kbps);
});
function statusText(s: string): string {
  const k = `circuits.status_${s}`;
  return te(k) ? t(k) : s;
}
function back() {
  if (window.history.length > 1) router.back();
  else router.push({ name: "adv-circuits" });
}

// ── WAN 位址 ──
const ping = reactive({ busy: false, done: false, rows: {} as Record<string, { alive: boolean; rtt: number | null }> });
const allIps = computed<CircuitWanIp[]>(() => (data.value?.addresses ?? []).flatMap((a) => a.ips));
function addrRoleText(r: CircuitAddressRole): string {
  return t(`circuits.addr_role_${r}`);
}
function seenText(r: CircuitWanIp): string {
  if (!r.last_seen) return "—";
  const src = r.last_seen_source ? sourceText(t, te, r.last_seen_source) : "";
  return src ? `${fmtRelative(r.last_seen)}（${src}）` : fmtRelative(r.last_seen);
}
const ipCols = computed<DataTableColumns<CircuitWanIp>>(() => [
  { title: "IP", key: "ip", width: 150, render: (r) => links.ipById(r.id, r.ip) },
  { title: t("cols.hostname"), key: "hostname", minWidth: 140, ellipsis: { tooltip: true },
    render: (r) => r.hostname || r.description || "—" },
  { title: t("circuits.wan_device"), key: "device", width: 160,
    render: (r) => (r.device ? links.device(r.device.id, r.device.name) : "—") },
  { title: "NAT", key: "nat_count", width: 70,
    render: (r) => (r.nat_count ? links.ipById(r.id, String(r.nat_count)) : "0") },
  { title: t("circuits.wan_last_seen"), key: "last_seen", width: 220, render: (r) => seenText(r) },
  { title: t("circuits.wan_signals"), key: "signals", width: 110,
    render: (r) => h(NTooltip, null, {
      trigger: () => h(NTag, { size: "small", bordered: false, type: r.signals ? "success" : "default" },
                       () => t("circuits.wan_signals_n", { n: r.signals })),
      default: () => t("circuits.wan_signals_hint"),
    }) },
  ...(ping.done ? [{
    title: "ping", key: "_ping", width: 110,
    render: (r: CircuitWanIp) => {
      const p = ping.rows[r.ip];
      if (!p) return "—";
      return h(NTag, { size: "small", bordered: false, type: p.alive ? "success" : "default" },
               () => (p.alive ? `${p.rtt ?? "?"} ms` : t("circuits.wan_ping_none")));
    },
  }] : []),
]);

async function checkSignals() {
  const ips = [...new Set(allIps.value.map((x) => x.ip))].slice(0, 64);
  if (!ips.length) return;
  ping.busy = true;
  try {
    const { data: res } = await apiClient.post("/api/v1/tools/net/ping", {
      targets: ips.join("\n"), count: 2, timeout: 1.5, concurrency: 16,
    });
    const rows: Record<string, { alive: boolean; rtt: number | null }> = {};
    for (const x of res.results ?? []) rows[x.target] = { alive: !!x.alive, rtt: x.rtt_avg_ms ?? null };
    ping.rows = rows;
    ping.done = true;
  } catch (e) { msg.error(apiErrMsg(e)); } finally { ping.busy = false; }
}

const addrForm = reactive({ show: false, busy: false, target: "", role: "wan" as CircuitAddressRole, note: "" });
function openAddAddress() {
  Object.assign(addrForm, { show: true, busy: false, target: "", role: "wan", note: "" });
}
async function saveAddress() {
  const d = data.value;
  if (!d || !addrForm.target.trim()) return;
  addrForm.busy = true;
  try {
    await Advanced.addCircuitAddress(d.circuit.id, { target: addrForm.target.trim(), role: addrForm.role,
                                                    note: addrForm.note.trim() || null });
    addrForm.show = false;
    msg.success(t("common.saved"));
    await load();
  } catch (e) { msg.error(apiErrMsg(e)); } finally { addrForm.busy = false; }
}
async function removeAddress(a: CircuitAddressLink) {
  try {
    await Advanced.deleteCircuitAddress(data.value!.circuit.id, a.id);
    await load();
  } catch (e) { msg.error(apiErrMsg(e)); }
}
const converting = ref(false);
const hasLegacy = computed(() => !!(data.value?.circuit.ip_address || data.value?.circuit.gateway));
async function convertLegacy() {
  converting.value = true;
  try {
    const r = await Advanced.convertLegacyAddresses(data.value!.circuit.id);
    if (r.created.length) msg.success(t("circuits.legacy_done", { n: r.created.length }));
    for (const s of r.skipped) {
      msg.warning(t(`circuits.legacy_skip_${s.reason}`, { value: s.value }), { duration: 8000 });
    }
    await load();
  } catch (e) { msg.error(apiErrMsg(e)); } finally { converting.value = false; }
}
function linkTitle(a: CircuitAddressLink): string {
  if (a.hidden) return t("circuits.wan_hidden");
  if (a.subnet) return a.subnet.cidr;
  return a.ips[0]?.ip ?? "—";
}

watch(() => route.params.id, (id) => { if (id) void load(); });
onMounted(load);
</script>

<template>
  <n-spin :show="loading">
    <n-space vertical :size="16">
      <n-card v-if="data">
        <template #header>
          <n-space align="center" :wrap-item="false">
            <n-icon :size="22"><PhysicalIcon /></n-icon>
            <span data-testid="circuit-cid">{{ data.circuit.cid }}</span>
            <n-tag size="small" :type="data.circuit.status === 'active' ? 'success' : 'default'">
              {{ statusText(data.circuit.status) }}
            </n-tag>
          </n-space>
        </template>
        <n-space align="center" justify="end" style="margin-bottom: 10px">
          <n-button size="small" @click="back">
            <template #icon><n-icon><ArrowLeftIcon /></n-icon></template>
            {{ t("common.back") }}
          </n-button>
        </n-space>
        <n-descriptions label-placement="left" :column="2" size="small" bordered>
          <n-descriptions-item :label="t('circuits.provider')">{{ data.provider?.name ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('circuits.type')">{{ data.type?.name ?? "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('circuits.bandwidth')">{{ bandwidth }}</n-descriptions-item>
          <n-descriptions-item :label="t('circuits.device')">
            <component :is="() => (data!.device ? links.device(data!.device.id, data!.device.name) : '—')" />
          </n-descriptions-item>
          <n-descriptions-item :label="t('circuits.install_date')">{{ fmtDate(data.circuit.install_date) }}</n-descriptions-item>
          <n-descriptions-item :label="t('circuits.contract_end')">{{ fmtDate(data.circuit.contract_end_date) }}</n-descriptions-item>
          <n-descriptions-item :label="t('circuits.account')">{{ data.provider?.account_number || "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('circuits.noc_contact')">{{ data.provider?.noc_contact || "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('circuits.dns_servers')">{{ data.circuit.dns_servers || "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('sections.description')">{{ data.circuit.description || "—" }}</n-descriptions-item>
        </n-descriptions>
        <div class="hint" style="margin-top: 8px">{{ t("circuits.detail_edit_hint") }}</div>
      </n-card>

      <!-- 聯絡人：電路自己的與供應商的分開列（同一個供應商的業務，每條電路都看得到） -->
      <n-card v-if="data" :title="() => cardHead(UsersIcon, t('circuits.contacts'))" data-testid="circuit-contacts">
        <ContactAssignments object-type="circuit" :object-id="data.circuit.id" :contacts="data.contacts.circuit"
                            :title="t('circuits.contacts_circuit')" :add-title="t('circuits.contact_add_circuit')"
                            :admin="isAdmin" @changed="load" />
        <ContactAssignments v-if="data.provider" object-type="provider" :object-id="data.provider.id"
                            :contacts="data.contacts.provider"
                            :title="t('circuits.contacts_provider', { name: data.provider.name })"
                            :add-title="t('circuits.contact_add_provider')" :admin="isAdmin" @changed="load" />
      </n-card>

      <!-- WAN 位址 -->
      <n-card v-if="data" :title="() => cardHead(AddressesIcon, t('circuits.wan_title'))" data-testid="circuit-wan">
        <n-space align="center" justify="end" style="margin-bottom: 10px">
          <n-button v-if="allIps.length" size="small" :loading="ping.busy" :disabled="ping.busy"
                    data-testid="circuit-wan-check" @click="checkSignals">
            <template #icon><n-icon><PlayIcon /></n-icon></template>
            {{ t("circuits.wan_check") }}
          </n-button>
          <n-button v-if="isAdmin && hasLegacy" size="small" :loading="converting" :disabled="converting"
                    data-testid="circuit-wan-legacy" @click="convertLegacy">
            <template #icon><n-icon><ImportIcon /></n-icon></template>
            {{ t("circuits.legacy_convert") }}
          </n-button>
          <n-button v-if="isAdmin" size="small" type="primary" data-testid="circuit-wan-add" @click="openAddAddress">
            <template #icon><n-icon><PlusIcon /></n-icon></template>
            {{ t("circuits.wan_add") }}
          </n-button>
        </n-space>
        <div class="hint" style="margin-bottom: 10px">{{ t("circuits.wan_hint") }}</div>
        <n-alert v-if="hasLegacy && !data.addresses.length" type="info" :bordered="false" style="margin-bottom: 10px">
          {{ t("circuits.legacy_hint", { ip: data.circuit.ip_address || "—", gw: data.circuit.gateway || "—" }) }}
        </n-alert>
        <n-empty v-if="!data.addresses.length" :description="t('circuits.wan_empty')" />
        <div v-for="a in data.addresses" :key="a.id" class="sec" data-testid="circuit-wan-link">
          <n-space align="center" justify="space-between" style="margin-bottom: 6px">
            <n-space align="center" :size="8">
              <n-tag size="small" :type="a.role === 'wan' ? 'info' : 'default'" :bordered="false">{{ addrRoleText(a.role) }}</n-tag>
              <span class="sec-title mono">{{ linkTitle(a) }}</span>
              <component v-if="a.subnet" :is="() => links.subnet(a.subnet!.id, a.subnet!.description || '')" />
              <span v-if="a.subnet && a.usable != null" class="dim">
                {{ t("circuits.wan_block_counts", { usable: a.usable, registered: a.registered }) }}
              </span>
              <span v-if="a.note" class="dim">{{ a.note }}</span>
            </n-space>
            <n-popconfirm v-if="isAdmin" @positive-click="removeAddress(a)">
              <template #trigger>
                <n-button size="tiny" type="error" ghost :title="t('common.delete')">
                  <template #icon><n-icon><DeleteIcon /></n-icon></template>
                </n-button>
              </template>
              {{ t("circuits.wan_remove_confirm") }}
            </n-popconfirm>
          </n-space>
          <n-data-table v-if="!a.hidden" size="small" :columns="ipCols" :data="a.ips" :bordered="false"
                        :row-key="(r: CircuitWanIp) => r.id" />
        </div>
      </n-card>

      <!-- 租用設備：在裝置頁設定，這裡列出掛在這條電路的 -->
      <n-card v-if="data" :title="() => cardHead(LeaseIcon, t('circuits.leases_title'))" data-testid="circuit-leases">
        <div class="hint" style="margin-bottom: 8px">{{ t("circuits.leases_hint") }}</div>
        <n-empty v-if="!data.leases.length" :description="t('circuits.leases_empty')" />
        <table v-else class="lease-table">
          <thead><tr>
            <th>{{ t("cols.device") }}</th><th>{{ t("circuits.lease_return") }}</th>
            <th>{{ t("circuits.lease_due") }}</th><th>{{ t("circuits.lease_note") }}</th>
          </tr></thead>
          <tbody>
            <tr v-for="l in data.leases" :key="l.id">
              <td><component :is="() => links.device(l.device.id, l.device.name)" /></td>
              <td>{{ l.return_required ? t("common.yes") : t("common.no") }}</td>
              <td>{{ l.return_due ? fmtDate(l.return_due) : "—" }}</td>
              <td>{{ l.note || "—" }}</td>
            </tr>
          </tbody>
        </table>
      </n-card>

      <n-space v-if="!data && !loading" justify="center" style="padding: 40px; opacity: .6">
        {{ t("common.no_data") }}
      </n-space>
    </n-space>

    <!-- 新增 WAN 位址 -->
    <n-modal v-model:show="addrForm.show" preset="card" style="width: 480px; max-width: 95vw" :title="t('circuits.wan_add')">
      <n-form label-placement="top">
        <n-form-item :label="t('circuits.wan_target')" :show-feedback="false">
          <n-input v-model:value="addrForm.target" placeholder="203.0.113.0/29 / 203.0.113.2" maxlength="64"
                   data-testid="circuit-wan-target" @keyup.enter="saveAddress" />
        </n-form-item>
        <div class="hint" style="margin: 4px 0 16px">{{ t("circuits.wan_target_hint") }}</div>
        <n-form-item :label="t('circuits.wan_role')">
          <n-radio-group v-model:value="addrForm.role" size="small">
            <n-radio-button value="wan">{{ t("circuits.addr_role_wan") }}</n-radio-button>
            <n-radio-button value="gateway">{{ t("circuits.addr_role_gateway") }}</n-radio-button>
            <n-radio-button value="peer">{{ t("circuits.addr_role_peer") }}</n-radio-button>
          </n-radio-group>
        </n-form-item>
        <n-form-item :label="t('circuits.lease_note')">
          <n-input v-model:value="addrForm.note" maxlength="1024" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="addrForm.show = false">
            <template #icon><n-icon><CancelIcon /></n-icon></template>
            {{ t("common.cancel") }}
          </n-button>
          <n-button type="primary" :loading="addrForm.busy" :disabled="!addrForm.target.trim() || addrForm.busy"
                    data-testid="circuit-wan-save" @click="saveAddress">
            <template #icon><n-icon><SaveIcon /></n-icon></template>
            {{ t("common.save") }}
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </n-spin>
</template>

<style scoped>
.sec + .sec { margin-top: 18px; }
.sec-title { font-weight: 600; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.dim { opacity: .65; font-size: 13px; }
.hint { font-size: 12.5px; opacity: .7; }
.lease-table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
.lease-table th, .lease-table td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--n-border-color, rgba(128,128,128,.2)); }
.lease-table th { font-weight: 600; opacity: .8; }
</style>
