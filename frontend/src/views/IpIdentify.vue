<template>
  <div class="idf-page">
    <!-- 標題列：IP、主機名稱、回到 IP 詳細資料、開始探測 -->
    <n-card :bordered="false" content-style="padding: 14px 16px">
      <div class="idf-head">
        <div class="idf-head__title">
          <n-icon :size="20"><IdentifyIcon /></n-icon>
          <span>{{ t("identify.title") }}</span>
          <span class="idf-head__ip">{{ ipText }}</span>
          <n-tag v-if="addr?.hostname" size="small" :bordered="false" round>{{ addr.hostname }}</n-tag>
          <!-- 以位址探測：IPAM 還沒有這筆記錄 -->
          <n-tag v-if="ipTarget && ipTarget.record_count === 0" size="small" type="warning" :bordered="false" round data-testid="identify-unregistered">
            {{ t("identify.unregistered", { subnet: ipTarget.subnet_cidr }) }}
          </n-tag>
          <!-- 未授權 IP 是從 ARP 表來的：最後一次在哪裡、什麼時候看到它 -->
          <span v-if="ipTarget?.arp_last_seen" class="idf-arp" data-testid="identify-arp-seen">
            {{ t("identify.arp_last_seen", { at: fmtDateTime(ipTarget.arp_last_seen), source: ipTarget.arp_source ?? "—" }) }}
          </span>
        </div>
        <n-space :size="8" align="center" :wrap="false">
          <n-button size="small" @click="goBack">
            <template #icon><n-icon><ArrowLeftIcon /></n-icon></template>{{ t("common.back") }}
          </n-button>
          <n-button v-if="isAdmin" type="primary" size="small" :loading="starting"
                    :disabled="anyRunning || (!addr && !ipTarget)" data-testid="identify-start" @click="start">
            <template #icon><n-icon><IdentifyIcon /></n-icon></template>
            {{ history.length ? t("identify.again") : t("identify.start") }}
          </n-button>
        </n-space>
      </div>
      <p class="idf-intro">{{ t("identify.intro") }}</p>
      <p class="idf-intro">{{ t("identify.page_hint") }}</p>
    </n-card>

    <n-result v-if="!isAdmin" status="403" :title="t('errors.admin_required')" style="margin-top: 24px" />
    <n-alert v-if="errorText" type="error" :bordered="false" style="margin-top: 12px">{{ errorText }}</n-alert>

    <div v-if="isAdmin" class="idf-body">
      <!-- 歷次探測：每次的結果都保留，點一筆就看那一次 -->
      <n-card :title="t('identify.history')" size="small" class="idf-hist" data-testid="identify-history">
        <n-spin v-if="loadingHistory && !history.length" :size="16" />
        <div v-else-if="!history.length" class="idf-muted">{{ t("identify.history_empty") }}</div>
        <div v-else class="idf-hist__list">
          <button v-for="h in history" :key="h.job_id" type="button" class="idf-hist__item"
                  :class="{ 'is-active': h.job_id === selectedId }" data-testid="identify-history-item"
                  @click="select(h.job_id)">
            <div class="idf-hist__top">
              <span>{{ fmtDateTime(h.created_at) }}</span>
              <n-tag size="tiny" :bordered="false" :type="statusType(h.status)">{{ statusLabel(h.status) }}</n-tag>
            </div>
            <div class="idf-hist__sub">
              <template v-if="h.summary">
                {{ t(`identify.type.${h.summary.device_type}`) }}<template v-if="h.summary.os"> · {{ h.summary.os }}</template>
              </template>
              <template v-else-if="h.agent_name">{{ h.agent_name }}</template>
            </div>
          </button>
        </div>
      </n-card>

      <!-- 選到的那一次 -->
      <div class="idf-main">
        <n-card v-if="job" size="small">
          <div class="idf-status" data-testid="identify-status">
            <n-spin v-if="running" :size="14" />
            <span>{{ statusText }}</span>
          </div>

          <!-- 執行中：可以展開看現在在做什麼 -->
          <n-collapse v-if="running" :default-expanded-names="['progress']" style="margin-top: 10px">
            <n-collapse-item :title="t('identify.progress')" name="progress" data-testid="identify-progress">
              <ol class="idf-steps">
                <li v-for="s in steps" :key="s.key" :class="`is-${s.state}`">
                  <span class="idf-steps__mark">
                    <n-spin v-if="s.state === 'active'" :size="12" />
                    <n-icon v-else-if="s.state === 'done'" :size="14"><CheckIcon /></n-icon>
                    <span v-else class="idf-steps__dot" />
                  </span>
                  <span>{{ t(`identify.step_${s.key}`) }}</span>
                </li>
              </ol>
              <div class="idf-muted">{{ t("identify.elapsed", { s: elapsedText }) }}</div>
            </n-collapse-item>
          </n-collapse>
          <!-- 卡住時可以取消（例如部署重啟時代理的回報掉了），不必等滿逾時 -->
          <n-popconfirm v-if="running" @positive-click="cancel">
            <template #trigger>
              <n-button size="small" secondary :loading="cancelling" style="margin-top: 10px"
                        data-testid="identify-cancel"><template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("identify.cancel") }}</n-button>
            </template>
            {{ t("identify.cancel_confirm") }}
          </n-popconfirm>

          <n-alert v-if="job.status === 'done' && job.summary && !job.summary.nmap_available" type="warning"
                   :bordered="false" style="margin-top: 12px">{{ t("identify.no_nmap") }}</n-alert>
          <n-alert v-if="scanError" type="warning" :bordered="false" style="margin-top: 12px"
                   data-testid="identify-scan-failed">{{ scanError }}</n-alert>
          <n-alert v-if="jobError" type="error" :bordered="false" style="margin-top: 12px">{{ jobError }}</n-alert>
        </n-card>
        <n-card v-else-if="!loadingHistory" size="small">
          <div class="idf-muted">{{ t("identify.none_yet") }}</div>
        </n-card>

        <!-- 其他整合系統認得這個位址嗎（虛擬化、監控、代理、DHCP、交換器）：探測前就知道對象是誰（2026-10-20） -->
        <n-card :title="t('identify.integrations_title')" size="small" data-testid="identify-integrations">
          <n-spin v-if="loadingDossier" :size="14" />
          <div v-else-if="dossier && !integrations.length" class="idf-muted">{{ t("identify.integrations_none") }}</div>
          <!-- 一段一張表、值放在自己的欄位（同頁憑證表的排法；2026-10-21 用「 · 」串成一行時參差不齊） -->
          <div v-else-if="integrations.length" class="idf-integ">
            <div v-for="tb in integrations" :key="tb.key" :data-testid="`identify-integ-${tb.key}`">
              <div class="idf-integ__k">{{ tb.rows.length > 1 ? `${tb.title}（${tb.rows.length}）` : tb.title }}</div>
              <n-data-table :columns="integCols(tb)" :data="integRows(tb)" size="small" :bordered="false"
                            :row-key="(r: Record<string, string>) => r.__k" :scroll-x="Math.max(520, tb.cols.length * 108)" />
            </div>
          </div>
        </n-card>

        <template v-if="job?.status === 'done' && job.summary">
          <!-- 換手前的探測：結果屬於上一台設備，畫面要講清楚，不可以當成現在這台（2026-10-12） -->
          <n-alert v-if="job.before_device_change" type="warning" :bordered="false" data-testid="identify-before-device-change">
            {{ t("identify.before_device_change") }}
          </n-alert>
          <!-- 探測時完全沒有回應：講清楚，不要只寫「無法判斷」讓人以為是探測壞掉 -->
          <n-alert v-if="job.summary.no_response" type="warning" :bordered="false" data-testid="identify-no-response">
            {{ t("identify.no_response_hint") }}
            <template v-if="job.summary.nic_vendor ?? job.summary.vendor"> {{ t("identify.no_response_vendor", { vendor: job.summary.nic_vendor ?? job.summary.vendor }) }}</template>
          </n-alert>
          <n-card :title="t('identify.summary')" size="small">
            <n-descriptions bordered :column="narrow ? 1 : 2" size="small" label-placement="left"
                            :label-style="{ whiteSpace: 'nowrap' }" data-testid="identify-summary">
              <n-descriptions-item :label="t('identify.device_type')">
                <span class="idf-type-row">
                  <span class="idf-type" :class="`idf-type--${job.summary.device_type}`">
                    {{ t(`identify.type.${job.summary.device_type}`) }}
                  </span>
                  <span v-if="!job.summary.no_response" class="idf-guess" data-testid="identify-guess">{{ t("identify.guess_tag") }}</span>
                </span>
                <!-- IP 記錄還會對照 IPAM 已知的事實；講出最後採用什麼、依據什麼，跟 IP 頁一致 -->
                <div v-if="job.summary.ipam" class="idf-ipam" data-testid="identify-ipam-kind">
                  {{ t("identify.ipam_kind", { kind: t(`identify.type.${job.summary.ipam.kind ?? "unknown"}`),
                                               source: ipamSource(job.summary.ipam.reason) }) }}
                </div>
              </n-descriptions-item>
              <n-descriptions-item :label="t('identify.os')">
                <span v-if="job.summary.os" class="idf-os">{{ job.summary.os }}</span>
                <template v-else>—</template>
              </n-descriptions-item>
              <!-- 設備廠牌（服務自己講的、可信的指紋）與網卡廠牌（MAC 的 OUI）分開：網卡的品牌不等於設備的品牌，
                   Mac 接 CalDigit 擴充座、PC 插了 Intel 網卡都很常見（使用者 2026-10-05） -->
              <n-descriptions-item :label="t('identify.vendor')">{{ job.summary.vendor ?? "—" }}</n-descriptions-item>
              <!-- MAC 本身也要看得到：以前只顯示網卡廠牌，隨機（私人）MAC 查不到廠牌時整格是「—」，
                   看起來像沒抓到 MAC（使用者 2026-10-07，iPhone） -->
              <n-descriptions-item :label="t('identify.mac_label')">
                <template v-if="probeMac">
                  <span class="idf-mono" data-testid="identify-mac">{{ probeMac }}</span>
                  <span v-if="job.summary.nic_vendor" data-testid="identify-nic-vendor"> （{{ job.summary.nic_vendor }}）</span>
                  <n-tooltip v-else-if="isRandomMac(probeMac)">
                    <template #trigger>
                      <n-tag size="small" :bordered="false" type="warning" style="margin-left: 6px"
                             data-testid="identify-mac-random">{{ t("identify.mac_random_tag") }}</n-tag>
                    </template>
                    {{ t("investigate.mac_random") }}
                  </n-tooltip>
                  <!-- 這次 nmap 看到的 MAC 跟 IP 記錄不同（換了網卡、IP 被別台用了） -->
                  <n-tag v-if="macSeenOther" size="small" :bordered="false" type="warning" style="margin-left: 6px"
                         data-testid="identify-mac-seen">{{ t("identify.mac_seen", { mac: macSeenOther }) }}</n-tag>
                </template>
                <span v-else data-testid="identify-nic-vendor">{{ job.summary.nic_vendor ?? "—" }}</span>
              </n-descriptions-item>
              <n-descriptions-item :label="t('identify.model')">
                <span data-testid="identify-model">{{ job.summary.model ?? "—" }}</span>
              </n-descriptions-item>
              <!-- Windows 自己講的電腦名稱、網域／工作群組（RDP 的 NTLM 資訊、SMB）：以前抓到了卻沒顯示 -->
              <n-descriptions-item v-if="windowsText" :label="t('identify.windows_label')">
                <span data-testid="identify-windows">{{ windowsText }}</span>
              </n-descriptions-item>
              <n-descriptions-item v-if="portCountsText" :label="t('identify.port_counts_label')">
                <span data-testid="identify-port-counts">{{ portCountsText }}</span>
              </n-descriptions-item>
              <n-descriptions-item v-if="scanText" :label="t('identify.scan_label')">
                <span data-testid="identify-scan">{{ scanText }}</span>
              </n-descriptions-item>
              <n-descriptions-item :label="t('identify.names')" :span="narrow ? 1 : 2">
                <template v-if="nameRows.length">
                  <div v-for="n in nameRows" :key="n.name" class="idf-name" data-testid="identify-name">
                    <span class="idf-mono">{{ n.name }}</span>
                    <span v-if="n.sources.length" class="idf-name__src">{{ n.sources.map(nameSource).join(" · ") }}</span>
                  </div>
                </template>
                <template v-else>—</template>
              </n-descriptions-item>
              <n-descriptions-item :label="t('identify.applications')" :span="narrow ? 1 : 2">
                <n-space v-if="job.summary.applications?.length" :size="4" data-testid="identify-apps">
                  <n-tag v-for="a in job.summary.applications" :key="a" size="small" :bordered="false" type="info">{{ a }}</n-tag>
                </n-space>
                <template v-else>—</template>
              </n-descriptions-item>
              <n-descriptions-item :label="t('identify.evidence')" :span="narrow ? 1 : 2">
                <n-space v-if="job.summary.evidence.length" :size="4">
                  <n-tag v-for="e in job.summary.evidence" :key="e" size="small" :bordered="false"
                         :type="evidenceType(e)" :color="e.startsWith('recog:') ? RECOG_TAG : undefined">{{ e }}</n-tag>
                </n-space>
                <template v-else>{{ t("identify.no_evidence") }}</template>
              </n-descriptions-item>
              <!-- 判斷的理由：為什麼作業系統是「—」、為什麼不採信指紋、哪些服務只是照埠號猜的 -->
              <n-descriptions-item v-if="noteTexts.length" :label="t('identify.notes_label')" :span="narrow ? 1 : 2">
                <ul class="idf-notes" data-testid="identify-notes">
                  <li v-for="n in noteTexts" :key="n">{{ n }}</li>
                </ul>
              </n-descriptions-item>
            </n-descriptions>
            <div class="idf-guess-note" data-testid="identify-guess-note">{{ t("identify.guess_note") }}</div>
            <div class="idf-guess-note" data-testid="identify-recog-note">
              {{ job.summary.recog ? t("identify.recog_used", { v: job.summary.recog }) : t("identify.recog_missing") }}
            </div>
          </n-card>

          <!-- TLS 憑證與 SSH 主機金鑰：簽發者、到期日、指紋（以前只取憑證上的名稱） -->
          <n-card v-if="certs.length || sshKeys.length" :title="t('identify.certs_title')" size="small"
                  data-testid="identify-certs">
            <n-data-table v-if="certs.length" :columns="certCols" :data="certs" size="small" :bordered="false"
                          :row-key="(r: IdentifyCert) => r.port" :scroll-x="820" />
            <n-data-table v-if="sshKeys.length" :columns="keyCols" :data="sshKeys" size="small" :bordered="false"
                          :row-key="(r: IdentifySshKey) => `${r.port}-${r.type}`" :scroll-x="640"
                          :style="certs.length ? 'margin-top: 10px' : ''" data-testid="identify-ssh-keys" />
          </n-card>

          <!-- 跟上一次比：新開、關掉、版本變了的服務，以及 MAC、作業系統、名稱、金鑰、憑證 -->
          <n-card v-if="job.changes" size="small" data-testid="identify-changes"
                  :title="t('identify.changes_since', { at: fmtDateTime(job.changes.previous_at) })">
            <div v-if="cmpBlocked" class="idf-muted" data-testid="identify-cmp-blocked">{{ cmpBlocked }}</div>
            <div v-else-if="!hasChanges" class="idf-muted">{{ t("identify.no_changes") }}</div>
            <div v-else class="idf-changes">
              <div v-if="job.changes.opened.length">
                <span class="idf-changes__k">{{ t("identify.opened") }}</span>
                <n-tag v-for="p in job.changes.opened" :key="p" size="small" type="success" :bordered="false">{{ p }}</n-tag>
              </div>
              <div v-if="job.changes.closed.length">
                <span class="idf-changes__k">{{ t("identify.closed") }}</span>
                <n-tag v-for="p in job.changes.closed" :key="p" size="small" type="error" :bordered="false">{{ p }}</n-tag>
              </div>
              <div v-for="c in job.changes.changed" :key="c.port">
                <span class="idf-changes__k">{{ t("identify.changed") }}</span>
                <n-tag size="small" type="warning" :bordered="false">{{ c.port }}</n-tag>
                <span class="idf-changes__v">{{ c.before }} → {{ c.after }}</span>
              </div>
              <div v-for="f in job.changes.fields ?? []" :key="f.field" data-testid="identify-change-field">
                <span class="idf-changes__k">{{ t(`identify.cmp_field_${f.field}`) }}</span>
                <span class="idf-changes__v">{{ cmpValue(f.field, f.before) }} → {{ cmpValue(f.field, f.after) }}</span>
              </div>
              <div v-if="job.changes.names_added?.length">
                <span class="idf-changes__k">{{ t("identify.names_added") }}</span>
                <span class="idf-changes__v idf-mono">{{ job.changes.names_added.join(", ") }}</span>
              </div>
              <div v-if="job.changes.names_removed?.length">
                <span class="idf-changes__k">{{ t("identify.names_removed") }}</span>
                <span class="idf-changes__v idf-mono">{{ job.changes.names_removed.join(", ") }}</span>
              </div>
              <div v-for="k in job.changes.ssh_keys ?? []" :key="`k-${k.port}-${k.type}`" data-testid="identify-change-key">
                <span class="idf-changes__k">{{ t("identify.key_changed") }}</span>
                <n-tag size="small" type="error" :bordered="false">{{ k.port }}</n-tag>
                <span class="idf-changes__v idf-mono">{{ k.type }} {{ k.before }} → {{ k.after }}</span>
              </div>
              <div v-for="c in job.changes.certs ?? []" :key="`c-${c.port}`" data-testid="identify-change-cert">
                <span class="idf-changes__k">{{ t("identify.cert_changed") }}</span>
                <n-tag size="small" type="error" :bordered="false">{{ c.port }}</n-tag>
                <span class="idf-changes__v">{{ certChangeText(c) }}</span>
              </div>
              <div v-if="(job.changes.ssh_keys?.length ?? 0) + (job.changes.certs?.length ?? 0)" class="idf-muted">
                {{ t("identify.key_changed_hint") }}
              </div>
            </div>
          </n-card>

          <n-card :title="t('identify.ports', { n: ports.length })" size="small">
            <n-data-table v-if="ports.length" :columns="portCols" :data="ports" size="small" :bordered="false"
                          :row-key="(r: IdentifyPort) => `${r.proto}/${r.port}`" :scroll-x="portTableWidth"
                          data-testid="identify-ports" />
            <div v-else class="idf-muted">{{ t("identify.no_ports") }}</div>
          </n-card>

          <n-card size="small">
            <n-collapse>
              <n-collapse-item :title="t('identify.raw')" name="raw">
                <template #header-extra>
                  <n-button size="tiny" data-testid="identify-download" @click.stop="downloadRaw">
                    <template #icon><n-icon><DownloadIcon /></n-icon></template>{{ t("identify.download") }}
                  </n-button>
                </template>
                <pre class="idf-raw">{{ rawText }}</pre>
              </n-collapse-item>
            </n-collapse>
          </n-card>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  NAlert, NButton, NCard, NCollapse, NCollapseItem, NDataTable, NDescriptions, NDescriptionsItem, NIcon,
  NPopconfirm, NResult, NSpace, NSpin, NTag, NTooltip, type DataTableColumns,
} from "naive-ui";
import { useI18n } from "vue-i18n";
import { isRandomMac } from "@/utils/mac";
import { apiErrMsg } from "@/api/client";
import { getAddress } from "@/api/addresses";
import {
  cancelIdentify, getIdentify, getIdentifyIpTarget, identifyHistory, startIdentify,
  type IdentifyBrief, type IdentifyCert, type IdentifyIpTarget, type IdentifyJob, type IdentifyPort,
  type IdentifySshKey, type IdentifyStatus, type IdentifyTarget,
} from "@/api/identify";
import { ArrowLeft as ArrowLeftIcon } from "@iconoir/vue";
import { CheckIcon, DownloadIcon, IdentifyIcon, CancelIcon } from "@/icons";
import { useAuthStore } from "@/stores/auth";
import type { IPAddress } from "@/types";
import { fmtDateTime } from "@/utils/datetime";
import { decodeNmapEscapes, serviceKind } from "@/utils/nmapText";
import { saveBlob } from "@/utils/saveFile";
import { investigate } from "@/api/investigate";
import { integrationTables, type IntegTable, type SectionHelpers } from "@/utils/investigateSections";
import { lnmsStatusLabel, wazuhStatusLabel } from "@/utils/integrationStatus";

