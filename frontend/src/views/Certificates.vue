<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { fmtDateTime } from "@/utils/datetime";
import { useI18n } from "vue-i18n";
import { useEntityLinks } from "@/composables/useEntityLinks";
import { listDevices } from "@/api/basic";
import {
  NCard, NTabs, NTabPane, NDataTable, NSpace, NButton, NIcon, NTag, NModal, NForm,
  NFormItem, NInput, NInputNumber, NDynamicTags, NSelect, NPopconfirm, NAlert,
  NCheckbox, NCheckboxGroup, NRadioGroup, NRadioButton, NTooltip, NDivider, NCollapse,
  NCollapseItem, NSwitch, NDropdown, useMessage, type DataTableColumns,
} from "naive-ui";
import {
  PlusIcon, RefreshIcon, CopyIcon, LockIcon, InfoIcon, SaveIcon, SearchIcon,
  ImportIcon, TokenIcon, SettingsIcon, SyncIcon, DeleteIcon, TestIcon, EyeIcon, ToolsIcon, CancelIcon, EditIcon,
  ExportIcon, WarnIcon, UpgradeIcon, CheckIcon,
} from "@/icons";
import { autoSort } from "@/composables/useTableSort";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { useTablePagination } from "@/composables/useTablePagination";
import { SUDO } from "@/utils/sudo";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import {
  listCertificates, createCertificate, deleteCertificate, uploadVersion, generateSelfSigned,
  setCertSource, fetchCertNow, testCertSource, genCertSourceSshKey, listVersions, downloadVersionFile, rebuildChain,
  listCertAgents, createCertAgent, rotateCertAgentKey, deleteCertAgent, getCertAgentKey, updateCertAgent,
  getServerAgentVersion,
  type Certificate, type CertAgent, type CertVersion,
} from "@/api/certificates";

const { t } = useI18n();
const msg = useMessage();
const router = useRouter();
const links = useEntityLinks(router);

const certs = ref<Certificate[]>([]);
const agents = ref<CertAgent[]>([]);
const loading = ref(false);

// 對應裝置下拉（編輯代理用）
const deviceOptions = ref<{ label: string; value: string }[]>([]);
async function loadDeviceOptions() {
  try {
    const r = await listDevices({ page: 1, pageSize: 500 });
    deviceOptions.value = r.items.map((d) => ({
      label: d.ip ? `${d.name}（${d.ip}）` : d.name, value: d.id }));
  } catch { /* silent */ }
}

// ── 篩選 ──
const certFilter = ref("");
const agentFilter = ref("");
const agentCertFilter = ref<string | null>(null);  // 依「可取憑證」篩選代理
const certsFiltered = computed(() => {
  const q = certFilter.value.trim().toLowerCase();
  if (!q) return certs.value;
  return certs.value.filter((c) =>
    c.name.toLowerCase().includes(q) || (c.domains ?? []).some((d) => d.toLowerCase().includes(q)));
});
const agentsFiltered = computed(() => {
  const q = agentFilter.value.trim().toLowerCase();
  const cid = agentCertFilter.value;
  return agents.value.filter((a) => {
    if (cid && !(a.scope_cert_ids ?? []).map(String).includes(cid)) return false;
    if (q && !(a.name.toLowerCase().includes(q) || (a.last_source_ip ?? "").includes(q))) return false;
    return true;
  });
});

