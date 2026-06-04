<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";

const { t } = useI18n();

// 工具結果欄位名稱 → 顯示文字（找不到就原樣顯示）
const FIELD_LABELS = computed<Record<string, string>>(() => ({
  ip: "IP", version: t("tools_page.f_version"),
  is_private: t("tools_page.f_is_private"), is_global: t("tools_page.f_is_global"), is_reserved: t("tools_page.f_is_reserved"),
  is_multicast: t("tools_page.f_is_multicast"), is_loopback: t("tools_page.f_is_loopback"), is_link_local: t("tools_page.f_is_link_local"),
  decimal: t("tools_page.f_decimal"), hex: t("tools_page.f_hex"), binary: t("tools_page.f_binary"), reverse_pointer: t("tools_page.f_reverse_pointer"),
  cidr: "CIDR", network_address: t("tools_page.f_network_address"), broadcast_address: t("tools_page.f_broadcast_address"),
  netmask: t("tools_page.f_netmask"), hostmask: t("tools_page.f_hostmask"), prefixlen: t("tools_page.f_prefixlen"),
  num_addresses: t("tools_page.f_num_addresses"), host_count: t("tools_page.f_host_count"),
  first_host: t("tools_page.f_first_host"), last_host: t("tools_page.f_last_host"),
  mac: "MAC", eui64: "EUI-64", modified_eui64: t("tools_page.f_modified_eui64"),
  link_local: t("tools_page.f_link_local"), interface_id: t("tools_page.f_interface_id"),
}));
function fieldLabel(k: unknown): string {
  const s = String(k);
  return FIELD_LABELS.value[s] ?? s;
}
import {
  NCard,
  NTabs,
  NTabPane,
  NSpace,
  NIcon,
  NInput,
  NInputNumber,
  NButton,
  NDescriptions,
  NDescriptionsItem,
  NCode,
  NAlert,
  NTag,
  NSelect,
  useMessage,
} from "naive-ui";
import { apiClient } from "@/api/client";
import { ToolsIcon, AddressesIcon, SubnetsIcon, GridIcon, DevicesIcon, ListIcon, SearchIcon, DnsIcon, PowerIcon } from "@/icons";
import { fmtDateTime } from "@/utils/datetime";

const msg = useMessage();

// ── IP Info ──
const ipInput = ref("8.8.8.8");
const ipResult = ref<Record<string, unknown> | null>(null);
async function runIpInfo() {
  try {
    const { data } = await apiClient.get("/api/v1/tools/ip-info", { params: { ip: ipInput.value } });
    ipResult.value = data;
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? "Error");
  }
}

// ── CIDR Info ──
const cidrInput = ref("192.168.0.0/24");
const cidrResult = ref<Record<string, unknown> | null>(null);
async function runCidrInfo() {
  try {
    const { data } = await apiClient.get("/api/v1/tools/cidr-info", { params: { cidr: cidrInput.value } });
    cidrResult.value = data;
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? "Error");
  }
}

// ── CIDR Split ──
const splitCidr = ref("192.168.0.0/24");
const splitNew = ref(28);
const splitResult = ref<{ subnets: string[]; count: number } | null>(null);
async function runSplit() {
  try {
    const { data } = await apiClient.get("/api/v1/tools/cidr-split", {
      params: { cidr: splitCidr.value, new_prefix: splitNew.value },
    });
    splitResult.value = data;
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? "Error");
  }
}

// ── MAC OUI 製造商查詢 + 維護 ──
const ouiStats = ref<{ count: number; last_updated: string | null } | null>(null);
const ouiLookupMac = ref("");
const ouiLookupResult = ref<string | null>(null);
const ouiLookupBusy = ref(false);

async function loadOuiStats() {
  try {
    const { data } = await apiClient.get("/api/v1/oui/stats");
    ouiStats.value = data;
  } catch { /* silent */ }
}

async function runOuiLookup() {
  if (!ouiLookupMac.value.trim()) return;
  ouiLookupBusy.value = true;
  try {
    const { data } = await apiClient.get("/api/v1/oui/lookup", {
      params: { mac: ouiLookupMac.value.trim() },
    });
    ouiLookupResult.value = data.vendor;
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? "Error");
    ouiLookupResult.value = null;
  } finally {
    ouiLookupBusy.value = false;
  }
}