const route = useRoute();
const router = useRouter();
const { t, te, locale } = useI18n();
const auth = useAuthStore();

// 兩種進入點：IP 記錄（/addresses/:id/identify）或 IPAM 沒有記錄的位址（/identify/ip/:ip，
// 從異常偵測的「未授權 IP」過來）。後者由後端確認位址在管理的子網路內。
const byIp = computed(() => (route.params.ip ? String(route.params.ip) : ""));
const addressId = computed(() => String(route.params.id ?? ""));
const target = computed<IdentifyTarget>(() => (byIp.value ? { ip: byIp.value } : { addressId: addressId.value }));
const isAdmin = computed(() => !!auth.me?.is_admin);
const addr = ref<IPAddress | null>(null);
const ipTarget = ref<IdentifyIpTarget | null>(null);
const ipText = computed(() => (addr.value ? String(addr.value.ip).split("/")[0] : (ipTarget.value?.ip ?? byIp.value)));

// 其他整合系統的對應：已登記的位址拿調查檔案（各整合回報的段落）；未登記的只有虛擬化（標題資訊一起帶回）
const dossier = ref<any>(null);
const loadingDossier = ref(false);
const invHelpers: SectionHelpers = { t: (k, p) => (p ? t(k, p) : t(k)), te: (k) => te(k), fmt: (v) => fmtDateTime(v) };
const integrations = computed(() => integrationTables(
  dossier.value, invHelpers, (s) => wazuhStatusLabel(t, s), (s) => lnmsStatusLabel(t, s)));