async function loadCerts() {
  loading.value = true;
  try { certs.value = (await listCertificates()).items; }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
  finally { loading.value = false; }
}
async function loadAgents() {
  try { agents.value = (await listCertAgents()).items; }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
const serverAgentVersion = ref<string | null>(null);
async function loadServerVersion() {
  try { serverAgentVersion.value = (await getServerAgentVersion()).version; }
  catch { /* 非致命 */ }
}
onMounted(() => { loadCerts(); loadAgents(); loadServerVersion(); loadDeviceOptions(); });

// 安裝說明：支援的 OS / 發行版（醒目標籤呈現）
const SUPPORTED_OS = [
  "Debian 11 / 12 / 13", "Ubuntu 22.04 / 24.04 / 26.04",
  "RHEL / Rocky / AlmaLinux / CentOS 8 / 9", "Fedora 38+", "openSUSE Leap 15+ / SLES 15+",
];

// ── 設定檔產生器 ──
const PROFILE_OPTIONS = [
  "nginx", "apache", "caddy", "traefik", "lighttpd", "haproxy", "zoraxy", "jetty",
  "postfix", "dovecot", "exim4", "mosquitto", "cockpit", "webmin", "wazuh-dashboard",
  "pve", "pmg", "pbs", "pdm", "zimbra",
  "files",
];
const dryRunCmd = `${SUDO} bash /usr/local/lib/jt-ipam-cert-agent/jt_ipam_cert_agent.sh --config /etc/jt-ipam-cert-agent/config --dry-run`;
const runCmd = `${SUDO} bash /usr/local/lib/jt-ipam-cert-agent/jt_ipam_cert_agent.sh --config /etc/jt-ipam-cert-agent/config`;
const showGen = ref(false);
const genAgentName = ref("");
const genScopeIds = ref<string[]>([]);
const genCerts = ref<string[]>([]);
const genProfiles = ref<string[]>([]);
const genManual = ref({ cert: "", fullchain: "", key: "", chain: "", crt: "", combined: "", reload: "", test: "" });
// 此代理 scope 內的憑證（依名稱）
const genCertOptions = computed(() =>
  certs.value.filter(c => genScopeIds.value.includes(c.id)).map(c => ({ label: c.name, value: c.name })));
function openGen(a: CertAgent) {
  genAgentName.value = a.name;
  genScopeIds.value = (a.scope_cert_ids ?? []).map(String);
  genCerts.value = []; genProfiles.value = [];
  genManual.value = { cert: "", fullchain: "", key: "", chain: "", crt: "", combined: "", reload: "", test: "" };
  showGen.value = true;
}
// 各 profile 預設寫入的檔案（kind → 完整路徑），與 agent 端 profile_spec 一致。
const TLS_BASE = "/etc/ssl/jt-ipam";
function profileFiles(profile: string, cert: string): { kind: string; path: string }[] {
  const b = TLS_BASE;
  switch (profile) {
    case "nginx": case "caddy": case "traefik": return [{ kind: "cert+chain", path: `${b}/${cert}.fullchain.pem` }, { kind: "key", path: `${b}/${cert}.key` }];
    case "apache": case "mosquitto": return [{ kind: "cert", path: `${b}/${cert}.crt` }, { kind: "chain", path: `${b}/${cert}.chain.pem` }, { kind: "key", path: `${b}/${cert}.key` }];
    case "haproxy": case "lighttpd": return [{ kind: "cert+chain+key", path: `${b}/${cert}.pem` }];
    case "postfix": case "dovecot": case "exim4": return [{ kind: "cert+chain", path: `${b}/${cert}.fullchain.pem` }, { kind: "key", path: `${b}/${cert}.key` }];
    case "zoraxy": return [{ kind: "cert+chain", path: `${b}/${cert}.crt` }, { kind: "key", path: `${b}/${cert}.key` }];
    case "jetty": return [{ kind: "PKCS#12 keystore", path: `${b}/${cert}.p12` }];
    case "cockpit": return [{ kind: "cert+chain+key", path: `/etc/cockpit/ws-certs.d/${cert}.cert` }];
    case "webmin": return [{ kind: "cert+chain+key", path: "/etc/webmin/miniserv.pem" }];
    case "pve": return [{ kind: "cert+chain (root:www-data 640)", path: "/etc/pve/local/pveproxy-ssl.pem" }, { kind: "key (root:www-data 640)", path: "/etc/pve/local/pveproxy-ssl.key" }];
    case "pmg": return [{ kind: "cert+chain+key (root:www-data 640)", path: "/etc/pmg/pmg-api.pem" }, { kind: "cert+chain+key (root:root 600)", path: "/etc/pmg/pmg-tls.pem" }];
    case "pbs": return [{ kind: "cert+chain (root:backup 640)", path: "/etc/proxmox-backup/proxy.pem" }, { kind: "key (root:backup 640)", path: "/etc/proxmox-backup/proxy.key" }];
    case "pdm": return [{ kind: "cert+chain (root:www-data 640)", path: "/etc/proxmox-datacenter-manager/auth/api.pem" }, { kind: "key (root:www-data 640)", path: "/etc/proxmox-datacenter-manager/auth/api.key" }];
    case "wazuh-dashboard": return [{ kind: "cert+chain (wazuh-dashboard 640)", path: "/etc/wazuh-dashboard/certs/dashboard.pem" }, { kind: "key (wazuh-dashboard 640)", path: "/etc/wazuh-dashboard/certs/dashboard-key.pem" }];
    case "jitsi": return [{ kind: "cert+chain (docker restart jitsi web)", path: "/root/.jitsi-meet-cfg/web/keys/cert.crt" }, { kind: "key", path: "/root/.jitsi-meet-cfg/web/keys/cert.key" }];
    case "coturn": return [{ kind: "cert+chain (root:65534 644)", path: "/etc/coturn/certs/turn.crt" }, { kind: "key (root:65534 640)", path: "/etc/coturn/certs/turn.key" }];
    case "zimbra": return [{ kind: "zmcertmgr deploycrt comm + zmcontrol restart", path: "/opt/zimbra/ssl/zimbra/commercial/commercial.{key,crt}" }];
    case "files": return [{ kind: "cert+chain（僅換檔，不 reload）", path: `${b}/${cert}.fullchain.pem` }, { kind: "key（僅換檔，不 reload）", path: `${b}/${cert}.key` }];
    default: return [];
  }
}
// 各 profile 對應的服務設定片段（指到上面寫入的路徑），給使用者貼進服務設定檔。
function serviceSnippet(profile: string, cert: string): string {
  const b = TLS_BASE;
  switch (profile) {
    case "nginx": return `ssl_certificate     ${b}/${cert}.fullchain.pem;\nssl_certificate_key ${b}/${cert}.key;`;
    case "apache": return `SSLCertificateFile      ${b}/${cert}.crt\nSSLCertificateKeyFile   ${b}/${cert}.key\nSSLCertificateChainFile ${b}/${cert}.chain.pem`;
    case "caddy": return `tls ${b}/${cert}.fullchain.pem ${b}/${cert}.key`;
    case "traefik": return `tls:\n  certificates:\n    - certFile: ${b}/${cert}.fullchain.pem\n      keyFile: ${b}/${cert}.key`;
    case "lighttpd": return `ssl.pemfile = "${b}/${cert}.pem"`;
    case "haproxy": return `bind *:443 ssl crt ${b}/${cert}.pem`;
    case "jetty": return `# jetty SslContextFactory:\nKeyStorePath=${b}/${cert}.p12\nKeyStoreType=PKCS12\nKeyStorePassword=`;
    case "postfix": return `smtpd_tls_cert_file = ${b}/${cert}.fullchain.pem\nsmtpd_tls_key_file  = ${b}/${cert}.key`;
    case "dovecot": return `ssl_cert = <${b}/${cert}.fullchain.pem\nssl_key  = <${b}/${cert}.key`;
    case "exim4": return `tls_certificate = ${b}/${cert}.fullchain.pem\ntls_privatekey  = ${b}/${cert}.key`;
    case "mosquitto": return `certfile ${b}/${cert}.crt\nkeyfile  ${b}/${cert}.key\ncafile   ${b}/${cert}.chain.pem`;
    case "wazuh-dashboard": return `# /etc/wazuh-dashboard/opensearch_dashboards.yml\nserver.ssl.certificate: "/etc/wazuh-dashboard/certs/dashboard.pem"\nserver.ssl.key:         "/etc/wazuh-dashboard/certs/dashboard-key.pem"`;
    default: return "";  // zoraxy/cockpit/webmin/jitsi/coturn/pve/pmg/pbs/pdm/zimbra：固定路徑或由各自 UI / 容器管理，不需手改設定檔
  }
}
const genServiceBlocks = computed(() =>
  genCerts.value.flatMap(cert => genProfiles.value.map(prof => ({
    cert, prof, files: profileFiles(prof, cert), snippet: serviceSnippet(prof, cert),
  }))));
const genConfig = computed(() => {
  const lines: string[] = [];
  let n = 1;
  for (const cert of genCerts.value) {
    for (const prof of genProfiles.value) {
      lines.push(`DEPLOY_${n}_CERT=${cert}`);
      lines.push(`DEPLOY_${n}_PROFILE=${prof}`);
      lines.push("");
      n++;
    }
  }
  const m = genManual.value;
  if (m.cert && (m.fullchain || m.crt || m.combined)) {
    lines.push(`DEPLOY_${n}_CERT=${m.cert}`);
    if (m.fullchain) lines.push(`DEPLOY_${n}_FULLCHAIN=${m.fullchain}`);
    if (m.crt) lines.push(`DEPLOY_${n}_CRT=${m.crt}`);
    if (m.chain) lines.push(`DEPLOY_${n}_CHAIN=${m.chain}`);
    if (m.combined) lines.push(`DEPLOY_${n}_COMBINED=${m.combined}`);
    if (m.key) lines.push(`DEPLOY_${n}_KEY=${m.key}`);
    if (m.reload) lines.push(`DEPLOY_${n}_RELOAD=${m.reload}`);
    if (m.test) lines.push(`DEPLOY_${n}_TEST=${m.test}`);
  }
  return lines.join("\n").trim();
});

// ── 到期狀態著色（到期日 / 剩餘天數 拆兩欄）──
function expDateCell(c: Certificate) {
  if (c.current_not_after === null)
    return h(NTag, { size: "small" }, () => t("certs.no_version"));
  return h("span", fmtDateTime(c.current_not_after).slice(0, 10));
}
function daysLeftCell(c: Certificate) {
  if (c.current_days_remaining === null) return h("span", { style: "opacity:.5" }, "—");
  const d = c.current_days_remaining;
  const type = d < 0 ? "error" : d <= 21 ? "warning" : "success";
  return h(NTag, { size: "small", type },
    () => d < 0 ? t("certs.expired") : t("certs.days_left", { n: d }));
}

// ── 憑證檔案 / 版本下載 ──
const DL_FORMATS = [
  { fmt: "fullchain", labelKey: "certFiles.f_fullchain" },
  { fmt: "cert", labelKey: "certFiles.f_cert" },
  { fmt: "chain", labelKey: "certFiles.f_chain" },
  { fmt: "key", labelKey: "certFiles.f_key" },
  { fmt: "combined", labelKey: "certFiles.f_combined" },
  { fmt: "der", labelKey: "certFiles.f_der" },
  { fmt: "pfx", labelKey: "certFiles.f_pfx" },
];
const showFiles = ref(false);
const filesTarget = ref<Certificate | null>(null);
const filesVersions = ref<CertVersion[]>([]);
const dlOptions = computed(() => DL_FORMATS.map(f => ({ key: f.fmt, label: t(f.labelKey) })));
async function openFiles(c: Certificate) {
  filesTarget.value = c; filesVersions.value = [];
  showFiles.value = true;
  try { filesVersions.value = await listVersions(c.id); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function doDownload(v: CertVersion, fmt: string) {
  if (!filesTarget.value) return;
  try { await downloadVersionFile(filesTarget.value.id, v.id, fmt); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function doRebuildChain(v: CertVersion) {
  if (!filesTarget.value) return;
  try {
    await rebuildChain(filesTarget.value.id, v.id);
    msg.success(t("certFiles.rebuild_done"));
    filesVersions.value = await listVersions(filesTarget.value.id);
    await loadCerts();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

// ── 新增憑證 ──
const showNew = ref(false);
const newForm = ref({ name: "", description: "" });
async function doCreate() {
  if (!newForm.value.name.trim()) { msg.warning(t("certs.name_required")); return; }
  try {
    await createCertificate({ name: newForm.value.name.trim(), description: newForm.value.description || null });
    showNew.value = false; newForm.value = { name: "", description: "" };
    await loadCerts(); msg.success(t("common.saved"));
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

// ── 上傳新版 ──
const showUpload = ref(false);
const uploadTarget = ref<Certificate | null>(null);
const upMode = ref<"file" | "paste">("file");
const upCert = ref<File | null>(null);
const upKey = ref<File | null>(null);
const upChain = ref<File | null>(null);
const pasteCert = ref("");
const pasteKey = ref("");
const pasteChain = ref("");
const upAllowExpired = ref(false);
const upBusy = ref(false);
function openUpload(c: Certificate) {
  uploadTarget.value = c; upMode.value = "file";
  upCert.value = upKey.value = upChain.value = null;
  pasteCert.value = pasteKey.value = pasteChain.value = "";
  upAllowExpired.value = false; showUpload.value = true;
}
function pick(ev: Event, slot: "cert" | "key" | "chain") {
  const f = (ev.target as HTMLInputElement).files?.[0] ?? null;
  if (slot === "cert") upCert.value = f; else if (slot === "key") upKey.value = f; else upChain.value = f;
}
function _pem(text: string, name: string): File {
  return new File([text], name, { type: "application/x-pem-file" });
}
async function doUpload() {
  if (!uploadTarget.value) return;
  let cert: File | null, key: File | null, chain: File | null;
  if (upMode.value === "paste") {
    if (!pasteCert.value.trim() || !pasteKey.value.trim()) { msg.warning(t("certs.need_cert_key")); return; }
    cert = _pem(pasteCert.value, "cert.pem");
    key = _pem(pasteKey.value, "key.pem");
    chain = pasteChain.value.trim() ? _pem(pasteChain.value, "chain.pem") : null;
  } else {
    if (!upCert.value || !upKey.value) { msg.warning(t("certs.need_cert_key")); return; }
    cert = upCert.value; key = upKey.value; chain = upChain.value;
  }
  upBusy.value = true;
  try {
    await uploadVersion(uploadTarget.value.id, { cert, key, chain }, upAllowExpired.value);
    showUpload.value = false; await loadCerts(); msg.success(t("certs.uploaded"));
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
  finally { upBusy.value = false; }
}

// ── 產生自簽 / 續簽 ──
const showSelf = ref(false);
const selfRenew = ref(false);  // true＝續簽（沿用現有 CN/SAN 重簽一張新版本）
const selfTarget = ref<Certificate | null>(null);
const selfForm = ref({ common_name: "", sans: [] as string[], days: 365 });
function openSelf(c: Certificate) {
  selfTarget.value = c; selfRenew.value = false;
  selfForm.value = { common_name: "", sans: [], days: 365 }; showSelf.value = true;
}
function openRenew(c: Certificate) {
  selfTarget.value = c; selfRenew.value = true;
  selfForm.value = {
    common_name: c.current_common_name ?? "",
    sans: [...(c.current_sans ?? [])],
    days: 365,
  };
  showSelf.value = true;
}
async function doSelf() {
  if (!selfTarget.value || !selfForm.value.common_name.trim()) { msg.warning(t("certs.cn_required")); return; }
  try {
    await generateSelfSigned(selfTarget.value.id, {
      common_name: selfForm.value.common_name.trim(), sans: selfForm.value.sans, days: selfForm.value.days });
    showSelf.value = false; await loadCerts();
    msg.success(selfRenew.value ? t("certs.renew_done") : t("certs.self_signed_done"));
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

async function removeCert(c: Certificate) {
  try { await deleteCertificate(c.id); await loadCerts(); msg.success(t("common.deleted")); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

// ── 自動抓取來源 ──
const showSource = ref(false);
const sourceTarget = ref<Certificate | null>(null);
const sourceForm = ref({
  source_type: "none" as "none" | "url" | "sftp",
  fetch_interval_hours: 24,
  cert_url: "", key_url: "", chain_url: "",
  host: "", port: 22, username: "", cert_path: "", key_path: "", chain_path: "",
  source_password: "", source_private_key: "",
});
function openSource(c: Certificate) {
  sourceTarget.value = c;
  const cfg = (c.source_config ?? {}) as any;
  sourceForm.value = {
    source_type: (c.source_type as any) || "none",
    fetch_interval_hours: Math.max(1, Math.round((c.fetch_interval_seconds || 86400) / 3600)),
    cert_url: cfg.cert_url ?? "", key_url: cfg.key_url ?? "", chain_url: cfg.chain_url ?? "",
    host: cfg.host ?? "", port: cfg.port ?? 22, username: cfg.username ?? "",
    cert_path: cfg.cert_path ?? "", key_path: cfg.key_path ?? "", chain_path: cfg.chain_path ?? "",
    source_password: "", source_private_key: "",
  };
  sshPubKey.value = "";
  sshInstalled.value = false;
  showSource.value = true;
}
function buildSourcePayload() {
  const f = sourceForm.value;
  let cfg: Record<string, unknown> = {};
  if (f.source_type === "url") cfg = { cert_url: f.cert_url, key_url: f.key_url || undefined, chain_url: f.chain_url || undefined };
  else if (f.source_type === "sftp") cfg = { host: f.host, port: f.port, username: f.username, cert_path: f.cert_path, key_path: f.key_path || undefined, chain_path: f.chain_path || undefined };
  return {
    source_type: f.source_type, source_config: cfg,
    fetch_interval_seconds: Math.max(300, f.fetch_interval_hours * 3600),
    source_password: f.source_password || undefined,
    source_private_key: f.source_private_key || undefined,
  };
}
async function saveSource() {
  if (!sourceTarget.value) return;
  try {
    await setCertSource(sourceTarget.value.id, buildSourcePayload());
    showSource.value = false; await loadCerts(); msg.success(t("common.saved"));
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
const testing = ref(false);
async function testSource() {
  if (!sourceTarget.value) return;
  testing.value = true;
  try {
    const r = await testCertSource(sourceTarget.value.id, buildSourcePayload());
    if (r.ok) msg.success(r.message || t("certSource.test_ok"));
    else msg.error(r.message || t("certSource.test_fail"));
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
  finally { testing.value = false; }
}
const sshPubKey = ref("");
const sshInstalled = ref(false);
const genningKey = ref(false);
async function genSshKey() {
  if (!sourceTarget.value) return;
  genningKey.value = true;
  try {
    const r = await genCertSourceSshKey(sourceTarget.value.id, buildSourcePayload());
    sshPubKey.value = r.public_key;
    sshInstalled.value = r.installed;
    sourceForm.value.source_private_key = "";  // 已存於後端,表單留空＝沿用
    if (r.installed) msg.success(r.message || t("certSource.key_installed"));
    else msg.warning(`${t("certSource.key_generated")}${r.message ? "（" + r.message + "）" : ""}`);
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
  finally { genningKey.value = false; }
}
async function doFetchNow(c: Certificate) {
  try {
    const r = await fetchCertNow(c.id);
    if (r.status === "updated") msg.success(t("certSource.fetched_updated"));
    else if (r.status === "skipped") msg.info(t("certSource.fetched_skipped"));
    else if (r.status === "error") msg.error(r.error ?? t("errors.server"));
    else msg.info(String(r.status));
    await loadCerts();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

// ── 派送代理 ──
const showNewAgent = ref(false);
const agentForm = ref({ name: "", description: "", scope_cert_ids: [] as string[] });
const newKey = ref<string | null>(null);
const viewMode = ref(false);  // true＝檢視既有代理（非剛建立）
const viewAgentName = ref("");
const certOptions = computed(() => certs.value.map(c => ({ label: c.name, value: c.id })));
// 編輯既有代理時，scope 內若有已被刪除的憑證（孤兒 UUID），用可讀標籤顯示而非裸 UUID，方便移除
const editCertOptions = computed(() => {
  const known = new Set(certs.value.map(c => c.id));
  const orphans = editForm.value.scope_cert_ids
    .filter(id => !known.has(id))
    .map(id => ({ label: `${id.slice(0, 8)}…（${t("certs.cert_deleted")}）`, value: id }));
  return [...certOptions.value, ...orphans];
});
async function viewAgent(a: CertAgent) {
  try {
    const r = await getCertAgentKey(a.id);
    newKey.value = r.enroll_key; viewMode.value = true; viewAgentName.value = a.name;
    showNewAgent.value = true;
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.server"));
  }
}
async function doCreateAgent() {
  if (!agentForm.value.name.trim()) { msg.warning(t("certs.name_required")); return; }
  try {
    const r = await createCertAgent({ name: agentForm.value.name.trim(),
      description: agentForm.value.description || null, scope_cert_ids: agentForm.value.scope_cert_ids });
    newKey.value = r.enroll_key; await loadAgents();
    agentForm.value = { name: "", description: "", scope_cert_ids: [] };
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function doRotate(a: CertAgent) {
  try {
    const r = await rotateCertAgentKey(a.id);
    newKey.value = r.enroll_key; viewMode.value = false; viewAgentName.value = a.name;
    showNewAgent.value = true;
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function removeAgent(a: CertAgent) {
  try { await deleteCertAgent(a.id); await loadAgents(); msg.success(t("common.deleted")); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function toggleAgent(a: CertAgent) {
  try { await updateCertAgent(a.id, { enabled: !a.enabled }); await loadAgents(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
const showEditAgent = ref(false);
const editForm = ref({ id: "", name: "", description: "", scope_cert_ids: [] as string[], enabled: true,
  device_id: null as string | null });
function editAgent(a: CertAgent) {
  editForm.value = {
    id: a.id, name: a.name, description: a.description ?? "",
    scope_cert_ids: (a.scope_cert_ids ?? []).map(String), enabled: a.enabled,
    device_id: a.device_id,
  };
  showEditAgent.value = true;
}
async function saveEditAgent() {
  if (!editForm.value.name.trim()) { msg.warning(t("certs.name_required")); return; }
  try {
    await updateCertAgent(editForm.value.id, {
      name: editForm.value.name.trim(), description: editForm.value.description || null,
      scope_cert_ids: editForm.value.scope_cert_ids, enabled: editForm.value.enabled,
      device_id: editForm.value.device_id,
    });
    showEditAgent.value = false; await loadAgents(); msg.success(t("common.saved"));
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
function copy(s: string) { navigator.clipboard?.writeText(s); msg.success(t("common.copied")); }

// ── 安裝說明 / 設定檔說明 ──
const showHelp = ref(false);
const showConfigHelp = ref(false);
const serverOrigin = window.location.origin;
// sudo 只在非 root 時加（見 utils/sudo）；帶環境變數一定要透過 env，否則 root 時 VAR=val 會被當成指令。
const installerOneLiner = computed(() =>
  `curl -fsSLk ${serverOrigin}/api/v1/cert-agents/installer.sh | ${SUDO} env `
  + `JT_IPAM_URL=${serverOrigin} JT_IPAM_AGENT_KEY=${newKey.value || "<建立代理時的-KEY>"} JT_IPAM_INSECURE=1 bash`);
const uninstallOneLiner = `curl -fsSLk ${serverOrigin}/api/v1/cert-agents/installer.sh | ${SUDO} env JT_IPAM_UNINSTALL=1 bash`;
const configExample = `# ── 快速模式（優先）：只設憑證 + 服務 ──
# 代理會把憑證寫到固定路徑並自動重載，你再把服務設定指過去：
DEPLOY_1_CERT=wildcard-example-com
DEPLOY_1_PROFILE=nginx
#   nginx → cert+chain /etc/ssl/jt-ipam/<cert>.fullchain.pem、key /etc/ssl/jt-ipam/<cert>.key

# ── 手動模式：自己指定每個檔案路徑 ──
DEPLOY_2_CERT=mail-cert
DEPLOY_2_FULLCHAIN=/etc/postfix/tls/mail.pem
DEPLOY_2_KEY=/etc/postfix/tls/mail.key
DEPLOY_2_RELOAD=systemctl reload postfix`;

// 來源類型選擇器：被選中的按鈕整顆填綠底白字，明顯看出目前選的是哪個。
const radioGreen = {
  buttonColorActive: "#18a058",
  buttonTextColorActive: "#fff",
  buttonBorderColorActive: "#18a058",
  buttonBoxShadowFocus: "inset 0 0 0 1px #18a058",
  buttonBoxShadowHover: "inset 0 0 0 1px #18a058",
};

// 操作欄按鈕：icon-only + hover tooltip 顯示文字（與全站列表操作欄一致）。
function actBtn(icon: any, label: string, onClick: () => void, props: Record<string, any> = {}) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, {
      size: "small", quaternary: true, ...props,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); },
    }, { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}

// ── 憑證表格欄位 + 顯示偏好 ──
const CERT_KEYS = ["name", "domains", "exp_date", "days_left", "version_count", "source", "actions"];
const certPg = useTablePagination();
const agentPg = useTablePagination();
const certPrefs = useColumnPrefs("certificates", CERT_KEYS, CERT_KEYS);
const certPickerItems = computed(() => [
  { key: "name", label: t("cols.name") },
  { key: "domains", label: t("certs.domains") },
  { key: "exp_date", label: t("certs.expiry_date") },
  { key: "days_left", label: t("certs.days_remaining") },
  { key: "version_count", label: t("certs.versions") },
  { key: "source", label: t("certSource.col_source") },
  { key: "actions", label: t("cols.actions") },
]);
const certExportCols = computed(() => [
  { key: "name", label: t("cols.name") },
  { key: "domains", label: t("certs.domains") },
  { key: "exp_date", label: t("certs.expiry_date") },
  { key: "days_left", label: t("certs.days_remaining") },
  { key: "version_count", label: t("certs.versions") },
  { key: "source", label: t("certSource.col_source") },
]);
const certExportRows = computed(() => certsFiltered.value.map((c) => ({
  name: c.name, domains: (c.domains ?? []).join("、"),
  exp_date: c.current_not_after ? fmtDateTime(c.current_not_after).slice(0, 10) : "",
  days_left: c.current_days_remaining != null ? String(c.current_days_remaining) : "",
  version_count: String(c.version_count),
  source: c.source_type === "none" ? "" : c.source_type.toUpperCase(),
})));
const certColsAll = computed<DataTableColumns<Certificate>>(() => autoSort([
  // name 與 domains 都設 minWidth（彈性）→ 多餘寬度由兩欄平分，避免任一欄獨自撐爆。
  { title: t("cols.name"), key: "name", minWidth: 160, ellipsis: { tooltip: true } },
  { title: t("certs.domains"), key: "domains", minWidth: 200,
    sorter: (a, b) => (a.domains?.[0] ?? "").localeCompare(b.domains?.[0] ?? ""),
    render: (c) => h(NSpace, { size: 4 }, () => (c.domains ?? []).slice(0, 4).map(d =>
      h(NTag, { size: "small" }, () => d))) },
  { title: t("certs.expiry_date"), key: "exp_date", width: 120,
    sorter: (a, b) => (a.current_not_after ?? "").localeCompare(b.current_not_after ?? ""),
    render: expDateCell },
  { title: t("certs.days_remaining"), key: "days_left", width: 110,
    sorter: (a, b) => (a.current_days_remaining ?? Infinity) - (b.current_days_remaining ?? Infinity),
    render: daysLeftCell },
  { title: t("certs.versions"), key: "version_count", width: 80 },
  { title: t("certSource.col_source"), key: "source", width: 90,
    sorter: (a, b) => a.source_type.localeCompare(b.source_type),
    render: (c) =>
      c.source_type === "none"
        ? h("span", { style: "opacity:.5" }, "—")
        : h(NTag, { size: "small", type: c.last_fetch_error ? "error" : "info" },
            () => c.source_type.toUpperCase()) },
  { title: t("cols.actions"), key: "actions", className: "col-actions", width: 248, fixed: "right",
    render: (c) => h("div", { style: "padding-right:8px" }, h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      c.version_count > 0
        ? actBtn(ExportIcon, t("certFiles.title"), () => openFiles(c))
        : actBtn(ExportIcon, t("certFiles.empty"), () => {}, { disabled: true }),
      actBtn(ImportIcon, t("certs.upload_version"), () => openUpload(c)),
      // 已設來源或已有版本 → 停用「產生自簽」避免覆蓋現有憑證；自簽憑證改提供「續簽」
      c.current_is_self_signed
        ? actBtn(RefreshIcon, t("certs.renew"), () => openRenew(c), { type: "primary", ghost: true })
        : (c.source_type !== "none" || c.version_count > 0)
          ? actBtn(TokenIcon, t("certs.self_signed_blocked"), () => {}, { disabled: true })
          : actBtn(TokenIcon, t("certs.self_signed"), () => openSelf(c)),
      actBtn(SettingsIcon, t("certSource.source"), () => openSource(c)),
      c.source_type !== "none"
        ? actBtn(SyncIcon, t("certSource.fetch_now"), () => doFetchNow(c), { type: "primary", ghost: true })
        : null,
      h(NPopconfirm, { onPositiveClick: () => removeCert(c) }, {
        trigger: () => actBtn(DeleteIcon, t("common.delete"), () => {}, { tertiary: true, type: "error" }),
        default: () => t("certs.delete_confirm") }),
    ])) },
]));
const certCols = computed<DataTableColumns<Certificate>>(() =>
  certColsAll.value.filter((c: any) => certPrefs.visibleKeys.value.includes(c.key)));

// ── 派送代理表格欄位 + 顯示偏好 ──
const AGENT_KEYS = ["name", "enabled", "scope", "agent_version", "source_ip", "last_seen_at", "reported", "actions"];
const agentPrefs = useColumnPrefs("cert_agents", AGENT_KEYS, AGENT_KEYS);
const agentPickerItems = computed(() => [
  { key: "name", label: t("cols.name") },
  { key: "enabled", label: t("cols.enabled") },
  { key: "scope", label: t("certs.scope") },
  { key: "agent_version", label: t("cols.version") },
  { key: "source_ip", label: t("cols.source_ip") },
  { key: "last_seen_at", label: t("cols.last_report") },
  { key: "reported", label: t("certs.deployed") },
  { key: "actions", label: t("cols.actions") },
]);
// 匯出（純字串欄位）
const agentExportCols = computed(() => [
  { key: "name", label: t("cols.name") },
  { key: "enabled", label: t("cols.enabled") },
  { key: "scope", label: t("certs.scope") },
  { key: "agent_version", label: t("cols.version") },
  { key: "source_ip", label: t("cols.source_ip") },
  { key: "last_seen_at", label: t("cols.last_report") },
  { key: "reported", label: t("certs.deployed") },
]);
const agentExportRows = computed(() => agentsFiltered.value.map((a) => {
  const reps = (a.reported ?? []) as any[];
  const ok = reps.filter((d) => d.status === "ok").length;
  const scopeNames = certs.value.filter((c) => (a.scope_cert_ids ?? []).map(String).includes(c.id)).map((c) => c.name);
  return {
    name: a.name, enabled: a.enabled ? "Y" : "N",
    scope: scopeNames.join("、"),
    agent_version: a.agent_version ? `v${a.agent_version}` : "",
    source_ip: a.last_source_ip ?? "",
    last_seen_at: a.last_seen_at ? fmtDateTime(a.last_seen_at) : "",
    reported: `${ok} / ${reps.length}`,
  };
}));
const agentColsAll = computed<DataTableColumns<CertAgent>>(() => autoSort([
  { title: t("cols.name"), key: "name", minWidth: 120, ellipsis: { tooltip: true },
    // 有對應裝置時，名稱可點去裝置詳情；未對應則純文字
    render: (a) => a.device_id
      ? h(NTooltip, null, {
          trigger: () => links.device(a.device_id, a.name),
          default: () => t("certs.go_device", { name: a.device_name ?? a.name }),
        })
      : a.name },
  { title: t("cols.enabled"), key: "enabled", width: 70,
    sorter: (a, b) => Number(a.enabled) - Number(b.enabled),
    render: (a) => h(NSwitch, { value: a.enabled, size: "small", onUpdateValue: () => toggleAgent(a) }) },
  { title: t("certs.scope"), key: "scope", width: 100,
    sorter: (a, b) => (a.scope_cert_ids ?? []).length - (b.scope_cert_ids ?? []).length,
    render: (a) => {
      const ids = (a.scope_cert_ids ?? []).map(String);
      const names = certs.value.filter(c => ids.includes(c.id)).map(c => c.name);
      const label = `${ids.length} ${t("certs.certs_unit")}`;
      if (!names.length) return label;
      return h(NTooltip, null, {
        trigger: () => h("span", { style: "border-bottom:1px dotted currentColor;cursor:help" }, label),
        default: () => names.join("、"),
      });
    } },
  { title: t("cols.version"), key: "agent_version", width: 120,
    render: (a) => {
      if (!a.agent_version) return "—";
      const outdated = !!a.server_agent_version && a.agent_version !== a.server_agent_version;
      const verTag = h(NTag, { size: "small", type: outdated ? "warning" : "success", bordered: false },
        () => `v${a.agent_version}`);
      if (!outdated) return verTag;
      // 「可更新」改成一個 icon，與版本同列不換行（寬度夠就完整顯示）
      return h("div", { style: "display:flex;align-items:center;gap:4px;white-space:nowrap" }, [
        verTag,
        h(NTooltip, null, {
          trigger: () => h(NIcon, { component: UpgradeIcon, size: 16,
            style: "color:var(--warning-color,#f0a020);cursor:help;flex-shrink:0" }),
          default: () => t("scan_agent.outdated_hint", { v: a.server_agent_version }),
        }),
      ]);
    } },
  { title: t("cols.source_ip"), key: "source_ip", minWidth: 150,
    render: (a) => a.last_source_ip
      ? h("div", { style: "display:flex;align-items:center;gap:4px;flex-wrap:wrap" }, [
          // 來源 IP 若對到 IPAM 的 IPAddress → 可點進該位址詳情；否則純文字
          a.source_ip_id
            ? h("span", { style: "font-family:monospace" }, [links.ipById(a.source_ip_id, a.last_source_ip)])
            : h("span", { style: "font-family:monospace" }, a.last_source_ip),
          a.multi_source_recent
            ? h(NTooltip, null, {
                trigger: () => h(NTag, { size: "tiny", type: "warning", round: true, bordered: false },
                  { default: () => t("certs.multi_ip_badge"), icon: () => h(NIcon, { component: WarnIcon }) }),
                default: () => t("certs.multi_ip_hint", { ips: a.recent_source_ips.join("、") }),
              })
            : null,
        ])
      : "—" },
  { title: t("cols.last_report"), key: "last_seen_at", minWidth: 170,
    sorter: (a, b) => (a.last_seen_at ?? "").localeCompare(b.last_seen_at ?? ""),
    render: (a) => a.last_seen_at ? fmtDateTime(a.last_seen_at) : "—" },
  { key: "reported", width: 110,
    title: () => h(NTooltip, null, {
      trigger: () => h("span", { style: "border-bottom:1px dotted currentColor;cursor:help" }, t("certs.deployed")),
      default: () => t("certs.deployed_hint"),
    }),
    sorter: (a, b) => (a.reported ?? []).length - (b.reported ?? []).length,
    render: (a) => {
      const reps = (a.reported ?? []) as any[];
      const ok = reps.filter(d => d.status === "ok").length;
      const label = `${ok} / ${reps.length}`;
      if (!reps.length) return label;
      // hover 顯示實際派送了哪些憑證 / 服務與狀態
      return h(NTooltip, null, {
        trigger: () => h("span", { style: "border-bottom:1px dotted currentColor;cursor:help" }, label),
        default: () => h("div", { style: "display:flex;flex-direction:column;gap:3px" },
          reps.map(d => h("div", { style: "display:flex;align-items:center;gap:6px;white-space:nowrap" }, [
            h(NTag, { size: "tiny", type: d.status === "ok" ? "success" : "warning", bordered: false },
              () => d.status ?? "?"),
            h("span", `${d.cert ?? "?"} / ${d.profile ?? "?"}`),
          ]))),
      });
    } },
  { title: t("cols.actions"), key: "actions", className: "col-actions", width: 213, fixed: "right",
    render: (a) => h("div", { style: "padding-right:8px" }, h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      actBtn(ToolsIcon, t("certGen.title"), () => openGen(a), { type: "primary", ghost: true }),
      actBtn(EditIcon, t("certs.edit_agent"), () => editAgent(a)),
      actBtn(EyeIcon, t("certs.view_key"), () => viewAgent(a)),
      actBtn(SyncIcon, t("certs.rotate_key"), () => doRotate(a)),
      h(NPopconfirm, { onPositiveClick: () => removeAgent(a) }, {
        trigger: () => actBtn(DeleteIcon, t("common.delete"), () => {}, { tertiary: true, type: "error" }),
        default: () => t("common.delete") + "?" }),
    ])) },
]));
const agentCols = computed<DataTableColumns<CertAgent>>(() =>
  agentColsAll.value.filter((c: any) => agentPrefs.visibleKeys.value.includes(c.key)));
</script>

<template>
  <n-card :bordered="false">
    <template #header>
      <n-space align="center" :size="8">
        <n-icon :component="LockIcon" /> {{ t("nav.certificates") }}
      </n-space>
    </template>

    <n-tabs type="line" animated>
      <!-- 憑證 -->
      <n-tab-pane name="certs">
        <template #tab><n-icon :component="LockIcon" style="margin-right:5px" />{{ t('certs.tab_certs') }}</template>
        <n-space justify="space-between" style="margin-bottom: 10px">
          <n-space :size="8" align="center">
            <n-button type="primary" size="small" @click="showNew = true">
              <template #icon><n-icon :component="PlusIcon" /></template>{{ t("certs.new") }}
            </n-button>
            <n-input v-model:value="certFilter" clearable
                     :placeholder="t('certs.filter_name')" style="width: 220px">
              <template #prefix><n-icon :component="SearchIcon" /></template>
            </n-input>
          </n-space>
          <n-space :size="8">
            <ExportButton :columns="certExportCols" :rows="certExportRows" filename="certificates"
                          :title="t('certs.tab_certs')" />
            <ColumnPicker :all="certPickerItems" :visible="certPrefs.visibleKeys.value"
                          @update:visible="certPrefs.setVisible" @reset="certPrefs.reset" />
            <n-button size="small" quaternary @click="loadCerts">
              <template #icon><n-icon :component="RefreshIcon" /></template>{{ t("common.refresh") }}
            </n-button>
          </n-space>
        </n-space>
        <n-data-table :columns="certCols" :data="certsFiltered" :loading="loading" size="small"
                      :scroll-x="1008" :pagination="certPg" :row-key="(r:Certificate) => r.id" />
      </n-tab-pane>

      <!-- 派送代理 -->
      <n-tab-pane name="agents">
        <template #tab><n-icon :component="ToolsIcon" style="margin-right:5px" />{{ t('certs.tab_agents') }}</template>
        <n-space justify="space-between" style="margin-bottom: 10px">
          <n-space :size="8" align="center">
            <n-button type="primary" size="small" @click="newKey = null; viewMode = false; showNewAgent = true">
              <template #icon><n-icon :component="PlusIcon" /></template>{{ t("certs.new_agent") }}
            </n-button>
            <n-button size="small" @click="showHelp = true">
              <template #icon><n-icon :component="InfoIcon" /></template>{{ t("certHelp.button") }}
            </n-button>
            <n-input v-model:value="agentFilter" clearable
                     :placeholder="t('certs.filter_agent')" style="width: 200px">
              <template #prefix><n-icon :component="SearchIcon" /></template>
            </n-input>
            <n-select v-model:value="agentCertFilter" :options="certOptions" clearable filterable
                      :placeholder="t('certs.filter_by_cert')" style="width: 200px" />
            <span v-if="serverAgentVersion" style="font-size:12px;opacity:.7">
              {{ t("certs.latest_agent_version") }}：<n-tag size="small" type="info" :bordered="false">v{{ serverAgentVersion }}</n-tag>
            </span>
          </n-space>
          <n-space :size="8">
            <ExportButton :columns="agentExportCols" :rows="agentExportRows" filename="cert-agents"
                          :title="t('certs.tab_agents')" />
            <ColumnPicker :all="agentPickerItems" :visible="agentPrefs.visibleKeys.value"
                          @update:visible="agentPrefs.setVisible" @reset="agentPrefs.reset" />
            <n-button size="small" quaternary @click="loadAgents">
              <template #icon><n-icon :component="RefreshIcon" /></template>{{ t("common.refresh") }}
            </n-button>
          </n-space>
        </n-space>
        <n-data-table :columns="agentCols" :data="agentsFiltered" size="small" :scroll-x="1041" :pagination="agentPg" :row-key="(r:CertAgent) => r.id" />
      </n-tab-pane>
    </n-tabs>
  </n-card>

  <!-- 新增憑證 -->
  <n-modal v-model:show="showNew" preset="card" :title="t('certs.new')" style="max-width: 460px">
    <n-form>
      <n-form-item :label="t('cols.name')">
        <n-input v-model:value="newForm.name" placeholder="wildcard-example-com" />
      </n-form-item>
      <n-form-item :label="t('common.description')">
        <n-input v-model:value="newForm.description" type="textarea" :rows="2" />
      </n-form-item>
    </n-form>
    <template #footer>
      <n-button type="primary" @click="doCreate">
        <template #icon><n-icon :component="SaveIcon" /></template>{{ t("common.save") }}
      </n-button>
    </template>
  </n-modal>

  <!-- 憑證檔案 / 下載 -->
  <n-modal v-model:show="showFiles" preset="card"
           :title="`${t('certFiles.title')} — ${filesTarget?.name}`" style="width: 640px; max-width: 94vw">
    <n-alert type="info" :bordered="false" :show-icon="true" style="margin-bottom: 14px">
      {{ t("certFiles.intro") }}
    </n-alert>
    <div v-if="!filesVersions.length" class="help-note">{{ t("certFiles.no_version") }}</div>
    <div v-for="(v, i) in filesVersions" :key="v.id"
         style="border:1px solid var(--n-border-color,#eee);border-radius:8px;padding:10px 12px;margin-bottom:10px">
      <n-space align="center" justify="space-between" :wrap="false" style="margin-bottom:8px">
        <n-space align="center" :size="6" :wrap="true">
          <n-tag v-if="v.is_current" size="tiny" type="success" :bordered="false">{{ t("certFiles.current") }}</n-tag>
          <span style="font-weight:600">{{ t("certFiles.version_n", { n: filesVersions.length - i }) }}</span>
          <n-tooltip>
            <template #trigger>
              <n-tag size="tiny" :bordered="false"
                     :type="v.chain_complete ? 'success' : (v.chain_can_rebuild ? 'warning' : 'error')">
                <template #icon><n-icon :component="v.chain_complete ? CheckIcon : WarnIcon" /></template>
                {{ v.chain_complete ? t("certFiles.chain_ok") : (v.chain_can_rebuild ? t("certFiles.chain_rebuildable") : t("certFiles.chain_no_root")) }}
              </n-tag>
            </template>
            {{ v.chain_complete ? t("certFiles.chain_ok_hint") : (v.chain_can_rebuild ? t("certFiles.chain_rebuildable_hint") : t("certFiles.chain_no_root_hint")) }}
          </n-tooltip>
        </n-space>
        <n-space :size="6" :wrap="false">
          <n-button v-if="v.chain_can_rebuild" size="small" type="warning" secondary @click="doRebuildChain(v)">
            <template #icon><n-icon :component="ToolsIcon" /></template>{{ t("certFiles.rebuild_chain") }}
          </n-button>
          <n-dropdown trigger="click" :options="dlOptions" @select="(fmt:string) => doDownload(v, fmt)">
            <n-button size="small" type="primary" secondary>
              <template #icon><n-icon :component="ExportIcon" /></template>{{ t("certFiles.download") }}
            </n-button>
          </n-dropdown>
        </n-space>
      </n-space>
      <dl class="cert-ver-detail">
        <dt>{{ t("certFiles.domains") }}</dt>
        <dd>{{ (v.domains ?? []).join("、") || "—" }}</dd>
        <dt>{{ t("certFiles.subject") }}</dt>
        <dd>{{ v.subject || "—" }}</dd>
        <dt>{{ t("certFiles.issuer") }}</dt>
        <dd>{{ v.issuer || "—" }}</dd>
        <dt>{{ t("certFiles.serial") }}</dt>
        <dd><code>{{ v.serial || "—" }}</code></dd>
        <dt>{{ t("certFiles.validity") }}</dt>
        <dd>{{ v.not_before ? fmtDateTime(v.not_before).slice(0, 10) : "—" }} ～ {{ fmtDateTime(v.not_after).slice(0, 10) }}</dd>
        <dt>{{ t("certFiles.fingerprint") }}</dt>
        <dd class="fp">
          <code>{{ v.fingerprint_sha256 }}</code>
          <n-button size="tiny" text @click="copy(v.fingerprint_sha256)"><template #icon><n-icon :component="CopyIcon" /></template></n-button>
        </dd>
        <dt>{{ t("certFiles.uploaded_at") }}</dt>
        <dd>{{ fmtDateTime(v.created_at) }}</dd>
      </dl>
    </div>
  </n-modal>

  <!-- 上傳新版 -->
  <n-modal v-model:show="showUpload" preset="card"
           :title="`${t('certs.upload_version')} — ${uploadTarget?.name}`" style="max-width: 520px">
    <n-radio-group v-model:value="upMode" size="small" style="margin-bottom: 12px">
      <n-radio-button value="file">{{ t("certs.mode_file") }}</n-radio-button>
      <n-radio-button value="paste">{{ t("certs.mode_paste") }}</n-radio-button>
    </n-radio-group>
    <n-form v-if="upMode === 'file'">
      <n-form-item :label="t('certs.crt_file')">
        <input type="file" accept=".crt,.pem,.cer" @change="(e) => pick(e, 'cert')" />
      </n-form-item>
      <n-form-item :label="t('certs.key_file')">
        <input type="file" accept=".key,.pem" @change="(e) => pick(e, 'key')" />
      </n-form-item>
      <n-form-item :label="t('certs.chain_file')">
        <input type="file" accept=".crt,.pem,.cer" @change="(e) => pick(e, 'chain')" />
      </n-form-item>
    </n-form>
    <n-form v-else>
      <n-form-item :label="t('certs.crt_file')">
        <n-input v-model:value="pasteCert" type="textarea" :rows="4"
                 placeholder="-----BEGIN CERTIFICATE-----" />
      </n-form-item>
      <n-form-item :label="t('certs.key_file')">
        <n-input v-model:value="pasteKey" type="textarea" :rows="4"
                 placeholder="-----BEGIN PRIVATE KEY-----" />
      </n-form-item>
      <n-form-item :label="t('certs.chain_file')">
        <n-input v-model:value="pasteChain" type="textarea" :rows="3"
                 placeholder="-----BEGIN CERTIFICATE-----（選填）" />
      </n-form-item>
    </n-form>
    <n-checkbox v-model:checked="upAllowExpired">{{ t("certs.allow_expired") }}</n-checkbox>
    <template #footer>
      <n-button type="primary" :loading="upBusy" @click="doUpload">{{ t("certs.upload") }}</n-button>
    </template>
  </n-modal>

  <!-- 產生自簽 -->
  <n-modal v-model:show="showSelf" preset="card"
           :title="`${selfRenew ? t('certs.renew') : t('certs.self_signed')} — ${selfTarget?.name}`" style="max-width: 480px">
    <n-alert v-if="selfRenew" type="info" :bordered="false" :show-icon="false" style="margin-bottom: 12px">
      {{ t("certs.renew_hint") }}
    </n-alert>
    <n-form>
      <n-form-item :label="t('certs.common_name')">
        <n-input v-model:value="selfForm.common_name" placeholder="host.lan" />
      </n-form-item>
      <n-form-item :label="t('certs.sans')">
        <n-dynamic-tags v-model:value="selfForm.sans" />
      </n-form-item>
      <n-form-item :label="t('certs.days')">
        <n-input-number v-model:value="selfForm.days" :min="1" :max="3650" />
      </n-form-item>
    </n-form>
    <template #footer>
      <n-button type="primary" @click="doSelf">{{ selfRenew ? t("certs.renew_action") : t("certs.generate") }}</n-button>
    </template>
  </n-modal>

  <!-- 自動抓取來源 -->
  <n-modal v-model:show="showSource" preset="card"
           :title="`${t('certSource.title')} — ${sourceTarget?.name}`" style="max-width: 600px">
    <n-form>
      <n-form-item :label="t('certSource.type')">
        <n-radio-group v-model:value="sourceForm.source_type" size="medium" :theme-overrides="radioGreen">
          <n-radio-button value="none">{{ t("certSource.type_none") }}</n-radio-button>
          <n-radio-button value="url">URL</n-radio-button>
          <n-radio-button value="sftp">SFTP</n-radio-button>
        </n-radio-group>
      </n-form-item>

      <template v-if="sourceForm.source_type === 'url'">
        <n-form-item label="cert_url"><n-input v-model:value="sourceForm.cert_url" placeholder="https://ca/cert.pem" /></n-form-item>
        <n-form-item label="key_url"><n-input v-model:value="sourceForm.key_url" :placeholder="t('certSource.optional_reuse_key')" /></n-form-item>
        <n-form-item label="chain_url"><n-input v-model:value="sourceForm.chain_url" :placeholder="t('certSource.optional')" /></n-form-item>
      </template>

      <template v-else-if="sourceForm.source_type === 'sftp'">
        <n-form-item :label="t('certSource.host')"><n-input v-model:value="sourceForm.host" placeholder="ca.example.com" /></n-form-item>
        <n-form-item :label="t('certSource.port')"><n-input-number v-model:value="sourceForm.port" :min="1" :max="65535" /></n-form-item>
        <n-form-item :label="t('certSource.username')"><n-input v-model:value="sourceForm.username" /></n-form-item>
        <n-divider style="margin: 4px 0 10px" title-placement="left">
          <span style="font-size: 12px; opacity: .7">{{ t("certSource.auth_section") }}</span>
        </n-divider>
        <div style="font-size: 12px; opacity: .7; margin: -4px 0 8px">{{ t("certSource.auth_hint") }}</div>
        <n-form-item :label="t('certSource.password')"><n-input v-model:value="sourceForm.source_password" type="password" show-password-on="click" :placeholder="t('certSource.secret_keep')" /></n-form-item>
        <n-form-item :label="t('certSource.private_key')">
          <n-space vertical :size="6" style="width:100%">
            <n-input v-model:value="sourceForm.source_private_key" type="textarea" :rows="3"
                     :disabled="!!sshPubKey"
                     :placeholder="sshPubKey ? t('certSource.key_managed') : t('certSource.ssh_key_keep')" />
            <n-space :size="8" align="center">
              <n-button size="tiny" secondary :loading="genningKey" @click="genSshKey">
                <template #icon><n-icon :component="TokenIcon" /></template>{{ t("certSource.gen_key") }}
              </n-button>
              <span style="font-size:12px;opacity:.6">{{ t("certSource.gen_key_hint") }}</span>
            </n-space>
            <n-alert v-if="sshPubKey" :type="sshInstalled ? 'success' : 'warning'" :bordered="true" :show-icon="false" style="font-size:12px">
              <div style="font-weight:600;margin-bottom:4px">
                {{ sshInstalled ? t("certSource.pub_key_installed") : t("certSource.pub_key_label") }}
              </div>
              <n-input :value="sshPubKey" type="textarea" :rows="2" readonly style="font-family:monospace;font-size:11px" />
              <n-space :size="6" style="margin-top:6px">
                <n-button size="tiny" secondary @click="copy(sshPubKey)">
                  <template #icon><n-icon :component="CopyIcon" /></template>{{ t("certHelp.copy") }}
                </n-button>
              </n-space>
            </n-alert>
          </n-space>
        </n-form-item>
        <n-divider style="margin: 4px 0 10px" title-placement="left">
          <span style="font-size: 12px; opacity: .7">{{ t("certSource.remote_files") }}</span>
        </n-divider>
        <n-form-item label="cert_path"><n-input v-model:value="sourceForm.cert_path" placeholder="/etc/ssl/cert.pem" /></n-form-item>
        <n-form-item label="key_path"><n-input v-model:value="sourceForm.key_path" :placeholder="t('certSource.optional_reuse_key')" /></n-form-item>
        <n-form-item label="chain_path"><n-input v-model:value="sourceForm.chain_path" :placeholder="t('certSource.optional')" /></n-form-item>
      </template>

      <n-form-item v-if="sourceForm.source_type !== 'none'" :label="t('certSource.interval_hours')">
        <n-input-number v-model:value="sourceForm.fetch_interval_hours" :min="1" :max="720" />
      </n-form-item>
    </n-form>
    <n-alert v-if="sourceTarget?.last_fetch_error" type="error" :bordered="false" style="margin-top: 4px">
      {{ t("certSource.last_error") }}: {{ sourceTarget.last_fetch_error }}
    </n-alert>
    <template #footer>
      <n-space justify="space-between">
        <n-button v-if="sourceForm.source_type !== 'none'" :loading="testing" @click="testSource">
          <template #icon><n-icon :component="TestIcon" /></template>{{ t("certSource.test") }}
        </n-button>
        <span v-else />
        <n-button type="primary" @click="saveSource">
          <template #icon><n-icon :component="SaveIcon" /></template>{{ t("common.save") }}
        </n-button>
      </n-space>
    </template>
  </n-modal>

  <!-- 新增代理 / 顯示 key + 安裝指令 -->
  <n-modal v-model:show="showNewAgent" preset="card" style="max-width: 600px"
           :title="newKey ? (viewMode ? `${t('certs.agent_info')} — ${viewAgentName}` : t('certs.new_agent')) : t('certs.new_agent')">
    <template v-if="newKey">
      <n-alert :type="viewMode ? 'info' : 'success'" :title="viewMode ? t('certs.key_label') : t('certs.key_saved')"
               :bordered="false" style="margin-bottom: 14px">
        <n-space align="center" :wrap="false">
          <code style="word-break: break-all; flex: 1">{{ newKey }}</code>
          <n-button size="tiny" secondary @click="copy(newKey!)">
            <template #icon><n-icon :component="CopyIcon" /></template>{{ t("certHelp.copy") }}
          </n-button>
        </n-space>
      </n-alert>
      <div style="font-weight: 600; margin: 4px 0 6px">{{ t("certHelp.oneliner_label") }}</div>
      <n-space align="center" :wrap="false" :size="8">
        <code class="help-code">{{ installerOneLiner }}</code>
        <n-button size="small" secondary @click="copy(installerOneLiner)">
          <template #icon><n-icon :component="CopyIcon" /></template>{{ t("certHelp.copy") }}
        </n-button>
      </n-space>
      <div class="help-subtle" style="margin-top:10px;margin-bottom:5px">{{ t("certHelp.distros_title") }}</div>
      <n-space :size="[6, 6]">
        <n-tag v-for="os in SUPPORTED_OS" :key="os" size="small" type="success" :bordered="false" round>{{ os }}</n-tag>
      </n-space>
      <div class="help-note" style="margin-top:6px">{{ t("certHelp.distros_note") }}</div>
      <n-alert type="warning" :bordered="false" :show-icon="true" style="margin-top:12px">
        {{ t("certs.one_key_per_host") }}
      </n-alert>
      <n-alert type="info" :bordered="false" :show-icon="true" style="margin-top:10px">
        <template #icon><n-icon :component="ToolsIcon" /></template>
        {{ t("certHelp.after_install_gen") }}
      </n-alert>
    </template>
    <n-form v-if="!newKey">
      <n-form-item :label="t('cols.name')">
        <n-input v-model:value="agentForm.name" placeholder="web01" />
      </n-form-item>
      <n-form-item :label="t('certs.scope_certs')">
        <n-select v-model:value="agentForm.scope_cert_ids" multiple filterable :options="certOptions"
                  :placeholder="t('certs.scope_hint')" />
      </n-form-item>
    </n-form>
    <template #footer>
      <n-button v-if="!newKey" type="primary" @click="doCreateAgent">
        <template #icon><n-icon :component="SaveIcon" /></template>{{ t("common.save") }}
      </n-button>
      <n-button v-else @click="showNewAgent = false">
        <template #icon><n-icon :component="CancelIcon" /></template>{{ t("common.close") }}
      </n-button>
    </template>
  </n-modal>

  <!-- 編輯代理 -->
  <n-modal v-model:show="showEditAgent" preset="card" :title="t('certs.edit_agent')" style="max-width: 540px">
    <n-form>
      <n-form-item :label="t('cols.name')">
        <n-input v-model:value="editForm.name" />
      </n-form-item>
      <n-form-item :label="t('certs.scope_certs')">
        <n-select v-model:value="editForm.scope_cert_ids" multiple filterable :options="editCertOptions"
                  :placeholder="t('certs.scope_hint')" />
      </n-form-item>
      <n-form-item :label="t('certs.device')">
        <div style="width:100%">
          <n-select v-model:value="editForm.device_id" filterable clearable :options="deviceOptions"
                    :placeholder="t('certs.device_hint')" />
          <div style="margin-top:4px;font-size:12px;color:#94a3b8;line-height:1.5">{{ t("certs.device_help") }}</div>
        </div>
      </n-form-item>
      <n-form-item :label="t('cols.enabled')">
        <n-switch v-model:value="editForm.enabled" />
      </n-form-item>
    </n-form>
    <template #footer>
      <n-button type="primary" @click="saveEditAgent">
        <template #icon><n-icon :component="SaveIcon" /></template>{{ t("common.save") }}
      </n-button>
    </template>
  </n-modal>

  <!-- 安裝說明 -->
  <n-modal v-model:show="showHelp" preset="card" :title="t('certHelp.title')"
           style="width: 760px; max-width: 94vw">
    <n-alert type="info" :bordered="false" :show-icon="true" style="margin-bottom: 20px">
      {{ t("certHelp.intro") }}
    </n-alert>

    <!-- 步驟 1 -->
    <div class="help-step">
      <div class="help-step-num">1</div>
      <div class="help-step-body">
        <div class="help-step-title">{{ t("certHelp.step1") }}</div>
      </div>
    </div>

    <!-- 步驟 2 -->
    <div class="help-step">
      <div class="help-step-num">2</div>
      <div class="help-step-body">
        <div class="help-step-title">{{ t("certHelp.step2") }}</div>
        <div class="help-subtle" style="margin-top:10px;margin-bottom:5px">{{ t("certHelp.distros_title") }}</div>
        <n-space :size="[6, 6]">
          <n-tag v-for="os in SUPPORTED_OS" :key="os" size="small" type="success" :bordered="false" round>{{ os }}</n-tag>
        </n-space>
      </div>
    </div>

    <!-- 步驟 3 -->
    <div class="help-step">
      <div class="help-step-num">3</div>
      <div class="help-step-body">
        <div class="help-step-title" style="display:flex; align-items:center; gap:6px; flex-wrap:wrap">
          <span>{{ t("certHelp.step3_gen_a") }}</span>
          <n-tag size="small" :bordered="false" type="primary">
            <template #icon><n-icon :component="ToolsIcon" /></template>{{ t("certGen.title") }}
          </n-tag>
          <span>{{ t("certHelp.step3_gen_b") }}</span>
        </div>
        <n-space :size="8" style="margin-top: 10px; margin-bottom: 4px">
          <n-button size="small" type="primary" secondary @click="showHelp = false; showConfigHelp = true">
            <template #icon><n-icon :component="InfoIcon" /></template>{{ t("certConfigHelp.button") }}
          </n-button>
        </n-space>
        <div class="help-note" style="margin-top: 6px">{{ t("certHelp.step3") }}</div>
        <div class="help-note" style="margin-top: 4px">{{ t("certHelp.step3_hint") }}</div>
      </div>
    </div>

    <n-divider style="margin: 6px 0 14px" />
    <div class="help-subtle" style="font-weight: 600; margin-bottom: 6px">{{ t("certHelp.uninstall_label") }}</div>
    <n-space align="center" :wrap="false" :size="8">
      <code class="help-code">{{ uninstallOneLiner }}</code>
      <n-button size="small" secondary @click="copy(uninstallOneLiner)">
        <template #icon><n-icon :component="CopyIcon" /></template>{{ t("certHelp.copy") }}
      </n-button>
    </n-space>
    <div class="help-note" style="margin-bottom: 14px">{{ t("certHelp.uninstall_note") }}</div>

    <n-space vertical :size="10">
      <n-alert type="warning" :bordered="true" :show-icon="true" style="font-size: 12px">
        {{ t("certs.one_key_per_host") }}
      </n-alert>
      <n-alert type="default" :bordered="true" :show-icon="false" style="font-size: 12px">
        {{ t("certHelp.service_note") }}
      </n-alert>
      <n-alert type="default" :bordered="true" :show-icon="false" style="font-size: 12px">
        {{ t("certHelp.requirements") }}
      </n-alert>
      <n-alert type="success" :bordered="true" :show-icon="false" style="font-size: 12px">
        {{ t("certHelp.autoupdate") }}
      </n-alert>
    </n-space>
  </n-modal>

  <!-- 設定檔說明 -->
  <n-modal v-model:show="showConfigHelp" preset="card" :title="t('certConfigHelp.title')"
           style="width: 760px; max-width: 94vw">
    <n-alert type="info" :bordered="false" :show-icon="true" style="margin-bottom: 14px">
      {{ t("certConfigHelp.intro") }}
    </n-alert>
    <div class="help-subtle" style="margin-bottom: 4px">{{ t("certHelp.config_label") }}</div>
    <pre class="help-pre">{{ configExample }}</pre>
    <div class="help-note">{{ t("certHelp.profiles") }}</div>
    <div class="help-note">{{ t("certHelp.dryrun") }}</div>
  </n-modal>

  <!-- 設定檔產生器 -->
  <n-modal v-model:show="showGen" preset="card"
           :title="`${t('certGen.title')} — ${genAgentName}`" style="width: 680px; max-width: 94vw">
    <n-alert type="info" :bordered="false" :show-icon="true" style="margin-bottom: 14px">
      {{ t("certGen.intro") }}
    </n-alert>

    <div style="font-weight:600;margin-bottom:6px">{{ t("certGen.quick") }}</div>
    <n-form-item :label="t('certGen.certs')" :show-feedback="false" style="margin-bottom:4px">
      <n-select v-model:value="genCerts" multiple :options="genCertOptions" :disabled="!genCertOptions.length"
                :placeholder="genCertOptions.length ? t('certGen.certs_ph') : t('certGen.no_scope')" />
    </n-form-item>
    <div class="help-note" style="margin:0 0 10px">{{ t("certGen.scope_hint") }}</div>
    <n-form-item :label="t('certGen.services')" :show-feedback="false">
      <n-checkbox-group v-model:value="genProfiles" style="width:100%">
        <div class="gen-svc-grid">
          <n-checkbox v-for="p in PROFILE_OPTIONS" :key="p" :value="p"
                      :label="p === 'files' ? t('certGen.files_only') : p" />
        </div>
      </n-checkbox-group>
    </n-form-item>

    <n-collapse style="margin-top:12px">
      <n-collapse-item :title="t('certGen.advanced')" name="adv">
        <div class="help-note" style="margin:-4px 0 10px">{{ t("certGen.advanced_hint") }}</div>
        <n-form-item :label="t('certGen.cert')" :show-feedback="false" style="margin-bottom:8px">
          <n-select v-model:value="genManual.cert" clearable :options="genCertOptions" :placeholder="t('certGen.certs_ph')" />
        </n-form-item>
        <n-form-item label="FULLCHAIN（cert+chain）" :show-feedback="false" style="margin-bottom:8px">
          <n-input v-model:value="genManual.fullchain" placeholder="/etc/nginx/ssl/site.pem" />
        </n-form-item>
        <n-form-item label="KEY" :show-feedback="false" style="margin-bottom:8px">
          <n-input v-model:value="genManual.key" placeholder="/etc/nginx/ssl/site.key" />
        </n-form-item>
        <n-form-item label="RELOAD" :show-feedback="false" style="margin-bottom:8px">
          <n-input v-model:value="genManual.reload" placeholder="systemctl reload nginx" />
        </n-form-item>
        <n-space :size="10" style="margin-bottom:8px">
          <n-input v-model:value="genManual.crt" placeholder="CRT（leaf）" />
          <n-input v-model:value="genManual.chain" placeholder="CHAIN" />
        </n-space>
        <n-space :size="10">
          <n-input v-model:value="genManual.combined" placeholder="COMBINED" />
          <n-input v-model:value="genManual.test" placeholder="TEST（config-test 指令）" />
        </n-space>
      </n-collapse-item>
    </n-collapse>

    <!-- 步驟 1：產生的設定檔內容 -->
    <n-divider style="margin: 14px 0 10px" />
    <div style="font-weight:600;margin-bottom:6px">① {{ t("certGen.preview") }}</div>
    <div class="help-note" style="margin-bottom:6px">{{ t("certGen.paste_hint") }}</div>
    <n-space align="start" :wrap="false" :size="8">
      <pre class="help-pre" style="min-height:54px;flex:1;margin:0">{{ genConfig || t("certGen.empty") }}</pre>
      <n-button size="small" type="primary" secondary :disabled="!genConfig" @click="copy(genConfig)">
        <template #icon><n-icon :component="CopyIcon" /></template>{{ t("certHelp.copy") }}
      </n-button>
    </n-space>

    <!-- 步驟 2：服務設定 + 寫入路徑（每個服務一塊） -->
    <template v-if="genServiceBlocks.length">
      <n-divider style="margin: 14px 0 10px" />
      <div style="font-weight:600;margin-bottom:4px">② {{ t("certGen.files_title") }}</div>
      <div class="help-note" style="margin-bottom:10px">{{ t("certGen.files_hint") }}</div>
      <div v-for="(blk, i) in genServiceBlocks" :key="i"
           style="margin-bottom:14px;border:1px solid var(--n-border-color,#eee);border-radius:8px;padding:10px 12px">
        <n-tag size="small" :bordered="false" type="info" style="margin-bottom:6px">{{ blk.cert }} / {{ blk.prof }}</n-tag>
        <div v-for="(f, j) in blk.files" :key="j" style="font-size:12px;line-height:1.9;display:flex;align-items:center;gap:6px">
          <span style="opacity:.6;flex:0 0 96px">{{ f.kind }}</span>
          <code style="font-family:monospace;flex:1;word-break:break-all">{{ f.path }}</code>
          <n-button size="tiny" quaternary @click="copy(f.path)"><template #icon><n-icon :component="CopyIcon" /></template></n-button>
        </div>
        <template v-if="blk.snippet">
          <div class="help-subtle" style="margin:8px 0 4px">{{ t("certGen.service_config") }}</div>
          <n-space align="start" :wrap="false" :size="8">
            <pre class="help-pre" style="flex:1;margin:0">{{ blk.snippet }}</pre>
            <n-button size="small" secondary @click="copy(blk.snippet)">
              <template #icon><n-icon :component="CopyIcon" /></template>{{ t("certHelp.copy") }}
            </n-button>
          </n-space>
        </template>
        <div v-else class="help-note" style="margin-top:6px">{{ t("certGen.no_config_needed") }}</div>
      </div>
    </template>

    <!-- 步驟 3：在主機上試跑 / 正式跑 -->
    <n-divider style="margin: 14px 0 10px" />
    <div style="font-weight:600;margin-bottom:6px">③ {{ t("certGen.run_title") }}</div>
    <div class="help-note" style="margin-bottom:4px">{{ t("certGen.run_dry") }}</div>
    <n-space align="start" :wrap="false" :size="8" style="margin-bottom:8px">
      <code class="help-code">{{ dryRunCmd }}</code>
      <n-button size="small" secondary @click="copy(dryRunCmd)"><template #icon><n-icon :component="CopyIcon" /></template>{{ t("certHelp.copy") }}</n-button>
    </n-space>
    <div class="help-note" style="margin-bottom:4px">{{ t("certGen.run_real") }}</div>
    <n-space align="start" :wrap="false" :size="8">
      <code class="help-code">{{ runCmd }}</code>
      <n-button size="small" secondary @click="copy(runCmd)"><template #icon><n-icon :component="CopyIcon" /></template>{{ t("certHelp.copy") }}</n-button>
    </n-space>
  </n-modal>
</template>

<style scoped>
.help-step { display: flex; gap: 12px; margin-bottom: 20px; }
.help-step-num {
  flex: 0 0 auto; width: 24px; height: 24px; border-radius: 50%;
  background: var(--primary-color, #18a058); color: #fff;
  display: flex; align-items: center; justify-content: center;
  font-size: 13px; font-weight: 600; line-height: 1;
}
.help-step-body { flex: 1; min-width: 0; }
.help-step-title { font-weight: 600; line-height: 1.6; }
.help-code {
  display: block;            /* 改 block：padding 對每一行一致，第一行不再被縮排 */
  flex: 1; min-width: 0; word-break: break-all;
  background: var(--n-color-embedded, rgba(0,0,0,.05)); padding: 9px 10px;
  border-radius: 6px; font-size: 12px; line-height: 1.5;
}
.help-pre {
  margin: 6px 0 0; background: var(--n-color-embedded, rgba(0,0,0,.05));
  padding: 10px 12px; border-radius: 6px; font-size: 12px; line-height: 1.6;
  white-space: pre-wrap;
}
.help-subtle { font-size: 12px; opacity: .7; }
.help-note { font-size: 12px; opacity: .68; line-height: 1.6; margin-top: 8px; }
/* 憑證版本詳細資訊：兩欄對齊（標籤欄 + 值欄），值整齊靠左對齊 */
.cert-ver-detail { display: grid; grid-template-columns: max-content 1fr; column-gap: 16px; row-gap: 7px; margin: 0; font-size: 12px; align-items: baseline; }
.cert-ver-detail dt { opacity: .5; font-weight: 600; white-space: nowrap; }
.cert-ver-detail dd { margin: 0; opacity: .92; word-break: break-word; }
.cert-ver-detail code { font-size: 11px; font-family: var(--n-font-family-mono, ui-monospace, "SF Mono", Menlo, Consolas, monospace); }
.cert-ver-detail dd.fp { display: flex; align-items: center; gap: 6px; min-width: 0; }
.cert-ver-detail dd.fp code { word-break: break-all; flex: 1; }
/* 服務多選格：欄寬足夠容下最長標籤（wazuh-dashboard），每項不換行 */
.gen-svc-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(135px, 1fr)); gap: 8px 10px; }
.gen-svc-grid :deep(.n-checkbox__label) { white-space: nowrap; }
</style>