// ── OUI 首碼 / 廠商名搜尋（多筆）──
const ouiSearchTerm = ref("");
const ouiSearchBusy = ref(false);
const ouiSearchRes = ref<{ count: number; truncated: boolean; vendors: { prefix: string; short_name: string | null; name: string }[] } | null>(null);
async function runOuiSearch() {
  const term = ouiSearchTerm.value.trim();
  if (!term) return;
  ouiSearchBusy.value = true;
  try {
    // 看起來像 hex 首碼(只含 0-9a-f:-) → 當 prefix；否則當廠商名
    const isHex = /^[0-9a-fA-F:-]+$/.test(term);
    const { data } = await apiClient.get("/api/v1/oui/search", {
      params: isHex ? { prefix: term } : { name: term },
    });
    ouiSearchRes.value = data;
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? "Error");
    ouiSearchRes.value = null;
  } finally {
    ouiSearchBusy.value = false;
  }
}

onMounted(() => { void loadOuiStats(); });

// ── 網路小工具（多合一） ──
async function callTool(path: string, params: Record<string, unknown>) {
  try {
    const { data } = await apiClient.get(`/api/v1/tools/${path}`, { params });
    return data;
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? "Error");
    return null;
  }
}
const nu = ref<Record<string, any>>({
  inCidrIp: "192.168.1.50", inCidrCidr: "192.168.1.0/24", inCidrRes: null,
  relA: "10.0.0.0/8", relB: "10.1.0.0/16", relRes: null,
  rStart: "192.168.1.10", rEnd: "192.168.1.200", rRes: null,
  c2rCidr: "192.168.1.0/24", c2rRes: null,
  aggIn: "192.168.0.0/24, 192.168.1.0/24", aggRes: null,
  nmVal: "255.255.255.0", nmRes: null,
  macVal: "00:11:22:33:44:55", macRes: null,
  fqdnVal: "sw1.dc.example.com", fqdnRes: null,
  dnsName: "example.com", dnsType: "ANY", dnsRes: null,
});
const dnsTypeOpts = ["ANY", "A", "AAAA", "PTR"].map((v) => ({ label: v, value: v }));
async function nuInCidr() { nu.value.inCidrRes = await callTool("ip-in-cidr", { ip: nu.value.inCidrIp, cidr: nu.value.inCidrCidr }); }
async function nuRel() { nu.value.relRes = await callTool("cidr-relation", { a: nu.value.relA, b: nu.value.relB }); }
async function nuRange() { nu.value.rRes = await callTool("range-to-cidr", { start: nu.value.rStart, end: nu.value.rEnd }); }
async function nuC2r() { nu.value.c2rRes = await callTool("cidr-to-range", { cidr: nu.value.c2rCidr }); }
async function nuAgg() { nu.value.aggRes = await callTool("aggregate", { cidrs: nu.value.aggIn }); }
async function nuNm() { nu.value.nmRes = await callTool("netmask", { value: nu.value.nmVal }); }
async function nuMac() { nu.value.macRes = await callTool("mac-format", { mac: nu.value.macVal }); }
async function nuFqdn() { nu.value.fqdnRes = await callTool("fqdn", { name: nu.value.fqdnVal }); }
async function nuDns() { nu.value.dnsRes = await callTool("dns-lookup", { name: nu.value.dnsName, type: nu.value.dnsType }); }

// ── 郵件 / DNS 診斷（MX / SPF / DKIM / DMARC）──
const mail = ref<{ domain: string; selector: string; res: any }>({ domain: "example.com", selector: "", res: null });
async function runMail() { mail.value.res = await callTool("dns-mail", { domain: mail.value.domain, dkim_selector: mail.value.selector }); }

// ── GeoIP（MaxMind GeoLite2 web service；需先在系統設定填憑證）──
const geo = ref<{ ip: string; res: any }>({ ip: "8.8.8.8", res: null });
async function runGeo() { geo.value.res = await callTool("geoip", { ip: geo.value.ip }); }

// ── 機房電力試算（純前端計算）──
const pw = ref({ volts: 220, amps: 16, phase: "1", pf: 0.95, watts: 1000, battWh: 1500, loadW: 500, pduA: 16 });
const phaseOpts = [{ label: t("tools_page.pw_1phase"), value: "1" }, { label: t("tools_page.pw_3phase"), value: "3" }];
const pwLoadW = computed(() => Math.round((pw.value.phase === "3" ? Math.sqrt(3) : 1) * (pw.value.volts || 0) * (pw.value.amps || 0) * (pw.value.pf || 0)));
const pwBtu = computed(() => Math.round((pw.value.watts || 0) * 3.412));
const pwUpsMin = computed(() => (pw.value.loadW > 0 ? Math.round((pw.value.battWh || 0) / pw.value.loadW * 60) : 0));
const pwPduSafe = computed(() => +(((pw.value.pduA || 0) * 0.8)).toFixed(1));

