<script setup lang="ts">
/**
 * 憑證推送目標（0227）：伺服器主動把憑證推到防火牆（FortiGate、OPNsense、Palo Alto、pfSense），不用裝代理。
 * 每次推送：匯入／替換 → 換上 → 連過去確認對方出示的是新憑證 → 確認不到就換回原本的（services/cert_push）。
 * 帳密只寫不讀（修改時留空＝沿用）；「立即推送」走背景作業。
 */
import { computed, h, onMounted, ref, type VNode } from "vue";
import { useI18n } from "vue-i18n";
import {
  NAlert, NButton, NCheckbox, NCheckboxGroup, NDataTable, NForm, NFormItem, NIcon, NInput, NModal,
  NPopconfirm, NPopover, NSelect, NSpace, NSwitch, NTag, NTooltip, useMessage, type DataTableColumns, type SelectOption,
} from "naive-ui";
import { apiErrMsg } from "@/api/client";
import {
  createCertPushTarget, deleteCertPushTarget, listCertPushTargets, previewPushDeviceCerts, pushCertNow,
  updateCertPushTarget, type CertPushKind, type CertPushTarget, type CertPushTargetIn, type DeviceCert,
} from "@/api/certificates";
import { trackTask } from "@/composables/useTaskTracker";
import { fmtDateTime } from "@/utils/datetime";
import { CancelIcon, DeleteIcon, EditIcon, InfoIcon, PlusIcon, RefreshIcon, SaveIcon, SendIcon } from "@/icons";

const props = defineProps<{ certOptions: { label: string; value: string }[] }>();
const { t, te, locale } = useI18n();
// 標籤欄固定寬度（依語言）：label-width="auto" 只在第一次打開時算一次，換設備後才出現的欄位
// 會跟其他列對不齊（10-23「排版不佳、不整齊」）。數值涵蓋各語言最長的標籤一行放得下
const labelWidth = computed(() => (String(locale.value).startsWith("en") ? 290 : String(locale.value).startsWith("ja") ? 250 : 200));
const msg = useMessage();

const rows = ref<CertPushTarget[]>([]);
const loading = ref(false);
async function load() {
  loading.value = true;
  try { rows.value = await listCertPushTargets(); } catch (e) { msg.error(apiErrMsg(e)); } finally { loading.value = false; }
}
onMounted(load);

const KINDS: CertPushKind[] = ["fortigate", "opnsense", "paloalto", "pfsense"];
const kindLabel = (k: string) => t(`certpush.kind_${k}`);
const kindOptions = computed(() => KINDS.map((k) => ({ label: kindLabel(k), value: k })));
const errText = (code?: string | null) => (code ? (te(`errors.${code}`) ? t(`errors.${code}`) : code) : "");

// ── 編輯對話框 ──
const show = ref(false);
const saving = ref(false);
const editing = ref<CertPushTarget | null>(null);
const f = ref<Required<Omit<CertPushTargetIn, "secret">> & { api_key: string; api_secret: string }>(blank());
function blank() {
  return { name: "", certificate_id: "", kind: "fortigate" as CertPushKind, api_url: "", verify_tls: true,
           options: { bind: ["admin"], vdom: "root" } as Record<string, any>, verify_endpoint: "", auto_push: false,
           enabled: true, api_key: "", api_secret: "" };
}
const DEFAULT_OPTS: Record<CertPushKind, Record<string, any>> = {
  fortigate: { bind: ["admin"], vdom: "root" },
  opnsense: { cert_uuid: "", restart_webgui: true },
  paloalto: { profile: "", commit: true },
  pfsense: { bind_webgui: true },
};
function openNew() {
  editing.value = null;
  f.value = blank();
  deviceCerts.value = [];
  show.value = true;
}
function openEdit(r: CertPushTarget) {
  editing.value = r;
  f.value = { name: r.name, certificate_id: r.certificate_id, kind: r.kind, api_url: r.api_url, verify_tls: r.verify_tls,
              options: { ...DEFAULT_OPTS[r.kind], ...r.options }, verify_endpoint: r.verify_endpoint || "",
              auto_push: r.auto_push, enabled: r.enabled, api_key: "", api_secret: "" };
  deviceCerts.value = [];
  show.value = true;
}
function onKind(k: CertPushKind) {
  f.value.kind = k;
  f.value.options = { ...DEFAULT_OPTS[k] };
}
const verifyOn = computed({
  get: () => f.value.options.verify !== false,
  set: (v: boolean) => { f.value.options = { ...f.value.options, verify: v }; },
});
async function save() {
  saving.value = true;
  const opts = { ...f.value.options };
  if (!opts.commit_admin) delete opts.commit_admin;
  if (opts.verify !== false) delete opts.verify;
  const body: CertPushTargetIn = {
    name: f.value.name.trim(), certificate_id: f.value.certificate_id, api_url: f.value.api_url.trim(),
    verify_tls: f.value.verify_tls, options: opts, verify_endpoint: (f.value.verify_endpoint || "").trim() || null,
    auto_push: f.value.auto_push, enabled: f.value.enabled,
  };
  if (f.value.api_key) body.secret = { api_key: f.value.api_key, api_secret: f.value.api_secret };
  try {
    if (editing.value) await updateCertPushTarget(editing.value.id, body);
    else await createCertPushTarget({ ...body, kind: f.value.kind });
    msg.success(t("common.saved"));
    show.value = false;
    await load();
  } catch (e) { msg.error(apiErrMsg(e)); } finally { saving.value = false; }
}