function integCols(tb: IntegTable): DataTableColumns<Record<string, string>> {
  return tb.cols.map((title, i) => ({ title, key: `c${i}`, minWidth: 90 }));
}
function integRows(tb: IntegTable): Record<string, string>[] {
  return tb.rows.map((r, n) => Object.fromEntries([["__k", String(n)], ...r.map((v, i) => [`c${i}`, v || "—"])]));
}
async function loadIntegrations() {
  dossier.value = null;
  if (ipTarget.value) {
    dossier.value = { found: true, virtualization: ipTarget.value.virtualization ?? null };
    return;
  }
  if (!addr.value) return;
  loadingDossier.value = true;
  try {
    dossier.value = (await investigate(ipText.value, false, locale.value)).dossier;
  } catch {
    dossier.value = null;   // 拿不到就不顯示對應，不擋探測本身
  } finally {
    loadingDossier.value = false;
  }
}

const history = ref<IdentifyBrief[]>([]);
const loadingHistory = ref(true);
const selectedId = ref<string | null>(null);
const job = ref<IdentifyJob | null>(null);
const starting = ref(false);
const errorText = ref("");
const now = ref(Date.now());
let pollTimer: ReturnType<typeof setTimeout> | null = null;
let clockTimer: ReturnType<typeof setInterval> | null = null;