// ── EUI-64 ──
const macInput = ref("00:11:22:33:44:55");
const prefixInput = ref("2001:db8::/64");
const eui64Result = ref<Record<string, unknown> | null>(null);
async function runEui64() {
  try {
    const { data } = await apiClient.get("/api/v1/tools/eui64", {
      params: { mac: macInput.value, prefix: prefixInput.value },
    });
    eui64Result.value = data;
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? "Error");
  }
}
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><ToolsIcon /></n-icon>
        <span>{{ t("tools_page.title") }}</span>
      </n-space>
    </template>
    <template #header-extra>
      <span class="tools-subtitle">{{ t("tools_page.subtitle") }}</span>
    </template>
    <n-tabs type="line" default-value="ip">
      <!-- ═══════════ IP 位址 ═══════════ -->
      <n-tab-pane name="ip">
        <template #tab>
          <span class="tab-h"><n-icon :size="16"><AddressesIcon /></n-icon>{{ t('tools_page.cat_ip') }}</span>
        </template>
        <div class="nu-grid">
          <!-- IP 資訊 -->
          <n-card size="small"><template #header><span class="nu-h"><n-icon :size="16"><AddressesIcon /></n-icon>{{ t('tools_page.ip_info') }}</span></template>
            <div class="nu-row">
              <n-input v-model:value="ipInput" placeholder="8.8.8.8" @keyup.enter="runIpInfo" />
              <n-button type="primary" class="nu-go" @click="runIpInfo"><template #icon><n-icon><SearchIcon /></n-icon></template>{{ t("tools_page.lookup") }}</n-button>
            </div>
            <n-descriptions v-if="ipResult" bordered :column="1" size="small" style="margin-top:10px" label-placement="left"
                            label-align="right" :label-style="{ whiteSpace: 'nowrap' }">
              <n-descriptions-item v-for="(v, k) in ipResult" :key="String(k)" :label="fieldLabel(k)">
                <code>{{ v ?? "—" }}</code>
              </n-descriptions-item>
            </n-descriptions>
          </n-card>

          <!-- IP ∈ CIDR -->
          <n-card size="small"><template #header><span class="nu-h"><n-icon :size="16"><AddressesIcon /></n-icon>{{ t('tools_page.t_in_cidr') }}</span></template>
            <div class="nu-row">
              <n-input v-model:value="nu.inCidrIp" placeholder="192.168.1.50" @keyup.enter="nuInCidr" />
              <n-input v-model:value="nu.inCidrCidr" placeholder="192.168.1.0/24" @keyup.enter="nuInCidr" />
              <n-button type="primary" class="nu-go" @click="nuInCidr"><template #icon><n-icon><SearchIcon /></n-icon></template>{{ t("tools_page.lookup") }}</n-button>
            </div>
            <n-tag v-if="nu.inCidrRes" :type="nu.inCidrRes.contains ? 'success' : 'warning'" style="margin-top:10px">
              {{ nu.inCidrRes.contains ? t('tools_page.contained') : t('tools_page.not_contained') }}
            </n-tag>
          </n-card>

          <!-- Netmask -->
          <n-card size="small"><template #header><span class="nu-h"><n-icon :size="16"><SubnetsIcon /></n-icon>{{ t('tools_page.t_netmask') }}</span></template>
            <div class="nu-row">
              <n-input v-model:value="nu.nmVal" placeholder="255.255.255.0 / 24 / /24" @keyup.enter="nuNm" />
              <n-button type="primary" class="nu-go" @click="nuNm"><template #icon><n-icon><SearchIcon /></n-icon></template>{{ t("tools_page.lookup") }}</n-button>
            </div>
            <div v-if="nu.nmRes" style="margin-top:10px">
              <code>/{{ nu.nmRes.prefixlen }}</code> · {{ nu.nmRes.netmask }} · wildcard {{ nu.nmRes.wildcard }}
            </div>
          </n-card>

          <!-- EUI-64 -->
          <n-card size="small"><template #header><span class="nu-h"><n-icon :size="16"><ListIcon /></n-icon>{{ t('tools_page.eui64') }}</span></template>
            <div class="nu-row">
              <n-input v-model:value="macInput" placeholder="00:11:22:33:44:55" @keyup.enter="runEui64" />
              <n-input v-model:value="prefixInput" placeholder="2001:db8::/64" @keyup.enter="runEui64" />
              <n-button type="primary" class="nu-go" @click="runEui64"><template #icon><n-icon><SearchIcon /></n-icon></template>{{ t("tools_page.lookup") }}</n-button>
            </div>
            <n-descriptions v-if="eui64Result" bordered :column="1" size="small" style="margin-top:10px">
              <n-descriptions-item v-for="(v, k) in eui64Result" :key="String(k)" :label="fieldLabel(k)">
                <code>{{ v ?? "—" }}</code>
              </n-descriptions-item>
            </n-descriptions>
          </n-card>

          <!-- GeoIP -->
          <n-card size="small"><template #header><span class="nu-h"><n-icon :size="16"><AddressesIcon /></n-icon>{{ t('tools_page.t_geoip') }}</span></template>
            <div class="nu-row">
              <n-input v-model:value="geo.ip" placeholder="8.8.8.8" @keyup.enter="runGeo" />
              <n-button type="primary" class="nu-go" @click="runGeo"><template #icon><n-icon><SearchIcon /></n-icon></template>{{ t("tools_page.lookup") }}</n-button>
            </div>
            <div v-if="geo.res" style="margin-top:10px; font-size:12px; line-height:1.7">
              <div v-if="geo.res.error"><n-tag type="warning">{{ geo.res.error === 'not_configured' ? t('tools_page.geoip_not_set') : geo.res.error }}</n-tag></div>
              <template v-else>
                <div><strong>{{ geo.res.country || '—' }}</strong> <span v-if="geo.res.country_iso">({{ geo.res.country_iso }})</span> {{ geo.res.city || '' }} {{ (geo.res.subdivisions||[]).join(' / ') }}</div>
                <div v-if="geo.res.latitude != null">lat/lng: {{ geo.res.latitude }}, {{ geo.res.longitude }} <span v-if="geo.res.time_zone">· {{ geo.res.time_zone }}</span></div>
                <div v-if="geo.res.asn">AS{{ geo.res.asn }} {{ geo.res.as_org || '' }}</div>
                <div v-if="geo.res.network">{{ geo.res.network }}</div>
              </template>
            </div>
          </n-card>
        </div>
      </n-tab-pane>

      <!-- ═══════════ 子網路 / CIDR ═══════════ -->
      <n-tab-pane name="cidr">
        <template #tab>
          <span class="tab-h"><n-icon :size="16"><SubnetsIcon /></n-icon>{{ t('tools_page.cat_cidr') }}</span>
        </template>
        <div class="nu-grid">
          <!-- CIDR 資訊 -->
          <n-card size="small"><template #header><span class="nu-h"><n-icon :size="16"><SubnetsIcon /></n-icon>{{ t('tools_page.cidr_info') }}</span></template>
            <div class="nu-row">
              <n-input v-model:value="cidrInput" placeholder="192.168.0.0/24" @keyup.enter="runCidrInfo" />
              <n-button type="primary" class="nu-go" @click="runCidrInfo"><template #icon><n-icon><SearchIcon /></n-icon></template>{{ t("tools_page.lookup") }}</n-button>
            </div>
            <n-descriptions v-if="cidrResult" bordered :column="1" size="small" style="margin-top:10px" label-placement="left"
                            label-align="right" :label-style="{ whiteSpace: 'nowrap' }">
              <n-descriptions-item v-for="(v, k) in cidrResult" :key="String(k)" :label="fieldLabel(k)">
                <code>{{ v ?? "—" }}</code>
              </n-descriptions-item>
            </n-descriptions>
          </n-card>

          <!-- CIDR 切割 -->
          <n-card size="small"><template #header><span class="nu-h"><n-icon :size="16"><GridIcon /></n-icon>{{ t('tools_page.cidr_split') }}</span></template>
            <div class="nu-row">
              <n-input v-model:value="splitCidr" placeholder="192.168.0.0/24" />
              <n-input-number v-model:value="splitNew" :min="0" :max="128" placeholder="new prefix" style="width: 140px; flex:0 0 auto" />
              <n-button type="primary" class="nu-go" @click="runSplit"><template #icon><n-icon><SearchIcon /></n-icon></template>{{ t("tools_page.split_btn") }}</n-button>
            </div>
            <n-code v-if="splitResult" :code="splitResult.subnets.join('\n')" language="plain" style="margin-top:10px; display:block" />
          </n-card>

          <!-- CIDR 關係 -->
          <n-card size="small"><template #header><span class="nu-h"><n-icon :size="16"><GridIcon /></n-icon>{{ t('tools_page.t_relation') }}</span></template>
            <div class="nu-row">
              <n-input v-model:value="nu.relA" placeholder="10.0.0.0/8" @keyup.enter="nuRel" />
              <n-input v-model:value="nu.relB" placeholder="10.1.0.0/16" @keyup.enter="nuRel" />
              <n-button type="primary" class="nu-go" @click="nuRel"><template #icon><n-icon><SearchIcon /></n-icon></template>{{ t("tools_page.lookup") }}</n-button>
            </div>
            <n-tag v-if="nu.relRes" type="info" style="margin-top:10px">{{ nu.relRes.relation }}</n-tag>
          </n-card>

          <!-- Range → CIDR -->
          <n-card size="small"><template #header><span class="nu-h"><n-icon :size="16"><SubnetsIcon /></n-icon>{{ t('tools_page.t_range2cidr') }}</span></template>
            <div class="nu-row">
              <n-input v-model:value="nu.rStart" placeholder="192.168.1.10" @keyup.enter="nuRange" />
              <n-input v-model:value="nu.rEnd" placeholder="192.168.1.200" @keyup.enter="nuRange" />
              <n-button type="primary" class="nu-go" @click="nuRange"><template #icon><n-icon><SearchIcon /></n-icon></template>{{ t("tools_page.lookup") }}</n-button>
            </div>
            <n-code v-if="nu.rRes" :code="nu.rRes.cidrs.join('\n')" language="plain" style="margin-top:10px; display:block" />
          </n-card>

          <!-- CIDR → Range -->
          <n-card size="small"><template #header><span class="nu-h"><n-icon :size="16"><ListIcon /></n-icon>{{ t('tools_page.t_cidr2range') }}</span></template>
            <div class="nu-row">
              <n-input v-model:value="nu.c2rCidr" placeholder="192.168.1.0/24" @keyup.enter="nuC2r" />
              <n-button type="primary" class="nu-go" @click="nuC2r"><template #icon><n-icon><SearchIcon /></n-icon></template>{{ t("tools_page.lookup") }}</n-button>
            </div>
            <div v-if="nu.c2rRes" style="margin-top:10px">
              <code>{{ nu.c2rRes.first }} – {{ nu.c2rRes.last }}</code> · {{ nu.c2rRes.num_addresses }}
            </div>
          </n-card>

          <!-- Aggregate -->
          <n-card size="small"><template #header><span class="nu-h"><n-icon :size="16"><GridIcon /></n-icon>{{ t('tools_page.t_aggregate') }}</span></template>
            <div class="nu-row">
              <n-input v-model:value="nu.aggIn" type="textarea" :autosize="{ minRows: 1, maxRows: 4 }" placeholder="192.168.0.0/24, 192.168.1.0/24" />
              <n-button type="primary" class="nu-go" @click="nuAgg"><template #icon><n-icon><SearchIcon /></n-icon></template>{{ t("tools_page.lookup") }}</n-button>
            </div>
            <n-code v-if="nu.aggRes" :code="nu.aggRes.aggregated.join('\n')" language="plain" style="margin-top:10px; display:block" />
          </n-card>
        </div>
      </n-tab-pane>

      <!-- ═══════════ MAC / 廠商 ═══════════ -->
      <n-tab-pane name="mac">
        <template #tab>
          <span class="tab-h"><n-icon :size="16"><DevicesIcon /></n-icon>{{ t('tools_page.cat_mac') }}</span>
        </template>
        <div class="nu-grid">
          <!-- MAC format -->
          <n-card size="small"><template #header><span class="nu-h"><n-icon :size="16"><DevicesIcon /></n-icon>{{ t('tools_page.t_mac') }}</span></template>
            <div class="nu-row">
              <n-input v-model:value="nu.macVal" placeholder="00:11:22:33:44:55" @keyup.enter="nuMac" />
              <n-button type="primary" class="nu-go" @click="nuMac"><template #icon><n-icon><SearchIcon /></n-icon></template>{{ t("tools_page.lookup") }}</n-button>
            </div>
            <n-descriptions v-if="nu.macRes" bordered :column="2" size="small" style="margin-top:10px" label-align="right">
              <n-descriptions-item label="colon"><code>{{ nu.macRes.colon }}</code></n-descriptions-item>
              <n-descriptions-item label="dash"><code>{{ nu.macRes.dash }}</code></n-descriptions-item>
              <n-descriptions-item label="cisco"><code>{{ nu.macRes.cisco_dot }}</code></n-descriptions-item>
              <n-descriptions-item label="bare"><code>{{ nu.macRes.bare }}</code></n-descriptions-item>
              <n-descriptions-item label="OUI"><code>{{ nu.macRes.oui }}</code></n-descriptions-item>
              <n-descriptions-item label="local/mcast"><code>{{ nu.macRes.is_local }} / {{ nu.macRes.is_multicast }}</code></n-descriptions-item>
            </n-descriptions>
          </n-card>

          <!-- MAC 製造商 (OUI) -->
          <n-card size="small"><template #header><span class="nu-h"><n-icon :size="16"><DevicesIcon /></n-icon>{{ t('tools_page.oui_tab') }}</span></template>
            <n-space vertical :size="14">
              <n-alert type="info" size="small">
                {{ t('tools_page.oui_alert_pre') }} <code>manuf</code> {{ t('tools_page.oui_alert_post') }}
                {{ t('tools_page.oui_alert_note') }}
              </n-alert>
              <n-space align="center" :wrap="true">
                <span>{{ t('tools_page.oui_db_count') }}</span>
                <n-tag type="info">{{ ouiStats?.count?.toLocaleString() ?? "—" }}</n-tag>
                <span style="margin-left: 16px">{{ t('tools_page.oui_last_updated') }}</span>
                <n-tag>{{ fmtDateTime(ouiStats?.last_updated) }}</n-tag>
                <span style="margin-left: 12px; font-size: 12px; opacity: .6">{{ t('tools_page.oui_managed_in_admin') }}</span>
              </n-space>
              <div>
                <div class="nu-row">
                  <n-input v-model:value="ouiLookupMac" placeholder="00:11:22:33:44:55" @keyup.enter="runOuiLookup" />
                  <n-button type="primary" class="nu-go" :loading="ouiLookupBusy" @click="runOuiLookup"><template #icon><n-icon><SearchIcon /></n-icon></template>{{ t('tools_page.oui_query') }}</n-button>
                </div>
                <div v-if="ouiLookupResult !== null" style="margin-top: 10px">
                  {{ t('tools_page.oui_vendor_label') }}
                  <n-tag v-if="ouiLookupResult" type="success">{{ ouiLookupResult }}</n-tag>
                  <n-tag v-else type="warning">{{ t('tools_page.oui_not_found') }}</n-tag>
                </div>
              </div>
              <!-- 依首碼 / 廠商名搜尋多筆 -->
              <div>
                <div class="nu-row">
                  <n-input v-model:value="ouiSearchTerm" :placeholder="t('tools_page.oui_search_ph')" @keyup.enter="runOuiSearch" />
                  <n-button type="primary" class="nu-go" :loading="ouiSearchBusy" @click="runOuiSearch"><template #icon><n-icon><SearchIcon /></n-icon></template>{{ t('tools_page.lookup') }}</n-button>
                </div>
                <div v-if="ouiSearchRes" style="margin-top: 10px">
                  <div style="margin-bottom:6px; font-size:12px; opacity:.75">
                    {{ t('tools_page.oui_search_count', { n: ouiSearchRes.count }) }}<span v-if="ouiSearchRes.truncated"> +</span>
                  </div>
                  <n-descriptions v-if="ouiSearchRes.vendors.length" bordered :column="1" size="small" label-align="right">
                    <n-descriptions-item v-for="v in ouiSearchRes.vendors" :key="v.prefix" :label="v.prefix">
                      {{ v.name }}<span v-if="v.short_name && v.short_name !== v.name" style="opacity:.6"> ({{ v.short_name }})</span>
                    </n-descriptions-item>
                  </n-descriptions>
                  <n-tag v-else type="warning">{{ t('tools_page.oui_not_found') }}</n-tag>
                </div>
              </div>
            </n-space>
          </n-card>
        </div>
      </n-tab-pane>

      <!-- ═══════════ DNS / 網域 ═══════════ -->
      <n-tab-pane name="dns">
        <template #tab>
          <span class="tab-h"><n-icon :size="16"><DnsIcon /></n-icon>{{ t('tools_page.cat_dns') }}</span>
        </template>
        <div class="nu-grid">
          <!-- DNS lookup -->
          <n-card size="small"><template #header><span class="nu-h"><n-icon :size="16"><DnsIcon /></n-icon>{{ t('tools_page.t_dns') }}</span></template>
            <div class="nu-row">
              <n-input v-model:value="nu.dnsName" placeholder="example.com / 8.8.8.8(PTR)" @keyup.enter="nuDns" />
              <n-select v-model:value="nu.dnsType" :options="dnsTypeOpts" style="width: 110px; flex: 0 0 auto" />
              <n-button type="primary" class="nu-go" @click="nuDns"><template #icon><n-icon><SearchIcon /></n-icon></template>{{ t("tools_page.lookup") }}</n-button>
            </div>
            <div v-if="nu.dnsRes" style="margin-top:10px">
              <div v-if="nu.dnsRes.error"><n-tag type="warning">{{ nu.dnsRes.error }}</n-tag></div>
              <template v-else>
                <div v-if="nu.dnsRes.A"><strong>A:</strong> <code>{{ nu.dnsRes.A.join(', ') || '—' }}</code></div>
                <div v-if="nu.dnsRes.AAAA"><strong>AAAA:</strong> <code>{{ nu.dnsRes.AAAA.join(', ') || '—' }}</code></div>
                <div v-if="nu.dnsRes.ptr"><strong>PTR:</strong> <code>{{ nu.dnsRes.ptr.join(', ') }}</code></div>
              </template>
            </div>
          </n-card>

          <!-- FQDN parse -->
          <n-card size="small"><template #header><span class="nu-h"><n-icon :size="16"><DnsIcon /></n-icon>{{ t('tools_page.t_fqdn') }}</span></template>
            <div class="nu-row">
              <n-input v-model:value="nu.fqdnVal" placeholder="sw1.dc.example.com" @keyup.enter="nuFqdn" />
              <n-button type="primary" class="nu-go" @click="nuFqdn"><template #icon><n-icon><SearchIcon /></n-icon></template>{{ t("tools_page.lookup") }}</n-button>
            </div>
            <div v-if="nu.fqdnRes" style="margin-top:10px">
              <n-tag :type="nu.fqdnRes.valid ? 'success' : 'error'">{{ nu.fqdnRes.valid ? t('tools_page.valid') : t('tools_page.invalid') }}</n-tag>
              <span v-if="nu.fqdnRes.valid" style="margin-left:8px">
                host=<code>{{ nu.fqdnRes.host }}</code> · domain=<code>{{ nu.fqdnRes.domain ?? '—' }}</code> · tld=<code>{{ nu.fqdnRes.tld ?? '—' }}</code>
              </span>
            </div>
          </n-card>

          <!-- 郵件 / DNS 診斷 -->
          <n-card size="small"><template #header><span class="nu-h"><n-icon :size="16"><DnsIcon /></n-icon>{{ t('tools_page.t_mail') }}</span></template>
            <div class="nu-row">
              <n-input v-model:value="mail.domain" placeholder="example.com" @keyup.enter="runMail" />
              <n-input v-model:value="mail.selector" :placeholder="t('tools_page.dkim_selector_ph')" style="flex: 0 1 150px" @keyup.enter="runMail" />
              <n-button type="primary" class="nu-go" @click="runMail"><template #icon><n-icon><SearchIcon /></n-icon></template>{{ t("tools_page.lookup") }}</n-button>
            </div>
            <div v-if="mail.res" style="margin-top:10px; font-size:12px; line-height:1.7">
              <div><strong>MX:</strong> <code>{{ (mail.res.mx || []).join(' · ') || '—' }}</code></div>
              <div><strong>SPF:</strong> <code>{{ (mail.res.spf || []).join(' ') || '—' }}</code></div>
              <div><strong>DMARC:</strong> <code>{{ (mail.res.dmarc || []).join(' ') || '—' }}</code></div>
              <div v-if="mail.res.dkim"><strong>DKIM ({{ mail.res.dkim_selector }}):</strong> <code style="word-break:break-all">{{ (mail.res.dkim || []).join(' ') || '—' }}</code></div>
            </div>
          </n-card>
        </div>
      </n-tab-pane>

      <!-- ═══════════ 機房 / 電力 ═══════════ -->
      <n-tab-pane name="power">
        <template #tab>
          <span class="tab-h"><n-icon :size="16"><PowerIcon /></n-icon>{{ t('tools_page.cat_power') }}</span>
        </template>
        <div class="nu-grid">
          <n-card size="small"><template #header><span class="nu-h"><n-icon :size="16"><PowerIcon /></n-icon>{{ t('tools_page.pw_load') }}</span></template>
            <div class="nu-row">
              <n-input-number v-model:value="pw.volts" :min="1" :show-button="false" style="width: 96px"><template #suffix>V</template></n-input-number>
              <n-input-number v-model:value="pw.amps" :min="0" :show-button="false" style="width: 96px"><template #suffix>A</template></n-input-number>
              <n-input-number v-model:value="pw.pf" :min="0" :max="1" :step="0.01" :show-button="false" style="width: 96px"><template #suffix>PF</template></n-input-number>
              <n-select v-model:value="pw.phase" :options="phaseOpts" style="width: 100px; flex:0 0 auto" />
            </div>
            <div style="margin-top:10px"><strong>{{ pwLoadW.toLocaleString() }} W</strong> · {{ (pwLoadW/1000).toFixed(2) }} kW</div>
          </n-card>
          <n-card size="small"><template #header><span class="nu-h"><n-icon :size="16"><PowerIcon /></n-icon>{{ t('tools_page.pw_heat') }}</span></template>
            <div class="nu-row">
              <n-input-number v-model:value="pw.watts" :min="0" :step="100" :show-button="false" style="width: 140px"><template #suffix>W</template></n-input-number>
            </div>
            <div style="margin-top:10px"><strong>{{ pwBtu.toLocaleString() }} BTU/hr</strong></div>
          </n-card>
          <n-card size="small"><template #header><span class="nu-h"><n-icon :size="16"><PowerIcon /></n-icon>{{ t('tools_page.pw_ups') }}</span></template>
            <div class="nu-row">
              <n-input-number v-model:value="pw.battWh" :min="0" :step="100" :show-button="false" style="width: 120px"><template #suffix>Wh</template></n-input-number>
              <n-input-number v-model:value="pw.loadW" :min="1" :step="50" :show-button="false" style="width: 120px"><template #suffix>W</template></n-input-number>
            </div>
            <div style="margin-top:10px"><strong>≈ {{ pwUpsMin }} {{ t('tools_page.pw_minutes') }}</strong></div>
          </n-card>
          <n-card size="small"><template #header><span class="nu-h"><n-icon :size="16"><PowerIcon /></n-icon>{{ t('tools_page.pw_pdu') }}</span></template>
            <div class="nu-row">
              <n-input-number v-model:value="pw.pduA" :min="1" :show-button="false" style="width: 140px"><template #suffix>A</template></n-input-number>
            </div>
            <div style="margin-top:10px"><strong>{{ pwPduSafe }} A</strong> ({{ t('tools_page.pw_safe80') }})</div>
          </n-card>
        </div>
      </n-tab-pane>
    </n-tabs>
  </n-card>
</template>

<style scoped>
/* 工具分類分頁標籤：icon + 文字 */
.tab-h { display: inline-flex; align-items: center; gap: 6px; }
/* 卡片標題列右側副標：說明這些工具 AI 也能用 */
.tools-subtitle { font-size: 12px; opacity: 0.7; }

/* 每個分類底下的工具一律一排兩個；寬度不夠（窄螢幕）才自動變一排一個 */
.nu-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  align-items: start;
}
@media (max-width: 820px) {
  .nu-grid { grid-template-columns: 1fr; }
}
/* 每個小工具的輸入 + 查詢鈕排成一列；窄時自動換行 */
.nu-row {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
.nu-row > .n-input { flex: 1 1 140px; min-width: 0; }
.nu-go { flex: 0 0 auto; }
.nu-h { display: inline-flex; align-items: center; gap: 6px; font-weight: 500; }
/* 每個小工具卡片要有明顯邊框，深色主題也分得出來 */
.nu-grid :deep(.n-card) {
  border: 1px solid rgba(127, 127, 127, 0.28);
  background: rgba(127, 127, 127, 0.035);
}
.nu-grid :deep(.n-card > .n-card-header) {
  border-bottom: 1px solid rgba(127, 127, 127, 0.18);
}
</style>