// OPNsense：列出設備上的憑證讓管理員選要替換哪一張。用表單上的帳密直接讀，不必先存檔
// （使用者 10-22：「替換設備上的哪一張不能選，正常嗎？」——這欄必填，以前卻要存檔後才讀得到）
const deviceCerts = ref<DeviceCert[]>([]);
const loadingCerts = ref(false);
const canLoadCerts = computed(() => /^https:\/\/./i.test(f.value.api_url.trim())
  && ((!!f.value.api_key && (f.value.kind !== "opnsense" || !!f.value.api_secret)) || !!editing.value?.has_secret));
async function loadDeviceCerts() {
  loadingCerts.value = true;
  try {
    deviceCerts.value = await previewPushDeviceCerts({
      kind: f.value.kind, api_url: f.value.api_url.trim(), verify_tls: f.value.verify_tls,
      secret: f.value.api_key ? { api_key: f.value.api_key, api_secret: f.value.api_secret } : null,
      target_id: editing.value?.id ?? null,
    });
    if (!deviceCerts.value.length) msg.info(t("certpush.opn_none"));
  } catch (e) { msg.error(apiErrMsg(e)); } finally { loadingCerts.value = false; }
}
// 下拉清單每一項：第一行名稱＋狀態，第二行 CN、簽發者、有效期限（使用者 10-23：要看得出狀態與有效日）
function certExpiry(c: DeviceCert): { text: string; color: string } | null {
  if (!c.valid_to) return null;
  const d = new Date(c.valid_to);
  const day = d.toISOString().slice(0, 10);
  const days = Math.floor((d.getTime() - Date.now()) / 86_400_000);
  if (days < 0) return { text: t("certpush.dc_expired", { d: day }), color: "#d03050" };
  if (days < 30) return { text: t("certpush.dc_expiring", { d: day, n: days }), color: "#f0a020" };
  return { text: t("certpush.dc_valid_to", { d: day }), color: "" };
}
function certStatusTag(c: DeviceCert) {
  if (c.in_use) return h(NTag, { size: "tiny", type: "success", bordered: false }, () => t(f.value.kind === "pfsense" ? "certpush.dc_webgui" : "certpush.dc_in_use"));
  if (c.is_user) return h(NTag, { size: "tiny", type: "info", bordered: false }, () => t("certpush.dc_user"));
  return h(NTag, { size: "tiny", bordered: false }, () => t("certpush.dc_unused"));
}
function renderCert(c: DeviceCert) {
  const exp = certExpiry(c);
  const meta = [c.subject, c.issuer ? t("certpush.dc_issuer", { v: c.issuer }) : ""].filter(Boolean);
  return h("div", { style: "display:flex;flex-direction:column;line-height:1.4;padding:3px 0;min-width:0" }, [
    h("div", { style: "display:flex;align-items:center;gap:6px;min-width:0" }, [
      h("span", { style: "overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, c.name || c.id), certStatusTag(c)]),
    h("div", { style: "font-size:12px;opacity:.75;display:flex;gap:10px;flex-wrap:wrap;white-space:nowrap" }, [
      ...meta.map((m) => h("span", null, m)),
      exp ? h("span", { style: exp.color ? `color:${exp.color};opacity:1` : "" }, exp.text) : null]),
  ]);
}
const deviceCertOptions = computed(() => {
  const opts: { label: string; value: string; cert?: DeviceCert }[] = deviceCerts.value.map((c) => ({
    label: `${c.name}${c.subject ? `（${c.subject}）` : ""}`, value: c.id, cert: c }));
  const cur = f.value.options.cert_uuid;
  if (cur && !opts.some((o) => o.value === cur)) opts.unshift({ label: cur, value: cur });
  return opts;
});
// 選單裡顯示兩行（狀態、CN、簽發者、到期日）；輸入框裡已選的那一項只顯示一行名稱。
// render-label 在輸入框與選單都會被呼叫、分不出來，所以改走只作用在選單的 render-option
function renderCertOption({ node, option }: { node: VNode; option: SelectOption }) {
  const c = (option as { cert?: DeviceCert }).cert;
  if (!c) return node;
  return h(node.type as string, node.props, [renderCert(c)]);
}

// ── 動作 ──
const pushing = ref<Set<string>>(new Set());
async function pushNow(r: CertPushTarget) {
  pushing.value = new Set([...pushing.value, r.id]);
  try {
    const { task_id } = await pushCertNow(r.id);
    trackTask(task_id, { onDone: () => {
      const n = new Set(pushing.value); n.delete(r.id); pushing.value = n;
      void load();
    } });
    msg.info(t("certpush.push_started", { name: r.name }));
  } catch (e) {
    msg.error(apiErrMsg(e));
    const n = new Set(pushing.value); n.delete(r.id); pushing.value = n;
  }
}
async function remove(r: CertPushTarget) {
  try { await deleteCertPushTarget(r.id); msg.success(t("common.deleted")); await load(); } catch (e) { msg.error(apiErrMsg(e)); }
}

const STATUS_TYPE: Record<string, "success" | "warning" | "error" | "default"> = {
  ok: "success", unverified: "warning", staged: "default", error: "error", rolled_back: "error",
};
function stepsList(r: CertPushTarget) {
  return h("div", { style: "max-width:420px;font-size:12.5px;line-height:1.7", "data-testid": "certpush-steps" },
    (r.last_steps || []).map((s) => h("div", { style: s.ok ? "" : "color:var(--error-color,#d03050)" }, [
      `${s.ok ? "✓" : "✗"} ${te(`certpush.step_${s.step}`) ? t(`certpush.step_${s.step}`) : s.step}`,
      s.code ? `：${errText(s.code)}` : "",
      s.detail ? h("span", { style: "opacity:.7" }, `（${s.detail}）`) : "",
    ])));
}
function iconBtn(icon: any, label: string, onClick: () => void, extra: Record<string, any> = {}) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, "aria-label": label, ...extra, onClick },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}

const cols = computed<DataTableColumns<CertPushTarget>>(() => [
  { title: t("cols.name"), key: "name", minWidth: 140, render: (r) => h("span", { style: r.enabled ? "" : "opacity:.5" }, r.name) },
  { title: t("certpush.certificate"), key: "certificate_name", minWidth: 140 },
  { title: t("certpush.device"), key: "kind", width: 120, render: (r) => kindLabel(r.kind) },
  { title: t("certpush.api_url"), key: "api_url", minWidth: 180, ellipsis: { tooltip: true } },
  { title: t("certpush.auto_push"), key: "auto_push", width: 100,
    render: (r) => (r.auto_push ? h(NTag, { size: "small", type: "info", bordered: false }, () => t("certpush.auto_on")) : "—") },
  { title: t("certpush.status"), key: "last_status", minWidth: 200, render: (r) => {
    if (!r.last_status) return h("span", { style: "opacity:.6" }, t("certpush.never"));
    const tag = h(NTag, { size: "small", type: STATUS_TYPE[r.last_status] ?? "default", bordered: false },
      () => t(`certpush.status_${r.last_status}`));
    const extra = [r.last_push_at ? fmtDateTime(r.last_push_at) : "",
                   r.up_to_date ? "" : t("certpush.not_current")].filter(Boolean).join(" · ");
    return h(NPopover, { trigger: "hover" }, {
      trigger: () => h("span", { style: "display:inline-flex;gap:6px;align-items:center;cursor:default" },
        [tag, h("span", { style: "font-size:12px;opacity:.7" }, extra)]),
      default: () => [r.last_error ? h("div", { style: "margin-bottom:6px" }, errText(r.last_error)) : null, stepsList(r)],
    });
  } },
  { title: t("common.actions"), key: "actions", width: 130, fixed: "right", render: (r) => h(NSpace, { size: 2, wrap: false }, () => [
    iconBtn(EditIcon, t("common.edit"), () => openEdit(r), { type: "success" }),
    iconBtn(SendIcon, t("certpush.push_now"), () => pushNow(r),
            { type: "info", loading: pushing.value.has(r.id), disabled: pushing.value.has(r.id) || !r.has_secret }),
    h(NPopconfirm, { onPositiveClick: () => remove(r) }, {
      trigger: () => iconBtn(DeleteIcon, t("common.delete"), () => {}, { type: "error" }),
      default: () => t("certpush.delete_confirm", { name: r.name }),
    }),
  ]) },
]);
</script>

<template>
  <div>
    <n-alert type="info" :show-icon="true" style="margin-bottom:10px">{{ t("certpush.intro") }}</n-alert>
    <n-space justify="space-between" style="margin-bottom:10px">
      <n-button type="primary" size="small" data-testid="certpush-new" @click="openNew">
        <template #icon><n-icon :component="PlusIcon" /></template>{{ t("certpush.new") }}
      </n-button>
      <n-button size="small" quaternary @click="load">
        <template #icon><n-icon :component="RefreshIcon" /></template>{{ t("common.refresh") }}
      </n-button>
    </n-space>
    <n-data-table :columns="cols" :data="rows" :loading="loading" size="small" :scroll-x="1100"
                  :row-key="(r: CertPushTarget) => r.id" data-testid="certpush-table" />

    <n-modal v-model:show="show" preset="card" :style="{ width: `${labelWidth + 520}px`, maxWidth: '95vw' }"
             :title="editing ? t('certpush.edit') : t('certpush.new')">
      <n-form label-placement="left" :label-width="labelWidth" size="small" :style="{ '--cp-label': `${labelWidth}px` }">
        <n-form-item :label="t('cols.name')" required><n-input v-model:value="f.name" data-testid="certpush-name" /></n-form-item>
        <n-form-item :label="t('certpush.certificate')" required>
          <n-select v-model:value="f.certificate_id" :options="props.certOptions" filterable data-testid="certpush-cert" />
        </n-form-item>
        <n-form-item :label="t('certpush.device')" required>
          <n-select :value="f.kind" :options="kindOptions" :disabled="!!editing" data-testid="certpush-kind" @update:value="onKind" />
        </n-form-item>
        <!-- 範例跟著設備換（使用者 10-22：「選不同設備時要有範例」）；填了字之後 placeholder 看不到，所以另外寫在下面 -->
        <n-form-item :label="t('certpush.api_url')" required
                     :feedback="t('certpush.url_example', { url: t(`certpush.url_ph_${f.kind}`) })">
          <n-input v-model:value="f.api_url" :placeholder="t(`certpush.url_ph_${f.kind}`)" data-testid="certpush-url" />
        </n-form-item>
        <n-form-item :label="t('certpush.verify_tls')"><n-switch v-model:value="f.verify_tls" /></n-form-item>
        <n-form-item :label="t(`certpush.key_${f.kind}`)" :required="!editing">
          <n-input v-model:value="f.api_key" type="password" show-password-on="click"
                   :placeholder="editing?.has_secret ? t('certpush.keep_secret') : ''" data-testid="certpush-key" />
        </n-form-item>
        <n-form-item v-if="f.kind === 'opnsense'" :label="t('certpush.secret_opnsense')" :required="!editing">
          <n-input v-model:value="f.api_secret" type="password" show-password-on="click"
                   :placeholder="editing?.has_secret ? t('certpush.keep_secret') : ''" />
        </n-form-item>
        <div class="cp-hint">{{ t(`certpush.perm_${f.kind}`) }}</div>

        <!-- 各家的換上方式 -->
        <template v-if="f.kind === 'fortigate'">
          <n-form-item :label="t('certpush.fg_bind')">
            <n-checkbox-group v-model:value="f.options.bind">
              <n-space><n-checkbox value="admin">{{ t("certpush.fg_admin") }}</n-checkbox>
                <n-checkbox value="sslvpn">{{ t("certpush.fg_sslvpn") }}</n-checkbox></n-space>
            </n-checkbox-group>
          </n-form-item>
          <n-form-item v-if="(f.options.bind || []).includes('sslvpn')" label="VDOM">
            <n-input v-model:value="f.options.vdom" placeholder="root" style="width:160px" />
          </n-form-item>
        </template>
        <template v-else-if="f.kind === 'opnsense'">
          <n-form-item :label="t('certpush.opn_cert')" required>
            <div style="display:flex;gap:8px;width:100%;min-width:0">
              <n-select v-model:value="f.options.cert_uuid" :options="deviceCertOptions" filterable tag size="small"
                        :render-option="renderCertOption" :virtual-scroll="false" :consistent-menu-width="false" style="flex:1;min-width:0" :placeholder="t('certpush.opn_pick_ph')"
                        data-testid="certpush-opn-cert" />
              <!-- 全站主題把 small 按鈕設成 34px、small 下拉是 28px：這一列讓按鈕跟著輸入框同高（10-23「高度不一樣」） -->
              <n-button size="small" :disabled="!canLoadCerts" :loading="loadingCerts" class="cp-row-btn"
                        data-testid="certpush-opn-load" @click="loadDeviceCerts">
                <template #icon><n-icon :component="RefreshIcon" /></template>{{ t("certpush.opn_load") }}
              </n-button>
            </div>
          </n-form-item>
          <n-form-item :label="t('certpush.opn_restart')"><n-switch v-model:value="f.options.restart_webgui" /></n-form-item>
        </template>
        <template v-else-if="f.kind === 'paloalto'">
          <n-form-item :label="t('certpush.pa_profile')" required>
            <n-input v-model:value="f.options.profile" placeholder="GP-Portal-Profile" />
          </n-form-item>
          <n-form-item :label="t('certpush.pa_commit')"><n-switch v-model:value="f.options.commit" /></n-form-item>
          <div class="cp-hint">{{ f.options.commit === false ? t("certpush.pa_commit_off_hint") : t("certpush.pa_commit_hint") }}</div>
          <n-form-item v-if="f.options.commit !== false" :label="t('certpush.pa_commit_admin')">
            <n-input v-model:value="f.options.commit_admin" :placeholder="t('certpush.pa_commit_admin_ph')"
                     data-testid="certpush-pa-commit-admin" />
          </n-form-item>
        </template>
        <template v-else>
          <n-form-item :label="t('certpush.pf_webgui')"><n-switch v-model:value="f.options.bind_webgui" /></n-form-item>
          <!-- pfSense 每次推送是新增一張再切過去，不用選替換哪一張；這裡唯讀列出設備上的憑證與哪張在用（10-23） -->
          <n-form-item :label="t('certpush.pf_certs')">
            <div style="display:flex;flex-direction:column;gap:6px;width:100%;min-width:0">
              <n-button size="small" :disabled="!canLoadCerts" :loading="loadingCerts" style="align-self:flex-start"
                        data-testid="certpush-pf-load" @click="loadDeviceCerts">
                <template #icon><n-icon :component="RefreshIcon" /></template>{{ t("certpush.opn_load") }}
              </n-button>
              <div v-if="deviceCerts.length" class="cp-certs" data-testid="certpush-pf-certs">
                <component :is="renderCert(c)" v-for="c in deviceCerts" :key="c.id" />
              </div>
            </div>
          </n-form-item>
        </template>

        <n-form-item :label="t('certpush.verify')"><n-switch v-model:value="verifyOn" /></n-form-item>
        <n-form-item v-if="verifyOn" :label="t('certpush.verify_endpoint')">
          <n-input v-model:value="f.verify_endpoint"
                   :placeholder="t(f.kind === 'paloalto' || f.kind === 'opnsense' ? 'certpush.verify_endpoint_ph_explicit' : 'certpush.verify_endpoint_ph')" />
        </n-form-item>
        <!-- Palo Alto、OPNsense：換的憑證可能不是管理介面在用的，沒填確認位址就不確認、不換回（11-03） -->
        <div v-if="verifyOn && (f.kind === 'paloalto' || f.kind === 'opnsense')" class="cp-hint" data-testid="cp-verify-explicit-hint">
          {{ t("certpush.verify_explicit_hint") }}</div>
        <n-form-item :label="t('certpush.auto_push')"><n-switch v-model:value="f.auto_push" /></n-form-item>
        <div class="cp-hint" data-testid="cp-auto-push-hint">{{ t("certpush.auto_push_hint") }}</div>
        <n-form-item :label="t('certpush.enabled')"><n-switch v-model:value="f.enabled" /></n-form-item>
        <div class="cp-hint"><n-icon :component="InfoIcon" /> {{ t("certpush.verify_hint") }}</div>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="show = false"><template #icon><n-icon :component="CancelIcon" /></template>{{ t("common.cancel") }}</n-button>
          <n-button type="primary" :loading="saving" :disabled="!f.name || !f.certificate_id || (!editing && !f.api_key)"
                    data-testid="certpush-save" @click="save">
            <template #icon><n-icon :component="SaveIcon" /></template>{{ t("common.save") }}
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
/* 全站把 small 按鈕撐到 34px；這裡與同列的 small 下拉清單（28px）對齊 */
.cp-row-btn { flex: none; --n-height: 28px !important; height: 28px !important; min-height: 28px !important; }
.cp-certs { display: flex; flex-direction: column; gap: 2px; border: 1px solid var(--n-border-color, rgba(128,128,128,.25)); border-radius: 6px; padding: 6px 10px; max-height: 220px; overflow: auto; }
.cp-hint { font-size: 12px; opacity: .75; margin: -4px 0 12px var(--cp-label, 200px); line-height: 1.6; }
</style>