const winW = ref(window.innerWidth);
const narrow = computed(() => winW.value < 720);
function onResize() { winW.value = window.innerWidth; }

const isRunning = (s?: IdentifyStatus) => s === "pending" || s === "running";
const running = computed(() => isRunning(job.value?.status));
const anyRunning = computed(() => history.value.some((x) => isRunning(x.status)));
const ports = computed<IdentifyPort[]>(() => job.value?.result?.nmap?.ports ?? []);
/** nmap 自己失敗（結束碼非零、輸出裡沒有這台、逾時）：不是主機沒回應 */
const scanError = computed(() => {
  const s = job.value?.summary;
  if (s?.scan_failed) return t("identify.scan_failed", { reason: s.scan_error ?? "" });
  return job.value?.result?.nmap?.error ?? "";
});
const rawText = computed(() => JSON.stringify(job.value?.result ?? null, null, 2));
const hasChanges = computed(() => {
  const c = job.value?.changes;
  if (!c) return false;
  return (c.opened.length + c.closed.length + c.changed.length + (c.fields?.length ?? 0)
          + (c.names_added?.length ?? 0) + (c.names_removed?.length ?? 0)
          + (c.ssh_keys?.length ?? 0) + (c.certs?.length ?? 0)) > 0;
});
/** 其中一次沒回應／失敗：連接埠不比（以前上一次沒回應，這次所有埠都算「新開」） */
const cmpBlocked = computed(() => {
  const c = job.value?.changes;
  if (c?.baseline) return t(`identify.cmp_baseline_${c.baseline}`);
  if (c?.current) return t(`identify.cmp_current_${c.current}`);
  return "";
});
function cmpValue(field: string, v: string): string {
  return field === "device_type" ? t(`identify.type.${v}`) : v;
}
function certChangeText(c: NonNullable<IdentifyJob["changes"]>["certs"] extends (infer U)[] | undefined ? U : never): string {
  const fp = (x: Record<string, string | null>) => String(x.sha256 ?? x.sha1 ?? "").slice(0, 16);
  const exp = (x: Record<string, string | null>) => (x.not_after ? fmtDate(x.not_after) : "—");
  return `${fp(c.before)}… (${exp(c.before)}) → ${fp(c.after)}… (${exp(c.after)})`;
}

