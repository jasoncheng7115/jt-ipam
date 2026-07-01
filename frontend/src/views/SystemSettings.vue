<script setup lang="ts">
/**
 * 系統設定（僅管理員）— 全域、所有使用者共用的設定，獨立於「個人設定」。
 * 含：地圖供應商、機櫃名稱對齊、上線判定閾值、GeoIP(MaxMind) 本地資料庫與排程。
 */
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NSpace, NIcon, NSelect, NInput, NInputNumber, NSwitch, NCheckbox, NButton, NTag, useMessage,
} from "naive-ui";
const origin = window.location.origin;
import { AdminIcon, SaveIcon, RefreshIcon } from "@/icons";
import { getLdap, putLdap, testLdap, testLdapAuth, type LdapConfig,
  getAuditForward, putAuditForward, testAuditForward, type AuditForward,
  getOidcConfig, putOidcConfig, testOidc, type OidcConfig,
  getSamlConfig, putSamlConfig, testSaml, type SamlConfig,
  getConsoleSecurity, setConsoleSecurity,
  getUiDisplay, setUiDisplay } from "@/api/system";
import { listGroups } from "@/api/admin";
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
// 連線管理資安：RDP 控制端貼上文字到被控端（預設關閉）
const rdpClipPaste = ref(false);
async function changeRdpClipPaste(v: boolean) {
  rdpClipPaste.value = v;
  try { await setConsoleSecurity({ rdp_clipboard_paste: v }); msg.success(t("common.ok")); }
  catch { rdpClipPaste.value = !v; msg.error(t("errors.network")); }
}

// 異動記錄淡化天數（超過 N 天的項目以淡色顯示；0 = 不淡化）
const changeLogDimDays = ref(30);
async function changeDimDays(v: number | null) {
  const days = Math.max(0, Math.min(3650, Math.round(v ?? 0)));
  changeLogDimDays.value = days;
  try { await setUiDisplay({ change_log_dim_days: days }); msg.success(t("common.ok")); }
  catch { msg.error(t("errors.network")); }
}

