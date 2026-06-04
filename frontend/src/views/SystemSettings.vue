<script setup lang="ts">
/**
 * 系統設定（僅管理員）— 全域、所有使用者共用的設定，獨立於「個人設定」。
 * 含：地圖供應商、機櫃名稱對齊、上線判定閾值、GeoIP(MaxMind) 本地資料庫與排程。
 */
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NSpace, NIcon, NSelect, NInput, NInputNumber, NSwitch, NButton, NTag, useMessage,
} from "naive-ui";
import { AdminIcon, SaveIcon, RefreshIcon, CopyIcon } from "@/icons";
import { getGraylogDsv, putGraylogDsv } from "@/api/system";
import { fmtDateTime, fmtRelative } from "@/utils/datetime";
import {
  getMapProvider, setMapProvider, getRackNameAlign, setRackNameAlign,
  getOnlineGrace, setOnlineGrace,
  getGeoipConfig, setGeoipConfig, updateGeoipDbNow,
  type GeoIPConfig, type RackNameAlign,
} from "@/api/basic";

const { t } = useI18n();
const msg = useMessage();

// 地圖供應商
const mapProvider = ref<"osm" | "google">("osm");
const mapProviderOpts = [
  { label: "OpenStreetMap", value: "osm" },
  { label: "Google Maps", value: "google" },
];
async function changeMapProvider(p: "osm" | "google") {
  mapProvider.value = p;
  try { await setMapProvider(p); msg.success(t("common.ok")); } catch { msg.error(t("errors.network")); }
}

// 機櫃裝置名稱對齊
const rackAlign = ref<RackNameAlign>("left");
const rackAlignOpts = computed(() => [
  { label: t("settings.system.align_left"), value: "left" },
  { label: t("settings.system.align_center"), value: "center" },
  { label: t("settings.system.align_right"), value: "right" },
]);
async function changeRackAlign(a: RackNameAlign) {
  rackAlign.value = a;
  try { await setRackNameAlign(a); msg.success(t("common.ok")); } catch { msg.error(t("errors.network")); }
}

// 上線判定閾值（分鐘）
const grace = ref(30);
async function changeGrace(v: number | null) {
  const n = v ?? 30;
  grace.value = n;
  try { await setOnlineGrace(n); msg.success(t("common.ok")); } catch { msg.error(t("errors.network")); }
}

// GeoIP
const geoip = ref<GeoIPConfig | null>(null);
const geoipAccount = ref("");
const geoipKey = ref("");
const geoipSaving = ref(false);
const geoipUpdating = ref(false);
const geoipEditionOpts = computed(() => (geoip.value?.all_editions ?? []).map((e) => ({ label: e, value: e })));
const geoipFreqOpts = computed(() => (geoip.value?.frequencies ?? []).map((f) => ({ label: t(`settings.system.freq_${f.replace("-", "_")}`), value: f })));
async function loadGeoip() {
  try { geoip.value = await getGeoipConfig(); geoipAccount.value = geoip.value.account_id ?? ""; } catch { /* ignore */ }
}
async function saveGeoip() {
  if (!geoip.value) return;
  geoipSaving.value = true;
  try {
    geoip.value = await setGeoipConfig({
      account_id: geoipAccount.value.trim() || null,
      license_key: geoipKey.value.trim() || null,
      editions: geoip.value.editions,
      auto_update: geoip.value.auto_update,
      frequency: geoip.value.frequency,
    });
    geoipKey.value = "";
    msg.success(t("common.saved"));
  } catch { msg.error(t("errors.network")); } finally { geoipSaving.value = false; }
}
async function updateGeoipNow() {
  geoipUpdating.value = true;
  try {
    const r = await updateGeoipDbNow();
    geoip.value = r.config;
    if (r.result?.error === "not_configured") msg.warning(t("settings.system.geoip_need_creds"));
    else msg.success(t("settings.system.geoip_updated"));
  } catch { msg.error(t("errors.network")); } finally { geoipUpdating.value = false; }
}
function fmtBytes(n: number | null): string {
  if (!n) return "—";
  return n > 1e6 ? (n / 1e6).toFixed(1) + " MB" : (n / 1e3).toFixed(0) + " KB";
}