const summaryNotes = computed(() => job.value?.summary?.notes ?? []);
// MAC 不同、隨機 MAC 已經在 MAC 那一格用標籤標了，這裡不重複
const NOTES_SHOWN_ELSEWHERE = new Set(["mac_differs", "mac_random"]);
const noteTexts = computed(() => summaryNotes.value
  .filter((n) => !NOTES_SHOWN_ELSEWHERE.has(n.code) && te(`identify.note_${n.code}`))
  .map((n) => t(`identify.note_${n.code}`, n.params)));
const macSeenOther = computed(() => {
  const n = summaryNotes.value.find((x) => x.code === "mac_differs");
  return n ? String(n.params.seen ?? "") : "";
});
const windowsText = computed(() => {
  const w = job.value?.summary?.windows;
  if (!w) return "";
  const parts: string[] = [];
  if (w.computer) parts.push(t("identify.win_computer", { v: w.computer }));
  const dom = [w.domain, w.dns_domain && w.dns_domain !== w.domain ? w.dns_domain : null].filter(Boolean);
  if (dom.length) parts.push(t("identify.win_domain", { v: dom.length > 1 ? `${dom[0]}（${dom[1]}）` : String(dom[0]) }));
  if (w.workgroup) parts.push(t("identify.win_workgroup", { v: w.workgroup }));
  return parts.join(" · ");
});
const portCountsText = computed(() => {
  const c = job.value?.summary?.port_counts;
  if (!c) return "";
  return c.filtered == null
    ? t("identify.port_counts_nofilter", { open: c.open, closed: c.closed })
    : t("identify.port_counts", { open: c.open, closed: c.closed, filtered: c.filtered });
});
function fmtDuration(sec: number): string {
  const s = Math.round(sec);
  return s >= 60 ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}` : `0:${String(s).padStart(2, "0")}`;
}
const scanText = computed(() => {
  const s = job.value?.summary;
  if (!s) return "";
  const parts: string[] = [];
  if (s.elapsed != null) parts.push(t("identify.took", { s: fmtDuration(s.elapsed) }));
  if (s.distance != null) parts.push(t("identify.distance", { n: s.distance }));
  if (s.uptime_seconds) parts.push(t("identify.uptime", { d: Math.max(1, Math.round(s.uptime_seconds / 86400)) }));
  return parts.join(" · ");
});
/** 名稱與來源（新摘要）；舊摘要只有名稱 */
const nameRows = computed(() => {
  const s = job.value?.summary;
  if (!s) return [];
  return s.name_sources ?? s.names.map((name) => ({ name, sources: [] as string[] }));
});
function nameSource(src: string): string {
  const key = `identify.name_src_${src}`;
  return te(key) ? t(key) : src;
}
const certs = computed<IdentifyCert[]>(() => job.value?.summary?.certs ?? []);
const sshKeys = computed<IdentifySshKey[]>(() => job.value?.summary?.ssh_keys ?? []);
function fmtDate(iso: string): string {
  return fmtDateTime(iso).slice(0, 10);
}
function daysLeft(iso: string | null): number | null {
  if (!iso) return null;
  const ms = new Date(iso.endsWith("Z") || /[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`).getTime();
  return Number.isFinite(ms) ? Math.floor((ms - Date.now()) / 86400000) : null;
}
const certCols = computed<DataTableColumns<IdentifyCert>>(() => [
  { title: t("identify.col_port"), key: "port", width: 90, render: (r) => h("span", { class: "idf-mono" }, r.port) },
  // 主體一行、簽發者（或「自簽」）與其他名稱在第二行：六欄並排在一般視窗寬度放不下
  { title: t("identify.col_subject"), key: "subject", minWidth: 240,
    render: (r) => h("div", null, [
      h("div", { class: "idf-mono" }, r.subject ?? "—"),
      h("div", { class: "idf-cert-sub" }, [
        r.self_signed
          ? h(NTag, { size: "tiny", bordered: false }, { default: () => t("identify.self_signed") })
          : h("span", null, t("identify.issued_by", { v: r.issuer ?? "—" })),
        r.san.length > 1 ? h("span", null, t("identify.san_more", { n: r.san.length - 1 })) : null,
      ]),
    ]) },
  { title: t("identify.col_valid_until"), key: "not_after", width: 170,
    render: (r) => {
      if (!r.not_after) return "—";
      const d = daysLeft(r.not_after);
      const tag = d == null ? null : d < 0
        ? h(NTag, { size: "tiny", type: "error", bordered: false, style: "margin-left: 6px" }, { default: () => t("identify.expired") })
        : d <= 30
          ? h(NTag, { size: "tiny", type: "warning", bordered: false, style: "margin-left: 6px" },
              { default: () => t("identify.expires_in", { n: d }) })
          : null;
      return h("span", null, [fmtDate(r.not_after), tag]);
    } },
  { title: t("identify.col_key"), key: "key", width: 90, render: (r) => r.key ?? "—" },
  // 指紋很長（SHA-256 是 64 個十六進位字）：顯示前 6 位元組，滑過看完整值
  { title: t("identify.col_fingerprint"), key: "sha256", width: 230,
    render: (r) => {
      const [algo, hex] = r.sha256 ? ["SHA-256", r.sha256] : r.sha1 ? ["SHA-1", r.sha1] : ["", ""];
      if (!hex) return "—";
      const short = (hex.match(/../g) ?? []).slice(0, 6).join(":").toUpperCase();
      return h("span", { class: "idf-fp", title: `${algo} ${hex}` }, `${algo} ${short}…`);
    } },
]);
const keyCols = computed<DataTableColumns<IdentifySshKey>>(() => [
  { title: t("identify.col_port"), key: "port", width: 90, render: (r) => h("span", { class: "idf-mono" }, r.port) },
  { title: t("identify.col_key_type"), key: "type", width: 190,
    render: (r) => h("span", { class: "idf-mono" }, `${r.type ?? "—"}${r.bits ? ` (${r.bits})` : ""}`) },
  { title: t("identify.col_fingerprint"), key: "fingerprint", minWidth: 320,
    render: (r) => h("span", { class: "idf-fp" }, r.fingerprint ?? "—") },
]);