const mapProvider = ref<"builtin" | "osm" | "google">("builtin");
const mapProviderOpts = computed(() => [
  { label: t("settings.system.map_builtin"), value: "builtin" },
  { label: "OpenStreetMap", value: "osm" },
  { label: "Google Maps", value: "google" },
]);
async function changeMapProvider(p: "builtin" | "osm" | "google") {
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

// 外部認證 / LDAP（AD）
const ldap = ref<LdapConfig>({
  enabled: false, server: null, port: 389, use_ssl: false, use_starttls: true,
  bind_dn: null, password_set: false, search_base: null,
  user_filter: "(sAMAccountName={username})", attr_email: "mail",
  attr_display_name: "displayName", attr_member_of: "memberOf", admin_groups: [],
  default_group_id: null,
});
const ldapGroups = ref<{ label: string; value: string }[]>([]);
const ldapGroupOpts = computed(() => [{ label: t("settings.system.ldap_no_default_role"), value: "" }, ...ldapGroups.value]);
const ldapDefaultGroup = computed<string>({
  get: () => ldap.value.default_group_id ?? "",
  set: (v) => { ldap.value.default_group_id = v || null; },
});
async function loadLdapGroups() {
  try { const r = await listGroups(200, 0); ldapGroups.value = r.items.map((g) => ({ label: g.name, value: g.id })); } catch { /* ignore */ }
}
const ldapPw = ref("");           // 留空＝不變更；輸入＝更新
const ldapSaving = ref(false);
const ldapTesting = ref(false);
const ldapTlsOpts = computed(() => [
  { label: "StartTLS (389)", value: "starttls" },
  { label: "LDAPS (636)", value: "ssl" },
  { label: t("settings.system.ldap_tls_none"), value: "none" },
]);
const ldapTlsMode = computed<"starttls" | "ssl" | "none">({
  get: () => ldap.value.use_ssl ? "ssl" : ldap.value.use_starttls ? "starttls" : "none",
  set: (m) => { ldap.value.use_ssl = m === "ssl"; ldap.value.use_starttls = m === "starttls"; },
});
const ldapGroupsText = computed<string>({
  get: () => ldap.value.admin_groups.join("\n"),
  set: (v) => { ldap.value.admin_groups = v.split("\n").map((s) => s.trim()).filter(Boolean); },
});
async function loadLdap() { try { ldap.value = await getLdap(); ldapPw.value = ""; } catch { /* ignore */ } }
async function saveLdap() {
  ldapSaving.value = true;
  try {
    const { password_set: _ps, ...rest } = ldap.value;
    ldap.value = await putLdap({ ...rest, bind_password: ldapPw.value ? ldapPw.value : null });
    ldapPw.value = "";
    msg.success(t("common.saved"));
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.network")); } finally { ldapSaving.value = false; }
}
async function clearLdapPw() {
  ldapSaving.value = true;
  try {
    const { password_set: _ps, ...rest } = ldap.value;
    ldap.value = await putLdap({ ...rest, bind_password: "" });
    ldapPw.value = "";
    msg.success(t("common.ok"));
  } catch { msg.error(t("errors.network")); } finally { ldapSaving.value = false; }
}
async function doTestLdap() {
  ldapTesting.value = true;
  try {
    const r = await testLdap();
    msg.success(`${t("settings.system.ldap_test_ok")} — ${r.who_am_i || r.server}`);
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("settings.system.ldap_test_fail")); }
  finally { ldapTesting.value = false; }
}

// ── OIDC SSO ──
const oidc = ref<OidcConfig>({
  enabled: false, issuer: null, client_id: null, client_secret_set: false,
  redirect_uri: null, scope: "openid profile email",
  groups_claim: "groups", username_claim: "preferred_username",
  admin_groups: [], default_group_id: null,
});
const oidcSecret = ref("");        // 留空＝不變更；輸入＝更新
const oidcSaving = ref(false);
const oidcTesting = ref(false);
const oidcAdminGroupsText = computed<string>({
  get: () => (oidc.value.admin_groups || []).join(", "),
  set: (v) => { oidc.value.admin_groups = v.split(",").map((x) => x.trim()).filter(Boolean); },
});
async function loadOidc() { try { oidc.value = await getOidcConfig(); oidcSecret.value = ""; } catch { /* ignore */ } }
async function saveOidc() {
  oidcSaving.value = true;
  try {
    const { client_secret_set: _s, ...rest } = oidc.value;
    const patch: any = { ...rest };
    if (oidcSecret.value) patch.client_secret = oidcSecret.value;  // 只在輸入時更新
    oidc.value = await putOidcConfig(patch);
    oidcSecret.value = "";
    msg.success(t("common.saved"));
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.network")); }
  finally { oidcSaving.value = false; }
}
async function doTestOidc() {
  oidcTesting.value = true;
  try {
    const r = await testOidc();   // 成功會回 discovery 資訊；失敗會丟錯
    msg.success(`${t("settings.system.oidc_test_ok")} — ${r.issuer || ""}`);
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("settings.system.oidc_test_fail")); }
  finally { oidcTesting.value = false; }
}

// ── SAML SSO ──
const saml = ref<SamlConfig>({
  enabled: false, idp_metadata_url: null, idp_metadata_xml: null,
  sp_entity_id: null, sp_acs_url: null, sp_sls_url: null, sp_x509_cert: null,
  sp_private_key_set: false, want_assertions_signed: true, want_assertions_encrypted: false,
  want_name_id_encrypted: false, authn_requests_signed: false,
  attr_username: "uid", attr_email: "email", attr_displayname: "displayName",
  attr_groups: "groups", admin_groups: [], default_group_id: null,
});
const samlKey = ref("");           // SP 私鑰，留空＝不變更
const samlSaving = ref(false);
const samlTesting = ref(false);
const samlAdminGroupsText = computed<string>({
  get: () => (saml.value.admin_groups || []).join(", "),
  set: (v) => { saml.value.admin_groups = v.split(",").map((x) => x.trim()).filter(Boolean); },
});
async function loadSaml() { try { saml.value = await getSamlConfig(); samlKey.value = ""; } catch { /* ignore */ } }
async function saveSaml() {
  samlSaving.value = true;
  try {
    const { sp_private_key_set: _s, ...rest } = saml.value;
    const patch: any = { ...rest };
    if (samlKey.value) patch.sp_private_key = samlKey.value;
    saml.value = await putSamlConfig(patch);
    samlKey.value = "";
    msg.success(t("common.saved"));
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.network")); }
  finally { samlSaving.value = false; }
}
async function doTestSaml() {
  samlTesting.value = true;
  try {
    const r = await testSaml();
    msg.success(`${t("settings.system.saml_test_ok")} — ${r.entity_id || ""}`);
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("settings.system.saml_test_fail")); }
  finally { samlTesting.value = false; }
}
// 用真實帳密測試完整驗證流程
const ldapTestUser = ref("");
const ldapTestPw = ref("");
const ldapAuthTesting = ref(false);
async function doTestLdapAuth() {
  if (!ldapTestUser.value || !ldapTestPw.value) { msg.warning(t("settings.system.ldap_authtest_need")); return; }
  ldapAuthTesting.value = true;
  try {
    const r = await testLdapAuth(ldapTestUser.value, ldapTestPw.value);
    msg.success(`✓ ${r.dn}${r.is_admin ? " · 管理員" : ""}${r.display_name ? " · " + r.display_name : ""}`, { duration: 8000 });
    ldapTestPw.value = "";
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("settings.system.ldap_test_fail")); }
  finally { ldapAuthTesting.value = false; }
}