// Graylog DSV 查表
const dsv = ref({ enabled: false, fmt: "csv", path: "ip-fqdn", token: "" });
const dsvSaving = ref(false);
const dsvFmtOpts = [{ label: "CSV (,)", value: "csv" }, { label: "TSV (Tab)", value: "tsv" }];
const dsvUrl = computed(() =>
  dsv.value.token ? `${location.origin}/api/v1/lookup/${dsv.value.path}?token=${dsv.value.token}` : "");
async function loadDsv() { try { dsv.value = await getGraylogDsv(); } catch { /* ignore */ } }
async function saveDsv(regenerate = false) {
  dsvSaving.value = true;
  try {
    dsv.value = await putGraylogDsv({
      enabled: dsv.value.enabled, fmt: dsv.value.fmt, path: dsv.value.path, regenerate_token: regenerate,
    });
    msg.success(t("common.saved"));
  } catch { msg.error(t("errors.network")); } finally { dsvSaving.value = false; }
}
function copyDsvUrl() {
  if (dsvUrl.value) { void navigator.clipboard.writeText(dsvUrl.value); msg.success(t("common.ok")); }
}

onMounted(() => {
  getMapProvider().then((p) => { mapProvider.value = p; }).catch(() => {});
  getRackNameAlign().then((a) => { rackAlign.value = a; }).catch(() => {});
  getOnlineGrace().then((m) => { grace.value = m; }).catch(() => {});
  void loadGeoip();
  void loadDsv();
});
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><AdminIcon /></n-icon>
        <span>{{ t("system_settings.title") }}</span>
      </n-space>
    </template>
    <div class="ss-wrap">
      <!-- 顯示與地圖 -->
      <section class="ss-group">
        <h3 class="ss-h">{{ t("system_settings.grp_display") }}</h3>
        <div class="ss-grid">
          <div class="fld">
            <label>{{ t("settings.system.map_provider") }}</label>
            <n-select :value="mapProvider" :options="mapProviderOpts" @update:value="changeMapProvider" />
            <div class="hint">{{ t("settings.system.map_provider_hint") }}</div>
          </div>
          <div class="fld">
            <label>{{ t("settings.system.rack_name_align") }}</label>
            <n-select :value="rackAlign" :options="rackAlignOpts" @update:value="changeRackAlign" />
            <div class="hint">{{ t("settings.system.rack_name_align_hint") }}</div>
          </div>
        </div>
      </section>

      <!-- 上線判定 -->
      <section class="ss-group">
        <h3 class="ss-h">{{ t("system_settings.grp_liveness") }}</h3>
        <div class="fld" style="max-width: 320px">
          <label>{{ t("settings.prefs.online_grace_minutes") }}</label>
          <n-input-number :value="grace" :min="1" :max="43200" style="width: 100%" @update:value="changeGrace" />
          <div class="hint">{{ t("settings.prefs.online_grace_minutes_hint") }}</div>
        </div>
      </section>

      <!-- GeoIP -->
      <section v-if="geoip" class="ss-group">
        <h3 class="ss-h">{{ t("settings.system.geoip") }}</h3>
        <div class="ss-grid">
          <div class="fld">
            <label>{{ t("settings.system.geoip_account") }}</label>
            <n-input v-model:value="geoipAccount" :placeholder="t('settings.system.geoip_account')" />
          </div>
          <div class="fld">
            <label>License Key</label>
            <n-input v-model:value="geoipKey" type="password" show-password-on="click"
                     :placeholder="geoip.has_key ? t('settings.system.geoip_key_set') : t('settings.system.geoip_key')" />
          </div>
        </div>
        <div class="fld" style="margin-top: 14px">
          <label>{{ t("settings.system.geoip_editions") }}</label>
          <n-select v-model:value="geoip.editions" :options="geoipEditionOpts" multiple />
          <div class="hint" style="line-height:1.5">{{ t("settings.system.geoip_asn_note") }}</div>
        </div>
        <div class="ss-row">
          <div style="display:flex; align-items:center; gap:8px">
            <n-switch v-model:value="geoip.auto_update" />
            <span style="font-size:13px">{{ t("settings.system.geoip_auto") }}</span>
          </div>
          <n-select v-model:value="geoip.frequency" :options="geoipFreqOpts" :disabled="!geoip.auto_update"
                    style="width: 200px" />
          <div style="flex:1"></div>
          <n-button size="small" :loading="geoipSaving" @click="saveGeoip">
            <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
          </n-button>
          <n-button size="small" type="primary" :loading="geoipUpdating" @click="updateGeoipNow">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("settings.system.geoip_update_now") }}
          </n-button>
        </div>
        <div class="ss-status">
          <div v-for="d in geoip.dbs" :key="d.edition" class="db-row">
            <n-tag size="tiny" :type="d.present ? 'success' : 'default'">{{ d.edition }}</n-tag>
            <span v-if="d.present" style="opacity:.7">{{ fmtBytes(d.size) }} · {{ fmtRelative(d.built_at) }}</span>
            <span v-else style="opacity:.5">{{ t("settings.system.geoip_db_missing") }}</span>
          </div>
          <div v-if="geoip.last_update_at" style="opacity:.6; margin-top:4px">
            {{ t("settings.system.geoip_last_update") }}: {{ fmtDateTime(geoip.last_update_at) }}
          </div>
          <div v-if="geoip.last_error" style="color:var(--err-color,#e88080); margin-top:4px">{{ geoip.last_error }}</div>
        </div>
        <div class="hint" style="line-height:1.6; margin-top:10px">
          {{ t("settings.system.geoip_hint") }}<br>
          {{ t("settings.system.geoip_freq_advice") }}
        </div>
      </section>

      <!-- Graylog DSV 查表 -->
      <section class="ss-group">
        <h3 class="ss-h">{{ t("settings.system.graylog_title") }}</h3>
        <div class="ss-row">
          <div style="display:flex; align-items:center; gap:8px">
            <n-switch v-model:value="dsv.enabled" @update:value="() => saveDsv()" />
            <span style="font-size:13px">{{ t("settings.system.graylog_enable") }}</span>
          </div>
        </div>
        <div class="ss-grid" style="margin-top:12px">
          <div class="fld">
            <label>{{ t("settings.system.graylog_path") }}</label>
            <n-input v-model:value="dsv.path" placeholder="ip-fqdn" />
          </div>
          <div class="fld">
            <label>{{ t("settings.system.graylog_format") }}</label>
            <n-select v-model:value="dsv.fmt" :options="dsvFmtOpts" />
          </div>
        </div>
        <div class="ss-row" style="margin-top:12px">
          <n-button size="small" :loading="dsvSaving" @click="() => saveDsv()">
            <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
          </n-button>
          <n-button size="small" @click="() => saveDsv(true)">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("settings.system.graylog_regen") }}
          </n-button>
        </div>
        <div v-if="dsvUrl" class="fld" style="margin-top:14px">
          <label>{{ t("settings.system.graylog_url") }}</label>
          <div style="display:flex; gap:8px; align-items:center">
            <n-input :value="dsvUrl" readonly style="flex:1" />
            <n-button size="small" type="primary" ghost @click="copyDsvUrl">
              <template #icon><n-icon><CopyIcon /></n-icon></template>{{ t("settings.system.graylog_copy") }}
            </n-button>
          </div>
        </div>
        <div class="hint" style="line-height:1.6; margin-top:10px">{{ t("settings.system.graylog_hint") }}</div>
      </section>
    </div>
  </n-card>
</template>

<style scoped>
.ss-wrap { display: flex; flex-direction: column; gap: 24px; max-width: 780px; }
.ss-group { border: 1px solid var(--n-border-color, rgba(127,127,127,.18)); border-radius: 14px;
  padding: 20px 22px 22px; background: rgba(127,127,127,0.028); box-shadow: 0 1px 3px rgba(15,23,42,.05); }
.ss-h { margin: 0 0 18px; font-size: 16px; font-weight: 700; padding-left: 12px; line-height: 1.25;
  border-left: 4px solid #18a058; }
.ss-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 640px) { .ss-grid { grid-template-columns: 1fr; } }
.fld label { display: block; font-size: 13px; font-weight: 500; margin-bottom: 5px; }
.hint { font-size: 11px; opacity: 0.65; margin-top: 4px; }
.ss-row { display: flex; align-items: center; gap: 12px; margin-top: 14px; flex-wrap: wrap; }
.ss-status { margin-top: 12px; font-size: 12px; display: flex; flex-direction: column; gap: 3px; }
.db-row { display: flex; gap: 8px; align-items: center; }
</style>