/** 顯示的 MAC：跟後端算網卡廠牌用的同一個（IP 記錄上的優先，沒有才用這次 nmap 看到的，同網段才拿得到），
 *  不然會出現「這個 MAC 配上另一個 MAC 的廠牌」 */
const probeMac = computed(() => {
  const s = job.value?.summary;
  // 新摘要直接告訴我們用的是哪一個（未登記 IP 用 ARP 的 MAC 算廠牌）；舊摘要照以前的推法
  const m = (s && "mac" in s ? s.mac : null) || (addr.value as any)?.mac || job.value?.result?.nmap?.mac || "";
  return m ? String(m).toLowerCase() : "";
});

const jobError = computed(() => {
  const j = job.value;
  if (!j || (j.status !== "failed" && j.status !== "expired")) return "";
  if (j.error_code) return t(`errors.${j.error_code}`);
  if (j.status === "expired") return t("identify.st_expired_hint");
  return j.error || t("identify.st_failed");
});

function statusLabel(s: IdentifyStatus): string {
  return t(`identify.st_short_${s}`);
}
function statusType(s: IdentifyStatus): "default" | "info" | "success" | "error" | "warning" {
  if (s === "done") return "success";
  if (s === "failed") return "error";
  if (s === "expired") return "warning";
  return "info";
}

const statusText = computed(() => {
  const j = job.value;
  if (!j) return "";
  const who = j.agent_name ? t("identify.by_agent", { agent: j.agent_name }) : "";
  switch (j.status) {
    case "pending": return `${t("identify.st_pending")}${who}`;
    case "running": return `${t("identify.st_running")}${who}`;
    case "done": return `${t("identify.st_done", { at: fmtDateTime(j.finished_at) })}${who}`;
    case "expired": return t("identify.st_expired");
    default: return t("identify.st_failed");
  }
});

/** 進度的四個階段：排隊 → 名稱查詢 → 連接埠／服務／OS → 整理結果 */
const steps = computed(() => {
  const j = job.value;
  const stage = j?.status === "pending" ? "queue" : (j?.progress?.stage || "queue_done");
  const order = ["queue", "names", "scan", "finish"];
  const cur = stage === "queue_done" ? 1 : Math.max(0, order.indexOf(stage));
  return order.map((key, i) => ({ key, state: i < cur ? "done" : i === cur ? "active" : "todo" }));
});
const elapsedText = computed(() => {
  const j = job.value;
  if (!j) return "0";
  const since = new Date(j.created_at).getTime();
  const sec = Math.max(0, Math.round((now.value - since) / 1000));
  return sec >= 60 ? `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}` : `0:${String(sec).padStart(2, "0")}`;
});

/** 判斷依據依來源上色：OS 指紋／服務特徵／OUI 廠商／Recog 指紋（紫色，跟服務特徵的綠色分開） */
const RECOG_TAG = { color: "rgba(138, 99, 210, .16)", textColor: "#8a63d2" };
function evidenceType(e: string): "info" | "success" | "warning" | "default" {
  if (e.startsWith("service:")) return "success";
  if (e.startsWith("os:") || e.startsWith("osclass:")) return "info";
  if (e.startsWith("oui:")) return "warning";
  return "default";
}

/**
 * 連接埠表：服務依類型上色（遠端連線／網頁／目錄與資料庫／檔案與列印／郵件／基礎設施），
 * 產品加粗、版本淡色，讀到的資訊一項一框，nmap 轉義的中文（\xHH）解回文字。
 */
// 欄寬依內容（使用者回饋：畫面拉寬了，產品與其他資訊兩欄內容很少卻平分寬度，
// 真正長的「讀到的資訊」反而被擠窄）。短欄量出需要的寬度，剩下的全部給最後一欄。
let measureCtx: CanvasRenderingContext2D | null = null;
function textWidth(str: string, font = "14px"): number {
  if (!measureCtx) measureCtx = document.createElement("canvas").getContext("2d");
  if (!measureCtx) return str.length * 8;
  measureCtx.font = `${font} ${getComputedStyle(document.body).fontFamily}`;
  return measureCtx.measureText(str).width;
}
function fitWidth(header: string, values: string[], min: number, max: number): number {
  const w = Math.max(textWidth(header) + 24, ...values.map((v) => textWidth(v)));
  return Math.round(Math.min(max, Math.max(min, w + 32)));
}
// 產品一行、版本另起一行（2026-10-21：「OpenSSH 9.6p1 Ubuntu 3ubuntu13.20」整串一行把欄位撐得太寬），
// 欄寬只看兩者中比較長的那段，再長的在欄內折行
const productWidth = computed(() => fitWidth(t("identify.col_product"),
  ports.value.flatMap((p) => [p.product || "", p.version || ""]), 110, 240));
const extraWidth = computed(() => fitWidth(t("identify.col_extra"),
  ports.value.map((p) => p.extrainfo || ""), 100, 300));
const portTableWidth = computed(() => 100 + 150 + productWidth.value + extraWidth.value + 360);