// 稽核轉送到 Graylog
const af = ref<AuditForward>({ enabled: false, host: null, port: 12201, protocol: "udp", fmt: "gelf" });
const afSaving = ref(false);
const afTesting = ref(false);
const afProtoOpts = [{ label: "UDP", value: "udp" }, { label: "TCP", value: "tcp" }];
const afFmtOpts = [{ label: "GELF", value: "gelf" }, { label: "Syslog (RFC5424)", value: "syslog" }, { label: "CEF", value: "cef" }];
async function loadAf() { try { af.value = await getAuditForward(); } catch { /* ignore */ } }
async function saveAf() {
  afSaving.value = true;
  try { af.value = await putAuditForward(af.value); msg.success(t("common.saved")); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.network")); } finally { afSaving.value = false; }
}
async function doTestAf() {
  if (!af.value.host) { msg.warning(t("settings.system.af_need_host")); return; }
  afTesting.value = true;
  try { const r = await testAuditForward(af.value); msg.success(`${t("settings.system.af_test_ok")} — ${r.sent_to}`); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("settings.system.af_test_fail")); } finally { afTesting.value = false; }
}

onMounted(() => {
  getUiDisplay().then((d) => { changeLogDimDays.value = d.change_log_dim_days; }).catch(() => {});
  getConsoleSecurity().then((c) => { rdpClipPaste.value = c.rdp_clipboard_paste; }).catch(() => {});
  getMapProvider().then((p) => { mapProvider.value = p; }).catch(() => {});
  getRackNameAlign().then((a) => { rackAlign.value = a; }).catch(() => {});
  getOnlineGrace().then((m) => { grace.value = m; }).catch(() => {});
  void loadGeoip();
  void loadLdap();
  void loadLdapGroups();
  void loadOidc();
  void loadSaml();
  void loadAf();
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
      <!-- 資安：連線管理 -->
      <section class="ss-group">
        <h3 class="ss-h">{{ t("system_settings.grp_security") }}</h3>
        <div class="ss-grid">
          <div class="fld">
            <label>{{ t("settings.system.rdp_clip_paste") }}</label>
            <n-switch :value="rdpClipPaste" @update:value="changeRdpClipPaste" />
            <div class="hint">{{ t("settings.system.rdp_clip_paste_hint") }}</div>
          </div>
        </div>
      </section>

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
          <div class="fld">
            <label>{{ t("settings.system.change_log_dim_days") }}</label>
            <n-input-number :value="changeLogDimDays" :min="0" :max="3650" :step="1"
                            @update:value="changeDimDays" style="width: 160px" />
            <div class="hint">{{ t("settings.system.change_log_dim_days_hint") }}</div>
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

      <!-- 外部認證 / LDAP（AD） -->
      <section class="ss-group">
        <h3 class="ss-h">{{ t("settings.system.ldap_title") }}</h3>
        <div class="fld">
          <n-space align="center">
            <n-switch v-model:value="ldap.enabled" />
            <span style="font-size:13px">{{ t("settings.system.ldap_enable") }}</span>
          </n-space>
        </div>
        <div style="display:grid; grid-template-columns:1fr 140px; gap:12px">
          <div class="fld">
            <label>{{ t("settings.system.ldap_server") }}</label>
            <n-input v-model:value="ldap.server" placeholder="dc01.example.com" />
          </div>
          <div class="fld">
            <label>{{ t("settings.system.ldap_port") }}</label>
            <n-input-number v-model:value="ldap.port" :min="1" :max="65535" style="width:100%" />
          </div>
        </div>
        <div class="fld">
          <label>{{ t("settings.system.ldap_tls") }}</label>
          <n-select v-model:value="ldapTlsMode" :options="ldapTlsOpts" />
        </div>
        <div class="fld">
          <label>{{ t("settings.system.ldap_bind_dn") }}</label>
          <n-input v-model:value="ldap.bind_dn" placeholder="CN=svc-ipam,OU=Svc,DC=example,DC=com" />
        </div>
        <div class="fld">
          <label>{{ t("settings.system.ldap_bind_pw") }}</label>
          <div style="display:flex; gap:8px; align-items:center">
            <n-input v-model:value="ldapPw" type="password" show-password-on="click"
                     :placeholder="ldap.password_set ? t('settings.system.ldap_pw_set') : t('settings.system.ldap_pw_unset')"
                     style="flex:1" />
            <n-button v-if="ldap.password_set" size="small" quaternary type="error" @click="clearLdapPw">
              {{ t("settings.system.ldap_pw_clear") }}
            </n-button>
          </div>
        </div>
        <div class="fld">
          <label>{{ t("settings.system.ldap_search_base") }}</label>
          <n-input v-model:value="ldap.search_base" placeholder="DC=example,DC=com" />
        </div>
        <div class="fld">
          <label>{{ t("settings.system.ldap_user_filter") }}</label>
          <n-input v-model:value="ldap.user_filter" placeholder="(sAMAccountName={username})" />
          <div class="hint" style="margin-top:4px">{{ t("settings.system.ldap_user_filter_hint") }}</div>
        </div>
        <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:12px">
          <div class="fld">
            <label>{{ t("settings.system.ldap_attr_email") }}</label>
            <n-input v-model:value="ldap.attr_email" placeholder="mail" />
          </div>
          <div class="fld">
            <label>{{ t("settings.system.ldap_attr_name") }}</label>
            <n-input v-model:value="ldap.attr_display_name" placeholder="displayName" />
          </div>
          <div class="fld">
            <label>{{ t("settings.system.ldap_attr_groups") }}</label>
            <n-input v-model:value="ldap.attr_member_of" placeholder="memberOf" />
          </div>
        </div>
        <div class="fld">
          <label>{{ t("settings.system.ldap_admin_groups") }}</label>
          <n-input v-model:value="ldapGroupsText" type="textarea" :autosize="{ minRows: 2, maxRows: 5 }"
                   placeholder="CN=IPAM-Admins,OU=Groups,DC=example,DC=com" />
          <div class="hint" style="margin-top:4px">{{ t("settings.system.ldap_admin_groups_hint") }}</div>
        </div>
        <div class="fld">
          <label>{{ t("settings.system.ldap_default_role") }}</label>
          <n-select v-model:value="ldapDefaultGroup" :options="ldapGroupOpts" />
          <div class="hint" style="margin-top:4px">{{ t("settings.system.ldap_default_role_hint") }}</div>
        </div>
        <n-space style="margin-top:6px">
          <n-button type="primary" :loading="ldapSaving" @click="saveLdap">
            <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
          </n-button>
          <n-button :loading="ldapTesting" @click="doTestLdap">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("settings.system.ldap_test") }}
          </n-button>
        </n-space>
        <div class="fld" style="margin-top:14px; border-top:1px dashed var(--n-border-color,#eee); padding-top:12px">
          <label>{{ t("settings.system.ldap_authtest") }}</label>
          <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap">
            <n-input v-model:value="ldapTestUser" :placeholder="t('settings.system.ldap_authtest_user')" style="flex:1; min-width:140px" />
            <n-input v-model:value="ldapTestPw" type="password" show-password-on="click" :placeholder="t('settings.system.ldap_authtest_pw')" style="flex:1; min-width:140px" @keyup.enter="doTestLdapAuth" />
            <n-button :loading="ldapAuthTesting" @click="doTestLdapAuth">
              <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("settings.system.ldap_authtest_btn") }}
            </n-button>
          </div>
          <div class="hint" style="margin-top:4px">{{ t("settings.system.ldap_authtest_hint") }}</div>
        </div>
        <div class="hint" style="line-height:1.6; margin-top:10px">{{ t("settings.system.ldap_hint") }}</div>
      </section>

      <!-- 單一登入 (OIDC) -->
      <section class="ss-group">
        <h3 class="ss-h">{{ t("settings.system.oidc_title") }}</h3>
        <div class="fld">
          <n-space align="center">
            <n-switch v-model:value="oidc.enabled" />
            <span style="font-size:13px">{{ t("settings.system.oidc_enable") }}</span>
          </n-space>
        </div>
        <div class="fld">
          <label>{{ t("settings.system.oidc_issuer") }}</label>
          <n-input v-model:value="oidc.issuer" placeholder="https://idp.example.com/realms/main" />
        </div>
        <div class="fld" style="display:flex; gap:12px; flex-wrap:wrap">
          <div style="flex:1; min-width:200px">
            <label>{{ t("settings.system.oidc_client_id") }}</label>
            <n-input v-model:value="oidc.client_id" />
          </div>
          <div style="flex:1; min-width:200px">
            <label>{{ t("settings.system.oidc_client_secret") }}</label>
            <n-input v-model:value="oidcSecret" type="password" show-password-on="click"
                     :placeholder="oidc.client_secret_set ? t('settings.system.oidc_secret_keep') : ''" />
          </div>
        </div>
        <div class="fld">
          <label>{{ t("settings.system.oidc_redirect_uri") }}</label>
          <n-input v-model:value="oidc.redirect_uri" placeholder="https://ipam.example.com/api/v1/auth/oidc/callback" />
        </div>
        <div class="fld" style="display:flex; gap:12px; flex-wrap:wrap">
          <div style="flex:1; min-width:160px">
            <label>{{ t("settings.system.oidc_scope") }}</label>
            <n-input v-model:value="oidc.scope" />
          </div>
          <div style="flex:1; min-width:140px">
            <label>{{ t("settings.system.oidc_username_claim") }}</label>
            <n-input v-model:value="oidc.username_claim" />
          </div>
          <div style="flex:1; min-width:140px">
            <label>{{ t("settings.system.oidc_groups_claim") }}</label>
            <n-input v-model:value="oidc.groups_claim" />
          </div>
        </div>
        <div class="fld">
          <label>{{ t("settings.system.oidc_admin_groups") }}</label>
          <n-input v-model:value="oidcAdminGroupsText" :placeholder="t('settings.system.oidc_admin_groups_ph')" />
        </div>
        <n-space style="margin-top:8px">
          <n-button type="primary" :loading="oidcSaving" @click="saveOidc">
            <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
          </n-button>
          <n-button :loading="oidcTesting" @click="doTestOidc">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("settings.system.oidc_test") }}
          </n-button>
        </n-space>
        <div class="hint" style="line-height:1.6; margin-top:10px">{{ t("settings.system.oidc_hint") }}</div>
      </section>

      <!-- 單一登入 (SAML 2.0) -->
      <section class="ss-group">
        <h3 class="ss-h">{{ t("settings.system.saml_title") }}</h3>
        <div class="fld">
          <n-space align="center">
            <n-switch v-model:value="saml.enabled" />
            <span style="font-size:13px">{{ t("settings.system.saml_enable") }}</span>
          </n-space>
        </div>
        <div class="fld">
          <label>{{ t("settings.system.saml_idp_metadata_url") }}</label>
          <n-input v-model:value="saml.idp_metadata_url" placeholder="https://idp.example.com/metadata" />
        </div>
        <div class="fld">
          <label>{{ t("settings.system.saml_idp_metadata_xml") }}</label>
          <n-input v-model:value="saml.idp_metadata_xml" type="textarea" :rows="3"
                   :placeholder="t('settings.system.saml_idp_metadata_xml_ph')" />
        </div>
        <div class="fld" style="display:flex; gap:12px; flex-wrap:wrap">
          <div style="flex:1; min-width:200px">
            <label>{{ t("settings.system.saml_sp_entity_id") }}</label>
            <n-input v-model:value="saml.sp_entity_id" :placeholder="t('settings.system.saml_auto_ph')" />
          </div>
          <div style="flex:1; min-width:200px">
            <label>{{ t("settings.system.saml_sp_acs_url") }}</label>
            <n-input v-model:value="saml.sp_acs_url" :placeholder="t('settings.system.saml_auto_ph')" />
          </div>
        </div>
        <div class="fld" style="display:flex; gap:12px; flex-wrap:wrap">
          <div style="flex:1; min-width:160px">
            <label>{{ t("settings.system.oidc_username_claim") }}</label>
            <n-input v-model:value="saml.attr_username" />
          </div>
          <div style="flex:1; min-width:160px">
            <label>{{ t("cols.email") }}</label>
            <n-input v-model:value="saml.attr_email" />
          </div>
          <div style="flex:1; min-width:160px">
            <label>{{ t("settings.system.oidc_groups_claim") }}</label>
            <n-input v-model:value="saml.attr_groups" />
          </div>
        </div>
        <div class="fld">
          <label>{{ t("settings.system.oidc_admin_groups") }}</label>
          <n-input v-model:value="samlAdminGroupsText" :placeholder="t('settings.system.oidc_admin_groups_ph')" />
        </div>
        <n-space align="center" style="margin-bottom:10px">
          <n-checkbox v-model:checked="saml.want_assertions_signed">{{ t("settings.system.saml_want_signed") }}</n-checkbox>
          <n-checkbox v-model:checked="saml.authn_requests_signed">{{ t("settings.system.saml_authn_signed") }}</n-checkbox>
          <n-checkbox v-model:checked="saml.want_assertions_encrypted">{{ t("settings.system.saml_want_encrypted") }}</n-checkbox>
        </n-space>
        <n-space>
          <n-button type="primary" :loading="samlSaving" @click="saveSaml">
            <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
          </n-button>
          <n-button :loading="samlTesting" @click="doTestSaml">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("settings.system.saml_test") }}
          </n-button>
          <a :href="`${origin}/api/v1/auth/saml/metadata`" target="_blank" rel="noopener"
             style="font-size:13px; align-self:center">{{ t("settings.system.saml_sp_metadata") }}</a>
        </n-space>
        <div class="hint" style="line-height:1.6; margin-top:10px">{{ t("settings.system.saml_hint") }}</div>
      </section>

      <!-- 稽核轉送到 Graylog -->
      <section class="ss-group">
        <h3 class="ss-h">{{ t("settings.system.af_title") }}</h3>
        <div class="fld">
          <n-space align="center">
            <n-switch v-model:value="af.enabled" />
            <span style="font-size:13px">{{ t("settings.system.af_enable") }}</span>
          </n-space>
        </div>
        <div style="display:grid; grid-template-columns:1fr 130px; gap:12px">
          <div class="fld">
            <label>{{ t("settings.system.af_host") }}</label>
            <n-input v-model:value="af.host" placeholder="graylog.example.com" />
          </div>
          <div class="fld">
            <label>{{ t("settings.system.af_port") }}</label>
            <n-input-number v-model:value="af.port" :min="1" :max="65535" style="width:100%" />
          </div>
        </div>
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px">
          <div class="fld">
            <label>{{ t("settings.system.af_protocol") }}</label>
            <n-select v-model:value="af.protocol" :options="afProtoOpts" />
          </div>
          <div class="fld">
            <label>{{ t("settings.system.af_format") }}</label>
            <n-select v-model:value="af.fmt" :options="afFmtOpts" />
          </div>
        </div>
        <n-space style="margin-top:6px">
          <n-button type="primary" :loading="afSaving" @click="saveAf">
            <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
          </n-button>
          <n-button :loading="afTesting" @click="doTestAf">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("settings.system.af_test") }}
          </n-button>
        </n-space>
        <div class="hint" style="line-height:1.6; margin-top:10px">{{ t("settings.system.af_hint") }}</div>
      </section>
    </div>
  </n-card>
</template>

<style scoped>
.ss-wrap { display: flex; flex-direction: column; gap: 24px; max-width: 780px; }
.ss-group { border: 1px solid var(--n-border-color, rgba(127,127,127,.18)); border-radius: 14px;
  padding: 20px 22px 22px; background: rgba(127,127,127,0.028); box-shadow: 0 1px 3px rgba(15,23,42,.05); }
.ss-h { margin: 0; font-size: 16px; font-weight: 700; padding-left: 12px; line-height: 1.25;
  border-left: 4px solid #18a058; }
/* 統一卡片內每個區塊的垂直間距，避免欄位標題緊貼上一個元素 */
.ss-group > * + * { margin-top: 16px; }
.ss-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 640px) { .ss-grid { grid-template-columns: 1fr; } }
.fld label { display: block; font-size: 13px; font-weight: 500; margin-bottom: 5px; }
.hint { font-size: 11px; opacity: 0.65; margin-top: 4px; }
.ss-row { display: flex; align-items: center; gap: 12px; margin-top: 14px; flex-wrap: wrap; }
.ss-status { margin-top: 12px; font-size: 12px; display: flex; flex-direction: column; gap: 3px; }
.db-row { display: flex; gap: 8px; align-items: center; }
</style>