const portCols = computed<DataTableColumns<IdentifyPort>>(() => [
  { title: t("identify.col_port"), key: "port", width: 100,
    render: (r) => h("span", { class: "idf-port" }, [h("b", null, String(r.port)), h("span", null, `/${r.proto}`)]) },
  { title: t("identify.col_service"), key: "service", width: 150,
    render: (r) => (r.service
      ? h("span", null, [
          h("span", { class: `idf-svc idf-svc--${serviceKind(r.service)}` },
            r.tunnel ? `${r.service} · ${r.tunnel}` : r.service),
          // nmap 沒認出服務、只是照埠號表寫的名稱：標出來，不要看起來像真的認出來
          r.method === "table"
            ? h(NTooltip, null, {
                trigger: () => h("span", { class: "idf-svc-guess", "data-testid": "identify-port-guess" },
                                 t("identify.guess_port_tag")),
                default: () => t("identify.guess_port_hint"),
              })
            : null,
        ])
      : "—") },
  { title: t("identify.col_product"), key: "product", width: productWidth.value,
    render: (r) => (r.product || r.version
      ? h("div", { class: "idf-prodcell" }, [r.product ? h("div", { class: "idf-prod" }, r.product) : null,
                                             r.version ? h("div", { class: "idf-ver" }, r.version) : null])
      : "—") },
  { title: t("identify.col_extra"), key: "extrainfo", width: extraWidth.value,
    render: (r) => (r.extrainfo ? h("span", { class: "idf-extra" }, r.extrainfo) : "—") },
  // 最後一欄不給寬度：吃掉剩下的全部空間
  { title: t("identify.col_scripts"), key: "scripts", minWidth: 360,
    render: (r) => {
      const entries = Object.entries(r.scripts ?? {});
      if (!entries.length) return "—";
      return h("div", { class: "idf-scripts" }, entries.map(([k, v]) =>
        h("div", { class: "idf-script" }, [
          h("span", { class: "idf-script__k" }, [k, isCut(r, k, v) ? h("span", { class: "idf-script__cut" },
                                                                         t("identify.truncated")) : null]),
          h("pre", { class: "idf-script__v" }, decodeNmapEscapes(v)),
        ])));
    } },
]);

/** 腳本輸出被截斷了沒：新代理直接講；舊代理看長度剛好是上限（600 字） */
function isCut(r: IdentifyPort, key: string, text: string): boolean {
  return r.truncated ? r.truncated.includes(key) : text.length >= 600;
}

function stopPolling() {
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
}

async function loadJob(id: string) {
  try {
    job.value = await getIdentify(target.value, id);
    // 清單上的狀態跟著更新（不用整份重抓）
    const row = history.value.find((x) => x.job_id === id);
    if (row && job.value) Object.assign(row, { status: job.value.status, summary: job.value.summary,
                                                finished_at: job.value.finished_at });
    errorText.value = "";
  } catch (e) {
    errorText.value = apiErrMsg(e);
  }
}

async function poll() {
  stopPolling();
  if (!selectedId.value) return;
  await loadJob(selectedId.value);
  if (running.value) pollTimer = setTimeout(poll, 3000);
}

async function loadHistory() {
  loadingHistory.value = true;
  try {
    history.value = await identifyHistory(target.value);
  } catch (e) {
    errorText.value = apiErrMsg(e);
  } finally {
    loadingHistory.value = false;
  }
}

async function select(id: string) {
  selectedId.value = id;
  void router.replace({ query: { ...route.query, job: id } });
  await poll();
}

const cancelling = ref(false);
async function cancel() {
  if (!job.value) return;
  cancelling.value = true;
  try {
    const id = job.value.job_id;
    await cancelIdentify(target.value, id);
    stopPolling();
    await loadJob(id);
    await loadHistory();
  } catch (e) {
    errorText.value = apiErrMsg(e);
  } finally {
    cancelling.value = false;
  }
}

async function start() {
  starting.value = true;
  errorText.value = "";
  try {
    const r = await startIdentify(target.value);
    await loadHistory();
    await select(r.job_id);
  } catch (e) {
    errorText.value = apiErrMsg(e);
  } finally {
    starting.value = false;
  }
}

function downloadRaw() {
  const stamp = (job.value?.finished_at || job.value?.created_at || "").replace(/[:.]/g, "-").slice(0, 19);
  const blob = new Blob([rawText.value], { type: "application/json" });
  saveBlob(`identify-${ipText.value}-${stamp}.json`, blob, blob.type, { source: "ip-identify" });
}

function goBack() {
  if (!byIp.value) { void router.push({ name: "address-detail", params: { id: addressId.value } }); return; }
  // 以位址探測沒有 IP 詳細頁可回：回上一頁（通常是異常偵測），直接開網址的話回異常偵測
  if (window.history.state?.back) router.back();
  else void router.push({ name: "anomaly" });
}

async function init() {
  stopPolling();
  job.value = null;
  selectedId.value = null;
  if (!isAdmin.value) { loadingHistory.value = false; return; }
  addr.value = null;
  ipTarget.value = null;
  try {
    if (byIp.value) {
      const info = await getIdentifyIpTarget(byIp.value);
      // 已經登記過的位址：改用那筆記錄的探測頁（歷次結果是同一份，以位址記的）
      if (info.address_id) {
        void router.replace({ name: "address-identify", params: { id: info.address_id }, query: route.query });
        return;
      }
      ipTarget.value = info;
    } else {
      addr.value = await getAddress(addressId.value);
    }
  } catch (e) {
    errorText.value = apiErrMsg(e);
    loadingHistory.value = false;
    return;
  }
  void loadIntegrations();
  await loadHistory();
  const wanted = String(route.query.job || "");
  const first = history.value.find((x) => x.job_id === wanted) ?? history.value[0];
  if (first) await select(first.job_id);
}

// isAdmin 也要看：剛登入時帳號資料可能還沒到（isAdmin 先是 false，init 直接結束），到了要重載，
// 否則歷次探測與整合對應都停在空白
watch([addressId, byIp, isAdmin], () => void init());
onMounted(() => {
  window.addEventListener("resize", onResize);
  clockTimer = setInterval(() => { now.value = Date.now(); }, 1000);
  void init();
});
onBeforeUnmount(() => {
  stopPolling();
  if (clockTimer) clearInterval(clockTimer);
  window.removeEventListener("resize", onResize);
});

/** 依據代碼（device:firewall、librenms:opnsense、wazuh:linux、virt:guest…）→ 人看得懂的來源名稱 */
function ipamSource(reason: string): string {
  const src = (reason || "").split(":")[0];
  const key = `identify.ipam_src_${src}`;
  return te(key) ? t(key) : src;
}
</script>

<style scoped>
.idf-page { display: flex; flex-direction: column; gap: 12px; }
.idf-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.idf-head__title { display: flex; align-items: center; gap: 8px; font-size: 17px; font-weight: 600; flex-wrap: wrap; min-width: 0; }
/* 空間不夠時整句換到下一行，不要斷在句子中間（「推測，可能不」/「準」，使用者 2026-10-07） */
.idf-type-row { display: inline-flex; flex-wrap: wrap; align-items: center; gap: 4px 8px; }
.idf-guess { white-space: nowrap; font-size: 12px; opacity: .6; }
.idf-guess-note { margin-top: 8px; font-size: 12px; opacity: .7; line-height: 1.6; }
.idf-arp { font-size: 12.5px; font-weight: 400; opacity: .7; }
.idf-head__ip { font-variant-numeric: tabular-nums; }
.idf-intro { margin: 8px 0 0; font-size: 13px; opacity: .75; line-height: 1.6; }
.idf-body { display: grid; grid-template-columns: 260px minmax(0, 1fr); gap: 12px; align-items: start; }
.idf-main { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.idf-hist__list { display: flex; flex-direction: column; gap: 4px; max-height: 70vh; overflow-y: auto; }
.idf-hist__item { all: unset; box-sizing: border-box; cursor: pointer; padding: 8px 10px; border-radius: 8px;
  border: 1px solid transparent; display: flex; flex-direction: column; gap: 2px; }
.idf-hist__item:hover { background: rgba(128, 128, 128, .1); }
.idf-hist__item.is-active { border-color: #18a058; background: rgba(24, 160, 88, .08); }
.idf-hist__top { display: flex; justify-content: space-between; align-items: center; gap: 6px; font-size: 13px;
  font-variant-numeric: tabular-nums; }
.idf-hist__sub { font-size: 12px; opacity: .65; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.idf-status { display: flex; align-items: center; gap: 8px; }
.idf-muted { opacity: .6; font-size: 13px; }
.idf-integ { display: flex; flex-direction: column; gap: 14px; }
.idf-integ__k { font-weight: 600; font-size: 13px; margin-bottom: 4px; }
.idf-steps { list-style: none; margin: 0 0 8px; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.idf-steps li { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.idf-steps li.is-todo { opacity: .45; }
.idf-steps li.is-done { color: #18a058; }
.idf-steps__mark { width: 16px; display: inline-flex; justify-content: center; }
.idf-steps__dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; opacity: .6; }
.idf-changes { display: flex; flex-direction: column; gap: 6px; }
.idf-changes > div { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.idf-changes__k { font-size: 13px; opacity: .7; min-width: 4em; }
.idf-changes__v { font-size: 13px; }
.idf-raw { max-height: 420px; overflow: auto; font-size: 12px; margin: 0; white-space: pre-wrap; word-break: break-all; }
/* 摘要 */
.idf-type { display: inline-block; white-space: nowrap; padding: 2px 10px; border-radius: 999px; font-weight: 600; font-size: 13px;
  background: rgba(32, 128, 240, .14); color: #2080f0; }
.idf-type--server { background: rgba(32, 128, 240, .14); color: #2080f0; }
.idf-type--no_response { background: rgba(240, 160, 32, .16); color: #d08a00; }
.idf-type--windows { background: rgba(0, 120, 212, .14); color: #1a7fd4; }
.idf-type--hypervisor, .idf-type--storage { background: rgba(138, 92, 246, .16); color: #8a5cf6; }
.idf-ipam { margin-top: 4px; font-size: 12px; opacity: .85; }
.idf-type--router, .idf-type--switch, .idf-type--firewall, .idf-type--wireless_ap {
  background: rgba(24, 160, 88, .15); color: #18a058; }
.idf-type--printer, .idf-type--camera, .idf-type--voip, .idf-type--media, .idf-type--specialized {
  background: rgba(240, 160, 32, .16); color: #d98a12; }
.idf-type--unknown { background: rgba(128, 128, 128, .16); color: inherit; opacity: .8; }
.idf-os { font-weight: 600; }
.idf-mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 13px; }
.idf-name { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.idf-name__src { font-size: 12px; opacity: .6; }
.idf-notes { margin: 0; padding-left: 18px; font-size: 13px; line-height: 1.6; }
:deep(.idf-fp) { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; word-break: break-all; }
:deep(.idf-cert-sub) { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 2px; font-size: 12px; opacity: .7; }
:deep(.idf-svc-guess) { margin-left: 6px; font-size: 11px; opacity: .65; border-bottom: 1px dotted currentColor; cursor: help; }
:deep(.idf-script__cut) { margin-left: 6px; font-weight: 400; color: #d08a00; }
/* 連接埠表 */
:deep(.idf-port) { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 13px; }
:deep(.idf-port b) { font-weight: 700; }
:deep(.idf-port span) { opacity: .55; }
:deep(.idf-svc) { display: inline-block; padding: 1px 8px; border-radius: 6px; font-size: 12px; font-weight: 600;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  background: rgba(128, 128, 128, .14); }
:deep(.idf-svc--remote) { background: rgba(240, 160, 32, .16); color: #d98a12; }
:deep(.idf-svc--web) { background: rgba(32, 128, 240, .14); color: #2080f0; }
:deep(.idf-svc--data) { background: rgba(138, 92, 246, .16); color: #8a5cf6; }
:deep(.idf-svc--file) { background: rgba(24, 160, 88, .15); color: #18a058; }
:deep(.idf-svc--mail) { background: rgba(208, 48, 80, .13); color: #d03050; }
:deep(.idf-svc--infra) { background: rgba(0, 160, 170, .15); color: #0e9aa7; }
:deep(.idf-prodcell) { overflow-wrap: anywhere; line-height: 1.45; }
:deep(.idf-prod) { font-weight: 600; }
:deep(.idf-ver) { opacity: .6; font-size: 12.5px; font-variant-numeric: tabular-nums; }
:deep(.idf-extra) { font-size: 12.5px; opacity: .7; }
:deep(.idf-scripts) { display: flex; flex-direction: column; gap: 6px; }
:deep(.idf-script) { display: flex; flex-direction: column; gap: 2px; }
:deep(.idf-script__k) { font-size: 11px; font-weight: 700; letter-spacing: .02em; color: #18a058; }
:deep(.idf-script__v) { margin: 0; padding: 4px 8px; border-radius: 6px; background: rgba(128, 128, 128, .1);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 11.5px; line-height: 1.5;
  white-space: pre-wrap; word-break: break-all; }
@media (max-width: 720px) {
  .idf-body { grid-template-columns: minmax(0, 1fr); }
  .idf-hist__list { max-height: 220px; }
}
</style>
